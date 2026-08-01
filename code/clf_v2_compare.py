#!/usr/bin/env python3
"""Декомпозиция изменений P2a: коррекция корпуса против исправления inner CV.

    python 09-tools/clf_v2_compare.py

Три прогона отличаются ровно двумя факторами, и каждое сравнение изолирует один:

- `clf-v1 → clf-v2-legacy` — коррекция корпуса при неизменном алгоритме;
- `clf-v2-legacy → clf-v2-valid` — исправление вложенного CV при неизменном корпусе.

Выбор варианта по результату запрещён (`amendment-clf-v2-inner-cv.md` §3): обе
таблицы публикуются вместе, основным остаётся `clf-v2-valid`.

Сравниваются основная модель `main/full`, выбранный C, out-of-fold метрики и FPR
на подгруппах hard-human.
"""

import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ANALYSIS = ROOT / "07-analysis"
# Ревизия матрицы задаётся аргументом: `--revision r3` сравнивает прогоны на
# исправленной матрице. Файлы прежнего сравнения не перезаписываются.
REVISION = ""
RUNS = []
OUT = None


def set_revision(revision=""):
    """Имена прогонов и выхода для ревизии матрицы."""
    global REVISION, RUNS, OUT
    REVISION = revision
    RUNS = [("clf-v1", "clf-v1-p2a-metrics.csv"),
            ("clf-v2-legacy", f"clf-v2{revision}-legacy-p2a-metrics.csv"),
            ("clf-v2-valid", f"clf-v2{revision}-p2a-metrics.csv")]
    OUT = ANALYSIS / f"clf-v2{revision}-comparison.md"


set_revision()

METRICS = [("auroc", "AUROC"), ("mcc", "MCC"),
           ("balanced_accuracy", "Balanced acc."),
           ("tpr_at_1pct_fpr", "TPR@1%FPR"), ("fpr", "FPR"),
           ("fpr_hard_human", "FPR hard-human")]
HARD_HUMAN = ["fpr_formal_register", "fpr_edited_news", "fpr_translation"]


def load(name):
    path = ANALYSIS / name
    if not path.exists():
        raise SystemExit(f"нет прогона {name}")
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["split"]: r for r in csv.DictReader(fh)
                if r.get("model") == "main" and r.get("estimand") == "full"}


def num(row, key):
    value = row.get(key, "")
    return float(value) if value not in ("", None) else None


def delta(a, b):
    return None if a is None or b is None else b - a


def fmt(value, digits=3):
    return "—" if value is None else f"{value:.{digits}f}"


def signed(value, digits=3):
    if value is None:
        return "—"
    return f"{value:+.{digits}f}"


def compare(left_name, left, right_name, right):
    """Построчная разница по holdout плюс сводка по абсолютным сдвигам."""
    rows, summary = [], {key: [] for key, _ in METRICS}
    c_changed = []
    for split in sorted(set(left) & set(right)):
        row = {"split": split}
        for key, _ in METRICS:
            d = delta(num(left[split], key), num(right[split], key))
            row[key] = d
            if d is not None:
                summary[key].append(abs(d))
        row["c_left"] = left[split].get("C", "")
        row["c_right"] = right[split].get("C", "")
        if row["c_left"] != row["c_right"]:
            c_changed.append(split)
        rows.append(row)
    return rows, summary, c_changed


def section(title, question, left_name, left, right_name, right):
    rows, summary, c_changed = compare(left_name, left, right_name, right)
    lines = [f"## {title}", "", question, "",
             f"Holdout сопоставлено: {len(rows)}. Выбранный C сменился у "
             f"**{len(c_changed)}**"
             + (f": {', '.join('`' + s + '`' for s in c_changed)}." if c_changed
                else "."), "",
             "| Метрика | Средний сдвиг по модулю | Максимальный сдвиг | Где максимум |",
             "|---|---|---|---|"]
    for key, label in METRICS:
        values = summary[key]
        if not values:
            lines.append(f"| {label} | — | — | — |")
            continue
        worst = max(rows, key=lambda r: abs(r[key]) if r[key] is not None else -1)
        lines.append(f"| {label} | {fmt(statistics.fmean(values))} | "
                     f"{signed(worst[key])} | `{worst['split']}` |")
    lines += ["", "### По каждому holdout", "",
              "| Holdout | C | ΔAUROC | ΔMCC | ΔFPR | ΔFPR hard-human |",
              "|---|---|---|---|---|---|"]
    for row in rows:
        c = (f"{row['c_left']}" if row["c_left"] == row["c_right"]
             else f"**{row['c_left']} → {row['c_right']}**")
        lines.append(f"| `{row['split']}` | {c} | {signed(row['auroc'])} | "
                     f"{signed(row['mcc'])} | {signed(row['fpr'])} | "
                     f"{signed(row['fpr_hard_human'])} |")
    lines.append("")
    return lines, c_changed


def inner_folds_note(name, path):
    """Сколько inner fold-ов заявлено и сколько использовано — по каждому holdout."""
    file = ANALYSIS / path
    if not file.exists():
        return []
    rows = []
    with file.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("model") != "main" or r.get("estimand") != "full":
                continue
            total, used = r.get("inner_folds_total"), r.get("inner_folds_used")
            if total and used and total != used:
                rows.append((r["split"], total, used,
                             r.get("inner_folds_skipped", "")))
    return rows


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="",
                        help="ревизия матрицы в именах прогонов, например r3")
    set_revision(parser.parse_args().revision)
    runs = {name: load(path) for name, path in RUNS}
    lines = ["# P2a: два источника изменения, разведённые по прогонам", "",
             "Собрано скриптом `09-tools/clf_v2_compare.py`. Основание — "
             "`02-preregistration/amendment-clf-v2-inner-cv.md`.", "",
             "Сравнивается основная модель `main/full`. Ни один вариант не "
             "выбирается по результату: `clf-v2-valid` назначен основным до "
             "расчёта, `clf-v2-legacy` остаётся sensitivity.", ""]

    part, c_corpus = section(
        "Коррекция корпуса: clf-v1 → clf-v2-legacy",
        "Алгоритм тот же, что в замороженном прогоне, включая пропуск семи "
        "одноклассовых validation. Различие даёт только очистка корпуса: "
        "исключены 34 документа, у 68 изменился текст.",
        "clf-v1", runs["clf-v1"], "clf-v2-legacy", runs["clf-v2-legacy"])
    lines += part

    part, c_inner = section(
        "Исправление вложенного CV: clf-v2-legacy → clf-v2-valid",
        "Корпус тот же. Различие даёт только схема inner-разбиения: у семи "
        "holdout validation стал двухклассовым, и подбор C идёт по всем "
        "заявленным fold-ам.",
        "clf-v2-legacy", runs["clf-v2-legacy"], "clf-v2-valid", runs["clf-v2-valid"])
    lines += part

    skipped = inner_folds_note("clf-v2-legacy",
                               f"clf-v2{REVISION}-legacy-p2a-metrics.csv")
    lines += ["## Пропущенные inner fold-ы в схеме A", ""]
    if skipped:
        lines += ["| Holdout | Заявлено | Использовано | Номера пропущенных |",
                  "|---|---|---|---|"]
        for split, total, used, which in skipped:
            lines.append(f"| `{split}` | {total} | {used} | {which} |")
        lines += ["", "В clf-v1 это же происходило молча: ни метрики, ни манифест, "
                      "ни отчёт числа использованных fold-ов не содержали.", ""]
    else:
        lines += ["Пропусков не зафиксировано.", ""]

    lines += ["## Итог", "",
              f"Коррекция корпуса сменила выбранный C у {len(c_corpus)} holdout, "
              f"исправление inner CV — у {len(c_inner)}.", ""]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"сравнение записано: {OUT.name}")
    print(f"  C сменился: коррекция корпуса {len(c_corpus)}, inner CV {len(c_inner)}")


if __name__ == "__main__":
    main()
