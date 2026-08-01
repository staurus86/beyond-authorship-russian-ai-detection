#!/usr/bin/env python
"""Исключение шести документов страты 3, найденных при починке NER.

Решение PI от 2026-07-25.

Пять — номера университетской газеты «Ленинградский университет», попавшие в
страту научных статей через тип `dc:type=Other` репозитория СПбГУ. Жанр не тот,
регламент не тот, текст испорчен распознаванием. Шестой — файл КиберЛенинки,
в котором склеены две разные статьи разных авторов.

Файлы с диска не удаляются: исключение — операция над реестром.

Запуск:
    python 09-tools/exclude_offscope.py --dry-run
    python 09-tools/exclude_offscope.py --apply
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-offscope-exclusion"

EXCLUSION_DATE = "2026-07-25"
DECIDED_BY = "principal investigator"

NEWSPAPER = "номер университетской газеты «Ленинградский университет» вместо научной статьи: "
CASES = [
    ("human_science_spbgu_0051", "SCOPE", NEWSPAPER + "шапка «Орган парткома, ректората, комитета ВЛКСМ», № 12 (547) от 29 марта 1946 года"),
    ("human_science_spbgu_0052", "SCOPE", NEWSPAPER + "шапка «Пролетарии всех стран, соединяйтесь», издание Ленинградского университета"),
    ("human_science_spbgu_0053", "SCOPE", NEWSPAPER + "передовица «Развивать содружество науки и производства», газетная полоса"),
    ("human_science_spbgu_0056", "SCOPE", NEWSPAPER + "шапка «УНИВЕРСИТЕТ. Орган парткома, ректората»"),
    ("human_science_spbgu_0058", "SCOPE", NEWSPAPER + "шапка «Учиться, работать и бороться по Ленину», газетная полоса"),
    (
        "human_science_cyberleninka_0012",
        "TECH",
        "в одном файле склеены две статьи: текст Е. Н. Лисовой со своим списком литературы "
        "занимает первые 12% объёма, дальше без разрыва идёт статья другого автора "
        "(Воронежский экономико-правовой институт) и обрывается заголовком списка без записей",
    ),
    (
        "human_science_cyberleninka_0061",
        "TECH",
        "тот же дефект у документа, собранного в замену: файл начинается формулами статьи "
        "о межсетевом экране, на 16% объёма несёт её нумерованный список литературы, "
        "дальше идёт другая статья — о системах автономного электроснабжения",
    ),
    (
        "human_science_cyberleninka_0062",
        "TECH",
        "извлечение потеряло начало и конец: файл открывается словами «вибратора, реализованного "
        "на D-триггере» и обрывается на «при сравнительно ма-», заголовок записи — "
        "«Портативная установка для исследования физических параметров водоёмов»",
    ),
    (
        "human_science_cyberleninka_0063",
        "SCOPE",
        "рецензия на книгу, а не исследовательская статья: текст открывается рубрикой "
        "«ЗАМЕТКИ О КНИГАХ» и разбирает монографию С. А. Ляушевой; хвост обрывается переносом "
        "«субъектов ис-»",
    ),
    (
        "human_science_cyberleninka_0064",
        "TECH",
        "файл открывается серединой англоязычного списка литературы («7. Kwitt R., Hofmann U. "
        "Robust Methods for Unsupervised PCA-based Anomaly Detection…») и заканчивается перечнем "
        "ключевых слов; связного текста одной статьи в файле нет",
    ),
    (
        "human_science_cyberleninka_0065",
        "LANG",
        "статья на украинском языке: «Особливостi маркетингової дiяльностi в Iнтернет», "
        "Харьковский институт торговли и экономики; доля украинских служебных слов 0.0218 "
        "против 0.0051 у ближайшего русского документа корпуса. Фильтр доли кириллицы её "
        "пропустил: диакритика потеряна при извлечении, «i» стоит латинская",
    ),
    (
        "human_science_cyberleninka_0066",
        "TECH",
        "хвост документа — украинская аннотация с испорченной кодировкой: «Ключовi слова: "
        "шдукцшний пщ^в; перетворювач частоти». Очистка `cut_references` её не снимает: "
        "она срезает хвостовые абзацы без кириллицы, а этот кириллический. Документ собран "
        "в паре с `cyberleninka_0067` и уступает ему по чистоте текста",
    ),
]

CYR = re.compile(r"[А-Яа-яЁё]{2,}")


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def describe(row: dict) -> str:
    """Признак пересчитывается перед исключением, а не берётся из прежней записи."""
    text = (ROOT / row["file_path"]).read_text(encoding="utf-8-sig", errors="replace")
    words = CYR.findall(text)
    head = " ".join(text.split()[:8])
    return f"слов {len(words)}, начало: «{head}»"


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
    human_before = sum(1 for row in reg_rows if row["origin_class"] == "H")
    human_after = sum(1 for row in kept_rows if row["origin_class"] == "H")
    print(f"\nреестр: {len(reg_rows)} -> {len(kept_rows)}; H: {human_before} -> {human_after}")

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
