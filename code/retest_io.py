#!/usr/bin/env python3
"""Общий ввод-вывод повторного прогона (retest-v1).

Процедура — `06-features/retest-spec.md`, §6. Экстрактор с ключом `--out`
пишет посчитанные записи в отдельный CSV и матрицу не трогает: без этого
прогон на подвыборке перезаписал бы значения остальных документов и пересчитал
`genre_percentile` по неполному пулу.

Модуль подключается всеми семью экстракторами и ничего не считает сам.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"


def read_ids(path):
    """Идентификаторы документов: по одному на строку либо колонка document_id."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        first = fh.readline().strip()
        fh.seek(0)
        if first.startswith("document_id"):
            return [row["document_id"] for row in csv.DictReader(fh) if row["document_id"].strip()]
        return [line.strip() for line in fh if line.strip()]


def write_records(path, records):
    """Записи в CSV по схеме матрицы. Порядок полей тот же, слияния нет."""
    with SCHEMA.open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return len(records)
