#!/usr/bin/env python
"""Исключение документов, ставших короче порога после чистки prep-v2.

Решение PI от 2026-07-25.

Правило §2.26 сняло ветки комментариев читателей. У этих документов авторского
текста оказалось меньше порога включения в 700 слов: объём им давали чужие
реплики, а не статья. Код `LEN` — длина вне диапазона задания.

Файлы с диска не удаляются: исключение — операция над реестром.

Запуск:
    python 09-tools/exclude_short_after_cleanup.py --dry-run
    python 09-tools/exclude_short_after_cleanup.py --apply
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v3" / "manifest.csv"
MANIFEST_BEFORE = ROOT / "04-corpus" / "derived" / "prep-v1" / "manifest.csv"
BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-short-exclusion"

EXCLUSION_DATE = "2026-07-25"
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

    _, manifest_rows = read_rows(MANIFEST)
    words = {row["document_id"]: int(row["prose_words"] or 0) for row in manifest_rows}
    _, before_rows = read_rows(MANIFEST_BEFORE)
    words_before = {row["document_id"]: int(row["prose_words"] or 0) for row in before_rows}

    reg_fields, reg_rows = read_rows(REGISTRY)
    exc_fields, exc_rows = read_rows(EXCLUSIONS)
    already = {row["document_id"] for row in exc_rows}

    # Исключаются документы, упавшие ниже порога из-за чистки, и документы,
    # собранные уже при `prep-v2`: у них прежнего замера нет, и порог проверяется
    # напрямую. Тексты, короткие в профиле prose ещё до правила §2.26, не
    # трогаются — таких 230, и порог к ним применялся по сырому объёму.
    pending = [
        row for row in reg_rows
        if row["document_id"] not in already
        and 0 < words.get(row["document_id"], 0) < MIN_WORDS
        and words_before.get(row["document_id"], MIN_WORDS) >= MIN_WORDS
    ]
    for row in pending:
        print(f"{row['document_id']} — {words[row['document_id']]} слов, в реестре {row['word_count']}")

    if not pending:
        print("\nисключать нечего")
        return 0

    drops = {row["document_id"] for row in pending}
    kept_rows = [row for row in reg_rows if row["document_id"] not in drops]
    human_before = sum(1 for row in reg_rows if row["origin_class"] == "H")
    human_after = sum(1 for row in kept_rows if row["origin_class"] == "H")
    print(f"\nреестр: {len(reg_rows)} -> {len(kept_rows)}; H: {human_before} -> {human_after}")

    new_rows = [
        {
            "document_id": row["document_id"],
            "stage": "препроцессинг prep-v2",
            "exclusion_date": EXCLUSION_DATE,
            "reason_code": "LEN",
            "reason_detail": (
                f"после снятия веток комментариев правилом §2.26 в профиле prose осталось "
                f"{words[row['document_id']]} слов при пороге {MIN_WORDS}; "
                f"в реестре {row['word_count']} слов, потому что счёт включал реплики читателей"
            ),
            "decided_by": DECIDED_BY,
            "replacement_document_id": "",
        }
        for row in pending
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
