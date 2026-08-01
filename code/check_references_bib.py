#!/usr/bin/env python3
"""Проверка references.bib перед сборкой статьи.

    python 09-tools/check_references_bib.py

Проверяет:
1. число и уникальность `ref_id`, совпадение состава с матрицей;
2. отсутствие дублирующихся DOI;
3. наличие title и year у каждой записи;
4. экранирование спецсимволов LaTeX и баланс фигурных скобок;
5. что каждый `ref_id`, упомянутый в literature-review.md, есть в bib.

Ненулевой код возврата означает, что bib к сборке не готов.
"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIB = ROOT / "01-literature" / "references.bib"
MATRIX = ROOT / "01-literature" / "evidence-matrix.csv"
CITING = [ROOT / "01-literature" / "literature-review.md",
          ROOT / "08-paper" / "related-work.md"]

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ENTRY = re.compile(r"@(\w+)\{([^,]+),(.*?)\n\}", re.S)
FIELD = re.compile(r"^\s*(\w+)\s*=\s*\{(.*)\},?\s*$", re.M)
# Спецсимвол считается неэкранированным, если перед ним нет обратного слэша.
UNESCAPED = re.compile(r"(?<!\\)[%&$#_]")


def parse_entries(text):
    out = []
    for etype, key, body in ENTRY.findall(text):
        fields = dict(FIELD.findall(body))
        out.append({"type": etype, "key": key.strip(), "fields": fields})
    return out


def main():
    if not BIB.exists():
        print(f"ПРОВАЛ: нет файла {BIB.relative_to(ROOT)}")
        return 1

    text = BIB.read_text(encoding="utf-8")
    entries = parse_entries(text)
    failures = []

    print(f"записей разобрано: {len(entries)}")

    # 1. ref_id: уникальность и совпадение с матрицей
    ref_ids = []
    for entry in entries:
        found = re.search(r"ref_id:\s*(\S+?);", entry["fields"].get("note", ""))
        if found:
            ref_ids.append(found.group(1))
        else:
            failures.append(f"запись {entry['key']} без ref_id в note")
    dupes = [r for r, n in Counter(ref_ids).items() if n > 1]
    if dupes:
        failures.append(f"повторяющиеся ref_id: {dupes}")

    matrix_ids = {r["ref_id"] for r in
                  csv.DictReader(MATRIX.open(encoding="utf-8", newline=""))}
    missing = sorted(matrix_ids - set(ref_ids))
    extra = sorted(set(ref_ids) - matrix_ids)
    if missing:
        failures.append(f"в матрице есть, в bib нет: {missing}")
    if extra:
        failures.append(f"в bib есть, в матрице нет: {extra}")
    print(f"1. ref_id: {len(set(ref_ids))} уникальных, "
          f"в матрице {len(matrix_ids)}")

    # 2. дубликаты DOI
    dois = [e["fields"]["doi"] for e in entries if e["fields"].get("doi")]
    doi_dupes = [d for d, n in Counter(dois).items() if n > 1]
    if doi_dupes:
        failures.append(f"дублирующиеся DOI: {doi_dupes}")
    print(f"2. DOI: {len(dois)} записей, дубликатов {len(doi_dupes)}")

    # 3. title и year
    no_title = [e["key"] for e in entries if not e["fields"].get("title")]
    no_year = [e["key"] for e in entries if not e["fields"].get("year")]
    if no_title:
        failures.append(f"без title: {no_title}")
    if no_year:
        failures.append(f"без year: {no_year}")
    print(f"3. поля: без title {len(no_title)}, без year {len(no_year)}")

    # 4. экранирование и скобки
    bad_escape = []
    for entry in entries:
        for field in ("title", "author", "booktitle", "journal", "howpublished"):
            value = entry["fields"].get(field, "")
            if UNESCAPED.search(value):
                bad_escape.append(f"{entry['key']}.{field}")
    if bad_escape:
        failures.append(f"неэкранированные спецсимволы LaTeX: {bad_escape}")
    balance = text.count("{") - text.count("}")
    if balance:
        failures.append(f"дисбаланс фигурных скобок: {balance:+d}")
    keys = [e["key"] for e in entries]
    key_dupes = [k for k, n in Counter(keys).items() if n > 1]
    if key_dupes:
        failures.append(f"повторяющиеся ключи bibtex: {key_dupes}")
    print(f"4. LaTeX: неэкранированных {len(bad_escape)}, "
          f"баланс скобок {balance:+d}, дубликатов ключей {len(key_dupes)}")

    # 5. ссылки обзора и Related Work
    cited = set()
    for path in CITING:
        if not path.exists():
            failures.append(f"нет цитирующего файла {path.name}")
            continue
        found = set(re.findall(r"\br0\d{2}\b", path.read_text(encoding="utf-8")))
        dangling = sorted(found - set(ref_ids))
        if dangling:
            failures.append(f"{path.name} ссылается на отсутствующие в bib: "
                            f"{dangling}")
        cited |= found
        print(f"5. {path.name}: упомянуто {len(found)} ref_id, "
              f"несопоставленных {len(dangling)}")
    unused = sorted(set(ref_ids) - cited)
    print(f"   ни разу не процитировано: {len(unused)}"
          + (f" — {unused}" if unused else ""))

    print()
    if failures:
        print("ПРОВАЛ:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("все проверки пройдены, bib готов к сборке")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
