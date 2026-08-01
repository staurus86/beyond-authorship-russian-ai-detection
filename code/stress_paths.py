#!/usr/bin/env python3
"""Единая точка: входы, ревизия и каталог попытки стресс-теста.

Прежде каталог панели и метка ревизии дублировались константой в каждом из
четырёх скриптов процедур. 30 июля 2026 это стоило двух часов сорока пяти минут:
при подготовке r3 и r4 константу перевели только в P1 и P2, а P3 и P4 продолжали
читать каталог прежней ревизии. Правило: имя каталога входов и метка ревизии
меняются здесь и больше нигде.

**Попытка как единица происхождения.** 31 июля выяснилось, что одной метки
ревизии мало. Прогон P3 был остановлен, код правился, прогон возобновлялся — и
первые ячейки оказались посчитаны одной версией скрипта, последующие другой.
Возобновление по хешу входа доказывает совпадение текста, но не совпадение
вычислительного кода. Манифест же обязан ссылаться на код, который фактически
создал результат.

Поэтому выходы, логи и манифесты пишутся в каталог попытки:

    07-analysis/stress-r5-attempts/<attempt_id>/

`attempt_id` — отметка UTC старта в формате `YYYYMMDDTHHMMSSZ`. Цепочка создаёт
каталог один раз и передаёт идентификатор дочерним процессам через переменную
окружения `STRESS_ATTEMPT_ID`, поэтому все шаги одной попытки пишут в одно место.
Существующий каталог не перезаписывается: повторный запуск заводит новую попытку,
а прежняя остаётся как есть вместе с логами и частичными выходами.

Какая попытка принята, решает отдельный файл `stress-r5-selected.json`; без него
ни один результат не считается финальным.

    import stress_paths as sp
    texts = sp.TEXTS
    out   = sp.analysis("p1", "scores.csv")          # каталог попытки
    fixed = sp.analysis("p4", "manifest.json", attempt=False)  # 07-analysis
"""
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Ревизия результатов. Панель при этом не пересобирается: r5 отличается от r4
# составом преобразований, а не текстами.
REVISION = "r5"

# Ревизия на процедуру. P2 идёт под меткой r10: матрица v5, досчёт пяти признаков и
# допуск последнего разряда для признаков на эмбеддингах,
# которые прежде приходили пропусками. Ревизии r6 и r7 отменены вместе с матрицей v5-r2;
# P1, P3 и P4 матрицу либо не читают, либо читают признаки, совпадающие в обеих
# версиях, поэтому остаются в r5 (`amendment-stress-r6-p2-matrix.md`). Метка
# живёт здесь, а не константой в скрипте процедуры: ровно из-за такого
# дублирования 30 июля два прогона прочитали каталог прежней ревизии.
PROCEDURE_REVISION = {"p2a": "r11"}

# Входы: панель 60 документов × выполнимые преобразования.
TEXTS = ROOT / "04-corpus" / "derived" / "stress-v3"
PANEL = ROOT / "07-analysis" / "stress-panel-v1.csv"
MANIFEST = TEXTS / "manifest.csv"

# Версия препроцессинга панели для записи в кеш. Совпадает с именем каталога
# входов и берётся из него, чтобы не разъезжаться при смене панели.
PREP_VERSION = TEXTS.name

ANALYSIS = ROOT / "07-analysis"
ATTEMPTS = ANALYSIS / f"stress-{REVISION}-attempts"
SELECTED = ANALYSIS / f"stress-{REVISION}-selected.json"

ATTEMPT_ENV = "STRESS_ATTEMPT_ID"


def new_attempt_id():
    """Идентификатор попытки из отметки UTC."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def attempt_id():
    """Идентификатор текущей попытки.

    Берётся из окружения, если цепочка его задала. Одиночный запуск скрипта
    заводит свою попытку: смешивать его выходы с чужими нельзя.
    """
    value = os.environ.get(ATTEMPT_ENV)
    if not value:
        value = new_attempt_id()
        os.environ[ATTEMPT_ENV] = value
    return value


def attempt_dir(create=False):
    """Каталог текущей попытки. Существующий каталог не перезаписывается."""
    path = ATTEMPTS / attempt_id()
    if create:
        path.mkdir(parents=True, exist_ok=True)
        (path / "logs").mkdir(exist_ok=True)
    return path


def start_attempt():
    """Заводит новый каталог попытки. Отказ, если такой уже есть."""
    path = ATTEMPTS / attempt_id()
    if path.exists():
        raise SystemExit(f"каталог попытки уже существует: {path.name}. "
                         f"Попытки не перезаписываются — заведите новую.")
    path.mkdir(parents=True)
    (path / "logs").mkdir()
    return path


def analysis(procedure, suffix, revision=None, attempt=True):
    """Путь результата процедуры.

    По умолчанию — внутри каталога попытки. `attempt=False` возвращает путь в
    `07-analysis`: так адресуются артефакты с уже замкнутой цепочкой
    происхождения, например деривация P4 r5 из завершённого прогона r4.
    """
    rev = revision or PROCEDURE_REVISION.get(procedure, REVISION)
    name = f"stress-{procedure}-{rev}-{suffix}"
    base = attempt_dir(create=True) if attempt else ANALYSIS
    return base / name


def log(step, script, revision=None):
    """Путь лога шага внутри каталога попытки.

    Имя содержит номер шага и скрипт; попытку задаёт каталог. Прежде лог
    назывался только по ревизии и шагу, поэтому повторный запуск того же шага
    затирал протокол неудачной попытки.
    """
    return attempt_dir(create=True) / "logs" / f"step{step}-{script}.log"
