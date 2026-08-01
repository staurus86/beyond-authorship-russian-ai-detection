#!/usr/bin/env python3
"""Проверка расчётных функций fairness-v1 на синтетике с известным ответом.

    python 09-tools/test_fairness_synth.py

Запускается **до** прогона `fairness_run.py`. Причина — правило замороженного
прогона: отладка «запустил, посмотрел, поправил» превращает каждую правку в
решение по итогам данных. Синтетика даёт отладку, не касаясь корпуса.

Случаи взяты вырожденные: нулевая разность, предельная разность, один кластер,
пустая группа, группа во весь тест.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fairness_run as fr  # noqa: E402

REPS = 400          # для теста хватает; в прогоне BOOTSTRAP из clf_run
failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def test_boot_fpr():
    flags = [1] * 40
    clusters = [f"s{i % 4}" for i in range(40)]
    point, lo, hi = fr.boot_fpr(flags, clusters, reps=REPS)
    check("FPR=1 на сплошных срабатываниях", point == 1.0 and lo == 1.0 and hi == 1.0,
          f"{point}, [{lo}; {hi}]")

    flags = [0] * 40
    point, lo, hi = fr.boot_fpr(flags, clusters, reps=REPS)
    check("FPR=0 при отсутствии срабатываний", point == 0.0 and hi == 0.0,
          f"{point}, [{lo}; {hi}]")

    flags = [1, 0] * 20
    point, lo, hi = fr.boot_fpr(flags, clusters, reps=REPS)
    check("FPR=0.5 на равной смеси", abs(point - 0.5) < 1e-12, f"{point}")

    point, lo, hi = fr.boot_fpr([1, 0, 1], ["one"] * 3, reps=REPS)
    check("один кластер не роняет расчёт", point is not None and lo == hi == point,
          f"{point}, [{lo}; {hi}]")


def test_boot_diff_zero():
    """Одинаковые доли в обеих частях: разность 0, интервал накрывает ноль."""
    in_flags = [1, 0] * 20
    out_flags = [1, 0] * 20
    in_clusters = [f"s{i % 5}" for i in range(40)]
    out_clusters = [f"s{i % 5}" for i in range(40)]
    point, lo, hi = fr.boot_diff(in_flags, in_clusters, out_flags, out_clusters,
                                 reps=REPS)
    check("нулевая разность", abs(point) < 1e-12, f"{point}")
    check("интервал нулевой разности накрывает ноль", lo <= 0 <= hi, f"[{lo}; {hi}]")


def test_boot_diff_strong():
    """Предельная разность: группа обвиняется всегда, остальные — никогда."""
    in_flags = [1] * 30
    out_flags = [0] * 30
    in_clusters = [f"a{i % 6}" for i in range(30)]
    out_clusters = [f"b{i % 6}" for i in range(30)]
    point, lo, hi = fr.boot_diff(in_flags, in_clusters, out_flags, out_clusters,
                                 reps=REPS)
    check("предельная разность равна 1", abs(point - 1.0) < 1e-12, f"{point}")
    check("нижняя граница предельной разности выше нуля", lo > 0, f"[{lo}; {hi}]")


def test_boot_diff_degenerate():
    point, lo, hi = fr.boot_diff([], [], [1, 0], ["s1", "s2"], reps=REPS)
    check("пустая группа даёт None", point is None and lo is None and hi is None)

    point, lo, hi = fr.boot_diff([1, 0], ["s1", "s2"], [], [], reps=REPS)
    check("пустой остаток даёт None", point is None)


def test_terciles():
    rows = [{"origin_class": "H", "word_count": str(w)} for w in range(1, 91)]
    rows += [{"origin_class": "A", "word_count": "10000"}] * 10
    low, high = fr.tercile_bounds(rows)
    check("терцили считаются только по человеческой части",
          low == 31 and high == 61, f"{low} / {high}")


def test_groups_of():
    row = {"hh_subgroups": "HH-translation", "genre": "translation",
           "source_platform": "rusltc", "word_count": "500"}
    groups = fr.groups_of(row, 400, 800)
    check("hard-human даёт сводную и подгруппу",
          "hh_all" in groups and "HH-translation" in groups, str(groups))
    check("длина 500 попадает в средние при границах 400/800",
          "len_mid" in groups, str(groups))

    row = {"hh_subgroups": "", "genre": "prose", "source_platform": "taiga_proza",
           "word_count": "400"}
    groups = fr.groups_of(row, 400, 800)
    check("граница терциля включается в короткие", "len_short" in groups, str(groups))
    check("документ без hard-human не получает hh_all",
          "hh_all" not in groups, str(groups))


def test_group_rows_shape():
    """Группа во весь тест: разность не определена, строка всё равно пишется."""
    human = [(1, "s1", {"hh_subgroups": "HH-polished", "genre": "news",
                        "source_platform": "lenta", "word_count": "500"})
             for _ in range(25)]
    rows = fr.group_rows("holdout_test", "main", "full", human, 400, 800)
    by_group = {r["group"]: r for r in rows}
    check("строка по всему тесту присутствует", "весь человеческий тест" in by_group)
    check("группа, совпадающая с тестом, не даёт разности",
          by_group["hh_all"]["delta"] is None, str(by_group["hh_all"]))
    check("FPR группы посчитан", by_group["hh_all"]["fpr"] == 1.0,
          str(by_group["hh_all"]["fpr"]))
    check("малая выборка не помечена при n=25",
          by_group["hh_all"]["note"] == "", by_group["hh_all"]["note"])


def main():
    print("синтетика fairness-v1")
    for test in (test_boot_fpr, test_boot_diff_zero, test_boot_diff_strong,
                 test_boot_diff_degenerate, test_terciles, test_groups_of,
                 test_group_rows_shape):
        test()
    if failures:
        raise SystemExit(f"\nпровалено проверок: {len(failures)} — прогон запрещён")
    print("\nвсе проверки пройдены")


if __name__ == "__main__":
    main()
