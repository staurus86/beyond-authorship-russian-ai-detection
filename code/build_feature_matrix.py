#!/usr/bin/env python3
"""Сборка итоговой матрицы признаков из слоёв и жанровых перцентилей.

    python 09-tools/build_feature_matrix.py --prep-version prep-v5

Слои экстракторов пишут `features-normalized-<версия>.csv` без колонки
`genre_percentile`: перцентиль зависит от состава корпуса, и при частичном
пересчёте пул внутри экстрактора оказался бы неполным. Этот скрипт склеивает
значения слоёв с отдельно посчитанными перцентилями в `feature-matrix-v5.csv`.

Перед склейкой сверяется, что перцентили посчитаны **по этому же файлу слоёв**:
ключ `genre-percentiles-<версия>.key.json` хранит sha256 своего входа. Иначе
матрица собралась бы из значений одной ревизии и рангов другой.
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FEATURES = ROOT / "06-features"
SCHEMA = FEATURES / "feature-matrix-schema.csv"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prep-version", default="prep-v5")
    args = parser.parse_args()
    version = args.prep_version
    short = version.replace("prep-", "")

    layers = FEATURES / f"features-normalized-{version}.csv"
    percentiles = FEATURES / f"genre-percentiles-{version}.csv"
    key_path = FEATURES / f"genre-percentiles-{version}.key.json"
    out_path = FEATURES / f"feature-matrix-{short}.csv"

    for path in (layers, percentiles, key_path):
        if not path.exists():
            raise SystemExit(f"нет входа {path.name}")
    # Имя выхода выводится из версии, поэтому `--prep-version prep-v5-r2` целится
    # ровно в уже собранную матрицу. Замороженный артефакт не перезаписывается:
    # для новой сборки нужна своя версия имени.
    if out_path.exists():
        raise SystemExit(f"{out_path.name} уже собран и не перезаписывается: "
                         "новая сборка идёт под своим именем версии")

    key = json.loads(key_path.read_text(encoding="utf-8"))
    actual = sha256_file(layers)
    if key.get("features_normalized_sha256") != actual:
        raise SystemExit(
            f"перцентили посчитаны по другой ревизии {layers.name}: в ключе "
            f"{str(key.get('features_normalized_sha256'))[:12]}, на диске "
            f"{actual[:12]}. Пересчитайте compute_percentiles.py")
    if key.get("registry_sha256") != sha256_file(REGISTRY):
        raise SystemExit("перцентили посчитаны на другом реестре")

    ranks = {}
    with percentiles.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ranks[(row["document_id"], row["feature_id"])] = row["genre_percentile"]

    with SCHEMA.open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))

    written = filled = 0
    with layers.open(encoding="utf-8", newline="") as src, \
            out_path.open("w", encoding="utf-8", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fields)
        writer.writeheader()
        for row in csv.DictReader(src):
            value = ranks.get((row["document_id"], row["feature_id"]), "")
            row["genre_percentile"] = value
            if value:
                filled += 1
            writer.writerow({name: row.get(name, "") for name in fields})
            written += 1

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (FEATURES / f"feature-matrix-{short}.key.json").write_text(json.dumps({
        "created_at": stamp,
        "prep_version": version,
        "inputs": {layers.name: actual, percentiles.name: sha256_file(percentiles),
                   key_path.name: sha256_file(key_path),
                   "documents-registry.csv": sha256_file(REGISTRY)},
        "rows": written, "percentiles_filled": filled,
        "output_sha256": sha256_file(out_path),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"матрица собрана: {out_path.name}")
    print(f"  строк {written}, из них с перцентилем {filled}")
    print(f"  ключ: feature-matrix-{short}.key.json")


if __name__ == "__main__":
    main()
