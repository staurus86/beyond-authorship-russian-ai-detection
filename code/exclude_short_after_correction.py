#!/usr/bin/env python
"""Исключение документов, ставших короче порога после коррекции извлечения.

Критерий записан до применения: §6 амендмента
`02-preregistration/amendment-prep-v5-data-quality.md`.

Эти документы проходили порог включения в 700 слов только за счёт дефекта:
дублированный текст засчитывался в объём. После снятия дубля авторского текста
в них меньше порога. Код `LEN` — тот же, что при исключении 2026-07-25, когда
документы укоротились после снятия веток комментариев.

Файлы с диска не удаляются: исключение — операция над реестром.

Запуск:
    python 09-tools/exclude_short_after_correction.py --dry-run
    python 09-tools/exclude_short_after_correction.py --apply
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
CORRECTIONS = ROOT / "04-corpus" / "prep-v5-corrections.csv"
BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-correction-exclusion"

EXCLUSION_DATE = "2026-07-29"
DECIDED_BY = "principal investigator"
MIN_WORDS = 700


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        parser.error("укажите --apply или --dry-run")

    _, corrections = read_rows(CORRECTIONS)
    below = {row["document_id"]: row for row in corrections
             if row["below_min_words"] == "1"}

    reg_fields, reg_rows = read_rows(REGISTRY)
    exc_fields, exc_rows = read_rows(EXCLUSIONS)
    already = {row["document_id"] for row in exc_rows}

    pending = [row for row in reg_rows
               if row["document_id"] in below and row["document_id"] not in already]

    for row in sorted(pending, key=lambda r: r["document_id"]):
        entry = below[row["document_id"]]
        print(f"{row['document_id']:34} {entry['words_before']:>5} -> "
              f"{entry['words_after']:>5} слов  [{entry['verdict']}]")

    if not pending:
        print("\nисключать нечего")
        return 0

    drops = {row["document_id"] for row in pending}
    kept_rows = [row for row in reg_rows if row["document_id"] not in drops]
    human_before = sum(1 for row in reg_rows if row["origin_class"] == "H")
    human_after = sum(1 for row in kept_rows if row["origin_class"] == "H")
    print(f"\nреестр: {len(reg_rows)} -> {len(kept_rows)}; H: {human_before} -> {human_after}")
    print("по источникам:")
    lost = Counter(row["source_platform"] for row in pending)
    total = Counter(row["source_platform"] for row in reg_rows
                    if row["origin_class"] == "H")
    for source, n in lost.most_common():
        print(f"  {source:16} -{n}, остаётся {total[source] - n} из {total[source]}")

    new_rows = [
        {
            "document_id": row["document_id"],
            "stage": "коррекция извлечения correction-v5.0",
            "exclusion_date": EXCLUSION_DATE,
            "reason_code": "LEN",
            "reason_detail": (
                f"после коррекции дефекта извлечения ({below[row['document_id']]['verdict']}) "
                f"осталось {below[row['document_id']]['words_after']} слов при пороге "
                f"{MIN_WORDS}; в реестре {row['word_count']} слов, потому что объём "
                f"давал продублированный текст"
            ),
            "decided_by": DECIDED_BY,
            "replacement_document_id": "",
        }
        for row in sorted(pending, key=lambda r: r["document_id"])
    ]

    if not args.apply:
        print(f"\nсухой прогон: в лог исключений добавилось бы {len(new_rows)} строк")
        return 0

    shutil.copy2(REGISTRY, BACKUP)
    write_rows(REGISTRY, reg_fields, kept_rows)
    write_rows(EXCLUSIONS, exc_fields, exc_rows + new_rows)
    print(f"\nреестр перезаписан, копия: {BACKUP.name}")
    print(f"в лог исключений добавлено строк: {len(new_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
