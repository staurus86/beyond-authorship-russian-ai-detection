"""Прогон пилотных ячеек real_claude через вложенный claude CLI.

Одна ячейка = один процесс = чистая сессия. Порядок ячеек задан сидом 20260723.
"""
import csv
import json
import pathlib
import random
import re
import subprocess
import sys
import time
from datetime import datetime

ROOT = pathlib.Path(r"<PROJECT_ROOT>")
PROMPTS = ROOT / "03-briefs" / "prompts"
OUT = ROOT / "04-corpus" / "raw-ai" / "real_claude"
META = OUT / "_meta"
RUNLOG = OUT / "_run-log.csv"

MODEL = "claude-opus-4-8"
MODEL_KEY = "real_claude"
SEED = 20260723
CHANNEL_NOTE = (
    "канал: вложенный claude CLI -p, чистый процесс на ячейку; "
    "CLAUDE.md отложен, хук UserPromptSubmit снят, скиллы отключены "
    "(--disable-slash-commands); остаточный слой — системный промпт Claude Code ~30k токенов"
)

FIELDS = [
    "attempt_id", "timestamp", "brief_id", "prompt_id", "prompt_condition",
    "model_provider", "model_exact_id", "repeat_index", "http_status", "latency_ms",
    "finish_reason", "refusal", "system_prompt_leak", "output_word_count",
    "retry_reason", "resulting_document_id", "notes",
]


def cell_order():
    rows = list(csv.DictReader(open(ROOT / "03-briefs" / "briefs-registry.csv", encoding="utf-8")))
    by = {}
    for r in rows:
        by.setdefault(r["genre"], []).append(r["brief_id"])
    rnd = random.Random(SEED)
    sel = []
    for genre, quota in (("seo", 2), ("analytics", 2), ("commercial", 1)):
        sel += sorted(rnd.sample(by[genre], quota))
    cells = [(b, p) for b in sel for p in ("P1", "P2", "P3")]
    rnd.shuffle(cells)
    return cells


def generate(prompt_text):
    cmd = [
        "claude", "-p", prompt_text,
        "--model", MODEL,
        "--disable-slash-commands",
        "--output-format", "json",
        "--disallowedTools", "WebSearch", "WebFetch", "Bash", "Read", "Write",
        "Edit", "Glob", "Grep", "Agent", "Task", "Skill",
    ]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1200, shell=False,
    )
    wall = int((time.monotonic() - t0) * 1000)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_parse_error": True, "_raw": proc.stdout[:2000], "_stderr": proc.stderr[:2000]}
    return payload, wall, proc.returncode


REFUSAL_MARKERS = (
    "не могу выполнить", "не могу написать", "я не могу помочь",
    "не буду выполнять", "отказываюсь",
)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    META.mkdir(parents=True, exist_ok=True)
    cells = cell_order()

    new_file = not RUNLOG.exists()
    fh = open(RUNLOG, "a", encoding="utf-8", newline="")
    writer = csv.DictWriter(fh, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()

    for idx, (brief, cond) in enumerate(cells, 1):
        prompt_id = f"{brief}_{cond}"
        target = OUT / f"{MODEL_KEY}_{brief}_{cond}_r1.txt"
        if target.exists() and target.stat().st_size > 0:
            print(f"[{idx:2d}/15] {prompt_id} — уже есть, пропуск", flush=True)
            continue

        prompt_text = (PROMPTS / f"{prompt_id}.txt").read_text(encoding="utf-8")
        print(f"[{idx:2d}/15] {prompt_id} — запуск", flush=True)
        payload, wall, rc = generate(prompt_text)

        text = payload.get("result", "") or ""
        used = sorted(payload.get("modelUsage", {}).keys())
        model_exact = used[0] if used else f"UNKNOWN(rc={rc})"
        words = len(re.findall(r"\S+", text))
        low = text.lower()
        refusal = "yes" if any(m in low for m in REFUSAL_MARKERS) and words < 400 else "no"
        api_err = payload.get("api_error_status")

        target.write_text(text, encoding="utf-8", newline="\n")
        (META / f"{prompt_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        note = CHANNEL_NOTE
        if model_exact != MODEL:
            note += f"; ВНИМАНИЕ фактическая модель {model_exact} вместо {MODEL}"
        if not (1200 <= words <= 1800):
            note += f"; объём {words} вне диапазона 1200-1800"

        writer.writerow({
            "attempt_id": f"rc-{idx:04d}",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "brief_id": brief,
            "prompt_id": prompt_id,
            "prompt_condition": cond,
            "model_provider": "anthropic",
            "model_exact_id": model_exact,
            "repeat_index": 1,
            "http_status": api_err if api_err else "",
            "latency_ms": payload.get("duration_api_ms", wall),
            "finish_reason": payload.get("stop_reason") or payload.get("subtype", ""),
            "refusal": refusal,
            "system_prompt_leak": "no",
            "output_word_count": words,
            "retry_reason": "",
            "resulting_document_id": target.stem,
            "notes": note,
        })
        fh.flush()
        print(f"        готово: {words} слов, {model_exact}, {payload.get('duration_api_ms', wall)} ms", flush=True)

    fh.close()
    print("ПРОГОН ЗАВЕРШЁН", flush=True)


if __name__ == "__main__":
    sys.exit(main())
