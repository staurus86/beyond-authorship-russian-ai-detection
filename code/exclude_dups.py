#!/usr/bin/env python
"""Исключение точных дублей, найденных dedup-v1.

Решение PI от 2026-07-25: из каждой пары побайтово совпадающих документов
выбывает один. Правило выбора детерминированное и от данных не зависит:
остаётся документ с меньшим порядковым номером (собран раньше), выбывает
с большим. Содержимое пар совпадает побайтово, поэтому выбор безразличен.

Файлы с диска не удаляются: исключение — операция над реестром, а не над
корпусом на диске.

Запуск:
    python 09-tools/exclude_dups.py --dry-run
    python 09-tools/exclude_dups.py --apply
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-dup-exclusion"

EXCLUSION_DATE = "2026-07-25"
DECIDED_BY = "principal investigator"

# (остаётся, выбывает) — по результату 09-tools/dedup.py, версия dedup-v1
PAIRS = [
    ("human_seo_alaev_0017", "human_seo_alaev_0022"),
    ("human_seo_alaev_0018", "human_seo_alaev_0023"),
    ("human_seo_alaev_0019", "human_seo_alaev_0024"),
    ("human_seo_alaev_0020", "human_seo_alaev_0025"),
    ("human_seo_drmax_0019", "human_seo_drmax_0022"),
    ("human_seo_drmax_0020", "human_seo_drmax_0023"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    parser.add_argument("--dry-run", action="store_true", help="только показать")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        parser.error("укажите --apply или --dry-run")

    reg_fields, reg_rows = read_rows(REGISTRY)
    by_id = {row["document_id"]: row for row in reg_rows}

    # Проверка перед изменением: обе стороны пары на месте и совпадают побайтово.
    for keep, drop in PAIRS:
        for doc_id in (keep, drop):
            if doc_id not in by_id:
                print(f"ОШИБКА: {doc_id} нет в реестре", file=sys.stderr)
                return 1
        keep_path = ROOT / by_id[keep]["file_path"]
        drop_path = ROOT / by_id[drop]["file_path"]
        keep_hash = hashlib.sha256(keep_path.read_bytes()).hexdigest()
        drop_hash = hashlib.sha256(drop_path.read_bytes()).hexdigest()
        if keep_hash != drop_hash:
            print(f"ОШИБКА: {keep} и {drop} не совпадают побайтово", file=sys.stderr)
            return 1
        print(f"{drop} выбывает, остаётся {keep} (sha256 {keep_hash[:16]}…)")

    drops = {drop for _, drop in PAIRS}
    kept_rows = [row for row in reg_rows if row["document_id"] not in drops]
    human_before = sum(1 for row in reg_rows if row["origin_class"] == "H")
    human_after = sum(1 for row in kept_rows if row["origin_class"] == "H")
    print(
        f"\nреестр: {len(reg_rows)} -> {len(kept_rows)}; "
        f"человеческая часть: {human_before} -> {human_after}"
    )

    exc_fields, exc_rows = read_rows(EXCLUSIONS)
    already = {row["document_id"] for row in exc_rows}
    new_rows = []
    for keep, drop in PAIRS:
        if drop in already:
            print(f"пропуск: {drop} уже в логе исключений")
            continue
        new_rows.append(
            {
                "document_id": drop,
                "stage": "дедупликация",
                "exclusion_date": EXCLUSION_DATE,
                "reason_code": "DUP",
                "reason_detail": (
                    f"побайтовый дубль {keep} (Jaccard 1.000, dedup-v1); "
                    "из пары оставлен документ с меньшим номером, "
                    "правило выбора не зависит от содержания"
                ),
                "decided_by": DECIDED_BY,
                "replacement_document_id": keep,
            }
        )

    if not args.apply:
        print(f"\nсухой прогон: в лог исключений добавилось бы {len(new_rows)} строк")
        return 0

    shutil.copy2(REGISTRY, BACKUP)
    write_rows(REGISTRY, reg_fields, kept_rows)
    write_rows(EXCLUSIONS, exc_fields, exc_rows + new_rows)
    print(f"\nзаписано. резервная копия реестра: {BACKUP.name}")
    print(f"в лог исключений добавлено строк: {len(new_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
