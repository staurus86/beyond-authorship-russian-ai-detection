#!/usr/bin/env python3
"""Синтетический тест скана дословных повторов repeat-scan-v2.

    python 09-tools/test_repeat_scan_synth.py

Проверяет расчётные функции до прогона на корпусе: сегментацию, фильтр длины,
правило знаменателя, пять признаков кандидата и сводку по классам.

Главное, что здесь проверяется, — **правило знаменателя**. Оно допускает два
прочтения, и от выбора зависит сравнимость с историческим числом 97: предложение,
встретившееся трижды, добавляет в числитель три вхождения, а не два избыточных.
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

# Предложения длиннее 40 символов: короче фильтр отбрасывает.
S1 = "Первое достаточно длинное предложение для проверки скана."
S2 = "Второе достаточно длинное предложение для проверки скана."
S3 = "Третье достаточно длинное предложение для проверки скана."
SHORT = "Коротко."


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


# ── 1. Сегментация и фильтр длины ────────────────────────────────────────────

def test_segment(m):
    print("\nсегментация и нормализация")
    check("границы по .!? с пробелом", len(m.segment(f"{S1} {S2}")), 2)
    check("короткое предложение отбрасывается", len(m.segment(f"{S1} {SHORT}")), 1)
    check("перевод строки — тоже разделитель", len(m.segment(f"{S1}\n{S2}")), 2)
    check("пробелы по краям снимаются", m.segment(f"   {S1}   ")[0], S1)
    check("текст без длинных предложений даёт пустой список", m.segment(SHORT), [])
    check("пустой текст не роняет сегментацию", m.segment(""), [])
    check("точка без пробела границей не считается",
          len(m.segment("Первое длинное предложение теста.Второе длинное предложение.")), 1)


# ── 2. Правило знаменателя ───────────────────────────────────────────────────

def test_denominator(m):
    print("\nправило знаменателя repeat_share")

    r = m.scan_text(f"{S1} {S1} {S2}")
    approx("двойной повтор: оба вхождения в числителе", r["repeat_share"], 2 / 3)

    r = m.scan_text(f"{S1} {S1} {S1} {S2}")
    approx("тройной повтор даёт три вхождения, а не два избыточных",
           r["repeat_share"], 3 / 4)
    check("максимальная кратность", r["max_multiplicity"], 3)
    check("различных повторяющихся предложений", r["repeated_unique"], 1)

    r = m.scan_text(f"{S1} {S2} {S3}")
    approx("текст без повторов даёт ноль", r["repeat_share"], 0.0)
    check("кратность без повторов равна единице", r["max_multiplicity"], 1)

    r = m.scan_text(f"{S1} {S1} {S2} {S2}")
    approx("два разных повтора", r["repeat_share"], 1.0)
    check("считаются оба", r["repeated_unique"], 2)

    check("текст без длинных предложений возвращает None", m.scan_text(SHORT), None)


# ── 3. Признаки кандидата ────────────────────────────────────────────────────

def test_features(m):
    print("\nпризнаки кандидата")

    r = m.scan_text(f"{S1} {S1} {S1} {S2}")
    check("цепочка подряд идущих повторов", r["longest_repeated_block"], 3)
    approx("смежность: оба избыточных вхождения вплотную", r["adjacent_share"], 1.0)

    # Тот же повтор, но разнесённый: рефрен, а не каскад.
    r = m.scan_text(f"{S1} {S2} {S1} {S3}")
    check("разнесённый повтор не даёт цепочки", r["longest_repeated_block"], 1)
    approx("смежность разнесённого повтора нулевая", r["adjacent_share"], 0.0)

    r = m.scan_text(f"{S1} {S2} {S3}")
    approx("смежность без повторов нулевая, деления на ноль нет",
           r["adjacent_share"], 0.0)
    approx("доля символов в повторах нулевая", r["repeat_char_share"], 0.0)

    r = m.scan_text(f"{S1} {S1}")
    approx("весь документ в повторе", r["repeat_char_share"], 1.0)

    long_s = "Длинное предложение из многих слов, которое заметно превышает сорок символов."
    r = m.scan_text(f"{long_s} {long_s} {S2}")
    got = r["repeat_char_share"]
    want = 2 * len(long_s) / (2 * len(long_s) + len(S2))
    approx("доля символов считается по вхождениям, а не по предложениям", got, want)


# ── 4. Склейки ───────────────────────────────────────────────────────────────

def test_glued(m):
    print("\nсклейки на границе предложений")
    r = m.scan_text("Первое длинное предложение для теста.Второе длинное предложение тут.")
    check("склейка найдена", r["glued_per_1000w"] > 0, True)
    r = m.scan_text(f"{S1} {S2}")
    approx("в нормальном тексте склеек нет", r["glued_per_1000w"], 0.0)


# ── 5. Отбор кандидатов и сводка ─────────────────────────────────────────────

def row(doc, cls, share, current=1, source="src"):
    return {"document_id": doc, "origin_class": cls, "source": source, "genre": "seo",
            "in_current_registry": current, "n_sentences": 10,
            "repeat_share": share, "repeated_unique": 1, "max_multiplicity": 2,
            "longest_repeated_block": 1, "repeat_char_share": 0.1,
            "adjacent_share": 0.0, "glued_per_1000w": 0.0, "verdict": ""}


def test_candidates(m):
    print("\nотбор кандидатов и сводка по классам")
    rows = [row("d1", "H", 0.20), row("d2", "H", 0.10), row("d3", "A", 0.15),
            row("d4", "A", 0.00), row("d5", "H", 0.11, current=0)]

    check("порог строгий: ровно 0.10 кандидатом не считается",
          {r["document_id"] for r in m.candidates(rows)}, {"d1", "d3", "d5"})
    check("отбор по классу", {r["document_id"] for r in m.candidates(rows, origin="H")},
          {"d1", "d5"})
    check("отбор по действующему реестру",
          {r["document_id"] for r in m.candidates(rows, current_only=True)}, {"d1", "d3"})

    table = {t["class"]: t for t in m.class_table(rows)}
    check("человеческих документов в сводке", table["H"]["documents"], 3)
    check("человеческих кандидатов", table["H"]["candidates"], 2)
    check("машинных кандидатов", table["A"]["candidates"], 1)
    approx("доля кандидатов среди машинных", table["A"]["share"], 0.5)

    table = {t["class"]: t for t in m.class_table(rows, current_only=True)}
    check("исключённый документ выпадает из действующего состава",
          table["H"]["documents"], 2)
    check("и из его кандидатов тоже", table["H"]["candidates"], 1)

    check("класс без документов в сводку не попадает",
          [t["class"] for t in m.class_table([row("d1", "H", 0.2)])], ["H"])


# ── 6. Порог и контрольное число ─────────────────────────────────────────────

def test_control(m):
    print("\nпараметры, зафиксированные спецификацией")
    check("порог", m.THRESHOLD, 0.10)
    check("фильтр длины", m.MIN_SENT_CHARS, 40)
    check("регулярка сегментации совпадает с v1", m.SENT_SPLIT.pattern, r"(?<=[.!?])\s+")
    check("контрольное число из v1", m.CONTROL_V1_HUMAN, 97)


def main():
    print("Синтетический тест скана повторов repeat-scan-v2")
    m = load("repeat_scan_v2")
    test_segment(m)
    test_denominator(m)
    test_features(m)
    test_glued(m)
    test_candidates(m)
    test_control(m)
    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
