#!/usr/bin/env python3
"""Контроль качества препроцессинга перед заморозкой (prep-qa-v1).

Процедура зафиксирована в `06-features/prep-qa-spec.md` до прогона; сами шесть
проверок требует §7 `preprocessing-spec.md`. Здесь только реализация —
расхождение между спецификацией и кодом считается ошибкой кода.

    python 09-tools/prep_qa.py            # отчёт и таблица срабатываний
    python 09-tools/prep_qa.py --sample   # только состав выборки

Скрипт ничего не исправляет. Найденный дефект означает решение PI о новой
версии профиля, а не правку по ходу.
"""

import argparse
import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "09-tools"
sys.path.insert(0, str(TOOLS))

import prep  # noqa: E402  — проверяется рабочий код, а не его копия

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
OUT_OF_CONFIRMATORY = ROOT / "06-features" / "pilot-1-out-of-confirmatory.csv"
MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
STANZA_CACHE = ROOT / "06-features" / "cache" / "stanza-v1"
REPORT_MD = ROOT / "06-features" / "prep-qa-report.md"
HITS_CSV = ROOT / "06-features" / "prep-qa-hits.csv"

QA_VERSION = "prep-qa-v1"
PER_GENRE = 3  # §1 спецификации

# §2.1. Перечень задан до прогона, из общеупотребительных русских сокращений.
ABBREVIATIONS = {
    "т", "д", "п", "г", "гг", "в", "вв", "рис", "табл", "см", "ср", "стр", "с",
    "руб", "тыс", "млн", "млрд", "им", "ул", "корп", "проф", "доц", "акад",
    "др", "пр", "напр",
}

# §2.2. Маркеры списка внутри абзаца.
INLINE_BULLET = re.compile(r"(?:(?<=[.!?;])\s|^)\s*[-–—•]\s+")
INLINE_NUMBER = re.compile(r"(?:(?<=[.!?;])\s|^)\s*\d{1,2}[).]\s+(?=[А-ЯЁA-Z])")
LONG_PARAGRAPH_WORDS = 60
SEMICOLON_LIMIT = 5
MIN_LIST_LINE_WORDS = 4

# §2.6. Тестовый пример гомоглифов: строка → ожидание.
HOMOGLYPH_CASES = [
    ("рaботa", "работа", "одиночная латинская a внутри строчного слова"),
    ("РОCСИЯ", "РОССИЯ", "латинская C среди заглавных кириллических"),
    ("Bерсия", "Bерсия", "заглавная латинская при строчном соседе — не заменяется"),
    ("CEO", "CEO", "латинская аббревиатура целиком"),
    ("ЮKassa", "ЮKassa", "цепочка длиннее двух букв"),
    ("точкеx1", "точкеx1", "цифра в слове"),
]

WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def read_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def build_sample():
    """§1: по три документа на жанр из выведенных за пределы confirmatory."""
    registry = {row["document_id"]: row for row in read_rows(DOCUMENTS, "utf-8-sig")}
    pool = [row["document_id"] for row in read_rows(OUT_OF_CONFIRMATORY)
            if row["document_id"] in registry]

    by_genre = defaultdict(list)
    for doc in sorted(pool):
        by_genre[registry[doc]["genre"]].append(doc)

    def source_of(doc):
        row = registry[doc]
        return row["generation_channel"].strip() or row["source_platform"].strip() or "—"

    sample = []
    for genre in sorted(by_genre):
        docs = by_genre[genre]
        picked, used_sources = [], set()

        # Сначала по одному документу с каждого источника: правила
        # препроцессинга зависят от площадки, и три текста одного блога
        # проверили бы одно правило трижды.
        for doc in docs:
            if len(picked) >= PER_GENRE:
                break
            if source_of(doc) in used_sources:
                continue
            picked.append(doc)
            used_sources.add(source_of(doc))

        # Если источников в жанре меньше трёх, добираются классы, потом остаток.
        for origin in ("H", "A"):
            if len(picked) >= PER_GENRE:
                break
            for doc in docs:
                if doc not in picked and registry[doc]["origin_class"] == origin:
                    picked.append(doc)
                    break
        for doc in docs:
            if len(picked) >= PER_GENRE:
                break
            if doc not in picked:
                picked.append(doc)
        sample.extend((doc, genre, registry[doc]["origin_class"]) for doc in picked)
    return sample, registry


def load_parse(doc_id):
    path = STANZA_CACHE / f"{doc_id}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def check_abbreviations(doc_id, parsed, hits):
    """§2.1. Граница предложения сразу после сокращения или инициала."""
    found = 0
    sentences = parsed["sentences"]
    for index, sentence in enumerate(sentences[:-1]):
        if not sentence:
            continue
        last = sentence[-1]["t"]
        previous = sentence[-2]["t"] if len(sentence) > 1 else ""
        if last != ".":
            continue
        stem = previous.strip().lower()
        is_abbr = stem in ABBREVIATIONS
        is_initial = len(previous) == 1 and previous.isalpha() and previous.isupper()
        if not (is_abbr or is_initial):
            continue
        found += 1
        tail = " ".join(token["t"] for token in sentence[-6:])
        head = " ".join(token["t"] for token in sentences[index + 1][:6])
        hits.append({"check": "2.1 сокращения", "document_id": doc_id,
                     "detail": "инициал" if is_initial else f"сокращение «{previous}.»",
                     "context": f"…{tail} ⟂ {head}…"})
    return found


def prose_paragraphs(manifest_row):
    path = Path(manifest_row["prose_path"])
    if not path.exists():
        return []
    return [block.strip() for block in path.read_text(encoding="utf-8").split("\n\n")
            if block.strip()]


def check_glued_lists(doc_id, paragraphs, hits):
    """§2.2. Список, не опознанный правилом, склеенный в абзац."""
    found = 0
    for paragraph in paragraphs:
        bullets = len(INLINE_BULLET.findall(paragraph))
        numbers = len(INLINE_NUMBER.findall(paragraph))
        words = len(paragraph.split())
        semicolons = paragraph.count(";")
        reason = ""
        if bullets >= 3:
            reason = f"{bullets} внутренних тире-маркеров"
        elif numbers >= 3:
            reason = f"{numbers} внутренних номеров пунктов"
        elif words > LONG_PARAGRAPH_WORDS and semicolons > SEMICOLON_LIMIT:
            reason = f"{words} слов и {semicolons} точек с запятой"
        if reason:
            found += 1
            hits.append({"check": "2.2 склейка списка", "document_id": doc_id,
                         "detail": reason, "context": paragraph[:220]})
    return found


def segment_raw(registry_row, boilerplate):
    """Блоки документа с типами — тем же кодом, что строит профили.

    Типы нужны, чтобы отличить строку списка от абзаца: в готовом профиле
    `full` пометок нет, и сравнение всех его блоков с `prose` давало бы
    совпадение на каждом обычном абзаце.
    """
    path = Path(registry_row["file_path"])
    if not path.exists():
        return []
    stats = Counter()
    text = prep.normalize(path.read_text(encoding="utf-8"), stats)
    text = prep.strip_channel_wrapper(text, stats)
    text = prep.strip_comment_thread(text, stats)
    text = prep.strip_platform_lines(text, stats)
    text = prep.strip_boilerplate(text, boilerplate, stats)
    return prep.segment(text, stats)


def check_blocks_leaked(doc_id, blocks, paragraphs, hits, kind):
    """§2.2 и §2.3: содержимое списков и таблиц не должно быть в prose."""
    wanted = "list_item" if kind == "list" else "table_row"
    prose_text = "\n\n".join(paragraphs)
    found = 0
    for block_kind, content in blocks:
        if block_kind != wanted:
            continue
        content = prep.strip_inline(content, Counter()).strip()
        if len(content.split()) < MIN_LIST_LINE_WORDS:
            continue
        if content and content in prose_text:
            found += 1
            if found <= 3:
                hits.append({"check": f"2.{2 if kind == 'list' else 3} блок в prose",
                             "document_id": doc_id, "detail": f"{kind}: строка целиком в prose",
                             "context": content[:220]})
    return found


def check_table_pipes(doc_id, paragraphs, hits):
    """§2.3. След неопознанной таблицы — два и более разделителя в абзаце."""
    found = 0
    for paragraph in paragraphs:
        if paragraph.count("|") >= 2:
            found += 1
            hits.append({"check": "2.3 разделители таблицы", "document_id": doc_id,
                         "detail": f"{paragraph.count('|')} знаков |", "context": paragraph[:220]})
    return found


def check_lemmas(doc_id, parsed, hits):
    """§2.4. Четыре разряда дефектов леммы у имён собственных."""
    counts = Counter()
    for sentence in parsed["sentences"]:
        for token in sentence:
            if token.get("p") != "PROPN":
                continue
            form, lemma = token["t"], token.get("l") or ""
            kinds = []
            if not lemma:
                kinds.append("пустая лемма")
            else:
                if re.search(r"[а-яёА-ЯЁ]", form) and re.fullmatch(r"[A-Za-z\W\d]+", lemma):
                    kinds.append("латинская лемма у кириллического токена")
                if form[:1].isupper() and lemma[:1].islower():
                    kinds.append("потеряна заглавная")
                if len(lemma) * 2 < len(form):
                    kinds.append("лемма короче половины токена")
            for kind in kinds:
                counts[kind] += 1
                if counts[kind] <= 5:
                    hits.append({"check": "2.4 леммы PROPN", "document_id": doc_id,
                                 "detail": kind, "context": f"{form} → {lemma}"})
    return counts


def check_word_counts(doc_id, manifest_row, paragraphs, parsed):
    """§2.5. Три уровня сверки числа слов."""
    declared = int(manifest_row["prose_words"] or 0)
    text = "\n\n".join(paragraphs)
    # prep считает слова как len(text.split()) — сверка идёт тем же
    # определением, иначе расхождение показывало бы разницу определений,
    # а не дефект файла.
    recomputed = len(text.split())
    # Счёт по буквенным последовательностям справочный: разница с ним
    # показывает, сколько «слов» на деле числа, знаки и обрывки.
    alphabetic = len(WORD_RE.findall(text))
    tokens = sum(1 for sentence in parsed["sentences"] for token in sentence
                 if WORD_RE.fullmatch(token["t"] or ""))
    return {"document_id": doc_id, "declared": declared, "recomputed": recomputed,
            "alphabetic": alphabetic, "stanza_tokens": tokens}


def check_homoglyphs():
    """§2.6. Тестовый пример на рабочей функции prep.fix_homoglyphs."""
    results = []
    for source, expected, what in HOMOGLYPH_CASES:
        got, count = prep.fix_homoglyphs(source)
        results.append({"source": source, "expected": expected, "got": got,
                        "replacements": count, "what": what, "ok": got == expected})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true", help="только состав выборки")
    args = parser.parse_args()

    sample, registry = build_sample()
    print(f"выборка: {len(sample)} документов, жанров {len({g for _, g, _ in sample})}")
    for doc, genre, origin in sample:
        print(f"  {genre:<12} {origin}  {doc}")
    if args.sample:
        return 0

    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    # Строки оболочки площадки: §2.27 опознаёт их частотой по всему корпусу,
    # поэтому таблица собирается на всех документах, а не на выборке.
    boilerplate = prep.collect_boilerplate(read_rows(DOCUMENTS, "utf-8-sig"))
    hits = []
    totals = Counter()
    lemma_counts = Counter()
    word_rows = []
    missing = []

    for doc, genre, origin in sample:
        parsed = load_parse(doc)
        row = manifest.get(doc)
        if parsed is None or row is None:
            missing.append(doc)
            continue
        paragraphs = prose_paragraphs(row)
        totals["2.1"] += check_abbreviations(doc, parsed, hits)
        totals["2.2"] += check_glued_lists(doc, paragraphs, hits)
        blocks = segment_raw(registry[doc], boilerplate)
        totals["2.2-leak"] += check_blocks_leaked(doc, blocks, paragraphs, hits, "list")
        totals["2.3-leak"] += check_blocks_leaked(doc, blocks, paragraphs, hits, "table")
        totals["2.3"] += check_table_pipes(doc, paragraphs, hits)
        lemma_counts.update(check_lemmas(doc, parsed, hits))
        word_rows.append(check_word_counts(doc, row, paragraphs, parsed))

    homoglyphs = check_homoglyphs()

    with HITS_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["check", "document_id", "detail", "context"])
        writer.writeheader()
        writer.writerows(hits)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    out = []
    w = out.append
    w(f"# Контроль качества препроцессинга ({QA_VERSION})")
    w("")
    w(f"Процедура — `06-features/prep-qa-spec.md`, зафиксирована до прогона; шесть проверок "
      f"требует §7 `preprocessing-spec.md`. Прогон {stamp}.")
    w("")
    w(f"Выборка — {len(sample)} документов, по три на каждый из семи жанров, все из "
      "`06-features/pilot-1-out-of-confirmatory.csv`. Скрипт ничего не исправляет: "
      "правка препроцессинга означает новую версию профиля и пересчёт всей матрицы.")
    w("")
    if missing:
        w(f"Без разбора в кэше: {', '.join(missing)}.")
        w("")

    w("## Состав выборки")
    w("")
    w("| Жанр | Класс | Документ | Слов в prose |")
    w("|---|---|---|---|")
    words_by_doc = {row["document_id"]: row["declared"] for row in word_rows}
    for doc, genre, origin in sample:
        w(f"| {genre} | {origin} | `{doc}` | {words_by_doc.get(doc, '—')} |")
    w("")

    w("## 2.1. Предложения не рвутся на сокращениях")
    w("")
    w(f"Срабатываний: **{totals['2.1']}**.")
    w("")
    for hit in [h for h in hits if h["check"].startswith("2.1")][:15]:
        w(f"- `{hit['document_id']}`, {hit['detail']}: {hit['context']}")
    w("")

    w("## 2.2. Списки не склеиваются в одно предложение")
    w("")
    w(f"Абзацев с признаками склейки: **{totals['2.2']}**. "
      f"Строк списка, дошедших до профиля `prose`: **{totals['2.2-leak']}**.")
    w("")
    for hit in [h for h in hits if h["check"].startswith("2.2")][:12]:
        w(f"- `{hit['document_id']}`, {hit['detail']}: {hit['context']}")
    w("")

    w("## 2.3. Таблицы не превращаются в поток слов")
    w("")
    w(f"Строк таблиц в профиле `prose`: **{totals['2.3-leak']}**. "
      f"Абзацев с разделителями `|`: **{totals['2.3']}**.")
    w("")
    for hit in [h for h in hits if h["check"].startswith("2.3")][:12]:
        w(f"- `{hit['document_id']}`, {hit['detail']}: {hit['context']}")
    w("")

    w("## 2.4. Лемматизация имён собственных")
    w("")
    if lemma_counts:
        w("| Разряд | Случаев |")
        w("|---|---|")
        for kind, count in lemma_counts.most_common():
            w(f"| {kind} | {count} |")
    else:
        w("Дефектов не найдено.")
    w("")
    for hit in [h for h in hits if h["check"].startswith("2.4")][:15]:
        w(f"- `{hit['document_id']}`, {hit['detail']}: {hit['context']}")
    w("")

    w("## 2.5. Число слов")
    w("")
    mismatched = [row for row in word_rows if row["declared"] != row["recomputed"]]
    w(f"Расхождений между манифестом и независимым пересчётом: **{len(mismatched)}** "
      f"из {len(word_rows)}.")
    w("")
    w("| Документ | Манифест | Пересчёт | Только буквенные | Словных токенов Stanza |")
    w("|---|---|---|---|---|")
    for row in word_rows:
        mark = " ❗" if row["declared"] != row["recomputed"] else ""
        w(f"| `{row['document_id']}` | {row['declared']}{mark} | {row['recomputed']} "
          f"| {row['alphabetic']} | {row['stanza_tokens']} |")
    w("")
    w("Расхождение с числом словных токенов Stanza вердикта не несёт: токенизация "
      "разбивает дефисные и кавычечные формы иначе, чем счёт по словам.")
    w("")

    w("## 2.6. Флаг гомоглифов")
    w("")
    w("| Строка | Ожидание | Получено | Замен | Что проверяется | Итог |")
    w("|---|---|---|---|---|---|")
    for case in homoglyphs:
        w(f"| `{case['source']}` | `{case['expected']}` | `{case['got']}` "
          f"| {case['replacements']} | {case['what']} | "
          f"{'сходится' if case['ok'] else '**расходится**'} |")
    w("")

    REPORT_MD.write_text("\n".join(out) + "\n", encoding="utf-8")

    print()
    for key in ("2.1", "2.2", "2.2-leak", "2.3", "2.3-leak"):
        print(f"  проверка {key}: {totals[key]} срабатываний")
    print(f"  леммы PROPN: {sum(lemma_counts.values())} случаев в {len(lemma_counts)} разрядах")
    print(f"  расхождений в счёте слов: {len(mismatched)} из {len(word_rows)}")
    print(f"  гомоглифы: сходится {sum(1 for c in homoglyphs if c['ok'])} из {len(homoglyphs)}")
    print(f"отчёт: {REPORT_MD.relative_to(ROOT)}")
    print(f"срабатывания: {HITS_CSV.relative_to(ROOT)}, строк {len(hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
