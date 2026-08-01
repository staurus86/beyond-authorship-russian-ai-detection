#!/usr/bin/env python3
"""Шлюз 4: допустимо ли частично переиспользовать NLL и оценки судьи.

    python 09-tools/reuse_gate_check.py --judge --nll

Перед пересчётом изменённых документов проверяется, воспроизводит ли среда
прежние числа на **неизменённых** текстах. Выборка фиксированная: документы,
у которых профиль `prose` совпадает между prep-v4 и prep-v5 побитово.

Судья: сравниваются **три исходные оценки** при тех же сидах, а не только
медиана. Если тройки не воспроизводятся, старые и новые оценки смешивать
нельзя — понадобится полный прогон процедуры 4 на prep-v5, иначе появится
неотделимый batch-эффект.

NLL: сравнивается значение с прежним при той же модели и ревизии.

Скрипт не пересчитывает корпус и ничего не перезаписывает.
"""

import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

V4 = ROOT / "04-corpus" / "derived" / "prep-v4"
V5 = ROOT / "04-corpus" / "derived" / "prep-v5"
JUDGE_SCORES = ROOT / "07-analysis" / "judge-v1-scores.csv"
JUDGE_MANIFEST = ROOT / "07-analysis" / "judge-v1-manifest.json"
NLL_SCORES = ROOT / "07-analysis" / "nll-v1-scores.csv"
NLL_MANIFEST = ROOT / "07-analysis" / "nll-v1-manifest.json"
SPEC = ROOT / "07-analysis" / "proc4-judge-spec.md"
OUT = ROOT / "07-analysis" / "reuse-gate-v5.md"

JUDGE_N, NLL_N = 5, 10
NLL_TOL = 1e-4          # погрешность float32 на CPU между запусками
SEEDS = [20260727, 20260728, 20260729]
API = "http://localhost:11434/api/generate"


def manifest(path):
    with (path / "manifest.csv").open(encoding="utf-8", newline="") as fh:
        return {r["document_id"]: r for r in csv.DictReader(fh)}


def unchanged_docs():
    """Неизменённые документы, чередуя классы: проверка не должна лечь на один канал."""
    m4, m5 = manifest(V4), manifest(V5)
    same = sorted(d for d in set(m4) & set(m5)
                  if m4[d]["prose_sha256"] == m5[d]["prose_sha256"])
    machine = [d for d in same if m5[d]["origin_class"] == "A"]
    human = [d for d in same if m5[d]["origin_class"] == "H"]
    mixed = []
    for i in range(max(len(machine), len(human))):
        if i < len(machine):
            mixed.append(machine[i])
        if i < len(human):
            mixed.append(human[i])
    return mixed


def judge_prompt_template():
    """Промпт §2 спецификации — тот же источник, что у judge_run.py."""
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("```", text.index("## 2. Промпт")) + 3
    end = text.index("```", start)
    return text[start:end].strip("\n")


def call_judge(cfg, prompt, seed):
    payload = {
        "model": cfg["model"], "prompt": prompt, "stream": False,
        "format": {"type": "object",
                   "properties": {"score": {"type": "integer"},
                                  "reason": {"type": "string"}},
                   "required": ["score", "reason"]},
        "options": {"temperature": 1.0, "top_p": 1.0, "top_k": 64,
                    "seed": seed, "num_ctx": cfg["num_ctx"],
                    "num_predict": cfg["num_predict"]},
    }
    request = urllib.request.Request(
        API, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        body = json.loads(response.read().decode("utf-8"))
    return json.loads(body["response"])["score"]


def check_judge(docs, lines):
    cfg_raw = json.loads(JUDGE_MANIFEST.read_text(encoding="utf-8"))["judge"]
    cfg = {"model": cfg_raw["model"], "num_ctx": 16384, "num_predict": 200}

    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=30) as r:
        tags = json.loads(r.read().decode("utf-8"))
    live = next((m for m in tags["models"] if m["name"] == cfg["model"]), None)
    same_digest = bool(live) and live["digest"] == cfg_raw["digest"]
    print(f"  судья: модель {cfg['model']}, digest совпадает: {same_digest}")

    with JUDGE_SCORES.open(encoding="utf-8", newline="") as fh:
        old = {r["document_id"]: r for r in csv.DictReader(fh) if r["status"] == "ok"}
    sample = [d for d in docs if d in old][:JUDGE_N]
    template = judge_prompt_template()

    rows, all_match = [], True
    for doc in sample:
        text = (V5 / "prose" / f"{doc}.txt").read_text(encoding="utf-8")
        prompt = template.replace("<<<TEXT>>>", text)
        got = [call_judge(cfg, prompt, seed) for seed in SEEDS]
        want = [int(float(old[doc][f"score_seed{i}"])) for i in (1, 2, 3)]
        match = got == want
        all_match &= match
        rows.append((doc, want, got, match))
        print(f"    {doc:34} было {want} стало {got} "
              f"{'совпало' if match else 'РАСХОЖДЕНИЕ'}")

    lines += ["## Судья: три оценки при тех же сидах", "",
              f"Модель `{cfg['model']}`, digest прежнего прогона "
              f"{'совпадает' if same_digest else '**не совпадает**'}, "
              f"температура 1.0, top_p 1.0, top_k 64, num_ctx {cfg['num_ctx']}, "
              f"num_predict {cfg['num_predict']}, сиды {SEEDS}.",
              "",
              "| Документ | Было | Стало | Совпало |", "|---|---|---|---|"]
    for doc, want, got, match in rows:
        lines.append(f"| `{doc}` | {want} | {got} | {'да' if match else '**нет**'} |")
    lines += ["", ("**Вердикт: тройки воспроизводятся.** Пересчёт только изменённых "
                   "человеческих документов допустим."
                   if all_match and same_digest else
                   "**Вердикт: воспроизведения нет.** Смешивать прежние и новые "
                   "оценки нельзя — процедуру 4 на prep-v5 нужно считать целиком, "
                   "иначе разница между старыми и новыми документами смешается с "
                   "batch-эффектом прогона."), ""]
    return all_match and same_digest


def check_nll(docs, lines):
    import nll_zero_shot as nz
    with NLL_SCORES.open(encoding="utf-8", newline="") as fh:
        old = {r["document_id"]: float(r["nll"]) for r in csv.DictReader(fh) if r["nll"]}
    sample = [d for d in docs if d in old][:NLL_N]

    _, cfg = nz.load_config()
    print(f"  NLL: модель {cfg['model']}, revision {cfg.get('revision', '')[:12]}, "
          f"устройство {cfg.get('device')}")
    scorer = nz.Scorer(cfg["model"], cfg.get("revision"), cfg.get("device", "cpu"),
                       getattr(nz.torch, cfg.get("dtype", "float32")))
    rows, worst = [], 0.0
    for doc in sample:
        text = (V5 / "prose" / f"{doc}.txt").read_text(encoding="utf-8")
        value, _, _, _ = scorer.score(text)
        delta = abs(value - old[doc])
        worst = max(worst, delta)
        rows.append((doc, old[doc], value, delta))
        print(f"    {doc:34} было {old[doc]:.6f} стало {value:.6f} Δ {delta:.2e}")

    lines += ["## NLL: воспроизведение на неизменённых текстах", "",
              "| Документ | Было | Стало | Δ |", "|---|---|---|---|"]
    for doc, want, got, delta in rows:
        lines.append(f"| `{doc}` | {want:.6f} | {got:.6f} | {delta:.2e} |")
    ok = worst <= NLL_TOL
    lines += ["", f"Наибольшее расхождение {worst:.2e} при допуске {NLL_TOL:.0e}. "
              + ("**Вердикт: воспроизводится.** Пересчитываются только изменённые "
                 "человеческие документы."
                 if ok else
                 "**Вердикт: не воспроизводится.** Процедуру 3 на prep-v5 нужно "
                 "считать целиком."), ""]
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--nll", action="store_true")
    args = parser.parse_args()
    if not (args.judge or args.nll):
        parser.error("укажите --judge и/или --nll")

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"шлюз переиспользования, {stamp}")
    docs = unchanged_docs()
    print(f"  неизменённых документов (prose sha256 совпал): {len(docs)}")

    lines = ["# Шлюз 4: допустимость частичного переиспользования", "",
             f"Собрано {stamp} скриптом `09-tools/reuse_gate_check.py`.", "",
             f"Выборка — документы, у которых профиль `prose` совпадает между "
             f"prep-v4 и prep-v5 побитово; таких {len(docs)}.", ""]
    verdicts = {}
    if args.judge:
        verdicts["судья"] = check_judge(docs, lines)
    if args.nll:
        verdicts["NLL"] = check_nll(docs, lines)

    lines += ["## Итог", "", "| Процедура | Переиспользование |", "|---|---|"]
    for name, ok in verdicts.items():
        lines.append(f"| {name} | {'частичный пересчёт допустим' if ok else '**нужен полный прогон**'} |")
    lines.append("")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT.name}")


if __name__ == "__main__":
    main()
