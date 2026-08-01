#!/usr/bin/env python3
"""Препроцессинг корпуса: производные версии текста в двух профилях.

Реализует 06-features/preprocessing-spec.md, версия prep-v4.

Запуск из корня папки исследования:
    python 09-tools/prep.py                    # собрать производные версии
    python 09-tools/prep.py --limit 20         # первые 20 документов, для проверки
    python 09-tools/prep.py --only <doc_id>    # один документ

Сырые файлы не редактируются никогда. Всё, что снимается — обёртка канала,
строки счётчиков, разметка, — снимается здесь и записывается счётчиком в
манифест, а не теряется.

Выход:
    04-corpus/derived/prep-v4/prose/<document_id>.txt
    04-corpus/derived/prep-v4/full/<document_id>.txt
    04-corpus/derived/prep-v4/manifest.csv

Профили (§1 спецификации):
    prose — только связный текст: абзацы. Заголовки, списки, таблицы, код
            исключены. На нём считаются лексика, синтаксис, ритм, семантика.
    full  — текст плюс заголовки, списки, таблицы, подписи. На нём считаются
            структурные и форматные признаки, плотность чисел и сущностей.

Сравнивать документы разных профилей запрещено (§1).
"""

import argparse
import csv
import hashlib
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
DERIVED = ROOT / "04-corpus" / "derived"

PREP_VERSION = "prep-v4"

# §2.27: строка снимается, если встречается не меньше чем у половины документов
# источника. Порог подобран так, чтобы не задеть содержательные заголовки.
BOILERPLATE_SHARE = 0.5
BOILERPLATE_MIN_DOCS = 3
BOILERPLATE_MIN_WORDS = 2
BOILERPLATE_MAX_WORDS = 15

# Правило снимает оболочку веб-страницы: сайдбар, подписку, кнопки шеринга,
# списки ссылок на другие статьи. У документа, извлечённого не из веб-страницы,
# такой оболочки нет по построению, а повторяющаяся строка там — предписанная
# жанром форма: «На правах рукописи», «Общая характеристика работы». Эта форма
# и есть то, что измеряет шкала регламентированности, и снимать её нельзя.
#
# Симметрия сохраняется: у машинных документов структура тоже предписана — её
# диктует бриф, — и она остаётся в тексте. Снимать предписанное у людей и
# оставлять у моделей значило бы настроить препроцессинг на разделение классов.
NON_WEB_SOURCES = {
    "urfu", "spbgu", "sudact",
    "lenta", "buriy_2014", "gazeta",
    "taiga_arzamas", "taiga_magazines", "taiga_proza",
    "rusltc",
}

PROFILES = ("prose", "full")

# --- нормализация (§3) ---------------------------------------------------

# Латинские буквы, у которых есть кириллический двойник по начертанию.
HOMOGLYPHS = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
}
CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
MIXED_WORD = re.compile(r"\b(?=\w*[а-яёА-ЯЁ])(?=\w*[A-Za-z])\w+\b")

CURLY_QUOTES = "«»“”„‘’‚‹›"
STRAIGHT_QUOTES = "\"'"

SOFT_HYPHEN = "­"
NBSP = " "
NARROW_NBSP = " "

# --- разметка ------------------------------------------------------------

MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
# Em-dash в начале строки в маркеры не входит: в русской прозе это прямая
# речь, а не буллит. Одиночная строка с дефисом или звёздочкой тоже остаётся
# абзацем — элементом списка она признаётся только в группе, см. mark_lists.
MD_BULLET = re.compile(r"^\s{0,3}([-*+•]|\d{1,3}[.)])\s+(.+)$")
# Линейка: три и более одинаковых знака подряд либо через пробел — «* * *»
# в художественной прозе разделяет части текста ровно так же, как «---».
MD_RULE = re.compile(r"^\s{0,3}([-*_])(\s?\1){2,}\s*$")
MD_TABLE = re.compile(r"^\s{0,3}\|.*\|\s*$")
MD_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
MD_QUOTE = re.compile(r"^\s{0,3}>\s?")

BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
INLINE_CODE = re.compile(r"`([^`]+)`")
MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

# Строка счётчиков площадки: дата, «комментариев N», «Просмотры» (§2.1).
# Правило написано по фактическому виду строк в архиве PI:
# «19.01.2016 1 Комментарий 2,817 Просмотры».
COUNTER_LINE = re.compile(
    r"^\s*\d{1,2}\.\d{1,2}\.\d{4}\b.*?(комментар|просмотр|views|comments)",
    re.IGNORECASE,
)

# Обёртка канала (§2.2): короткий вводный абзац, за которым идёт линейка.
WRAPPER_LIMIT = 300

# Заголовок без разметки: короткая одиночная строка без конечной пунктуации.
# Порог в словах подобран по человеческой части, где заголовки разделов
# размечены только переводом строки («Какие бывают типы ошибок»).
PLAIN_HEADING_WORDS = 12
SENTENCE_END = tuple(".!?…:;")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def normalize(text, stats):
    """Нормализация §3. Считает то, что снимает: снятое становится признаком."""
    text = unicodedata.normalize("NFC", text)

    stats["curly_quotes"] = sum(text.count(ch) for ch in CURLY_QUOTES)
    stats["straight_quotes"] = sum(text.count(ch) for ch in STRAIGHT_QUOTES)
    stats["soft_hyphens"] = text.count(SOFT_HYPHEN)
    stats["nbsp"] = text.count(NBSP) + text.count(NARROW_NBSP)

    text = text.replace(SOFT_HYPHEN, "")
    text = text.replace(NBSP, " ").replace(NARROW_NBSP, " ")

    # Кавычки к единому стилю. Исходное распределение уже записано выше.
    for ch in "“”„‹›":
        text = text.replace(ch, '"')
    for ch in "‘’‚":
        text = text.replace(ch, "'")

    text, fixes = fix_homoglyphs(text)
    stats["homoglyph_fixes"] = fixes

    # Схлопывание пробелов и пустых строк. Абзацная структура сохраняется:
    # одна пустая строка — граница абзаца.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fix_homoglyphs(text):
    """Латиница внутри кириллического слова заменяется, число случаев пишется
    во флаг (§3.3).

    Заменяется короткая цепочка латиницы: одиночная буква — если соседняя
    кириллическая буква того же регистра («кaк», «пo», «Росcии»); пара — только
    зажатая кириллицей с обеих сторон. Цепочка длиннее двух букв, смена
    регистра на стыке и цифра в слове означают не опечатку, а термин или
    склейку при извлечении: «ЮKassa», «SEOшников», «величиныA», «осьOx»,
    «точкеx1». Правило проверено на 963 смешанных вхождениях корпуса.
    """
    count = 0
    latin_run = re.compile(r"[A-Za-z]+")

    def same_case(left, right):
        return left.isupper() == right.isupper()

    def repl(match):
        nonlocal count
        word = match.group(0)
        if not CYRILLIC.search(word) or any(ch.isdigit() for ch in word):
            return word
        out = list(word)
        for run in latin_run.finditer(word):
            start, end = run.span()
            length = end - start
            if length > 2 or not all(ch in HOMOGLYPHS for ch in run.group(0)):
                continue
            before = word[start - 1] if start else ""
            after = word[end] if end < len(word) else ""
            inside = CYRILLIC.match(before or "") and CYRILLIC.match(after or "")
            if length == 2 and not inside:
                continue
            if length == 1:
                neighbour = before if CYRILLIC.match(before or "") else after
                if not (neighbour and CYRILLIC.match(neighbour) and same_case(neighbour, word[start])):
                    continue
            for position in range(start, end):
                out[position] = HOMOGLYPHS[word[position]]
                count += 1
        return "".join(out)

    return MIXED_WORD.sub(repl, text), count


def strip_channel_wrapper(text, stats):
    """§2.2. Первый абзац снимается, если он короткий, стоит в самом начале и
    за ним идёт горизонтальная линейка. Правило применяется ко всем каналам."""
    stats["wrapper_removed"] = 0
    lines = text.split("\n")
    first, rest = [], []
    for index, line in enumerate(lines):
        if line.strip() == "":
            rest = lines[index:]
            break
        first.append(line)
    else:
        return text

    block = " ".join(first).strip()
    if len(block) > WRAPPER_LIMIT:
        return text
    if MD_HEADING.match(first[0]) or MD_BULLET.match(first[0]):
        return text

    for index, line in enumerate(rest):
        if line.strip() == "":
            continue
        if MD_RULE.match(line):
            stats["wrapper_removed"] = 1
            return "\n".join(rest[index + 1 :]).strip()
        return text
    return text


# §2.26. Оболочка площадки: ветка комментариев и служебные строки.
REPLY_WORD = re.compile(r"\bОтветить\b")
COMMENT_START = re.compile(
    r"(?:^|\s)(?:\d{1,3}\.\s|\d{1,2}\.\d{2}\.\d{4}|[А-ЯЁ][\w-]{1,20}\s*:\s*\d{1,2}\.\d{2}\.\d{4})"
)
PLATFORM_LINES = [
    re.compile(r"Автор:\s*[А-ЯЁ][\w .-]{2,40}\s+в\s+[А-ЯЁ][\w .-]{2,30}(?:\s*,\s*\d{2}/\d{2}/\d{4})?"),
    re.compile(r"Сайт дня\s*:(?:\s*\S+){0,12}"),
    re.compile(r"\d[\d\s,]{0,9}\s*[Пп]росмотр\w*"),
    re.compile(r"\d[\d\s,]{0,9}\s*[Кк]оммента\w*"),
    re.compile(r"\bПоделиться\s*:?"),
]
MIN_REPLIES = 2


def strip_comment_thread(text, stats):
    """§2.26. Ветка комментариев читателей отрезается вместе с хвостом.

    Условие — два и более отдельных «Ответить»: одиночное слово встречается в
    связном тексте как обычный глагол, ветка без повторов не бывает. Начало
    первого комментария ищется назад по номеру, дате или подписи «Имя : дата».
    """
    stats["comments_cut_chars"] = 0
    replies = list(REPLY_WORD.finditer(text))
    if len(replies) < MIN_REPLIES:
        return text
    first = replies[0].start()
    marker = None
    for match in COMMENT_START.finditer(text[:first]):
        marker = match.start()
    cut = marker if marker is not None else first
    stats["comments_cut_chars"] = len(text) - cut
    return text[:cut].rstrip()


def strip_platform_lines(text, stats):
    """§2.26. Служебные строки площадки снимаются по форме, а не по источнику.

    Шаблон применяется к строке целиком: «Поделиться» и «1000 просмотров»
    внутри фразы — содержание текста, а не кнопка площадки. Первый прогон
    правила без этого условия срезал такие слова у двенадцати машинных
    документов, где они стоят в предложении.
    """
    removed = 0
    kept = []
    for line in text.splitlines():
        stripped = line.strip(" \t·|—–-")
        if stripped and any(regex.fullmatch(stripped) for regex in PLATFORM_LINES):
            removed += 1
            continue
        kept.append(line)
    stats["platform_lines_removed"] = removed
    return "\n".join(kept)


def strip_counters(lines, stats):
    """§2.1. Строка счётчиков площадки удаляется целиком."""
    kept, removed = [], 0
    for line in lines:
        if COUNTER_LINE.match(line):
            removed += 1
            continue
        kept.append(line)
    stats["counter_lines_removed"] = removed
    return kept


def strip_inline(text, stats):
    """Снятие инлайновой разметки. Слова остаются, маркеры считаются."""
    stats["bold_spans"] = stats.get("bold_spans", 0) + len(BOLD.findall(text))
    stats["code_spans"] = stats.get("code_spans", 0) + len(INLINE_CODE.findall(text))
    stats["links"] = stats.get("links", 0) + len(MD_LINK.findall(text))

    text = MD_LINK.sub(lambda m: m.group(1) or m.group(2), text)
    text = BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = ITALIC.sub(lambda m: m.group(1), text)
    text = INLINE_CODE.sub(lambda m: m.group(1), text)
    return text


# §2.28. Перенос слова по слогам. Решение о слове принимает внешний словарь
# OpenCorpora через pymorphy3: частоты самого корпуса в правиле не участвуют,
# иначе решение о слове зависело бы от того, какие тексты в корпус попали.
HYPHEN_BREAK = re.compile(r"\b([а-яё]{2,})\s*-\s+([а-яё]{2,})\b")

_morph = None
_known_cache = {}


def known_word(word):
    global _morph
    if _morph is None:
        import pymorphy3

        _morph = pymorphy3.MorphAnalyzer()
    if word not in _known_cache:
        _known_cache[word] = any(parse.is_known for parse in _morph.parse(word))
    return _known_cache[word]


def join_hyphen_breaks(text, stats):
    """§2.28. `за- ключение` → `заключение`.

    Порядок проверок обязателен. Первая редакция правила проверяла склейку
    раньше самостоятельности частей и схлопывала «плата- на» в «платана» —
    словарное слово, но не то. Обе части словарные означают тире между
    словами либо составное с авторским дефисом; склейка испортила бы оба.
    """
    joins = 0

    def repl(match):
        nonlocal joins
        left, right = match.group(1), match.group(2)
        if known_word(left) and known_word(right):
            return match.group(0)
        if known_word(left + right):
            joins += 1
            return left + right
        return match.group(0)

    text = HYPHEN_BREAK.sub(repl, text)
    stats["hyphen_joins"] = joins
    return text


def looks_like_heading(block):
    """Заголовок без разметки. Единое правило для всех документов: у машинных
    заголовки размечены решётками, у архивных — только переводом строки, и
    разное обращение с ними завело бы конфаундер обработки вдоль origin."""
    if len(block) != 1:
        return False
    line = block[0].strip()
    if not line or line.endswith(SENTENCE_END):
        return False
    if len(line.split()) > PLAIN_HEADING_WORDS:
        return False
    return True


def mark_lists(lines):
    """Какие строки считать элементами списка.

    Маркер в начале строки сам по себе списка не делает: в художественной
    прозе с него начинается реплика («- Вы не против...»), а разделитель
    «* * *» размечает части текста. Элементом списка строка признаётся
    только в группе — когда соседняя непустая строка тоже начинается с
    маркера. Правило одно для всех документов корпуса.
    """
    candidates = [bool(MD_BULLET.match(line)) and not MD_RULE.match(line) for line in lines]
    if not any(candidates):
        return candidates

    verdict = [False] * len(lines)
    index = 0
    while index < len(lines):
        if not candidates[index]:
            index += 1
            continue
        run, cursor = [index], index + 1
        while cursor < len(lines):
            if lines[cursor].strip() == "":
                cursor += 1
                continue
            if candidates[cursor]:
                run.append(cursor)
                cursor += 1
                continue
            break
        if len(run) > 1:
            for position in run:
                verdict[position] = True
        index = max(cursor, index + 1)
    return verdict


# §2.29. Порог доли строк, оканчивающихся знаком конца предложения.
# Назначен после просмотра распределения по 304 документам; опирается на
# пунктуацию конца строки, не на класс документа и не на значения признаков.
LINE_PARAGRAPH_END_SHARE = 0.8
LINE_PARAGRAPH_BLANK_SHARE = 0.25
LINE_ENDINGS = (".", "!", "?", "…", "»", '"')


def line_is_paragraph_mode(lines):
    """§2.29. Абзацы разделены одиночным переводом строки?"""
    nonempty = [line.strip() for line in lines if line.strip()]
    if len(nonempty) < 10:
        return False
    blanks = len(lines) - len(nonempty)
    if blanks >= LINE_PARAGRAPH_BLANK_SHARE * len(nonempty):
        return False
    ends = sum(1 for line in nonempty if line.endswith(LINE_ENDINGS))
    return ends / len(nonempty) >= LINE_PARAGRAPH_END_SHARE


# §2.30. Висячая подводка: короткий абзац с двоеточием перед списком.
LIST_LEAD_WORDS = 3


def mark_list_leads(blocks):
    """§2.30. Подводка уходит из prose вместе со своим списком."""
    out, removed = [], 0
    for index, (kind, content) in enumerate(blocks):
        if kind == "paragraph" and content.rstrip().endswith(":")                 and len(content.split()) <= LIST_LEAD_WORDS:
            following = blocks[index + 1][0] if index + 1 < len(blocks) else ""
            # Кодовый блок добавлен по итогам реализации: у машинных текстов
            # подводка чаще ведёт к примеру URL или фрагменту разметки,
            # чем к списку, а код в профиль prose не попадает так же.
            if following in ("list_item", "table_row", "code"):
                out.append(("list_lead", content))
                removed += 1
                continue
        out.append((kind, content))
    return out, removed


def segment(text, stats):
    """Разбор на блоки. Тип блока определяет, в какой профиль он попадёт."""
    lines = strip_counters(text.split("\n"), stats)
    is_list = mark_lists(lines)
    # §2.29. Абзацы разделены одиночным переводом строки — каждая строка
    # закрывает блок сама, иначе документ слипается в один абзац.
    one_line_paragraphs = line_is_paragraph_mode(lines)
    stats["line_is_paragraph"] = int(one_line_paragraphs)

    blocks = []
    buffer, in_fence = [], False
    counters = Counter()

    def flush():
        if not buffer:
            return
        if looks_like_heading(buffer):
            blocks.append(("heading_plain", " ".join(buffer).strip()))
            counters["heading_plain"] += 1
        else:
            blocks.append(("paragraph", " ".join(line.strip() for line in buffer).strip()))
            counters["paragraph"] += 1
        buffer.clear()

    for position, line in enumerate(lines):
        if MD_FENCE.match(line):
            flush()
            in_fence = not in_fence
            counters["code_blocks"] += 0 if in_fence else 1
            continue
        if in_fence:
            blocks.append(("code", line))
            continue

        stripped = line.strip()
        if stripped == "":
            flush()
            continue
        if MD_RULE.match(line):
            flush()
            counters["rules"] += 1
            continue

        heading = MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append((f"heading_md{len(heading.group(1))}", heading.group(2).strip()))
            counters["heading_md"] += 1
            continue

        if is_list[position]:
            flush()
            blocks.append(("list_item", MD_BULLET.match(line).group(2).strip()))
            counters["list_items"] += 1
            continue

        if MD_TABLE.match(line):
            flush()
            blocks.append(("table_row", stripped.strip("|").strip()))
            counters["table_rows"] += 1
            continue

        buffer.append(MD_QUOTE.sub("", line))
        if one_line_paragraphs:
            flush()
    flush()

    for key, value in counters.items():
        stats[key] = value
    return blocks


def render(blocks, profile, stats):
    """Сборка профиля. prose — только абзацы, full — всё, кроме кода."""
    out = []
    for kind, content in blocks:
        if profile == "prose":
            if kind != "paragraph":
                continue
        else:
            if kind == "code":
                continue
        out.append(strip_inline(content, stats))
    return "\n\n".join(part for part in out if part.strip())


def collect_boilerplate(rows):
    """§2.27. Строки, повторяющиеся не меньше чем у половины документов источника.

    Первый проход по корпусу: оболочка площадки опознаётся частотой внутри
    одного издателя, а не списком фраз. Список фраз пришлось бы защищать как
    решение после просмотра данных, частотный критерий — нет.
    """
    by_source = defaultdict(list)
    for row in rows:
        source = row["source_platform"] or row["generation_channel"] or "unknown"
        by_source[source].append(row)

    boilerplate = {}
    for source, group in by_source.items():
        if source in NON_WEB_SOURCES or len(group) < BOILERPLATE_MIN_DOCS:
            continue
        seen = Counter()
        for row in group:
            path = ROOT / row["file_path"]
            if not path.exists():
                continue
            lines = set()
            for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                normalized = re.sub(r"\s+", " ", line).strip()
                if BOILERPLATE_MIN_WORDS <= len(normalized.split()) <= BOILERPLATE_MAX_WORDS:
                    lines.add(normalized.casefold())
            seen.update(lines)
        limit = max(BOILERPLATE_MIN_DOCS, len(group) * BOILERPLATE_SHARE)
        found = {line for line, count in seen.items() if count >= limit}
        if found:
            boilerplate[source] = found
    return boilerplate


def strip_boilerplate(text, lines, stats):
    """§2.27. Снятие строк оболочки источника."""
    stats["boilerplate_lines_removed"] = 0
    if not lines:
        return text
    kept, removed = [], 0
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip().casefold()
        if normalized in lines:
            removed += 1
            continue
        kept.append(line)
    stats["boilerplate_lines_removed"] = removed
    return "\n".join(kept)


def process(text, boilerplate=None):
    stats = Counter()
    text = normalize(text, stats)
    text = join_hyphen_breaks(text, stats)
    text = strip_channel_wrapper(text, stats)
    text = strip_comment_thread(text, stats)
    text = strip_platform_lines(text, stats)
    text = strip_boilerplate(text, boilerplate, stats)
    blocks = segment(text, stats)
    # §2.30. Подводка уходит из prose вместе со своим списком.
    blocks, list_leads = mark_list_leads(blocks)
    stats["list_leads_removed"] = list_leads

    # Заголовок статьи первой строкой файла (§2.1): в профиле prose он не
    # должен считаться предложением. После сегментации он уже помечен как
    # heading_plain либо heading_md, отдельного правила не требуется.
    rendered = {}
    for profile in PROFILES:
        profile_stats = Counter()
        rendered[profile] = render(blocks, profile, profile_stats)
        for key, value in profile_stats.items():
            stats[f"{profile}_{key}"] = value
    return rendered, stats


def main():
    global PREP_VERSION
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="обработать только N первых документов")
    parser.add_argument("--only", help="один document_id")
    # prep-v5, 2026-07-29: коррекция дефекта извлечения. Значения по умолчанию
    # сохраняют поведение prep-v4, поэтому прежний прогон воспроизводится без флагов.
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга, каталог внутри derived/")
    parser.add_argument("--corrected-root",
                        help="каталог корректированных входов; если файл документа "
                             "там есть, читается он вместо raw из реестра")
    args = parser.parse_args()

    PREP_VERSION = args.prep_version
    corrected_root = Path(args.corrected_root) if args.corrected_root else None
    if corrected_root and not corrected_root.is_absolute():
        corrected_root = ROOT / corrected_root

    rows = read_rows(DOCUMENTS)
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    for profile in PROFILES:
        (DERIVED / PREP_VERSION / profile).mkdir(parents=True, exist_ok=True)

    boilerplate = collect_boilerplate(read_rows(DOCUMENTS))
    print(f"Препроцессинг: {PREP_VERSION}, документов {len(rows)}")
    print(f"  оболочка источников: {sum(len(v) for v in boilerplate.values())} строк у {len(boilerplate)} источников")
    dump = ROOT / "04-corpus" / "boilerplate-lines.csv"
    with dump.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "line"])
        for source in sorted(boilerplate):
            for line in sorted(boilerplate[source]):
                writer.writerow([source, line])

    manifest_path = DERIVED / PREP_VERSION / "manifest.csv"
    fields = [
        "document_id", "prep_version", "origin_class", "generation_channel",
        "prose_path", "prose_sha256", "prose_words",
        "full_path", "full_sha256", "full_words",
        "raw_words", "wrapper_removed", "comments_cut_chars", "platform_lines_removed",
        "counter_lines_removed", "boilerplate_lines_removed", "homoglyph_fixes",
        "curly_quotes", "straight_quotes", "soft_hyphens", "nbsp",
        "heading_md", "heading_plain", "list_items", "table_rows", "rules", "code_blocks",
        "paragraph", "prose_bold_spans", "prose_code_spans", "prose_links",
        "full_bold_spans", "full_code_spans", "full_links",
        # prep-v4: §2.28, §2.29, §2.30
        "hyphen_joins", "line_is_paragraph", "list_leads_removed",
    ]

    empty, written = [], 0
    with manifest_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            path = ROOT / row["file_path"]
            source = row["source_platform"] or row["generation_channel"] or "unknown"
            if corrected_root:
                fixed = corrected_root / source / f"{row['document_id']}.txt"
                if fixed.exists():
                    path = fixed
            if not path.exists():
                continue
            raw = path.read_text(encoding="utf-8", errors="replace")
            rendered, stats = process(raw, boilerplate.get(source))

            record = {
                "document_id": row["document_id"],
                "prep_version": PREP_VERSION,
                "origin_class": row["origin_class"],
                "generation_channel": row.get("generation_channel") or "",
                "raw_words": len(raw.split()),
            }
            for profile in PROFILES:
                out_path = DERIVED / PREP_VERSION / profile / f"{row['document_id']}.txt"
                # newline="\n" обязателен: без него Windows пишет CRLF, а sha256
                # ниже считается от строки с LF, и хеш манифеста перестаёт
                # сходиться с файлом на диске. Текст от этого не меняется —
                # при чтении в текстовом режиме CRLF всё равно даёт LF.
                out_path.write_text(rendered[profile], encoding="utf-8", newline="\n")
                record[f"{profile}_path"] = str(out_path.relative_to(ROOT)).replace("\\", "/")
                record[f"{profile}_sha256"] = hashlib.sha256(rendered[profile].encode("utf-8")).hexdigest()
                record[f"{profile}_words"] = len(rendered[profile].split())

            for key in fields:
                if key not in record:
                    record[key] = stats.get(key, 0)
            writer.writerow(record)
            written += 1

            if record["prose_words"] < 100:
                empty.append((row["document_id"], record["prose_words"], record["raw_words"]))

    print(f"Записано документов: {written}")
    print(f"Манифест: {manifest_path.relative_to(ROOT)}")
    if empty:
        print(f"  ! профиль prose короче 100 слов: {len(empty)}")
        for doc_id, prose_words, raw_words in empty[:10]:
            print(f"    {doc_id}: prose {prose_words} слов из raw {raw_words}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
