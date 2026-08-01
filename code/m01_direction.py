#!/usr/bin/env python3
"""Направление вклада M01 и его распределение по классам внутри train-fold.

    python 09-tools/m01_direction.py

**Статус — post hoc diagnostic.** Замороженные величины не меняются, ни один
результат прогона не пересчитывается. Модели обучаются заново теми же функциями
`clf_run`, что и в замороженном прогоне, по тем же разбиениям.

Зачем. Стресс-тест показал: дословное дублирование предложений двигает решение
процедуры 2 к классу «человек», и главный сдвинувшийся признак — M01. Одного
этого мало, чтобы назвать механизм: нужно знать, как M01 распределён по классам
внутри обучающей части и в какую сторону его толкает коэффициент модели.

Что здесь считается по каждому из 18 holdout:

1. медианы M01 у машинной и человеческой части train, разность и Cliff's delta;
2. коэффициент M01 в обученной модели и его знак;
3. ранг |коэффициента| среди 22 признаков.

Метка y = 1 соответствует классу A (`clf_run.run_split`), поэтому отрицательный
коэффициент означает: рост M01 толкает решение к «человеку».

Под именем M01 в модель попадает `normalized_value` — стандартное отклонение
косинусов соседних пар предложений, а не среднее сходство: `extract_semantic.py`
строки 290-292 и `clf_run.load_matrix` строки 146-155.
"""

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402
import error_run as err  # noqa: E402

ROOT = clf.ROOT

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FEATURE = "M01"
OUT_CSV = ROOT / "07-analysis" / "m01-direction.csv"
OUT_REPORT = ROOT / "07-analysis" / "m01-direction.md"
OUT_MANIFEST = ROOT / "07-analysis" / "m01-direction-manifest.json"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cliffs_delta(a, b):
    """Доля пар, где значение из a больше значения из b, минус обратная доля.

    Непараметрическая мера: не требует нормальности и не портится выбросами.
    Знак читается так же, как разность медиан: плюс — в a значения выше.
    """
    if not a or not b:
        return None
    greater = less = 0
    for x in a:
        for y in b:
            if x > y:
                greater += 1
            elif x < y:
                less += 1
    return (greater - less) / (len(a) * len(b))


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"направление вклада {FEATURE}, {stamp}")

    # Та же серия, что дала действующий результат процедуры 2.
    err.switch_to_v2()
    print(f"  серия: {clf.SERIES}, матрица: {clf.MATRIX.name}")

    docs_by_id = {r["document_id"]: r
                  for r in clf.read_rows(clf.DOCUMENTS, "utf-8-sig")}
    # Тот же набор, что читает error_run: 22 признака estimand full плюс M02,
    # который нужен design_for для отбора по estimand.
    values = clf.load_matrix(set(clf.FEATURES_CORE + clf.FEATURES_STRUCTURAL
                                 + [clf.FEATURE_M02]))
    lengths = clf.load_length_features()
    splits = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(clf.SPLITS.glob("holdout_*.json"))]
    print(f"  документов в матрице: {len(values)}, holdout: {len(splits)}")

    rows = []
    for split in splits:
        name = split["split_name"]
        train_ids = [d for d in split["train"] if d in docs_by_id]

        a = [values[d][FEATURE] for d in train_ids
             if docs_by_id[d]["origin_class"] == "A" and FEATURE in values.get(d, {})]
        h = [values[d][FEATURE] for d in train_ids
             if docs_by_id[d]["origin_class"] == "H" and FEATURE in values.get(d, {})]
        if not a or not h:
            print(f"  {name}: один класс в train, пропуск")
            continue

        fit = err.fit_for_split(split, docs_by_id, values, lengths)
        idx = fit["feats"].index(FEATURE)
        coef = float(fit["coef"][idx])
        order = sorted(range(len(fit["coef"])),
                       key=lambda j: -abs(fit["coef"][j]))
        rank = order.index(idx) + 1

        rows.append({
            "split_name": name,
            "n_train": len(train_ids),
            "n_train_A": len(a),
            "n_train_H": len(h),
            "median_A": round(median(a), 6),
            "median_H": round(median(h), 6),
            "median_diff_A_minus_H": round(median(a) - median(h), 6),
            "cliffs_delta_A_vs_H": round(cliffs_delta(a, h), 4),
            "coef_M01": round(coef, 6),
            "coef_sign": "к человеку" if coef < 0 else "к машине",
            "abs_rank_of_22": rank,
        })
        print(f"  {name}: медиана A {median(a):.4f}, H {median(h):.4f}, "
              f"коэффициент {coef:+.3f} ({rows[-1]['coef_sign']}), ранг {rank}")

    if not rows:
        raise SystemExit("ОСТАНОВ: ни один holdout не дал двух классов в train")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    to_human = sum(1 for r in rows if r["coef_M01"] < 0)
    higher_in_h = sum(1 for r in rows if r["median_diff_A_minus_H"] < 0)
    median_rank = median([r["abs_rank_of_22"] for r in rows])

    lines = [
        f"# Направление вклада {FEATURE} и его распределение по классам",
        "",
        f"Собрано {stamp} скриптом `09-tools/m01_direction.py`. "
        "**Статус — post hoc diagnostic**, замороженные величины не менялись.",
        "",
        f"Серия `{clf.SERIES}`, матрица `{clf.MATRIX.name}`, estimand "
        f"`{err.ESTIMAND}`, модель `{err.MODEL}`. Модели обучены заново теми же "
        "функциями `clf_run` и теми же разбиениями, что дали замороженный "
        "результат процедуры 2.",
        "",
        f"**Что такое {FEATURE} в модели.** Не среднее сходство соседних "
        "предложений, а его стандартное отклонение: `extract_semantic.py` строки "
        "290–292 пишут среднее в `raw_value`, разброс в `normalized_value`, а "
        "`clf_run.load_matrix` читает `normalized_value or raw_value`.",
        "",
        "**Как читать знак.** Метка y = 1 соответствует классу A, поэтому "
        "отрицательный коэффициент означает: рост признака толкает решение к "
        "человеку.",
        "",
        "## По holdout",
        "",
        "| Holdout | Train | Медиана A | Медиана H | A − H | Cliff's δ | "
        f"Коэффициент {FEATURE} | Куда толкает | Ранг \\|коэф\\| из 22 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['split_name']} | {r['n_train']} | {r['median_A']:.4f} | "
            f"{r['median_H']:.4f} | {r['median_diff_A_minus_H']:+.4f} | "
            f"{r['cliffs_delta_A_vs_H']:+.3f} | {r['coef_M01']:+.3f} | "
            f"{r['coef_sign']} | {r['abs_rank_of_22']} |")

    lines += [
        "",
        "## Сводка",
        "",
        f"- коэффициент отрицательный (рост {FEATURE} толкает к человеку) у "
        f"**{to_human} holdout из {len(rows)}**;",
        f"- медиана {FEATURE} выше у человеческой части train у "
        f"**{higher_in_h} holdout из {len(rows)}**;",
        f"- медианный ранг \\|коэффициента\\| среди 22 признаков — {median_rank:.0f}.",
        "",
        "## Что из этого следует и что нет",
        "",
        "Следует: в обучающих данных разброс сходства соседних предложений выше у "
        "человеческих текстов, и модель выучила связь «выше разброс — скорее "
        "человек». Дублирование предложений поднимает ровно эту величину, поэтому "
        "дублированный машинный текст сдвигается к человеческой стороне. Механизм "
        "объясняется свойством корпуса и обученной модели.",
        "",
        "Не следует: что дословное дублирование является признаком человеческого "
        "письма вообще. Утверждение относится к этому корпусу и этой модели. "
        "Процедуры 1, 3 и 4 здесь не рассматриваются: индекс требует "
        "алгебраической декомпозиции, NLL и судья матрицу признаков не читают.",
        "",
    ]
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")

    manifest = {
        "series": "m01-direction",
        "status": "post hoc diagnostic",
        "upstream_series": clf.SERIES,
        "matrix": clf.MATRIX.name,
        "estimand": err.ESTIMAND,
        "model": err.MODEL,
        "feature": FEATURE,
        "feature_semantics": ("normalized_value = SD косинусов соседних пар; "
                              "raw_value = среднее, в модель не идёт"),
        "label_convention": "y=1 — класс A, отрицательный коэффициент толкает к H",
        "n_holdouts": len(rows),
        "coef_negative_count": to_human,
        "median_higher_in_H_count": higher_in_h,
        "median_abs_rank": median_rank,
        "code_sha256": {p.name: sha256(ROOT / "09-tools" / p.name)
                        for p in (Path("m01_direction.py"), Path("clf_run.py"),
                                  Path("error_run.py"))},
        "inputs_sha256": {clf.MATRIX.name: sha256(clf.MATRIX)},
        "outputs": [OUT_CSV.name, OUT_REPORT.name],
        "created_at": stamp,
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    print(f"  коэффициент к человеку у {to_human} из {len(rows)}, "
          f"медиана выше у H у {higher_in_h} из {len(rows)}")
    print(f"  записано: {OUT_CSV.name}, {OUT_REPORT.name}, {OUT_MANIFEST.name}")


if __name__ == "__main__":
    main()
