#!/usr/bin/env python3
"""Одиннадцать выполнимых преобразований стресс-теста.

    python 09-tools/stress_transforms.py --self-check

Состав зафиксирован `07-analysis/analysis-closure.md` §6.2: выполняются 1–5, 10,
11, 13–16; преобразования 6–9 и 12 требуют данных, которых в корпусе нет, и
получают статус `not executable — intervention data unavailable`.

Каждое преобразование детерминировано и зависит только от текста и от сида,
записанного до прогона. Ни одно не обращается к оценкам процедур.
"""

import argparse
import random
import re
import sys
import unicodedata
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

SEED = 20260729

NOT_EXECUTABLE = {
    6: "лёгкая человеческая редактура — ветки A→H в корпусе нет",
    7: "глубокая человеческая редактура — ветки A→H в корпусе нет",
    8: "машинный рерайт — прогон модели-рерайтера не выполнялся",
    9: "перевод и обратный перевод — прогон переводчика не выполнялся",
    12: "перефразирование — прогон модели не выполнялся",
    14: "удаление типичных клише — неисполнимо после prep-v5: препроцессинг "
        "снимает оболочку источника, и зарегистрированный перечень артефактов "
        "не даёт ни одного срабатывания на панели; перечня стилевых клише в "
        "опубликованном источнике не найдено",
}

# Клише для преобразования 14 берутся из зафиксированного списка артефактного
# слоя, а не сочиняются здесь: собственный список после сбора корпуса попал бы
# под запрет на признаки, придуманные после результатов.
CLICHE_SOURCE = "06-features/artifacts-spec.md"

HOMOGLYPHS = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
              "х": "x", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
              "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X"}


def t01_dashes(text, rng):
    """Замена длинных тире на короткие и обратно — оформление, не смысл."""
    return text.replace("—", "–").replace(" - ", " – ")


def t02_markdown(text, rng):
    """Снятие markdown-выделения: жирное и курсив становятся обычным текстом."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text, flags=re.S)
    return re.sub(r"__(.+?)__", r"\1", text, flags=re.S)


def t03_headings(text, rng):
    """Удаление заголовков. Меняет структуру — просадка ожидаема."""
    kept = [line for line in text.split("\n")
            if not line.lstrip().startswith("#")]
    return "\n".join(kept)


def t04_paragraphs(text, rng):
    """Перестановка абзацев. Смысл текста в целом сохраняется."""
    parts = [p for p in text.split("\n\n") if p.strip()]
    if len(parts) < 3:
        return text
    body = parts[1:-1]
    rng.shuffle(body)
    return "\n\n".join([parts[0]] + body + [parts[-1]])


def t05_punctuation(text, rng):
    """Нормализация пунктуации: кавычки, многоточия, пробелы перед знаками."""
    text = text.replace("«", '"').replace("»", '"').replace("…", "...")
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return re.sub(r"([,.!?;:])(?=\S)", r"\1 ", text)


def t10_shorten(text, rng):
    """Сокращение: удаляется каждое четвёртое предложение, кроме первого."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) < 8:
        return text
    return " ".join(s for i, s in enumerate(sentences) if i == 0 or i % 4 != 3)


def t11_expand(text, rng):
    """Расширение: каждое четвёртое предложение дублируется соседним абзацем.

    Дублирование — не содержательное расширение, а контролируемый рост объёма
    без нового смысла. Именно это и нужно проверить: реагирует ли балл на длину.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) < 8:
        return text
    out = []
    for i, s in enumerate(sentences):
        out.append(s)
        if i % 4 == 3:
            out.append(s)
    return " ".join(out)


def t13_typos(text, rng):
    """Орфографические ошибки: перестановка соседних букв в каждом 40-м слове."""
    words = text.split(" ")
    for i, w in enumerate(words):
        if i % 40 != 7 or len(w) < 5 or not w.isalpha():
            continue
        j = rng.randrange(1, len(w) - 2)
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


def load_cliches(root):
    """Клише читаются из зафиксированного слоя артефактов, а не сочиняются."""
    path = root / CLICHE_SOURCE
    if not path.exists():
        return []
    found = re.findall(r"`([^`]{4,40})`", path.read_text(encoding="utf-8"))
    return sorted({f for f in found if " " in f and f.lower() == f})


def t14_cliches(text, rng, cliches=()):
    """Удаление типичных клише: проверка, держится ли сигнал без лексики.

    Схлопывается только горизонтальный пробел. Прежняя редакция использовала
    `\\s{2,}`, а `\\s` включает перевод строки, поэтому документ становился одной
    строкой: границы абзацев исчезали, и у шести ячеек из шестидесяти профиль
    prose оказывался пустым. Преобразование заявлено как лексическое, а на деле
    меняло и формат, причём формат сильнее. Исправлено решением PI 2026-07-30,
    амендмент amendment-stress-r4-invariant-t14.md.
    """
    for phrase in cliches:
        text = re.sub(re.escape(phrase), "", text, flags=re.I)
    return re.sub(r"[ \t]{2,}", " ", text)


def t15_homoglyphs(text, rng):
    """Подмена кириллицы латиницей в каждом 30-м подходящем символе."""
    out, seen = [], 0
    for ch in text:
        if ch in HOMOGLYPHS:
            seen += 1
            out.append(HOMOGLYPHS[ch] if seen % 30 == 0 else ch)
        else:
            out.append(ch)
    return "".join(out)


def t16_format(text, rng):
    """Перенос markdown → plain text: списки и заголовки теряют разметку."""
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        lines.append(line)
    return unicodedata.normalize("NFC", "\n".join(lines))


TRANSFORMS = {
    1: ("замена длинных тире", t01_dashes, "смысл сохраняется"),
    2: ("изменение markdown-разметки", t02_markdown, "смысл сохраняется"),
    3: ("удаление заголовков", t03_headings, "меняет структуру"),
    4: ("перестановка абзацев", t04_paragraphs, "смысл сохраняется"),
    5: ("исправление пунктуации", t05_punctuation, "смысл сохраняется"),
    10: ("сокращение текста", t10_shorten, "меняет объём"),
    11: ("расширение текста", t11_expand, "меняет объём"),
    13: ("добавление орфографических ошибок", t13_typos, "смысл сохраняется"),
    # 14 переведено в NOT_EXECUTABLE амендментом r5 от 2026-07-31. Функция
    # t14_cliches и загрузчик списка оставлены в файле: по ним воспроизводится
    # разбор дефекта, но из TRANSFORMS преобразование убрано и не считается.
    15: ("подмена символов и гомоглифы", t15_homoglyphs, "смысл сохраняется"),
    16: ("перенос между форматами", t16_format, "смысл сохраняется"),
}

# Подмножество, по которому считается индекс нестабильности: преобразования,
# сохраняющие смысл (`stress-tests.md`, раздел «Индекс нестабильности»).
MEANING_PRESERVING = (1, 2, 4, 5, 15, 16)


def apply_transform(number, text, root=None, seed=SEED):
    rng = random.Random(seed + number)
    name, func, _ = TRANSFORMS[number]
    if number == 14:
        return func(text, rng, load_cliches(root or Path(".")))
    return func(text, rng)


def self_check():
    """Проверка на синтетике: преобразование меняет текст и детерминировано.

    Оба условия блокирующие. Прежняя редакция печатала `changed`, но в счётчик
    провалов брала только `deterministic`, поэтому преобразование, не менявшее
    текст, получало отметку `ok`. Так прошёл незамеченным пустой список клише у
    t14 — см. амендмент r5 от 2026-07-31.

    Синтетика подобрана так, чтобы срабатывало каждое выполнимое преобразование:
    шесть абзацев дают t04 непустую середину для перестановки, а слово на
    позиции 7 буквенное и длиннее четырёх символов — этого требует t13.
    """
    sample = (
        "# Заголовок\n\n"
        "Первый абзац — с тире и **жирным**. Второе предложение содержит "
        "достаточно длинные слова. Третье предложение тут. Четвёртое "
        "предложение. Пятое предложение. Шестое предложение.\n\n"
        "- пункт списка\n- второй пункт\n\n"
        "Третий абзац с обычным текстом и парой «кавычек».\n\n"
        "Четвёртый абзац добавлен, чтобы перестановка абзацев меняла порядок.\n\n"
        "Пятый абзац нужен по той же причине.\n\n"
        "Последний абзац «в кавычках»."
    )
    root = Path(__file__).resolve().parent.parent
    failed = 0
    for number in sorted(TRANSFORMS):
        first = apply_transform(number, sample, root)
        second = apply_transform(number, sample, root)
        name = TRANSFORMS[number][0]
        deterministic = first == second
        changed = first != sample
        mark = "ok" if deterministic and changed else "СБОЙ"
        if not (deterministic and changed):
            failed += 1
        print(f"  [{mark}] {number:>2} {name}: изменил текст {changed}, "
              f"детерминирован {deterministic}, длина {len(sample)} → {len(first)}")
    print(f"\nневыполнимых преобразований: {len(NOT_EXECUTABLE)}")
    for number, reason in sorted(NOT_EXECUTABLE.items()):
        print(f"  {number}: {reason}")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        return self_check()
    print(f"преобразований выполнимых: {len(TRANSFORMS)}, "
          f"невыполнимых: {len(NOT_EXECUTABLE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
