#!/usr/bin/env python3
"""Синтетический тест сборщика слоя features-normalized-prep-v5-r2 и пересчёта рангов.

    python 09-tools/test_features_v5_r2_synth.py

Проверяется на игрушечных данных, корпус не читается. Две части:

1. правило замены значений в слое — меняется только `normalized_value` у D04 и
   D05, `raw_value` и посторонние поля не трогаются, порядок строк сохраняется;
2. правило перцентиля из `genre-percentiles-prep-v5.key.json` — пул внутри пары
   признак × жанр, ранг как доля строго меньших, ties по нижней границе,
   документ без значения в пул не входит, и смена одного значения двигает ранги
   соседей по пулу.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FAILED = 0


def check(label, got, want):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    print(f"  [{'ok' if ok else 'СБОЙ'}] {label}: {got!r}" +
          ("" if ok else f" — ожидалось {want!r}"))


def row(doc, fid, raw, norm, reason=""):
    return {"document_id": doc, "feature_id": fid, "raw_value": raw,
            "normalized_value": norm, "unit": "на 1000 слов",
            "genre_percentile": "", "missing_reason": reason}


def percentiles(rows, genre):
    """Правило percentile-v1.0 в чистом виде."""
    pools = defaultdict(list)
    for r in rows:
        value = r["normalized_value"] or r["raw_value"]
        if value:
            pools[(r["feature_id"], genre[r["document_id"]])].append(float(value))
    for key in pools:
        pools[key].sort()
    out = {}
    for r in rows:
        value = r["normalized_value"] or r["raw_value"]
        if not value:
            continue
        pool = pools[(r["feature_id"], genre[r["document_id"]])]
        rank = sum(1 for item in pool if item < float(value))
        out[(r["document_id"], r["feature_id"])] = f"{rank / len(pool):.4f}"
    return out


def main():
    print("Синтетический тест: слой v5-r2 и пересчёт рангов")
    import build_features_v5_r2 as builder

    rows = [row("docA", "D04", "3", "1.00000"), row("docA", "D05", "5", "2.00000"),
            row("docA", "L01", "9", "9.00000"), row("docB", "D04", "4", "3.00000"),
            row("docB", "D05", "6", "4.00000"), row("docB", "L01", "8", "8.00000")]
    order_before = [(r["document_id"], r["feature_id"]) for r in rows]
    raw_before = [r["raw_value"] for r in rows]

    changed = builder.apply_fix(rows, {"docA": {"D04": "5.00000", "D05": "2.00000"}})
    check("изменено значений", len(changed), 1)
    check("изменён только D04 у docA", (changed[0]["document_id"],
                                        changed[0]["feature_id"]), ("docA", "D04"))
    check("совпавшее значение не переписано",
          next(r for r in rows if r["document_id"] == "docA"
               and r["feature_id"] == "D05")["normalized_value"], "2.00000")
    check("raw_value не тронут", [r["raw_value"] for r in rows], raw_before)
    check("порядок строк сохранён",
          [(r["document_id"], r["feature_id"]) for r in rows] == order_before, True)
    check("посторонний признак не тронут",
          all(r["normalized_value"] == {"docA": "9.00000", "docB": "8.00000"}[r["document_id"]]
              for r in rows if r["feature_id"] == "L01"), True)
    check("перцентиль в слое остался пустым",
          all(r["genre_percentile"] == "" for r in rows), True)

    # Пропуск не подменяется значением и в пул не входит.
    gap = [row("docC", "D04", "", "", "нет словных токенов")]
    builder.apply_fix(gap, {"docC": {"D04": ""}})
    check("пропуск сохранён", gap[0]["normalized_value"], "")
    check("причина пропуска сохранена", gap[0]["missing_reason"], "нет словных токенов")

    # Правило перцентиля.
    genre = {"d1": "seo", "d2": "seo", "d3": "seo", "d4": "news"}
    pool_rows = [row("d1", "D04", "1", "1.0"), row("d2", "D04", "2", "2.0"),
                 row("d3", "D04", "3", "3.0"), row("d4", "D04", "9", "9.0")]
    p = percentiles(pool_rows, genre)
    check("наименьший в пуле", p[("d1", "D04")], "0.0000")
    check("средний в пуле", p[("d2", "D04")], "0.3333")
    check("жанр news считается отдельно", p[("d4", "D04")], "0.0000")

    ties = [row("t1", "D04", "1", "5.0"), row("t2", "D04", "1", "5.0"),
            row("t3", "D04", "2", "7.0")]
    pt = percentiles(ties, {"t1": "seo", "t2": "seo", "t3": "seo"})
    check("ties получают нижнюю границу",
          pt[("t1", "D04")] == pt[("t2", "D04")] == "0.0000", True)

    with_gap = [row("g1", "D04", "1", "1.0"), row("g2", "D04", "", "", "нет данных"),
                row("g3", "D04", "2", "2.0")]
    pg = percentiles(with_gap, {"g1": "seo", "g2": "seo", "g3": "seo"})
    check("документ без значения перцентиль не получает",
          ("g2", "D04") in pg, False)
    check("пул без пропуска считается по двум", pg[("g3", "D04")], "0.5000")

    # Смена одного значения двигает ранги соседей — ради этого и нужен пересчёт.
    base = [row("s1", "D04", "1", "1.0"), row("s2", "D04", "2", "2.0"),
            row("s3", "D04", "3", "3.0")]
    g = {"s1": "seo", "s2": "seo", "s3": "seo"}
    before = percentiles(base, g)
    base[0]["normalized_value"] = "9.0"          # s1 уходит в конец пула
    after = percentiles(base, g)
    check("сосед по пулу сменил ранг",
          before[("s2", "D04")] != after[("s2", "D04")], True)
    check("сменивший значение получил новый ранг",
          before[("s1", "D04")] != after[("s1", "D04")], True)

    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
