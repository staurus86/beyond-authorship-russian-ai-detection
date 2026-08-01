#!/usr/bin/env python3
"""Синтетический тест отбора нестабильных случаев instability-v1.

    python 09-tools/test_instability_selection_synth.py

Проверяет расчётные функции `select_instability_cases.py` до прогона на данных
прогона: правило сортировки §2, разрешение ничьих, проверку наполненности
класса, направление перехода, топ сдвинутых признаков и границы терцилей.

Тест подаёт тот же тип данных, что читает продакшн, — строки из CSV, где все
поля строковые. Прежняя редакция теста шлюзов сравнивала флаги только со
строкой, и на живом прогоне шлюз молча пропускался; здесь та же ошибка стоила бы
неверного порядка сортировки при числовом сравнении строк.
"""

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FAILED = 0


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(label, got, want, detail=""):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    mark = "ok  " if ok else "FAIL"
    suffix = f"  ({detail})" if detail and not ok else ""
    print(f"  [{mark}] {label}: получено {got!r}, ожидалось {want!r}{suffix}"
          if not ok else f"  [{mark}] {label}")


def cell(doc, number, flip, inst=0.5, mx=0.5, mean=0.1, models=4,
         origin="A", genre="seo"):
    """Строка ячейки в том виде, в каком её отдаёт csv.DictReader — строками."""
    return {"document_id": doc, "transform_number": str(number),
            "origin_class": origin, "genre": genre, "n_models": str(models),
            "mean_delta_prob": f"{mean:.10f}", "instability_rate": f"{flip and inst or inst:.6f}",
            "flip_rate": f"{flip:.6f}", "max_abs_delta": f"{mx:.10f}"}


def score(split, doc, number, before, after, status="ok"):
    return {"split_name": split, "document_id": doc, "transform_number": str(number),
            "prob_baseline": f"{before:.10f}", "prob_transformed": f"{after:.10f}",
            "status": status}


# ── 1. Правило сортировки §2 ─────────────────────────────────────────────────

def test_sort_rule(m):
    print("\n§2 правило сортировки")

    rows = [cell("doc_b", 1, 0.25, mx=0.9),
            cell("doc_a", 1, 1.00, mx=0.3),
            cell("doc_c", 1, 0.50, mx=0.8)]
    chosen, boundary = m.select(rows, n=3)
    check("первый ключ — flip_rate по убыванию",
          [c["document_id"] for c in chosen], ["doc_a", "doc_c", "doc_b"])
    check("граничное значение — flip последней ячейки", boundary, 0.25)

    rows = [cell("doc_a", 1, 0.75, mx=0.10),
            cell("doc_b", 1, 0.75, mx=0.90),
            cell("doc_c", 1, 0.75, mx=0.50)]
    chosen, _ = m.select(rows, n=3)
    check("второй ключ — max_abs_delta по убыванию при равном flip",
          [c["document_id"] for c in chosen], ["doc_b", "doc_c", "doc_a"])

    rows = [cell("doc_z", 1, 0.5, mx=0.5), cell("doc_a", 1, 0.5, mx=0.5)]
    chosen, _ = m.select(rows, n=2)
    check("третий ключ — document_id по возрастанию",
          [c["document_id"] for c in chosen], ["doc_a", "doc_z"])

    rows = [cell("doc_a", 11, 0.5, mx=0.5), cell("doc_a", 2, 0.5, mx=0.5)]
    chosen, _ = m.select(rows, n=2)
    check("четвёртый ключ — transform_number по возрастанию, числом а не строкой",
          [int(c["transform_number"]) for c in chosen], [2, 11])

    # Ничья на границе: пять кандидатов с одинаковым flip при трёх местах.
    rows = [cell(f"doc_{i}", 1, 0.75, mx=0.5 - i / 100) for i in range(5)]
    chosen, boundary = m.select(rows, n=3)
    check("ничья на границе разрешается тай-брейком, набор полный", len(chosen), 3)
    check("на границе остаются сильнейшие по max|Δ|",
          [c["document_id"] for c in chosen], ["doc_0", "doc_1", "doc_2"])

    # Сортировка строк как чисел: "0.9" > "0.75" лексикографически ложно.
    rows = [cell("doc_a", 1, 0.9, mx=0.5), cell("doc_b", 1, 0.75, mx=0.5)]
    chosen, _ = m.select(rows, n=2)
    check("flip сравнивается числом, а не строкой",
          [c["document_id"] for c in chosen], ["doc_a", "doc_b"])


# ── 2. Проверка наполненности класса ─────────────────────────────────────────

def test_class_check(m):
    print("\n§2 проверка: все карточки со сменой решения")
    rows = [cell("doc_a", 1, 0.5), cell("doc_b", 1, 0.0)]
    chosen, _ = m.select(rows, n=2)
    zero = [c for c in chosen if float(c["flip_rate"]) <= 0]
    check("ячейка без смены решения опознаётся", len(zero), 1)

    rows = [cell(f"doc_{i}", 1, 0.5) for i in range(3)]
    chosen, _ = m.select(rows, n=3)
    zero = [c for c in chosen if float(c["flip_rate"]) <= 0]
    check("набор целиком со сменой решения проходит", len(zero), 0)


# ── 3. Сторона перехода ──────────────────────────────────────────────────────

def test_flip_direction(m):
    print("\nсторона перехода и список holdout")

    rows = [score("holdout_a", "d", 1, 0.30, 0.70)]
    flipped, direction = m.flips_of_cell(rows)
    check("переход человек→машина", direction, "H→A")
    check("запись перехода с вероятностями", flipped, "holdout_a: 0.300→0.700")

    rows = [score("holdout_a", "d", 1, 0.80, 0.20)]
    _, direction = m.flips_of_cell(rows)
    check("переход машина→человек", direction, "A→H")

    rows = [score("holdout_a", "d", 1, 0.60, 0.70)]
    flipped, direction = m.flips_of_cell(rows)
    check("сдвиг без пересечения порога не считается сменой", flipped, "")
    check("направления у такой ячейки нет", direction, "")

    rows = [score("holdout_b", "d", 1, 0.20, 0.80),
            score("holdout_a", "d", 1, 0.90, 0.10)]
    flipped, direction = m.flips_of_cell(rows)
    check("holdout перечисляются по алфавиту",
          flipped.startswith("holdout_a"), True, flipped)
    check("обе стороны перехода в одной ячейке", direction, "A→H; H→A")

    # Ровно на пороге: 0.5 не больше 0.5, значит сторона «человек».
    rows = [score("holdout_a", "d", 1, 0.50, 0.51)]
    _, direction = m.flips_of_cell(rows)
    check("значение ровно 0.5 относится к человеческой стороне", direction, "H→A")


# ── 4. Сдвиг признаков ───────────────────────────────────────────────────────

def test_shifted_features(m):
    print("\nтоп сдвинутых признаков")
    stats = {"L01": (100.0, 10.0), "L02": (0.5, 0.1), "S01": (15.0, 5.0),
             "F04": (0.0, 0.0)}

    base = {"L01": 100.0, "L02": 0.5, "S01": 15.0}
    transformed = {"L01": 120.0, "L02": 0.55, "S01": 10.0}
    out = m.shifted_features(base, transformed, stats, top=3)
    check("порядок по убыванию |Δz|",
          [part.split()[0] for part in out.split("; ")], ["L01", "S01", "L02"])
    check("знак сдвига сохраняется", "S01 Δz=-1.00" in out, True, out)

    out = m.shifted_features(base, transformed, stats, top=2)
    check("отсечка top работает", len(out.split("; ")), 2)

    base2 = {"L01": 100.0, "F04": 0.0}
    transformed2 = {"L01": 110.0, "F04": 5.0}
    out = m.shifted_features(base2, transformed2, stats, top=5)
    check("признак с нулевым разбросом по корпусу пропускается",
          "F04" in out, False, out)

    out = m.shifted_features({"L01": 100.0}, {"S01": 10.0}, stats, top=5)
    check("признак без пары значений пропускается", out, "")

    out = m.shifted_features({"L01": 100.0}, {"L01": 100.0}, stats, top=5)
    check("нулевой сдвиг в список не идёт", out, "")


# ── 5. Терцили длины ─────────────────────────────────────────────────────────

def test_tertiles(m):
    print("\nтерцили длины")
    panel = [{"word_count": str(w)} for w in range(1, 91)]
    low, high = m.tertiles(panel)
    check("нижняя граница — треть выборки", low, 31)
    check("верхняя граница — две трети", high, 61)
    check("короткий документ", m.length_note(10, low, high), "короткие")
    check("средний документ", m.length_note(45, low, high), "средние")
    check("длинный документ", m.length_note(90, low, high), "длинные")
    check("значение ровно на границе идёт в нижний терциль",
          m.length_note(low, low, high), "короткие")

    panel = [{"word_count": ""} for _ in range(3)]
    low, high = m.tertiles(panel)
    check("пустая длина читается нулём без падения", (low, high), (0, 0))


# ── 6. Состав набора при частичных данных ────────────────────────────────────

def test_selection_size(m):
    print("\nразмер набора")
    rows = [cell(f"doc_{i}", 1, 1.0 - i / 100) for i in range(10)]
    chosen, _ = m.select(rows, n=30)
    check("кандидатов меньше квоты — берутся все", len(chosen), 10)

    chosen, boundary = m.select([], n=30)
    check("пустой вход не роняет отбор", (len(chosen), boundary), (0, None))


def main():
    print("Синтетический тест отбора instability-v1")
    m = load("select_instability_cases")
    test_sort_rule(m)
    test_class_check(m)
    test_flip_direction(m)
    test_shifted_features(m)
    test_tertiles(m)
    test_selection_size(m)
    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
