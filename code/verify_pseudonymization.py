#!/usr/bin/env python3
"""Проверка псевдонимизации без таблицы соответствия.

    python code/verify_pseudonymization.py

Скрипт публичный: он не знает исходных значений и не может их восстановить. Он
проверяет то, что видно снаружи, — форму кодов, согласованность полей, сохранность
групп и совпадение кодов между реестром и разбиениями.
"""

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "documents-registry.csv"
SPLITS = ROOT / "data" / "splits-v5"
CODE = re.compile(r"^author_\d{4}$")

checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


rows = list(csv.DictReader(REGISTRY.open(encoding="utf-8")))
human = [r for r in rows if r["origin_class"] == "H"]
machine = [r for r in rows if r["origin_class"] == "A"]

bad = [r["document_id"] for r in human
       if not CODE.match(r["author_or_model_id"])
       or not CODE.match(r["split_group_author"])]
check("у всех человеческих документов код вида author_NNNN", not bad,
      f"нарушений {len(bad)}")

check("идентификаторы моделей не тронуты",
      all(not CODE.match(r["author_or_model_id"]) for r in machine))

pairs = defaultdict(set)
for r in human:
    pairs[r["author_or_model_id"]].add(r["split_group_author"])
check("один код автора соответствует одной группе",
      all(len(v) == 1 for v in pairs.values()),
      str([k for k, v in pairs.items() if len(v) > 1][:3]))

sizes = Counter(r["split_group_author"] for r in human)
check("групп больше сотни", len(sizes) > 100, f"{len(sizes)} групп")
check("нет группы, покрывающей больше четверти корпуса",
      max(sizes.values()) < len(human) / 4, f"максимум {max(sizes.values())}")

codes_in_registry = {r["split_group_author"] for r in human}
unknown = set()
for f in sorted(SPLITS.glob("*.json")):
    data = json.loads(f.read_text(encoding="utf-8"))
    if data.get("holdout_field") == "split_group_author":
        for v in data.get("holdout_values", []):
            if CODE.match(v) and v not in codes_in_registry:
                unknown.add(v)
check("коды в разбиениях известны реестру", not unknown, str(sorted(unknown)[:3]))

cyr = [r["document_id"] for r in human
       if re.search(r"[А-Яа-я]{3,}", r["author_or_model_id"] + r["split_group_author"])]
check("кириллических имён в полях автора нет", not cyr, f"найдено {len(cyr)}")

failed = [c for c in checks if not c[1]]
print(f"проверок: {len(checks)}, непройденных: {len(failed)}")
for name, ok, detail in failed:
    print(f"  ПРОВАЛ: {name}" + (f" — {detail}" if detail else ""))
if failed:
    sys.exit(1)
print("псевдонимизация согласована")
print("напоминание: это псевдонимизация, а не анонимизация — ссылки на публичные "
      "первоисточники сохранены")
