#!/usr/bin/env python3
"""Проверка идентифицируемости моделей M1–M3 до подгонки.

    python 09-tools/mixed_identifiability.py

Порядок задан `07-analysis/analysis-closure.md` §6.4: модель подгоняется только
после проверки. Проверка смотрит на матрицу плана и состав групп, а не на
зависимую переменную, поэтому идёт до расчёта и результат не подглядывает.

Что считается:

- ранг матрицы фиксированных эффектов против числа столбцов;
- имена столбцов, не добавляющих ранга, — то есть выраженных через предыдущие;
- число кластеров на каждый случайный эффект и число наблюдений в самом мелком;
- таблицы совместимости факторов, про которые дизайн уже знает: `genre` против
  `regulation_level` в M2 и `genre` против `origin` в M3.

Спецификация моделей — `02-preregistration/preregistration.md` §11.
"""

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
OUT_REPORT = ROOT / "07-analysis" / "mixed-identifiability.md"
OUT_JSON = ROOT / "07-analysis" / "mixed-identifiability.json"
LEVEL = re.compile(r"regulation_level=(\d)")


def read_registry():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def regulation_level(row):
    m = LEVEL.search(row.get("notes") or "")
    return m.group(1) if m else ""


def spline_terms(values):
    """Ограниченный кубический сплайн на трёх узлах: линейный терм и один базис.

    Узлы — 10-й, 50-й и 90-й перцентили log(word_count). Узлы задаются составом
    выборки, а не результатом, поэтому их выбор проверку не портит.
    """
    x = np.asarray(values, dtype=float)
    k = np.percentile(x, [10, 50, 90])
    denom = (k[2] - k[0]) ** 2
    if denom == 0:
        return {"log_length": x}
    cube = lambda t: np.where(t > 0, t ** 3, 0.0)  # noqa: E731
    basis = (cube(x - k[0]) - cube(x - k[1]) * (k[2] - k[0]) / (k[2] - k[1])
             + cube(x - k[2]) * (k[1] - k[0]) / (k[2] - k[1])) / denom
    return {"log_length": x, "log_length_rcs1": basis}


def dummies(name, values):
    """Индикаторы уровней без базового: базовый уровень уходит в свободный член."""
    levels = sorted({v for v in values if v != ""})
    out = {}
    for level in levels[1:]:
        out[f"{name}={level}"] = np.array([1.0 if v == level else 0.0 for v in values])
    return out


def interaction(left, right):
    out = {}
    for ln, lv in left.items():
        for rn, rv in right.items():
            out[f"{ln} × {rn}"] = lv * rv
    return out


def aliased(columns):
    """Столбцы, не добавляющие ранга к уже набранным."""
    names = list(columns)
    base = np.ones((len(next(iter(columns.values()))), 1))
    taken, redundant = base, []
    rank = np.linalg.matrix_rank(taken)
    for name in names:
        candidate = np.hstack([taken, columns[name].reshape(-1, 1)])
        new_rank = np.linalg.matrix_rank(candidate)
        if new_rank == rank:
            redundant.append(name)
        else:
            taken, rank = candidate, new_rank
    # Кандидатов считаем вместе со свободным членом, иначе число столбцов
    # окажется числом принятых, и таблица станет нечитаемой.
    return rank, len(names) + 1, redundant


def cluster_stats(rows, key):
    counts = Counter(key(r) for r in rows)
    counts.pop("", None)
    sizes = sorted(counts.values())
    return {"clusters": len(counts), "min_size": sizes[0] if sizes else 0,
            "median_size": sizes[len(sizes) // 2] if sizes else 0,
            "singletons": sum(1 for s in sizes if s == 1)}


def crosstab(rows, left, right):
    table = defaultdict(Counter)
    for r in rows:
        table[left(r)][right(r)] += 1
    pure = [l for l, c in table.items() if len([k for k in c if k != ""]) == 1]
    return {"rows": len(table), "single_valued": sorted(pure)}


def build_m1(rows):
    machine = [r for r in rows if r["origin_class"] == "A"]
    length = np.log([max(int(r["word_count"] or 1), 1) for r in machine])
    prompt = dummies("prompt", [r["prompt_condition"] for r in machine])
    genre = dummies("genre", [r["genre"] for r in machine])
    channel = dummies("channel", [r["generation_channel"] for r in machine])
    wrapper = dummies("wrapper", [r["wrapper_version"] for r in machine])
    cols = {}
    cols.update(prompt); cols.update(genre); cols.update(channel); cols.update(wrapper)
    cols.update(spline_terms(length))
    cols.update(interaction(prompt, genre))
    cols.update(interaction(prompt, channel))
    return machine, cols, {
        "задание (brief_id)": cluster_stats(machine, lambda r: r["brief_id"]),
        "повтор (repeat_index)": cluster_stats(machine, lambda r: r["repeat_index"]),
    }, {}


def build_m2(rows):
    human = [r for r in rows if r["origin_class"] == "H"]
    length = np.log([max(int(r["word_count"] or 1), 1) for r in human])
    level = dummies("level", [regulation_level(r) for r in human])
    genre = dummies("genre", [r["genre"] for r in human])
    cols = {}
    cols.update(level); cols.update(genre)
    cols.update(spline_terms(length))
    cols.update(interaction(level, genre))
    return human, cols, {
        "источник": cluster_stats(human, lambda r: r["split_group_source"]),
        "автор": cluster_stats(human, lambda r: r["split_group_author"]),
        "задание (brief_id)": cluster_stats(human, lambda r: r["brief_id"]),
    }, {"genre → regulation_level": crosstab(human, lambda r: r["genre"],
                                             regulation_level)}


def build_m3(rows):
    length = np.log([max(int(r["word_count"] or 1), 1) for r in rows])
    origin = dummies("origin", [r["origin_class"] for r in rows])
    genre = dummies("genre", [r["genre"] for r in rows])
    cols = {}
    cols.update(origin); cols.update(genre)
    cols.update(spline_terms(length))
    cols.update(interaction(origin, {"log_length": np.asarray(length)}))
    return rows, cols, {
        "задание (brief_id)": cluster_stats(rows, lambda r: r["brief_id"]),
        "источник": cluster_stats(rows, lambda r: r["split_group_source"]),
        "автор": cluster_stats(rows, lambda r: r["split_group_author"]),
        "канал": cluster_stats(rows, lambda r: r["generation_channel"]),
    }, {"genre → origin": crosstab(rows, lambda r: r["genre"],
                                   lambda r: r["origin_class"])}


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = read_registry()
    print(f"проверка идентифицируемости M1–M3, {stamp}")
    print(f"  документов в реестре {len(rows)}")

    results = []
    for name, builder, purpose in (
            ("M1", build_m1, "машинный корпус, даёт O1"),
            ("M2", build_m2, "человеческий корпус, даёт O2"),
            ("M3", build_m3, "объединённый корпус, даёт O3")):
        data, cols, clusters, tables = builder(rows)
        rank, total, redundant = aliased(cols)
        verdict = "идентифицируема" if not redundant else "не идентифицируема"
        results.append({"model": name, "purpose": purpose, "n": len(data),
                        "columns_candidate": total, "rank": rank, "aliased": redundant,
                        "clusters": clusters, "crosstabs": tables,
                        "verdict": verdict})
        print(f"  {name}: наблюдений {len(data)}, столбцов-кандидатов {total}, "
              f"ранг {rank}, "
              f"вырожденных {len(redundant)} — {verdict}")

    lines = ["# Идентифицируемость моделей M1–M3", "",
             f"Собрано {stamp} скриптом `09-tools/mixed_identifiability.py`. "
             "Проверка идёт до подгонки по правилу `analysis-closure.md` §6.4 и "
             "смотрит на матрицу плана, а не на зависимую переменную.", "",
             "| Модель | Назначение | Наблюдений | Столбцов-кандидатов | Ранг | "
             "Вырожденных | Вердикт |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['model']} | {r['purpose']} | {r['n']} | "
                     f"{r['columns_candidate']} | "
                     f"{r['rank']} | {len(r['aliased'])} | **{r['verdict']}** |")
    for r in results:
        lines += ["", f"## {r['model']}", ""]
        if r["aliased"]:
            lines += ["Столбцы, выраженные через предыдущие:", ""]
            lines += [f"- `{name}`;" for name in r["aliased"][:30]]
            if len(r["aliased"]) > 30:
                lines.append(f"- …и ещё {len(r['aliased']) - 30};")
            lines.append("")
        lines += ["| Случайный эффект | Кластеров | Минимальный | Медианный | Одиночек |",
                  "|---|---|---|---|---|"]
        for key, stat in r["clusters"].items():
            lines.append(f"| {key} | {stat['clusters']} | {stat['min_size']} | "
                         f"{stat['median_size']} | {stat['singletons']} |")
        for key, table in r["crosstabs"].items():
            lines += ["", f"Совместимость `{key}`: уровней {table['rows']}, "
                          f"из них с единственным значением второго фактора "
                          f"{len(table['single_valued'])}"
                          + (f" — {', '.join('`' + v + '`' for v in table['single_valued'])}"
                             if table["single_valued"] else "") + "."]
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"created_at": stamp, "models": results},
                                   ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
