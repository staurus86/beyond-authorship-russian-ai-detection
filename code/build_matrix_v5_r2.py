#!/usr/bin/env python3
"""Сборка feature-matrix-v5-r2: согласование D04 и D05 с профилями prep-v5.

    python 09-tools/build_matrix_v5_r2.py --dry-run   # только шлюзы
    python 09-tools/build_matrix_v5_r2.py             # собрать артефакт

Корпус и препроцессинг не менялись: версия препроцессинга остаётся v5, ревизия
матрицы — r2. Прежний файл не перезаписывается.

Меняются только D04 и D05 и только у документов, где значение матрицы разошлось
с пересчётом замороженным `disc-v1`. Все прочие строки переносятся байт в байт, в
исходном порядке.

Основание — `02-preregistration/amendment-feature-matrix-v5-r2-discourse.md`.
"""
import argparse
import csv
import gzip
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

SRC = ROOT / "06-features" / "feature-matrix-v5.csv"
DST = ROOT / "06-features" / "feature-matrix-v5-r2.csv"
AUDIT = ROOT / "07-analysis" / "corpus-audit-d04-d05.json"
MANIFEST = ROOT / "06-features" / "feature-matrix-v5-r2-manifest.json"
MSK = timezone(timedelta(hours=3))

EXPECTED_ROWS = 118566
EXPECTED_DOCS = 1882
EXPECTED_FEATURES = 63
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


def quantized(value):
    return f"{value:.6g}"


def recompute(prep_version="prep-v5"):
    """Пересчёт D04 и D05 замороженным disc-v1 по кешу разбора.

    Версия препроцессинга передаётся явно. Прежде функция читала
    `disc.PREP_VERSION`, а это модульное умолчание равно `prep-v4` и меняется
    только аргументом командной строки самого экстрактора. 1 августа 2026 из-за
    этого пересчёт пошёл по текстам прежней версии, а собранная матрица заменила
    корректные значения на неверные — откат
    `07-analysis/rollback-decision-2026-08-01-d04-d05.json`.
    """
    import feature_cache as fc
    import extract_discourse as disc

    index = fc.load_index(disc.STANZA_CACHE)
    out, missing = {}, []
    for doc_id in fc.manifest(prep_version):
        input_sha = fc.sha_for(prep_version, "prose", doc_id)
        path = (fc.lookup(disc.STANZA_CACHE, index, doc_id, input_sha,
                          disc.STANZA_REVISION) if input_sha else None)
        if path is None:
            missing.append(doc_id)
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)
        result = disc.document_features(parsed)
        words = result["words"]
        out[doc_id] = {
            fid: (quantized(result[fid] * 1000 / words) if words > 0 else "")
            for fid in TARGET}
    return out, missing


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with SRC.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = list(reader.fieldnames)

    print(f"исходная матрица: строк {len(rows)}, "
          f"документов {len({r['document_id'] for r in rows})}")

    fresh, missing = recompute()
    print(f"пересчитано документов: {len(fresh)}, без разбора: {len(missing)}")

    changed, untouched = [], 0
    for row in rows:
        fid = row["feature_id"]
        if fid not in TARGET:
            untouched += 1
            continue
        new = fresh.get(row["document_id"], {}).get(fid)
        if new is None:
            untouched += 1
            continue
        old = row["normalized_value"]
        if old != new:
            changed.append({"document_id": row["document_id"], "feature_id": fid,
                            "was": old, "now": new})
            if not args.dry_run:
                row["normalized_value"] = new
        else:
            untouched += 1

    docs_changed = sorted({c["document_id"] for c in changed})
    audit_docs = sorted({m["document_id"] for m in
                         json.loads(AUDIT.read_text(encoding="utf-8"))["mismatch_documents"]})

    problems = []
    if len(rows) != EXPECTED_ROWS:
        problems.append(f"строк {len(rows)}, ожидалось {EXPECTED_ROWS}")
    if len({r["document_id"] for r in rows}) != EXPECTED_DOCS:
        problems.append("число документов изменилось")
    if len({r["feature_id"] for r in rows}) != EXPECTED_FEATURES:
        problems.append("число признаков изменилось")
    if missing:
        problems.append(f"{len(missing)} документов без разбора в кеше")
    if docs_changed != audit_docs:
        problems.append(f"изменённые документы не совпали с аудитом: "
                        f"{len(docs_changed)} против {len(audit_docs)}")
    off_target = [c for c in changed if c["feature_id"] not in TARGET]
    if off_target:
        problems.append(f"правки вне D04/D05: {len(off_target)}")

    print(f"\nшлюзы приёмки:")
    print(f"  строк: {len(rows)} из {EXPECTED_ROWS}")
    print(f"  изменено значений: {len(changed)} у {len(docs_changed)} документов")
    print(f"  список изменённых совпал с аудитом: {docs_changed == audit_docs}")
    print(f"  правок вне D04/D05: {len(off_target)}")
    print(f"  строк без изменений: {untouched}")

    if problems:
        print("\nПРИЁМКА НЕ ПРОЙДЕНА:")
        for item in problems:
            print(f"  - {item}")
        return 1
    if args.dry_run:
        print("\nвсе шлюзы пройдены, --dry-run: артефакт не записан")
        return 0

    with DST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    now = datetime.now(timezone.utc)
    manifest = {
        "artifact": DST.name,
        "revision": "v5-r2",
        "prep_version": "prep-v5",
        "basis": "02-preregistration/amendment-feature-matrix-v5-r2-discourse.md",
        "reason": "дискурсивный слой не был пересчитан после коррекции "
                  "извлечения correction-v5.0: D04 и D05 хранились по текстам "
                  "до коррекции",
        "supersedes": {"file": SRC.name, "sha256": sha256_file(SRC)},
        "audit": {"file": AUDIT.name, "sha256": sha256_file(AUDIT)},
        "gates": {"rows": len(rows), "documents": EXPECTED_DOCS,
                  "features": EXPECTED_FEATURES,
                  "values_changed": len(changed),
                  "documents_changed": len(docs_changed),
                  "matches_audit": True, "off_target_edits": 0,
                  "row_order_preserved": True},
        "changed_documents": docs_changed,
        "changed_values": changed,
        "output_sha256": sha256_file(DST),
        "built_at_utc": now.isoformat(timespec="seconds"),
        "built_at_moscow": now.astimezone(MSK).isoformat(timespec="seconds"),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nзаписано: {DST.name} и {MANIFEST.name}")
    print(f"sha256 артефакта: {manifest['output_sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
