#!/usr/bin/env python
"""Пересчёт помет hard-human на источниках, добавленных 2026-07-24.

Долг из записки за 24 июля: `HH-polished` и `HH-formal-register` проставлены
до расширения страт третьим источником. Пометы приписаны не документам
поодиночке, а уровню регламентированности целиком, и это видно по реестру:

    regulation_level=1 (новости)  -> HH-polished        lenta 60, buriy_2014 60
    regulation_level=3 (наука)    -> HH-formal-register cyberleninka 60, urfu 48

Третьи источники этих страт — `gazeta` и `spbgu` — остались без помет. Скрипт
применяет к ним то же правило и ничего больше.

Запуск:
    python 09-tools/mark_hard_human.py --dry-run
    python 09-tools/mark_hard_human.py --apply
"""

from __future__ import annotations

import argparse
import csv
import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-hh-recount"

RULE = {"1": "HH-polished", "3": "HH-formal-register"}


def regulation_level(row: dict) -> str:
    for part in (row.get("notes") or "").split(";"):
        part = part.strip()
        if part.startswith("regulation_level="):
            return part.split("=", 1)[1].strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        parser.error("укажите --apply или --dry-run")

    with REGISTRY.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    changed = Counter()
    conflicts = []
    for row in rows:
        if row["origin_class"] != "H":
            continue
        mark = RULE.get(regulation_level(row))
        if not mark:
            continue
        current = [item.strip() for item in (row["hh_subgroups"] or "").split(";") if item.strip()]
        if mark in current:
            continue
        if current:
            conflicts.append((row["document_id"], current, mark))
            continue
        row["hh_subgroups"] = mark
        changed[(row["source_platform"], mark)] += 1

    for key, count in sorted(changed.items()):
        print(f"{key[0]}: {key[1]} проставлена {count} документам")
    if conflicts:
        print("\nне тронуты, уже имеют другую помету:")
        for doc_id, current, mark in conflicts[:10]:
            print(f"  {doc_id}: {current} — предлагалась {mark}")
    if not changed:
        print("менять нечего")

    totals = Counter()
    for row in rows:
        for item in (row["hh_subgroups"] or "").split(";"):
            if item.strip():
                totals[item.strip()] += 1
    print("\nитог по пометам:", dict(sorted(totals.items())))

    if not args.apply:
        print("\nсухой прогон, реестр не изменён")
        return 0

    shutil.copy2(REGISTRY, BACKUP)
    with REGISTRY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nзаписано, резервная копия: {BACKUP.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
