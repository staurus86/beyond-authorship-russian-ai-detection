#!/usr/bin/env python3
"""Описательные пересчёты по выходам замороженных прогонов, post hoc.

    python 09-tools/posthoc_descriptives.py

**Статус — post hoc descriptive.** Код написан 2026-07-29, после просмотра
результатов всех четырёх процедур. Он не трогает корпус, не пересчитывает ни
одной замороженной величины и не создаёт новых вариантов анализа: считает
описательные характеристики уже записанных выходов `judge-v1-scores.csv` и
`clf-v1-p2a-metrics.csv`. Числа нужны разделу ограничений — см.
`08-paper/main-claim-and-limitations.md` §3 и §4.

Что считается:
  1. разрешающая способность шкалы судьи: сколько различных значений медианы
     она произвела на корпусе и как они распределены по классам;
  2. из чего собран контраст судьи `P3 − P1`: распределение парных разностей,
     реконструкция пар по `канал × бриф × повтор`;
  3. диапазоны AUROC основной модели и диагностических baseline по
     двухклассовым holdout процедуры 2.

Реконструкция пар в п. 2 — проверочная, а не замена контраста: замороженная
величина лежит в `judge-v1-o1-contrasts.csv` и здесь не переписывается.
Расхождение средних между реконструкцией и отчётом печатается как контроль.
"""

import csv
import hashlib
import re
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

JUDGE_SCORES = ROOT / "07-analysis" / "judge-v1-scores.csv"
JUDGE_CONTRASTS = ROOT / "07-analysis" / "judge-v1-o1-contrasts.csv"
CLF_METRICS = ROOT / "07-analysis" / "clf-v1-p2a-metrics.csv"
OUT_REPORT = ROOT / "07-analysis" / "posthoc-descriptives-2026-07-29.md"

DOC_ID = re.compile(r"^(?P<channel>.+)_(?P<brief>b\d+)_(?P<mode>P\d)_(?P<repeat>r\d)$")
BASELINE_ORDER = ["main", "format-only", "genre-only", "source-only", "length-only"]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def judge_scale(rows):
    """Разрешающая способность шкалы: значения, их частоты, разброс по классам."""
    values = [float(r["median"]) for r in rows]
    counts = Counter(values)
    top = counts.most_common(2)
    by_class = {}
    for cls in ("A", "H"):
        v = [float(r["median"]) for r in rows if r["origin_class"] == cls]
        c = Counter(v)
        by_class[cls] = {
            "n": len(v),
            "median": st.median(v),
            "mean": st.mean(v),
            "sd": st.pstdev(v),
            "share_65": c[65.0] / len(v),
            "share_68": c[68.0] / len(v),
            "share_70_plus": sum(n for val, n in c.items() if val >= 70) / len(v),
        }
    return {
        "n": len(values),
        "distinct": len(counts),
        "counts": counts,
        "share_two_modes": sum(n for _, n in top) / len(values),
        "range_mean": st.mean(float(r["range"]) for r in rows),
        "range_median": st.median([float(r["range"]) for r in rows]),
        "by_class": by_class,
    }


def judge_pairs(rows, mode, base="P1"):
    """Парные разности судьи, пары восстановлены по канал × бриф × повтор."""
    scores = {}
    for r in rows:
        if r["origin_class"] != "A":
            continue
        m = DOC_ID.match(r["document_id"])
        if m:
            key = (m["channel"], m["brief"], m["repeat"], m["mode"])
            scores[key] = float(r["median"])
    diffs = [scores[(c, b, rp, mode)] - scores[(c, b, rp, base)]
             for (c, b, rp, x) in scores
             if x == mode and (c, b, rp, base) in scores]
    counts = Counter(diffs)
    return {
        "n_pairs": len(diffs),
        "mean": st.mean(diffs),
        "zero_share": counts[0.0] / len(diffs),
        "counts": counts,
    }


def frozen_contrast(mode):
    """Замороженная величина из отчёта процедуры 4 — для контроля реконструкции."""
    for r in read_csv(JUDGE_CONTRASTS):
        if r["estimand"] == "full" and r["contrast"] == f"{mode}-P1":
            return float(r["mean_diff"]), int(r["n_pairs"])
    return None, None


def auroc_ranges(rows):
    """Диапазоны AUROC по двухклассовым holdout: AUROC там определим."""
    out = []
    for model in BASELINE_ORDER:
        for estimand in ("full", "net"):
            v = [float(r["auroc"]) for r in rows
                 if r["model"] == model and r["estimand"] == estimand and r["auroc"]]
            if v:
                out.append((model, estimand, len(v), min(v), max(v)))
    return out


def main():
    judge_rows = [r for r in read_csv(JUDGE_SCORES) if r["status"] == "ok"]
    clf_rows = read_csv(CLF_METRICS)

    scale = judge_scale(judge_rows)
    pairs = {mode: judge_pairs(judge_rows, mode) for mode in ("P3", "P2")}
    ranges = auroc_ranges(clf_rows)

    lines = []
    add = lines.append
    add("# Описательные пересчёты по выходам прогонов, post hoc")
    add("")
    add(f"Собрано {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        "скриптом `09-tools/posthoc_descriptives.py`.")
    add("")
    add("**Статус — post hoc descriptive.** Расчёт сделан после просмотра результатов "
        "всех четырёх процедур. Замороженные величины не пересчитываются и не "
        "переписываются; здесь считаются описательные характеристики уже записанных "
        "выходов.")
    add("")
    add("| Вход | sha256 |")
    add("|---|---|")
    for path in (JUDGE_SCORES, JUDGE_CONTRASTS, CLF_METRICS):
        add(f"| `{path.relative_to(ROOT).as_posix()}` | `{sha256(path)}` |")
    add("")

    add("## 1. Разрешающая способность шкалы судьи")
    add("")
    add(f"Документов со статусом `ok`: {scale['n']}. "
        f"Различных значений медианы: **{scale['distinct']}**. "
        f"Доля документов в двух самых частых значениях: "
        f"**{scale['share_two_modes']:.1%}**.")
    add("")
    add("| Значение медианы | Документов | Доля |")
    add("|---|---|---|")
    for val, n in scale["counts"].most_common():
        add(f"| {val:g} | {n} | {n / scale['n']:.1%} |")
    add("")
    add("| Класс | N | Медиана | Среднее | SD | Доля 65 | Доля 68 | Доля 70 и выше |")
    add("|---|---|---|---|---|---|---|---|")
    for cls, s in scale["by_class"].items():
        add(f"| {cls} | {s['n']} | {s['median']:g} | {s['mean']:.2f} | {s['sd']:.2f} | "
            f"{s['share_65']:.1%} | {s['share_68']:.1%} | {s['share_70_plus']:.1%} |")
    add("")
    add(f"Средний размах трёх прогонов одного документа — {scale['range_mean']:.2f}, "
        f"медианный — {scale['range_median']:g}. Шум измерения на документ сопоставим "
        "с разбросом оценок по корпусу.")
    add("")

    add("## 2. Из чего собран контраст судьи")
    add("")
    for mode, res in pairs.items():
        frozen_mean, frozen_n = frozen_contrast(mode)
        add(f"### {mode} − P1")
        add("")
        add(f"Пар восстановлено {res['n_pairs']}, в замороженном отчёте {frozen_n}. "
            f"Средняя разность реконструкции {res['mean']:.4f}, в отчёте "
            f"{frozen_mean:.4f}.")
        add("")
        add("| Разность | Пар | Доля |")
        add("|---|---|---|")
        for val, n in res["counts"].most_common(6):
            add(f"| {val:+g} | {n} | {n / res['n_pairs']:.1%} |")
        add("")
        add(f"Разность ровно ноль у {res['zero_share']:.1%} пар.")
        add("")

    add("## 3. Диапазоны AUROC по двухклассовым holdout, процедура 2")
    add("")
    add("| Модель | Estimand | Holdout | AUROC min | AUROC max |")
    add("|---|---|---|---|---|")
    for model, estimand, n, lo, hi in ranges:
        add(f"| {model} | {estimand} | {n} | {lo:.3f} | {hi:.3f} |")
    add("")
    add("Одноклассовые holdout в диапазоны не входят: на них определим только FPR.")
    add("")

    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"записано: {OUT_REPORT.relative_to(ROOT).as_posix()}")
    print(f"судья: {scale['distinct']} значений шкалы, "
          f"{scale['share_two_modes']:.1%} в двух модах")
    for mode, res in pairs.items():
        print(f"{mode}-P1: пар {res['n_pairs']}, средняя {res['mean']:.4f}, "
              f"нулевых {res['zero_share']:.1%}")


if __name__ == "__main__":
    main()
