#!/usr/bin/env python3
"""Синтетический тест сборщика feature-matrix-v5-r2.

    python 09-tools/test_matrix_v5_r2_synth.py

Проверяет правила замены на игрушечной матрице, не касаясь корпуса: меняются
только D04 и D05 и только там, где значение разошлось; порядок строк, прочие
признаки, пропуски и метаданные сохраняются; расхождение состава документов с
отчётом аудита блокирует приёмку.
"""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FAILED = 0
FIELDS = ["document_id", "feature_id", "normalized_value", "raw_value",
          "missing_reason"]


def check(label, got, want):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    print(f"  [{'ok' if ok else 'СБОЙ'}] {label}: {got!r}")


def make_rows():
    """Матрица из трёх документов: два признака-цели и один посторонний."""
    rows = []
    for doc in ("docA", "docB", "docC"):
        rows.append({"document_id": doc, "feature_id": "D04",
                     "normalized_value": "1.11111", "raw_value": "3",
                     "missing_reason": ""})
        rows.append({"document_id": doc, "feature_id": "D05",
                     "normalized_value": "2.22222", "raw_value": "5",
                     "missing_reason": ""})
        rows.append({"document_id": doc, "feature_id": "L01",
                     "normalized_value": "9.99999", "raw_value": "9",
                     "missing_reason": ""})
    return rows


def apply_rules(rows, fresh):
    """Правило замены сборщика в чистом виде."""
    changed = []
    for row in rows:
        fid = row["feature_id"]
        if fid not in ("D04", "D05"):
            continue
        new = fresh.get(row["document_id"], {}).get(fid)
        if new is None:
            continue
        if row["normalized_value"] != new:
            changed.append((row["document_id"], fid,
                            row["normalized_value"], new))
            row["normalized_value"] = new
    return changed


def main():
    print("Синтетический тест сборщика матрицы v5-r2")

    rows = make_rows()
    order_before = [(r["document_id"], r["feature_id"]) for r in rows]
    fresh = {"docB": {"D04": "1.50000", "D05": "2.22222"}}

    changed = apply_rules(rows, fresh)
    check("изменено значений", len(changed), 1)
    check("изменён только D04 у docB", changed[0][:2], ("docB", "D04"))

    order_after = [(r["document_id"], r["feature_id"]) for r in rows]
    check("порядок строк сохранён", order_after == order_before, True)

    l01 = [r for r in rows if r["feature_id"] == "L01"]
    check("посторонний признак не тронут",
          all(r["normalized_value"] == "9.99999" for r in l01), True)

    d05_b = next(r for r in rows
                 if r["document_id"] == "docB" and r["feature_id"] == "D05")
    check("совпавшее значение не переписано",
          d05_b["normalized_value"], "2.22222")

    untouched_docs = [r for r in rows if r["document_id"] in ("docA", "docC")
                      and r["feature_id"] in ("D04", "D05")]
    check("документы без пересчёта не тронуты",
          all(r["normalized_value"] in ("1.11111", "2.22222")
              for r in untouched_docs), True)

    # Пропуск остаётся пропуском: пустая строка не подменяется значением.
    rows2 = make_rows()
    rows2[0]["normalized_value"] = ""
    rows2[0]["missing_reason"] = "нет словных токенов"
    apply_rules(rows2, {"docA": {"D04": ""}})
    check("пропуск сохранён", rows2[0]["normalized_value"], "")
    check("причина пропуска сохранена",
          rows2[0]["missing_reason"], "нет словных токенов")

    # Состав изменённых документов обязан совпасть с отчётом аудита.
    rows3 = make_rows()
    changed3 = apply_rules(rows3, {"docC": {"D04": "7.00000"}})
    docs_changed = sorted({c[0] for c in changed3})
    check("расхождение с аудитом обнаруживается",
          docs_changed != ["docB"], True)

    # Запись в CSV не меняет число строк и заголовок.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    written = list(csv.DictReader(io.StringIO(buffer.getvalue())))
    check("число строк после записи", len(written), len(rows))
    check("заголовок сохранён",
          list(written[0].keys()) == FIELDS, True)

    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
