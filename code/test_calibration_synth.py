#!/usr/bin/env python3
"""Синтетический тест калибровки процедуры 2.

    python 09-tools/test_calibration_synth.py

Проверяет расчётные функции до прогона на корпусе. Главный предмет проверки —
**правило размещения ties**, заданное PI: группа одинаковых вероятностей целиком
идёт в один бин по середине своего рангового интервала,
`bin = min(B−1, floor(B × midpoint_rank / n))`, пустые бины исключаются.

Второй предмет — risk-coverage: при совпадении confidence на границе включается
вся группа ties, и публиковаться должно фактически достигнутое покрытие, а не
целевое.
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
        print(f"  [FAIL] {label}: получено {got!r}, ожидалось {want!r}"
              + (f"  ({detail})" if detail else ""))
    else:
        print(f"  [ok  ] {label}")


def approx(label, got, want, tol=1e-9):
    global FAILED
    ok = abs(got - want) <= tol
    if not ok:
        FAILED += 1
        print(f"  [FAIL] {label}: получено {got!r}, ожидалось {want!r}")
    else:
        print(f"  [ok  ] {label}")


def item(p, y, decision=None):
    return {"p": p, "y": y, "decision": int(p >= 0.5) if decision is None else decision,
            "origin_class": "A" if y else "H", "document_id": f"d{p}{y}", "split": "s"}


# ── 1. Brier ─────────────────────────────────────────────────────────────────

def test_brier(m):
    print("\nBrier score")
    approx("идеальный прогноз даёт ноль",
           m.brier([item(1.0, 1), item(0.0, 0)]), 0.0)
    approx("полностью неверный даёт единицу",
           m.brier([item(0.0, 1), item(1.0, 0)]), 1.0)
    approx("неуверенный прогноз даёт 0.25",
           m.brier([item(0.5, 1), item(0.5, 0)]), 0.25)
    approx("усреднение по наблюдениям",
           m.brier([item(1.0, 1), item(0.0, 1)]), 0.5)


# ── 2. Размещение ties по правилу PI ─────────────────────────────────────────

def test_bins(m):
    print("\nразмещение по бинам, §2a")

    items = [item(i / 10, 1) for i in range(10)]
    bins = m.bin_assignment(items, 10)
    check("десять различных значений — десять бинов", len(bins), 10)
    check("в каждом бине по одному", {len(v) for v in bins.values()}, {1})

    # Группа из четырёх равных значений не делится между бинами.
    items = [item(0.5, 1) for _ in range(4)] + [item(0.9, 1), item(0.1, 1)]
    bins = m.bin_assignment(items, 3)
    sizes = sorted(len(v) for v in bins.values())
    check("группа равных не делится между бинами", 4 in sizes, True, sizes)

    # Группа больше размера бина: занимает свой бин, соседние пустеют.
    items = [item(1.0, 1) for _ in range(8)] + [item(0.1, 0), item(0.2, 0)]
    bins = m.bin_assignment(items, 5)
    check("крупная группа целиком в одном бине", max(len(v) for v in bins.values()), 8)
    check("непустых бинов меньше пяти", len(bins) < 5, True, len(bins))

    # Проверка самой формулы: n=10, B=10, группа из 2 значений на рангах 0-1,
    # midpoint = 1.0 → bin = floor(10 * 1.0 / 10) = 1.
    items = [item(0.1, 1), item(0.1, 1)] + [item(0.2 + i / 100, 1) for i in range(8)]
    bins = m.bin_assignment(items, 10)
    group_bin = [b for b, v in bins.items() if len(v) == 2 and v[0]["p"] == 0.1]
    check("бин группы считается по середине рангового интервала", group_bin, [1])

    # Верхняя граница: значение максимума не должно уходить за B-1.
    items = [item(i / 5, 1) for i in range(5)]
    bins = m.bin_assignment(items, 5)
    check("индекс бина не превышает B−1", max(bins), 4)

    check("все наблюдения распределены",
          sum(len(v) for v in bins.values()), len(items))


# ── 3. ECE и MCE ─────────────────────────────────────────────────────────────

def test_ece(m):
    print("\nECE и MCE")

    # Идеальная калибровка: в каждом бине доля меток равна средней вероятности.
    items = [item(0.0, 0), item(0.0, 0), item(1.0, 1), item(1.0, 1)]
    ece, mce, nb = m.ece_mce(items, 2)
    approx("идеально откалиброванный даёт нулевой ECE", ece, 0.0)
    approx("и нулевой MCE", mce, 0.0)

    # Полное расхождение: вероятность 0, метки единицы.
    items = [item(0.0, 1) for _ in range(4)]
    ece, mce, nb = m.ece_mce(items, 2)
    approx("полное расхождение даёт ECE равный единице", ece, 1.0)
    approx("MCE тоже единица", mce, 1.0)
    check("одна группа — один непустой бин", nb, 1)

    # Взвешивание по размеру бина: большой бин с нулевым разрывом тянет ECE вниз.
    items = ([item(0.0, 0) for _ in range(8)] + [item(0.0, 1) for _ in range(2)])
    ece, mce, nb = m.ece_mce(items, 2)
    approx("ECE взвешен по n_b/n", ece, 0.2)
    approx("MCE берёт максимум по бину, а не среднее", mce, 0.2)

    # Пустые бины не входят ни в ECE, ни в счётчик.
    items = [item(0.5, 1) for _ in range(6)]
    ece, mce, nb = m.ece_mce(items, 6)
    check("пустые бины исключены из счёта", nb, 1)
    approx("ECE считается только по непустым", ece, 0.5)


# ── 4. Risk–coverage ─────────────────────────────────────────────────────────

def test_risk_coverage(m):
    print("\nrisk-coverage")

    # Уверенные верны, неуверенные ошибаются: risk падает с покрытием.
    items = [item(0.99, 1), item(0.98, 1), item(0.51, 0), item(0.52, 0)]
    rc = {round(p["target_coverage"], 2): p for p in m.risk_coverage(items)}
    approx("при полном покрытии ошибаются двое из четырёх", rc[1.0]["risk"], 0.5)
    check("покрытие 100% достигается точно", rc[1.0]["achieved_coverage"], 1.0)
    approx("при половинном покрытии ошибок нет", rc[0.5]["risk"], 0.0)

    check("confidence симметрична относительно 0.5",
          m.risk_coverage([item(0.9, 1), item(0.1, 0)])[0]["risk"], 0.0)

    # Ties на границе: включается вся группа, покрытие превышает целевое.
    items = [item(0.9, 1)] + [item(0.6, 1) for _ in range(3)]
    rc = {round(p["target_coverage"], 2): p for p in m.risk_coverage(items)}
    check("группа ties на границе включается целиком",
          rc[0.5]["n_kept"], 4, f"получено {rc[0.5]['n_kept']}")
    check("публикуется фактически достигнутое покрытие",
          rc[0.5]["achieved_coverage"] > 0.5, True)

    check("сетка целевых покрытий зафиксирована",
          m.COVERAGE_TARGETS, (1.00, 0.90, 0.80, 0.70, 0.50))


# ── 5. Свёртка к документу ───────────────────────────────────────────────────

def test_document_level(m):
    print("\nsensitivity ансамблевой свёртки")
    rows = [dict(item(0.8, 1), document_id="d1", split="a"),
            dict(item(0.6, 1), document_id="d1", split="b"),
            dict(item(0.2, 0), document_id="d2", split="a")]
    docs = {d["document_id"]: d for d in m.document_level(rows)}
    check("документов столько же, сколько уникальных id", len(docs), 2)
    approx("вероятности усредняются арифметически", docs["d1"]["p"], 0.7)
    check("метка берётся от документа", docs["d1"]["y"], 1)
    check("решение пересчитывается по усреднённой вероятности",
          docs["d1"]["decision"], 1)

    rows = [dict(item(0.9, 1), document_id="d3", split="a"),
            dict(item(0.0, 1), document_id="d3", split="b")]
    docs = {d["document_id"]: d for d in m.document_level(rows)}
    approx("усреднение может увести решение к нулю", docs["d3"]["p"], 0.45)
    check("и решение становится отрицательным", docs["d3"]["decision"], 0)


# ── 6. Константы спецификации ────────────────────────────────────────────────

def test_constants(m):
    print("\nконстанты спецификации")
    check("основное число бинов", m.BINS_MAIN, 10)
    check("sensitivity-варианты", m.BINS_SENSITIVITY, (5, 20))
    check("ожидаемое число строк", m.EXPECTED_ROWS, 12112)
    check("ожидаемое число holdout", m.EXPECTED_SPLITS, 18)
    check("модель и estimand", (m.MODEL, m.ESTIMAND), ("main", "full"))
    check("хеш источника зафиксирован", len(m.EXPECTED_SHA), 64)


def main():
    print("Синтетический тест калибровки")
    m = load("calibration_run")
    test_brier(m)
    test_bins(m)
    test_ece(m)
    test_risk_coverage(m)
    test_document_level(m)
    test_constants(m)
    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
