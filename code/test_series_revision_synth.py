#!/usr/bin/env python3
"""Синтетический тест разбора имени серии с суффиксом ревизии матрицы.

    python 09-tools/test_series_revision_synth.py

Повод — дефект 2026-07-31: суффикс `-r2` в имени серии сравнивался с именем
схемы вложенного CV целиком, поэтому `clf-v2-legacy-r2` молча ушёл на схему B
и повторил `clf-v2-valid-r2` строка в строку, а строгий режим подбора C
отключился. Второй дефект того же происхождения: серия `-r2` отсутствовала в
таблице профилей, и дочерний манифест выписывался с пустым блоком хешей кода.

Тест проверяет разбор имени и таблицу профилей, корпуса не касается.
"""
import sys
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


def main():
    print("Синтетический тест: суффикс ревизии в имени серии")
    import clf_run as clf
    import preflight_v2_run as pf

    # 1. Базовое имя серии — только схема, без ревизии матрицы.
    check("clf-v1", clf.base_series("clf-v1"), "clf-v1")
    check("clf-v2-valid", clf.base_series("clf-v2-valid"), "clf-v2-valid")
    check("clf-v2-legacy", clf.base_series("clf-v2-legacy"), "clf-v2-legacy")
    check("clf-v2-valid-r2", clf.base_series("clf-v2-valid-r2"), "clf-v2-valid")
    check("clf-v2-legacy-r2", clf.base_series("clf-v2-legacy-r2"),
          "clf-v2-legacy")

    # 2. Схема A остаётся схемой A на любой ревизии матрицы: это и был дефект.
    for series in ("clf-v2-legacy", "clf-v2-legacy-r2"):
        check(f"{series} → схема A", clf.base_series(series) == "clf-v2-legacy",
              True)
    for series in ("clf-v2-valid", "clf-v2-valid-r2"):
        check(f"{series} → строгий режим",
              clf.base_series(series) == "clf-v2-valid", True)

    # 3. Ревизия матрицы не превращает legacy в valid и наоборот.
    check("legacy-r2 не совпадает с valid",
          clf.base_series("clf-v2-legacy-r2") == clf.base_series("clf-v2-valid"),
          False)

    # 4. Незнакомый суффикс не отрезается: чинить надо список ревизий.
    check("неизвестный суффикс сохраняется",
          clf.base_series("clf-v2-valid-r9"), "clf-v2-valid-r9")

    # 5. У каждой серии r2 есть профиль процедуры с хешами кода.
    for series in ("clf-v2-valid-r2", "clf-v2-legacy-r2b", "fairness-v2-r2b",
                   "error-v2-r2b", "synthesis-v2-r2b"):
        name = pf.profile_of(series)
        profile = pf.PROCEDURE_PROFILE.get(name, {})
        check(f"{series} → профиль {name}", bool(profile.get("code")), True)

    # 6. Профиль серии r2 совпадает с профилем предшественника.
    for series in ("clf-v2-valid", "fairness-v2", "error-v2", "synthesis-v2"):
        check(f"{series} и {series}-r2 читают один профиль",
              pf.profile_of(series) == pf.profile_of(f"{series}-r2"),
              True)

    # 7. Неизвестная серия обязана ронять запись манифеста, а не давать пустой.
    try:
        pf.emit_child_manifest("clf-v2-unknown-series", inputs=[], outputs=[])
        check("неизвестная серия отклонена", False, True)
    except SystemExit as exc:
        check("неизвестная серия отклонена", "профил" in str(exc), True)

    # 10. Откатанные ревизии не запускаются: они читают матрицу, собранную
    # пересчётом по профилям prep-v4 (решение об откате 2026-08-01).
    check("список откатанных ревизий", clf.ROLLED_BACK_REVISIONS,
          ("r2", "r2b", "r3"))
    for revision in ("r2", "r2b", "r3"):
        try:
            clf.reject_rolled_back(revision, f"clf-v2-valid-{revision}")
            check(f"ревизия {revision} отклонена", False, True)
        except SystemExit as exc:
            check(f"ревизия {revision} отклонена",
                  "отменена откатом" in str(exc), True)
    for revision in ("", "r4"):
        try:
            clf.reject_rolled_back(revision, "clf-v2-valid")
            check(f"ревизия {revision or chr(8212)} проходит", True, True)
        except SystemExit:
            check(f"ревизия {revision or chr(8212)} проходит", False, True)
    try:
        clf.switch_to_v2("clf-v2-valid-r3")
        check("switch_to_v2 отклоняет откатанную серию", False, True)
    except SystemExit:
        check("switch_to_v2 отклоняет откатанную серию", True, True)
    clf.switch_to_v2("clf-v2-valid")
    check("действующая серия читает матрицу v5", clf.MATRIX.name,
          "feature-matrix-v5.csv")

    # Запрет обязан стоять во всех потребителях, а не только в процедуре 2.
    tools = Path(__file__).resolve().parent
    for name in ("fairness_run.py", "error_run.py", "synthesis_o1.py"):
        body = (tools / name).read_text(encoding="utf-8")
        check(f"{name} вызывает запрет", "reject_rolled_back" in body, True)

    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
