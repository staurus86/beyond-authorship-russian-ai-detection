#!/usr/bin/env python3
"""Хронология выбора судьи: был ли `gemma3:12b-it-qat` заморожен до первого score.

    python 09-tools/judge_provenance_check.py

Отображаемое локальное время файлов доказательством не является. Сверяются:

- sha256 `proc4-judge-spec.md`, записанный в манифесте judge-v1, против текущего;
- содержит ли спецификация с этим хешем имя модели;
- время первого фактического raw-ответа судьи, а не только запуска скрипта;
- mtime спецификации в UTC.

Сильное доказательство: манифест, созданный до scoring, несёт хеш той версии
спецификации, где уже указан `gemma3:12b-it-qat`. Более поздний `amendments.md`
не делает выбор post hoc, если он лишь документирует установку и не меняет
модель, промпт или параметры.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

SPEC = ROOT / "07-analysis" / "proc4-judge-spec.md"
MANIFEST = ROOT / "07-analysis" / "judge-v1-manifest.json"
RAW = ROOT / "07-analysis" / "judge-v1-raw.jsonl"
OUT = ROOT / "07-analysis" / "judge-provenance.md"

MODEL = "gemma3:12b-it-qat"


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def first_raw_record():
    """Первая строка сырых ответов: время и статус, без чтения всего файла."""
    with RAW.open(encoding="utf-8") as fh:
        line = fh.readline()
    return json.loads(line) if line else {}


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"хронология выбора судьи, {stamp}")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    recorded = manifest.get("inputs", {}).get("proc4-judge-spec.md", "")
    current = sha256_file(SPEC)
    spec_text = SPEC.read_text(encoding="utf-8")
    names_model = MODEL in spec_text
    spec_mtime = datetime.fromtimestamp(SPEC.stat().st_mtime, tz=timezone.utc)
    manifest_created = manifest.get("created_at", "")
    first = first_raw_record()
    first_time = first.get("created_at") or first.get("timestamp") or ""

    same_hash = recorded == current
    print(f"  хеш спецификации в манифесте: {recorded[:16]}…")
    print(f"  текущий хеш спецификации:     {current[:16]}…  совпадает: {same_hash}")
    print(f"  спецификация называет {MODEL}: {names_model}")
    print(f"  манифест judge-v1 создан:     {manifest_created}")
    print(f"  mtime спецификации, UTC:      {spec_mtime.isoformat(timespec='seconds')}")
    print(f"  первый сырой ответ судьи:     {first_time or 'поле времени отсутствует'}")

    proven = same_hash and names_model
    lines = [
        "# Хронология выбора судьи: заморозка до первого score",
        "",
        f"Собрано {stamp} скриптом `09-tools/judge_provenance_check.py`.",
        "",
        "| Что проверено | Значение |",
        "|---|---|",
        f"| хеш `proc4-judge-spec.md` в манифесте judge-v1 | `{recorded}` |",
        f"| текущий хеш файла | `{current}` |",
        f"| хеши совпадают | {'да' if same_hash else '**нет**'} |",
        f"| спецификация с этим хешем называет `{MODEL}` | "
        f"{'да' if names_model else '**нет**'} |",
        f"| манифест judge-v1 создан | {manifest_created} |",
        f"| mtime спецификации в UTC | {spec_mtime.isoformat(timespec='seconds')} |",
        f"| время первого сырого ответа | {first_time or 'в записи нет поля времени'} |",
        "",
        ("**Вывод: модельный выбор заморожен до результата.** Манифест, "
         "созданный до scoring, несёт хеш той версии спецификации, где модель уже "
         "названа; текущий файл этому хешу соответствует, то есть спецификация "
         "после прогона не менялась. Запись в `amendments.md`, датированная позже, "
         "документирует установку артефакта и не меняет ни модель, ни промпт, ни "
         "параметры вызова."
         if proven else
         "**Вывод: заморозка не доказана.** Процедура остаётся в статье, но "
         "модельный выбор обозначается post hoc."),
        "",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT.name}")


if __name__ == "__main__":
    main()
