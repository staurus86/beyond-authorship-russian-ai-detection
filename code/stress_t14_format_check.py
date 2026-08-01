#!/usr/bin/env python3
"""Диагностика: остаётся ли у t14 форматный вклад после правки r4.

    python 09-tools/stress_t14_format_check.py

Только чтение. Расчётный код не меняется — берутся функции самого прогона:
`frozen_pools` и `percentile` из `stress_run_p1`. R06, R07, F01 и P01 целиком
выводятся из манифеста панели, поэтому разбор и эмбеддинги для этой проверки не
нужны.

Отчёт: `07-analysis/stress-t14-cliche-source-defect.md`.
"""
import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "09-tools"))
import stress_run_p1 as p1  # noqa: E402

MANIFEST = ROOT / "04-corpus" / "derived" / "stress-v3" / "manifest.csv"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def manifest_features(row):
    """Те же формулы, что в extract_features.document_features, строки 405-419."""
    full_words = float(row["full_words"] or 0)
    if not full_words:
        return {}
    scale = 1000 / full_words
    out = {}
    for fid, column in (("R07", "list_items"), ("F01", "full_bold_spans"),
                        ("R06", "heading_md")):
        out[fid] = float(row.get(column) or 0) * scale
    out["P01"] = 1 - float(row["prose_words"] or 0) / full_words
    return out


def main():
    genre_of = {r["document_id"]: r["genre"]
                for r in p1.read_csv(p1.REGISTRY, "utf-8-sig")}
    pools = p1.frozen_pools()

    by_doc = defaultdict(dict)
    with MANIFEST.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            by_doc[r["document_id"]][int(r["transformation_id"])] = r

    shifted_value = defaultdict(int)
    shifted_pct = defaultdict(int)
    pct_delta = defaultdict(list)
    crossed = []

    for doc, cells in by_doc.items():
        genre = genre_of.get(doc)
        base, t14 = manifest_features(cells[1]), manifest_features(cells[14])
        for fid in ("R06", "R07", "F01", "P01"):
            if abs(base[fid] - t14[fid]) > 1e-12:
                shifted_value[fid] += 1
            pb = p1.percentile(pools, fid, genre, base[fid])
            pt = p1.percentile(pools, fid, genre, t14[fid])
            if pb is None or pt is None:
                continue
            if abs(pb - pt) > 1e-12:
                shifted_pct[fid] += 1
                pct_delta[fid].append(pt - pb)
            tb = 0 if pb < 1 / 3 else (1 if pb < 2 / 3 else 2)
            tt = 0 if pt < 1 / 3 else (1 if pt < 2 / 3 else 2)
            if tb != tt:
                crossed.append((doc, fid, round(pb, 4), round(pt, 4), tb, tt))

    print("t14 против t01, 60 документов панели, ревизия r4\n")
    print(f"{'признак':<8} {'значение сдвинулось':>20} {'перцентиль сдвинулся':>22} "
          f"{'медиана dP':>12} {'макс |dP|':>11}")
    for fid in ("R06", "R07", "F01", "P01"):
        d = pct_delta[fid]
        med = f"{st.median(d):+.4f}" if d else "—"
        mx = f"{max(map(abs, d)):.4f}" if d else "—"
        print(f"{fid:<8} {shifted_value[fid]:>20} {shifted_pct[fid]:>22} "
              f"{med:>12} {mx:>11}")

    print(f"\nпересечений границы терциля: {len(crossed)}")
    for row in crossed:
        print("   ", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
