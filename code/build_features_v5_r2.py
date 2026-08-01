#!/usr/bin/env python3
"""Сборка features-normalized-prep-v5-r2: вход перцентилей с исправленными D04 и D05.

    python 09-tools/build_features_v5_r2.py --dry-run   # только шлюзы
    python 09-tools/build_features_v5_r2.py             # собрать артефакт

Перцентиль считается не по итоговой матрице, а по слою нормализованных значений
(`compute_percentiles.py` отказывается принимать матрицу на вход). Дефект D04 и
D05 живёт и в этом слое, поэтому пересчёт рангов требует исправленной его копии.

Пересчёт значений берётся у проверенного сборщика матрицы: импортируется его
`recompute()`, сам файл не изменяется. Меняется только `normalized_value` у D04
и D05 и только у документов из отчёта аудита; `raw_value` не трогается — счётчики
вхождений не менялись, изменился объём текста.

Основание — `02-preregistration/amendment-feature-matrix-v5-r2-discourse.md`.
"""
import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

SRC = ROOT / "06-features" / "features-normalized-prep-v5.csv"
DST = ROOT / "06-features" / "features-normalized-prep-v5-r2.csv"
AUDIT = ROOT / "07-analysis" / "corpus-audit-d04-d05.json"
MANIFEST = ROOT / "06-features" / "features-normalized-prep-v5-r2-manifest.json"
MSK = timezone(timedelta(hours=3))

EXPECTED_ROWS = 118566
EXPECTED_DOCS = 1882
TARGET = ("D04", "D05")

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_fix(rows, fresh):
    """Замена нормализованных значений D04 и D05. Возвращает список изменений.

    Правило то же, что у сборщика матрицы: совпавшее значение не переписывается,
    пропуск остаётся пропуском, посторонние поля и порядок строк не трогаются.
    """
    changed = []
    for row in rows:
        fid = row["feature_id"]
        if fid not in TARGET:
            continue
        new = fresh.get(row["document_id"], {}).get(fid)
        if new is None:
            continue
        old = row["normalized_value"]
        if old != new:
            changed.append({"document_id": row["document_id"], "feature_id": fid,
                            "was": old, "now": new})
            row["normalized_value"] = new
    return changed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if DST.exists() and not args.dry_run:
        print(f"ОТКАЗ: {DST.name} уже существует и не перезаписывается")
        return 2

    import build_matrix_v5_r2 as builder

    with SRC.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = list(reader.fieldnames)
    print(f"исходный слой: строк {len(rows)}, "
          f"документов {len({r['document_id'] for r in rows})}")

    fresh, missing = builder.recompute()
    print(f"пересчитано документов: {len(fresh)}, без разбора: {len(missing)}")

    # Копия полей, которые обязаны остаться неизменными: сверяется поштучно, а
    # не по итоговому хешу, чтобы отличить правку raw от правки normalized.
    before = [(r["raw_value"], r["unit"], r["missing_reason"]) for r in rows]
    changed = apply_fix(rows, fresh)
    after = [(r["raw_value"], r["unit"], r["missing_reason"]) for r in rows]

    docs_changed = sorted({c["document_id"] for c in changed})
    audit_docs = sorted({m["document_id"] for m in
                         json.loads(AUDIT.read_text(encoding="utf-8"))["mismatch_documents"]})

    raw_touched = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    percentile_filled = sum(1 for r in rows if r["genre_percentile"])

    problems = []
    if len(rows) != EXPECTED_ROWS:
        problems.append(f"строк {len(rows)}, ожидалось {EXPECTED_ROWS}")
    if len({r["document_id"] for r in rows}) != EXPECTED_DOCS:
        problems.append("число документов изменилось")
    if missing:
        problems.append(f"{len(missing)} документов без разбора в кеше")
    if docs_changed != audit_docs:
        problems.append(f"изменённые документы не совпали с аудитом: "
                        f"{len(docs_changed)} против {len(audit_docs)}")
    if any(c["feature_id"] not in TARGET for c in changed):
        problems.append("правки вне D04 и D05")
    if raw_touched:
        problems.append(f"изменён raw_value у {len(raw_touched)} строк")
    if percentile_filled:
        problems.append(f"перцентиль заполнен в слое у {percentile_filled} строк")

    print("\nшлюзы приёмки:")
    print(f"  строк: {len(rows)} из {EXPECTED_ROWS}")
    print(f"  изменено значений: {len(changed)} у {len(docs_changed)} документов")
    print(f"  список изменённых совпал с аудитом: {docs_changed == audit_docs}")
    print(f"  raw_value не тронут: {not raw_touched}")
    print(f"  перцентиль в слое остаётся пустым: {percentile_filled == 0}")

    if problems:
        for p in problems:
            print(f"  ПРОБЛЕМА: {p}")
        return 1
    if args.dry_run:
        print("\ndry-run: файл не записан")
        return 0

    with DST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    stamp = datetime.now(timezone.utc)
    MANIFEST.write_text(json.dumps({
        "artifact": DST.name,
        "sha256": sha256_file(DST),
        "source": {"file": SRC.name, "sha256": sha256_file(SRC)},
        "audit": {"file": AUDIT.name, "sha256": sha256_file(AUDIT)},
        "changed_values": len(changed),
        "changed_documents": len(docs_changed),
        "rule": "заменён normalized_value у D04 и D05; raw_value, прочие "
                "признаки, пропуски, метаданные и порядок строк не менялись",
        "created_at_utc": stamp.isoformat(timespec="seconds"),
        "created_at_moscow": stamp.astimezone(MSK).isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nзаписано: {DST.name}")
    print(f"  sha256: {sha256_file(DST)[:16]}…")
    print(f"  манифест: {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
