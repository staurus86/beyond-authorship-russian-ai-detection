"""Основной прогон ячеек генерации через вложенный claude CLI.

Одна ячейка = один процесс = чистая сессия (правило 3 из prompt-conditions.md).
Порядок ячеек рандомизирован сидом, готовые файлы пропускаются — прогон
можно останавливать и продолжать без потери сделанного.

Запуск:
    python 09-tools/run_generation_cli.py --repeats 1 2
    python 09-tools/run_generation_cli.py --repeats 1 --briefs b001 b002
"""
import argparse
import csv
import json
import pathlib
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "03-briefs" / "prompts"

MODEL = "claude-opus-4-8"
MODEL_KEY = "real_claude"
SEED = 20260723
CONDITIONS = ("P1", "P2", "P3")

def shell_version():
    """Версия оболочки на момент прогона.

    Системный промпт CLI объявлен частью канала (§5.1 preregistration), поэтому
    смена версии оболочки меняет канал. В волне 2026-07-24 версия этого канала
    не записывалась вовсе, а прогон занял два календарных дня — восстановить её
    задним числом оказалось нечем: payload claude CLI версии не содержит.
    """
    try:
        out = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=30, shell=True
        )
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001 — версия не критична для прогона
        return "версия оболочки не определена"


SHELL_VERSION = shell_version()

CHANNEL_NOTE = (
    "канал: вложенный claude CLI -p, чистый процесс на ячейку; "
    "CLAUDE.md отложен, хук UserPromptSubmit снят, скиллы отключены "
    "(--disable-slash-commands), инструменты запрещены; "
    "остаточный слой — системный промпт Claude Code ~30k токенов; "
    f"оболочка: {SHELL_VERSION}"
)

FIELDS = [
    "attempt_id", "timestamp", "brief_id", "prompt_id", "prompt_condition",
    "model_provider", "model_exact_id", "repeat_index", "http_status", "latency_ms",
    "finish_reason", "refusal", "system_prompt_leak", "output_word_count",
    "retry_reason", "resulting_document_id", "notes",
]

# Порог ТЕХНИЧЕСКОЙ валидности, а не соблюдения объёма. Правлено 2026-07-24
# по рецензии: прежние 480 слов смешивали две разные вещи и выбросили валидный
# ответ на 405 слов с finish_reason=stop. Режим P3 (anti-slop) даёт самые
# короткие тексты, поэтому перегенерация коротких систематически завышала бы
# P3 и смещала первичный исход O1.
#
# Ниже порога — только обрыв канала и пустое тело ответа: строка «API Error»
# укладывается в 11 слов, пустой ответ шлюза даёт 0. Всё, что длиннее,
# считается валидным и размечается переменной length_instruction_compliance.
MIN_VALID_WORDS = 50

# проверка утечки оболочки: раньше поле проставлялось константой "no", то есть
# было утверждением, а не проверкой. Скан 2026-07-24 нашёл документ, где модель
# рассуждала о своём инструментарии до начала статьи
LEAK_PATTERNS = (
    r"<system-reminder>", r"<user_instructions>", r"CLAUDE\.md", r"AGENTS\.md",
    r"You are Claude Code", r"OpenAI Codex v", r"^workdir:", r"^sandbox:",
    r"релевантн\w+ навык\w*[^.\n]{0,40}нет", r"пишу текст напрямую",
    r"инструменты (?:запрещены|недоступны|не нужны)",
)


def leak_flag(text):
    return "yes" if any(re.search(p, text, re.I | re.M) for p in LEAK_PATTERNS) else "no"


REFUSAL_MARKERS = (
    "не могу выполнить", "не могу написать", "я не могу помочь",
    "не буду выполнять", "отказываюсь",
)


def all_briefs():
    rows = csv.DictReader(open(ROOT / "03-briefs" / "briefs-registry.csv", encoding="utf-8"))
    return [r["brief_id"] for r in rows if r["status"] == "ready"]


def cell_order(briefs, repeats):
    cells = [(b, c, r) for b in briefs for c in CONDITIONS for r in repeats]
    random.Random(SEED).shuffle(cells)
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
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=1200, shell=False,
        )
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return {"_timeout": True}, int((time.monotonic() - t0) * 1000), -1
    wall = int((time.monotonic() - t0) * 1000)
    try:
        return json.loads(stdout), wall, rc
    except json.JSONDecodeError:
        return {"_parse_error": True, "_raw": stdout[:2000], "_stderr": stderr[:2000]}, wall, rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", nargs="+", type=int, default=[1])
    ap.add_argument("--briefs", nargs="+", default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument(
        "--max-consecutive-empty", type=int, default=5,
        help="подряд пустых ответов, после которых прогон останавливается: "
             "при обрыве сети канал отдаёт пустоту мгновенно и без стопа "
             "прожигает весь остаток ячеек за минуту",
    )
    args = ap.parse_args()

    out = ROOT / "04-corpus" / "raw-ai" / MODEL_KEY
    meta = out / "_meta"
    runlog = out / "_run-log.csv"
    out.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)

    briefs = args.briefs or all_briefs()
    cells = cell_order(briefs, args.repeats)

    new_file = not runlog.exists()
    fh = open(runlog, "a", encoding="utf-8", newline="")
    writer = csv.DictWriter(fh, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()

    total = len(cells)
    lock = threading.Lock()
    stop = threading.Event()
    counter = {"done": 0, "failed": 0, "seen": 0, "skipped": 0, "empty_streak": 0}

    def run_cell(cell):
        brief, cond, rep = cell
        prompt_id = f"{brief}_{cond}"
        target = out / f"{MODEL_KEY}_{brief}_{cond}_r{rep}.txt"
        if target.exists() and target.stat().st_size > 0:
            return
        if stop.is_set():
            with lock:
                counter["skipped"] += 1
            return

        prompt_text = (PROMPTS / f"{prompt_id}.txt").read_text(encoding="utf-8")
        with lock:
            counter["seen"] += 1
            print(f"[{counter['seen']}/{total}] {prompt_id} r{rep}", flush=True)

        retry_reason = ""
        for attempt in (1, 2):
            payload, wall, rc = generate(prompt_text)
            text = payload.get("result", "") or ""
            if text:
                break
            retry_reason = "пустой ответ канала, повтор запроса"
            if attempt == 2:
                break

        if not text:
            with lock:
                counter["failed"] += 1
                counter["empty_streak"] += 1
                streak = counter["empty_streak"]
            (meta / f"{prompt_id}_r{rep}.FAILED.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"    ПУСТОЙ ОТВЕТ rc={rc}, ячейка пропущена", flush=True)
            if streak >= args.max_consecutive_empty:
                stop.set()
                print(
                    f"    СТОП: {streak} пустых ответов подряд, канал недоступен. "
                    f"Остальные ячейки не тронуты, прогон продолжится с них при перезапуске.",
                    flush=True,
                )
            return

        used = sorted(payload.get("modelUsage", {}).keys())
        model_exact = used[0] if used else f"UNKNOWN(rc={rc})"
        words = len(re.findall(r"\S+", text))
        low = text.lower()
        refusal = "yes" if any(m in low for m in REFUSAL_MARKERS) and words < 400 else "no"
        api_err = payload.get("api_error_status")

        # критерий валидного ответа, preregistration §5.1: не менее 40% нижней
        # границы объёма. Обрыв канала приходит сюда короткой строкой вида
        # «API Error: Connection closed mid-response» — записать её файлом
        # значит пометить ячейку сделанной и лишить подборщик шанса
        if refusal == "no" and words < MIN_VALID_WORDS:
            (meta / f"{prompt_id}_r{rep}.SHORT.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with lock:
                counter["failed"] += 1
                writer.writerow({
                    "attempt_id": f"rc-{brief}-{cond}-r{rep}",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "brief_id": brief,
                    "prompt_id": prompt_id,
                    "prompt_condition": cond,
                    "model_provider": "anthropic",
                    "model_exact_id": model_exact,
                    "repeat_index": rep,
                    "http_status": api_err if api_err else "",
                    "latency_ms": payload.get("duration_api_ms", wall),
                    "finish_reason": payload.get("stop_reason") or payload.get("subtype", ""),
                    "refusal": refusal,
                    "system_prompt_leak": leak_flag(text),
                    "output_word_count": words,
                    "retry_reason": "ответ короче порога валидности",
                    "resulting_document_id": "",
                    "notes": CHANNEL_NOTE + f"; порог {MIN_VALID_WORDS} слов, файл не записан",
                })
                fh.flush()
            print(f"    БРАК: {words} слов при пороге {MIN_VALID_WORDS}, файл не записан", flush=True)
            return

        target.write_text(text, encoding="utf-8", newline="\n")
        (meta / f"{prompt_id}_r{rep}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        note = CHANNEL_NOTE
        if model_exact != MODEL:
            note += f"; ВНИМАНИЕ фактическая модель {model_exact} вместо {MODEL}"
        if not (1200 <= words <= 1800):
            note += f"; объём {words} вне диапазона 1200-1800"

        with lock:
            writer.writerow({
                "attempt_id": f"rc-{brief}-{cond}-r{rep}",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "brief_id": brief,
                "prompt_id": prompt_id,
                "prompt_condition": cond,
                "model_provider": "anthropic",
                "model_exact_id": model_exact,
                "repeat_index": rep,
                "http_status": api_err if api_err else "",
                "latency_ms": payload.get("duration_api_ms", wall),
                "finish_reason": payload.get("stop_reason") or payload.get("subtype", ""),
                "refusal": refusal,
                "system_prompt_leak": leak_flag(text),
                "output_word_count": words,
                "retry_reason": retry_reason,
                "resulting_document_id": target.stem,
                "notes": note,
            })
            fh.flush()
            counter["done"] += 1
            counter["empty_streak"] = 0
        print(f"    {words} слов, {model_exact}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run_cell, cells))

    fh.close()
    tail = " — ОСТАНОВЛЕН ПО СЕРИИ ПУСТЫХ ОТВЕТОВ" if stop.is_set() else ""
    print(
        f"ПРОГОН ЗАВЕРШЁН{tail}: сгенерировано {counter['done']}, "
        f"пустых ответов {counter['failed']}, не тронуто после стопа {counter['skipped']}",
        flush=True
    )


if __name__ == "__main__":
    sys.exit(main())
