#!/usr/bin/env python
"""Исключение трёх испорченных документов, найденных при прогоне lex-v1.

Решение PI от 2026-07-25.

Два человеческих документа написаны по-английски и попали в корпус мимо
языкового фильтра сборщика блогов. Один машинный испорчен сбоем генерации:
больше половины букв — повтор одного китайского сочетания.

Файлы с диска не удаляются: исключение — операция над реестром.

Запуск:
    python 09-tools/exclude_defective.py --dry-run
    python 09-tools/exclude_defective.py --apply
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-defective-exclusion"

EXCLUSION_DATE = "2026-07-25"
DECIDED_BY = "principal investigator"

CASES = [
    (
        "human_seo_shakin_0006",
        "LANG",
        "текст на английском языке: доля кириллицы 0.000 при 982 словах, "
        "в реестре language=ru; языковой фильтр сборщика блогов не сработал",
    ),
    (
        "human_seo_shakin_0016",
        "LANG",
        "текст на английском языке: доля кириллицы 0.000 при 1068 словах, "
        "в реестре language=ru; языковой фильтр сборщика блогов не сработал",
    ),
    (
        "nemotron_b028_P3_r2",
        "TECH",
        "сбой генерации: 9430 знаков CJK, сочетание «那些» повторено 4715 раз, "
        "плюс 21 знак хангыля — больше половины букв документа",
    ),
    (
        "human_science_spbgu_0047",
        "TECH",
        "служебные параметры PDF вместо текста: хвост от 15 300-го знака до конца "
        "документа, около четверти объёма, состоит из строк distiller "
        "(/MonoImageDownsampleType, setdistillerparams, setpagedevice)",
    ),
]


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def script_of(char: str) -> str:
    try:
        return unicodedata.name(char).split()[0]
    except ValueError:
        return "UNKNOWN"


def describe(row: dict) -> str:
    """Пересчёт признака порчи перед исключением, а не доверие записи."""
    text = (ROOT / row["file_path"]).read_text(encoding="utf-8", errors="replace")
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return "букв нет"
    cyrillic = sum(1 for char in letters if script_of(char) == "CYRILLIC")
    foreign = sum(1 for char in letters if script_of(char) not in ("CYRILLIC", "LATIN"))
    return f"букв {len(letters)}, кириллица {cyrillic / len(letters):.3f}, посторонние письменности {foreign}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    parser.add_argument("--dry-run", action="store_true", help="только показать")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        parser.error("укажите --apply или --dry-run")

    reg_fields, reg_rows = read_rows(REGISTRY)
    by_id = {row["document_id"]: row for row in reg_rows}

    exc_fields, exc_rows = read_rows(EXCLUSIONS)
    already = {row["document_id"] for row in exc_rows}

    pending = []
    for doc_id, code, detail in CASES:
        if doc_id not in by_id:
            if doc_id in already:
                print(f"{doc_id} — уже исключён ранее, пропуск")
                continue
            print(f"ОШИБКА: {doc_id} нет ни в реестре, ни в логе исключений", file=sys.stderr)
            return 1
        print(f"{doc_id} [{code}] — {describe(by_id[doc_id])}")
        pending.append((doc_id, code, detail))

    if not pending:
        print("\nисключать нечего")
        return 0

    drops = {doc_id for doc_id, _, _ in pending}
    kept_rows = [row for row in reg_rows if row["document_id"] not in drops]
    machine_before = sum(1 for row in reg_rows if row["origin_class"] == "A")
    machine_after = sum(1 for row in kept_rows if row["origin_class"] == "A")
    human_before = sum(1 for row in reg_rows if row["origin_class"] == "H")
    human_after = sum(1 for row in kept_rows if row["origin_class"] == "H")
    print(
        f"\nреестр: {len(reg_rows)} -> {len(kept_rows)}; "
        f"A: {machine_before} -> {machine_after}; H: {human_before} -> {human_after}"
    )

    new_rows = [
        {
            "document_id": doc_id,
            "stage": "расчёт признаков",
            "exclusion_date": EXCLUSION_DATE,
            "reason_code": code,
            "reason_detail": detail,
            "decided_by": DECIDED_BY,
            "replacement_document_id": "",
        }
        for doc_id, code, detail in pending
    ]

    if not args.apply:
        print(f"\nсухой прогон: в лог исключений добавилось бы {len(new_rows)} строк")
        return 0

    shutil.copy2(REGISTRY, BACKUP)
    write_rows(REGISTRY, reg_fields, kept_rows)
    write_rows(EXCLUSIONS, exc_fields, exc_rows + new_rows)
    print(f"\nзаписано. резервная копия реестра: {BACKUP.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
