#!/usr/bin/env python3
"""Прогон ячеек генерации через codex CLI (канал OpenAI).

Одна ячейка = один процесс `codex exec` = чистая сессия. Готовые файлы
пропускаются, поэтому прогон можно останавливать и продолжать.

Финальный ответ забирается через `--output-last-message`: стандартный вывод
codex содержит текст дважды (поток и итоговое сообщение) и для корпуса
непригоден. Полный вывод всё равно сохраняется в `_codex-cli-logs/` как
свидетельство прогона.

Канал восстановлен по логам прогона 2026-07-23: модель gpt-5.5, reasoning
effort high, sandbox read-only, рабочий каталог — корень исследования.

    python 09-tools/run_codex_cli.py --dry-run
    python 09-tools/run_codex_cli.py --repeat 1
    python 09-tools/run_codex_cli.py --repeat 1 --briefs b023 b024
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PROMPTS_DIR = ROOT / "03-briefs" / "prompts"
OUT_DIR = ROOT / "04-corpus" / "raw-ai" / "gpt"
LOG_DIR = OUT_DIR / "_codex-cli-logs"
GEN_LOG = ROOT / "04-corpus" / "generation-log.csv"

MODEL = "gpt-5.5"
MODEL_KEY = "gpt"
PROVIDER = "openai"
CONDITIONS = ("P1", "P2", "P3")

def shell_version():
    """Версия оболочки на момент прогона.

    Системный промпт CLI объявлен частью канала (§5.1 preregistration), поэтому
    смена версии оболочки меняет канал. В волне 2026-07-24 это вскрылось задним
    числом: 204 ячейки прошли на codex CLI 0.145.0, 44 на 0.142.5. Версия
    пишется в журнал на каждую ячейку, чтобы восстанавливать её больше не
    приходилось.
    """
    try:
        out = subprocess.run(
            ["codex", "--version"], capture_output=True, text=True, timeout=30, shell=True
        )
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001 — версия не критична для прогона
        return "версия оболочки не определена"


SHELL_VERSION = shell_version()

CHANNEL_NOTE = (
    "канал: codex CLI exec, чистый процесс на ячейку; sandbox read-only, "
    "reasoning effort high, инструменты не вызывались; "
    "остаточный слой — системный промпт Codex CLI; "
    f"оболочка: {SHELL_VERSION}"
)

LOG_FIELDS = [
    "attempt_id", "timestamp", "brief_id", "prompt_id", "prompt_condition",
    "model_provider", "model_exact_id", "repeat_index", "http_status", "latency_ms",
    "finish_reason", "refusal", "system_prompt_leak", "output_word_count",
    "retry_reason", "resulting_document_id", "notes",
]

REFUSAL_MARKERS = (
    "не могу выполнить", "не могу написать", "я не могу помочь",
    "не буду выполнять", "отказываюсь",
)

# Хвосты системного промпта и служебной разметки канала, которых в тексте быть не должно.
LEAK_MARKERS = ("<user_instructions>", "AGENTS.md", "sandbox:", "approval:")

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


def codex_binary():
    """На Windows npm кладёт shim .cmd — subprocess без shell берёт именно его."""
    if os.name == "nt":
        for name in ("codex.cmd", "codex.exe"):
            path = Path(os.environ.get("APPDATA", "")) / "npm" / name
            if path.exists():
                return str(path)
    return "codex"


def load_cells(briefs, conditions, repeat):
    cells = []
    for path in sorted(PROMPTS_DIR.glob("b*_P*.txt")):
        match = re.match(r"^(b\d{3})_(P[123])$", path.stem)
        if not match:
            continue
        brief, condition = match.groups()
        if briefs and brief not in briefs:
            continue
        if condition not in conditions:
            continue
        cells.append({"brief": brief, "condition": condition, "path": path, "repeat": repeat})
    return cells


def run_cell(prompt_text, timeout):
    binary = codex_binary()
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as fh:
        last_message = Path(fh.name)
    # Промпт идёт через stdin: как аргумент командной строки он проходит через
    # npm-shim .cmd и приезжает обрезанным по первой строке.
    cmd = [
        binary, "exec",
        "--model", MODEL,
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--color", "never",
        "--output-last-message", str(last_message),
        "-",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, input=prompt_text, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(ROOT), timeout=timeout, shell=False,
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        last_message.unlink(missing_ok=True)
        return "", "", f"таймаут {timeout} с", -1, int((time.monotonic() - started) * 1000)

    elapsed = int((time.monotonic() - started) * 1000)
    text = last_message.read_text(encoding="utf-8").strip() if last_message.exists() else ""
    last_message.unlink(missing_ok=True)
    return text, stdout + ("\n" + stderr if stderr else ""), "", code, elapsed


def append_log(row):
    exists = GEN_LOG.exists()
    with GEN_LOG.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--briefs", nargs="+", default=None)
    parser.add_argument("--conditions", default="P1,P2,P3")
    parser.add_argument("--limit", type=int, default=0, help="сколько ячеек прогнать, 0 — все")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=2, help="попыток на ячейку при пустом ответе")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    conditions = tuple(c.strip() for c in args.conditions.split(",") if c.strip())
    cells = load_cells(args.briefs, conditions, args.repeat)
    pending = [
        cell for cell in cells
        if not (OUT_DIR / f"{MODEL_KEY}_{cell['brief']}_{cell['condition']}_r{args.repeat}.txt").exists()
    ]
    if args.limit:
        pending = pending[: args.limit]

    print(f"Ячеек всего: {len(cells)}, готово: {len(cells) - len([c for c in cells if c in pending])}, "
          f"к прогону: {len(pending)}, модель {MODEL}, повтор r{args.repeat}")
    print(f"Ответы: {OUT_DIR.relative_to(ROOT)}\n")

    if args.dry_run:
        for cell in pending[:10]:
            print(f"  [сухой прогон] {MODEL_KEY}_{cell['brief']}_{cell['condition']}_r{args.repeat}")
        if len(pending) > 10:
            print(f"  ... и ещё {len(pending) - 10}")
        return

    done = failed = 0
    for index, cell in enumerate(pending, 1):
        attempt_id = f"{MODEL_KEY}_{cell['brief']}_{cell['condition']}_r{args.repeat}"
        prompt_text = cell["path"].read_text(encoding="utf-8")
        print(f"[{index}/{len(pending)}] {attempt_id}", flush=True)

        retry_reason = ""
        for attempt in range(1, args.retries + 1):
            text, stdout, problem, code, elapsed = run_cell(prompt_text, args.timeout)
            if text:
                break
            retry_reason = problem or f"пустой ответ канала (rc={code})"
            print(f"    попытка {attempt}: {retry_reason}", flush=True)
            if attempt < args.retries:
                time.sleep(args.delay * 5)

        (LOG_DIR / f"{attempt_id}.log").write_text(stdout, encoding="utf-8")

        if not text:
            failed += 1
            append_log({
                "attempt_id": attempt_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "brief_id": cell["brief"], "prompt_id": f"{cell['brief']}_{cell['condition']}",
                "prompt_condition": cell["condition"], "model_provider": PROVIDER,
                "model_exact_id": MODEL, "repeat_index": args.repeat,
                "latency_ms": elapsed, "retry_reason": retry_reason,
                "notes": CHANNEL_NOTE + "; ответ не получен, файл не записан",
            })
            continue

        words = len(re.findall(r"\S+", text))
        low = text.lower()
        refusal = "yes" if any(marker in low for marker in REFUSAL_MARKERS) and words < 400 else "no"
        leak = "yes" if any(marker in text for marker in LEAK_MARKERS) else "no"

        # критерий валидного ответа, preregistration §5.1: не менее 40% нижней
        # границы объёма. Короткий ответ файлом не становится, иначе прогон
        # сочтёт ячейку сделанной и подборщик её не переделает
        if refusal == "no" and words < MIN_VALID_WORDS:
            print(f"    БРАК: {words} слов при пороге {MIN_VALID_WORDS}, файл не записан", flush=True)
            append_log({
                "attempt_id": attempt_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                "brief_id": cell["brief"], "prompt_id": f"{cell['brief']}_{cell['condition']}",
                "prompt_condition": cell["condition"], "model_provider": "openai",
                "model_exact_id": MODEL, "repeat_index": args.repeat,
                "latency_ms": elapsed, "output_word_count": words,
                "retry_reason": "ответ короче порога валидности",
                "notes": CHANNEL_NOTE + f"; порог {MIN_VALID_WORDS} слов, файл не записан",
            })
            continue

        (OUT_DIR / f"{attempt_id}.txt").write_text(text + "\n", encoding="utf-8", newline="\n")

        note = CHANNEL_NOTE
        if not (1200 <= words <= 1800):
            note += f"; объём {words} вне диапазона 1200-1800"

        append_log({
            "attempt_id": attempt_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "brief_id": cell["brief"],
            "prompt_id": f"{cell['brief']}_{cell['condition']}",
            "prompt_condition": cell["condition"],
            "model_provider": PROVIDER,
            "model_exact_id": MODEL,
            "repeat_index": args.repeat,
            "http_status": "",
            "latency_ms": elapsed,
            "finish_reason": "stop",
            "refusal": refusal,
            "system_prompt_leak": leak,
            "output_word_count": words,
            "retry_reason": retry_reason,
            "resulting_document_id": attempt_id,
            "notes": note,
        })

        flag = "  ⚠ утечка служебной разметки" if leak == "yes" else ""
        print(f"    {words} слов, {elapsed} мс{flag}", flush=True)
        done += 1
        time.sleep(args.delay)

    print(f"\nСделано: {done}, без ответа: {failed}")


if __name__ == "__main__":
    main()
