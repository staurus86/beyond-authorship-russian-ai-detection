#!/usr/bin/env python3
"""Деривация P4 ревизии r5 из завершённого прогона r4.

    python 09-tools/derive_p4_r5.py            # выполнить деривацию
    python 09-tools/derive_p4_r5.py --dry-run  # только проверки, без записи

Судья повторно не вызывается. Тексты десяти преобразований в r5 те же, что в r4,
поэтому промпты совпадают, и повторный прогон при фиксированных сидах дал бы те же
ответы. Ревизия r5 получает свои файлы детерминированным преобразованием журнала
r4: удаляются записи преобразования t14, остальные переносятся без изменений.

Основание — `02-preregistration/amendment-stress-r5-t14-not-executable.md`.

Четыре шлюза, все обязательны:

1. 600 медиан в таблице оценок;
2. 1800 принятых seed-оценок в журнале;
3. ни одной строки t14 ни в журнале, ни в таблице;
4. медиана каждой из 600 ячеек, пересчитанная из перенесённого журнала,
   совпадает со строкой r4 посимвольно.

Ненулевой код возврата означает, что деривация не выполнена.
"""
import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "07-analysis"

SRC_RAW = ANALYSIS / "stress-p4-r4-raw.jsonl"
SRC_CSV = ANALYSIS / "stress-p4-r4-scores.csv"
SRC_MANIFEST = ANALYSIS / "stress-p4-r4-manifest.json"
AMENDMENT = (ROOT / "02-preregistration"
             / "amendment-stress-r5-t14-not-executable.md")

OUT_RAW = ANALYSIS / "stress-p4-r5-raw.jsonl"
OUT_CSV = ANALYSIS / "stress-p4-r5-scores.csv"
OUT_MANIFEST = ANALYSIS / "stress-p4-r5-manifest.json"
OUT_REPORT = ANALYSIS / "stress-p4-r5-derivation.md"

DROPPED_TRANSFORM = 14
EXPECTED_CELLS = 600
EXPECTED_CALLS = 1800

MSK = timezone(timedelta(hours=3))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_raw(path):
    out = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for path in (SRC_RAW, SRC_CSV, AMENDMENT):
        if not path.exists():
            print(f"ОТКАЗ: нет файла {path.relative_to(ROOT)}")
            return 2

    raw_r4 = load_raw(SRC_RAW)
    with SRC_CSV.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows_r4 = list(reader)
        fields = list(reader.fieldnames)

    raw_r5 = [r for r in raw_r4
              if int(r["transform_number"]) != DROPPED_TRANSFORM]
    rows_r5 = [r for r in rows_r4
               if int(r["transform_number"]) != DROPPED_TRANSFORM]

    print(f"вход:  журнал {len(raw_r4)} записей, таблица {len(rows_r4)} строк")
    print(f"выход: журнал {len(raw_r5)} записей, таблица {len(rows_r5)} строк")

    failures = []
    checks = {}

    # Шлюз 1: 600 медиан
    with_median = [r for r in rows_r5 if (r.get("median_transformed") or "").strip()]
    checks["cells"] = {"rows": len(rows_r5), "with_median": len(with_median),
                       "expected": EXPECTED_CELLS}
    if len(rows_r5) != EXPECTED_CELLS:
        failures.append(f"строк {len(rows_r5)}, ожидалось {EXPECTED_CELLS}")
    if len(with_median) != EXPECTED_CELLS:
        failures.append(f"строк с медианой {len(with_median)}, "
                        f"ожидалось {EXPECTED_CELLS}")

    # Шлюз 2: 1800 принятых seed-оценок
    accepted = [r for r in raw_r5 if r.get("status") == "ok"
                and r.get("score") is not None]
    checks["calls"] = {"records": len(raw_r5), "accepted": len(accepted),
                       "expected": EXPECTED_CALLS}
    if len(raw_r5) != EXPECTED_CALLS:
        failures.append(f"записей журнала {len(raw_r5)}, "
                        f"ожидалось {EXPECTED_CALLS}")
    if len(accepted) != EXPECTED_CALLS:
        failures.append(f"принятых оценок {len(accepted)}, "
                        f"ожидалось {EXPECTED_CALLS}")

    # Шлюз 3: ни одной строки t14 в том, что уйдёт в выходы r5.
    # Проверяется дважды — здесь на подготовленных данных и после записи на
    # перечитанных файлах.
    left_raw = sum(1 for r in raw_r5
                   if int(r["transform_number"]) == DROPPED_TRANSFORM)
    left_csv = sum(1 for r in rows_r5
                   if int(r["transform_number"]) == DROPPED_TRANSFORM)
    kept_raw_r4 = sum(1 for r in raw_r4
                      if int(r["transform_number"]) == DROPPED_TRANSFORM)
    kept_csv_r4 = sum(1 for r in rows_r4
                      if int(r["transform_number"]) == DROPPED_TRANSFORM)
    checks["t14_removed"] = {"raw": left_raw, "csv": left_csv,
                             "dropped_raw": len(raw_r4) - len(raw_r5),
                             "dropped_csv": len(rows_r4) - len(rows_r5),
                             "kept_in_r4_raw": kept_raw_r4,
                             "kept_in_r4_csv": kept_csv_r4}
    if left_raw or left_csv:
        failures.append(f"остались строки t14: журнал {left_raw}, "
                        f"таблица {left_csv}")
    # r4 — завершённый прогон: строки t14 обязаны остаться его частью.
    if not kept_raw_r4 or not kept_csv_r4:
        failures.append("во входах r4 нет строк t14: прогон r4 повреждён "
                        f"(журнал {kept_raw_r4}, таблица {kept_csv_r4})")

    # Шлюз 4: медианы пересчитываются из журнала и совпадают со строками r4
    per_cell = defaultdict(dict)
    for rec in accepted:
        per_cell[(rec["document_id"], int(rec["transform_number"]))][rec["seed"]] = \
            rec["score"]

    mismatched = []
    recomputed = 0
    for row in rows_r5:
        key = (row["document_id"], int(row["transform_number"]))
        values = list(per_cell.get(key, {}).values())
        if not values:
            mismatched.append(f"{key}: нет оценок в журнале")
            continue
        median_now = f"{statistics.median(values):.1f}"
        recomputed += 1
        if median_now != (row.get("median_transformed") or "").strip():
            mismatched.append(f"{key}: журнал {median_now}, "
                              f"таблица r4 {row.get('median_transformed')}")
    checks["medians"] = {"recomputed": recomputed,
                         "mismatched": len(mismatched)}
    if mismatched:
        failures.append(f"расхождение медиан: {len(mismatched)} ячеек, "
                        f"первые — {mismatched[:3]}")

    print(f"\nшлюз 1 — строк {len(rows_r5)}, с медианой {len(with_median)}")
    print(f"шлюз 2 — записей {len(raw_r5)}, принятых {len(accepted)}")
    print(f"шлюз 3 — осталось t14: журнал {left_raw}, таблица {left_csv}; "
          f"удалено {len(raw_r4) - len(raw_r5)} записей и "
          f"{len(rows_r4) - len(rows_r5)} строк")
    print(f"шлюз 4 — пересчитано медиан {recomputed}, расхождений "
          f"{len(mismatched)}")

    if failures:
        print("\nДЕРИВАЦИЯ НЕ ВЫПОЛНЕНА:")
        for item in failures:
            print(f"  - {item}")
        return 1

    if args.dry_run:
        print("\nвсе четыре шлюза пройдены, --dry-run: файлы не записаны")
        return 0

    src_raw_before = sha256_file(SRC_RAW)
    src_csv_before = sha256_file(SRC_CSV)

    with OUT_RAW.open("w", encoding="utf-8") as fh:
        for rec in raw_r5:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows_r5)

    # Контроль записанного: t14 не должно быть ни в одном из выходов, а входы r4
    # обязаны остаться нетронутыми.
    written_raw = load_raw(OUT_RAW)
    with OUT_CSV.open(encoding="utf-8", newline="") as fh:
        written_csv = list(csv.DictReader(fh))
    post = []
    left_out_raw = sum(1 for r in written_raw
                       if int(r["transform_number"]) == DROPPED_TRANSFORM)
    left_out_csv = sum(1 for r in written_csv
                       if int(r["transform_number"]) == DROPPED_TRANSFORM)
    if left_out_raw or left_out_csv:
        post.append(f"в выходах r5 остались строки t14: журнал {left_out_raw}, "
                    f"таблица {left_out_csv}")
    if len(written_raw) != EXPECTED_CALLS:
        post.append(f"записано {len(written_raw)} записей журнала, "
                    f"ожидалось {EXPECTED_CALLS}")
    if len(written_csv) != EXPECTED_CELLS:
        post.append(f"записано {len(written_csv)} строк таблицы, "
                    f"ожидалось {EXPECTED_CELLS}")
    if sha256_file(SRC_RAW) != src_raw_before:
        post.append("журнал r4 изменился во время деривации")
    if sha256_file(SRC_CSV) != src_csv_before:
        post.append("таблица r4 изменилась во время деривации")
    checks["written"] = {"raw": len(written_raw), "csv": len(written_csv),
                         "t14_in_outputs": left_out_raw + left_out_csv,
                         "r4_unchanged": not post}
    if post:
        print("\nКОНТРОЛЬ ЗАПИСИ НЕ ПРОЙДЕН:")
        for item in post:
            print(f"  - {item}")
        return 1
    print(f"контроль записи: выходы r5 без t14, входы r4 не изменились")

    now = datetime.now(timezone.utc)
    manifest = {
        "procedure": "P4",
        "revision": "r5",
        "kind": "derivation",
        "status": "completed",
        "derived_from": "r4",
        "judge_called": False,
        "rule": f"drop transform_number == {DROPPED_TRANSFORM}",
        "basis": "02-preregistration/amendment-stress-r5-t14-not-executable.md",
        "counts": {
            "raw_in": len(raw_r4), "raw_out": len(raw_r5),
            "csv_in": len(rows_r4), "csv_out": len(rows_r5),
            "accepted_scores": len(accepted),
        },
        "gates": checks,
        "inputs": {
            "stress-p4-r4-raw.jsonl": sha256_file(SRC_RAW),
            "stress-p4-r4-scores.csv": sha256_file(SRC_CSV),
            "amendment-stress-r5-t14-not-executable.md": sha256_file(AMENDMENT),
            "derive_p4_r5.py": sha256_file(Path(__file__)),
        },
        "outputs": {
            "stress-p4-r5-raw.jsonl": sha256_file(OUT_RAW),
            "stress-p4-r5-scores.csv": sha256_file(OUT_CSV),
        },
        "derived_at_utc": now.isoformat(timespec="seconds"),
        "derived_at_moscow": now.astimezone(MSK).isoformat(timespec="seconds"),
    }
    if SRC_MANIFEST.exists():
        manifest["inputs"]["stress-p4-r4-manifest.json"] = \
            sha256_file(SRC_MANIFEST)
    OUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    report = [
        "# Деривация P4 ревизии r5 из прогона r4", "",
        f"Выполнено {now.astimezone(MSK).isoformat(timespec='seconds')}.",
        "Судья повторно не вызывался.", "",
        "## Правило", "",
        f"Из журнала и таблицы r4 удалены записи преобразования "
        f"t{DROPPED_TRANSFORM}; остальные перенесены без изменений. Основание — "
        "`amendment-stress-r5-t14-not-executable.md`.", "",
        "## Шлюзы", "",
        "| Шлюз | Ожидалось | Получено |",
        "|---|---|---|",
        f"| медианы | {EXPECTED_CELLS} | {len(with_median)} |",
        f"| принятые seed-оценки | {EXPECTED_CALLS} | {len(accepted)} |",
        f"| строки t14 | 0 | журнал {left_raw}, таблица {left_csv} |",
        f"| совпадение медиан с r4 | {EXPECTED_CELLS} | "
        f"{recomputed - len(mismatched)} |", "",
        "Медианы пересчитаны из перенесённого журнала и сверены со строками r4 "
        "посимвольно.", "",
        "## Хеши", "",
        "| Файл | Роль | sha256 |", "|---|---|---|",
    ]
    for name, digest in manifest["inputs"].items():
        report.append(f"| `{name}` | вход | `{digest}` |")
    for name, digest in manifest["outputs"].items():
        report.append(f"| `{name}` | выход | `{digest}` |")
    report += ["", "Прогон r4 сохранён целиком и не перезаписан.", ""]
    OUT_REPORT.write_text("\n".join(report), encoding="utf-8")

    print(f"\nвсе четыре шлюза пройдены")
    print(f"записано: {OUT_RAW.name}, {OUT_CSV.name}, {OUT_MANIFEST.name}, "
          f"{OUT_REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
