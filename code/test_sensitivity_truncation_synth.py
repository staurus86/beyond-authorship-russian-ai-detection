#!/usr/bin/env python3
"""Синтетический тест преобразования sensitivity-проверки s01.

    python 09-tools/test_sensitivity_truncation_synth.py

Проверяет то, ради чего преобразование и заведено: что оно **не** повторяет
дефекты t10 и t11. Абзацные разделители остаются на месте, дословных повторов не
появляется, пустых строк не возникает, порядок предложений сохраняется.

Отдельно проверяется детерминированность: одинаковый вход обязан давать
одинаковый выход при любом числе прогонов, иначе воспроизвести расчёт нельзя.
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

S = ["Первое достаточно длинное предложение для проверки работы правила.",
     "Второе достаточно длинное предложение для проверки работы правила.",
     "Третье достаточно длинное предложение для проверки работы правила.",
     "Четвёртое достаточно длинное предложение для проверки правила.",
     "Пятое достаточно длинное предложение для проверки работы правила.",
     "Шестое достаточно длинное предложение для проверки работы правила.",
     "Седьмое достаточно длинное предложение для проверки работы правила.",
     "Восьмое достаточно длинное предложение для проверки работы правила."]


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


# ── 1. Абзацная разметка ─────────────────────────────────────────────────────

def test_paragraphs(m):
    print("\nабзацная разметка сохраняется")

    text = f"{S[0]} {S[1]} {S[2]} {S[3]}\n\n{S[4]} {S[5]} {S[6]} {S[7]}"
    out = m.truncate(text)
    check("двойные переводы строки на месте", out.count("\n\n"), text.count("\n\n"))

    text = f"# Заголовок\n\n{S[0]} {S[1]}\n\n{S[2]} {S[3]}\n\n{S[4]} {S[5]}"
    out = m.truncate(text)
    check("три абзаца остаются тремя", out.count("\n\n"), 3)
    check("заголовок не тронут", out.startswith("# Заголовок\n\n"), True, out[:40])

    text = f"- пункт списка один\n- пункт списка два\n- пункт списка три"
    out = m.truncate(text)
    check("одиночные переводы строки сохраняются", out.count("\n"), text.count("\n"))

    text = f"{S[0]}\n\n\n\n{S[1]}"
    out = m.truncate(text)
    check("пустые строки не схлопываются", out.count("\n"), text.count("\n"))

    # Ровно то, на чём сломались t10 и t11: сборка через " ".join по документу.
    text = f"{S[0]} {S[1]} {S[2]} {S[3]}\n\n{S[4]}"
    out = m.truncate(text)
    check("документ не превращается в одну строку", "\n\n" in out, True, out)


# ── 2. Дублирование не вносится ──────────────────────────────────────────────

def test_no_duplication(m):
    print("\nдословные повторы не появляются")

    text = " ".join(S)
    out = m.truncate(text)
    parts = [p for p in m.SENT_SPLIT.split(out) if p.strip()]
    check("все предложения выхода различны", len(parts), len(set(parts)))
    check("доля повторов нулевая", m.repeat_share(out), 0.0)

    # Прямая мера внесённого дублирования — число вхождений повторяющихся
    # предложений. Удаление увеличить её не может.
    text = f"{S[0]} {S[0]} {S[1]} {S[2]} {S[3]} {S[4]}"
    before, after = m.repeat_count(text), m.repeat_count(m.truncate(text))
    check("число повторяющихся вхождений не растёт", after <= before, True,
          f"было {before}, стало {after}")

    # А доля вырасти может, и это свойство знаменателя, а не преобразования:
    # уходит неповторяющееся предложение, повторы остаются. Проверка
    # зафиксирована, чтобы эффект не приняли за внесённое дублирование.
    share_before, share_after = m.repeat_share(text), m.repeat_share(m.truncate(text))
    check("доля повторов растёт от сокращения знаменателя",
          share_after > share_before, True,
          f"было {share_before:.3f}, стало {share_after:.3f}")
    check("при этом абсолютное число повторов то же", after, before)


# ── 3. Порядок и состав ──────────────────────────────────────────────────────

def test_order(m):
    print("\nпорядок и состав предложений")

    text = " ".join(S)
    out = m.truncate(text)
    kept = [p.strip() for p in m.SENT_SPLIT.split(out) if p.strip()]
    check("порядок оставшихся сохранён",
          kept, [s for s in S if s in kept])
    check("удаляется каждое четвёртое", len(kept), 6, f"осталось {kept}")
    check("четвёртое предложение удалено", S[3] in kept, False)
    check("восьмое предложение удалено", S[7] in kept, False)
    check("первое предложение остаётся", S[0] in kept, True)

    check("ни одно предложение не продублировано",
          len(kept), len(set(kept)))


# ── 4. Строка не остаётся пустой ─────────────────────────────────────────────

def test_never_empty(m):
    print("\nпустых строк не возникает")

    # Строка из одного предложения, попавшего под удаление по сквозному счёту.
    text = f"{S[0]} {S[1]} {S[2]}\n{S[3]}\n{S[4]}"
    out = m.truncate(text)
    lines = out.split("\n")
    check("число строк не изменилось", len(lines), 3)
    check("ни одна строка не пуста", all(l.strip() for l in lines), True, lines)

    text = f"{S[3]}"
    out = m.truncate(text)
    check("документ из одного предложения не опустошается", out.strip() != "", True)


# ── 5. Детерминированность и сквозной счёт ───────────────────────────────────

def test_determinism(m):
    print("\nдетерминированность")
    text = f"{S[0]} {S[1]} {S[2]} {S[3]}\n\n{S[4]} {S[5]} {S[6]} {S[7]}"
    runs = {m.truncate(text) for _ in range(5)}
    check("пять прогонов дают один результат", len(runs), 1)

    check("правило отбора зафиксировано", (m.DROP_EVERY, m.DROP_OFFSET), (4, 3))
    check("целевой диапазон объёма зафиксирован", m.VOLUME_RANGE, (0.70, 0.80))

    # Счёт сквозной по документу, а не внутри строки: иначе в коротких строках
    # никогда бы ничего не удалялось и сокращение не достигло бы цели.
    text = f"{S[0]} {S[1]}\n{S[2]} {S[3]}\n{S[4]} {S[5]}"
    out = m.truncate(text)
    kept = [p.strip() for p in m.SENT_SPLIT.split(out.replace("\n", " ")) if p.strip()]
    check("сквозной счёт удалил предложение из второй строки", S[3] in kept, False)


# ── 6. Объём ─────────────────────────────────────────────────────────────────

def test_volume(m):
    print("\nобъём")
    text = " ".join(S * 4)          # 32 предложения одинаковой длины
    out = m.truncate(text)
    ratio = len(out.split()) / len(text.split())
    check("сокращение около четверти", 0.70 <= ratio <= 0.80, True, f"ratio {ratio:.3f}")


def main():
    print("Синтетический тест преобразования s01")
    m = load("sensitivity_truncation")
    test_paragraphs(m)
    test_no_duplication(m)
    test_order(m)
    test_never_empty(m)
    test_determinism(m)
    test_volume(m)
    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
