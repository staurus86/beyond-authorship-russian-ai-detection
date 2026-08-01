#!/usr/bin/env python3
"""Проверка реестров корпуса: целостность, ссылки, покрытие ячеек, утечки.

Запуск из корня папки исследования:
    python 09-tools/validate_registry.py
    python 09-tools/validate_registry.py --hashes   # плюс сверка SHA-256 файлов

Скрипт ничего не меняет на диске. Выход: 0 — ошибок нет, 1 — есть.
"""

import argparse
import csv
import hashlib
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows-консоль по умолчанию не в UTF-8, иначе русский вывод превращается в кракозябры.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
BRIEFS = ROOT / "03-briefs" / "briefs-registry.csv"
PROMPTS = ROOT / "03-briefs" / "prompt-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
PEOPLE = ROOT / "00-admin" / "people-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"

# Поля, без которых документ не участвует в анализе.
REQUIRED_DOC_FIELDS = [
    "document_id",
    "file_path",
    "language",
    "genre",
    "origin_class",
    "preprocessing_profile",
    "leakage_group",
    "revision_family_id",
]

# Поля, обязательные только для машинных текстов.
# Паспорт канала добавлен 2026-07-24: сравниваются каналы генерации, а не
# модели, поэтому оболочка и её версия обязаны быть записаны у каждой ячейки.
REQUIRED_AI_FIELDS = [
    "model_provider",
    "model_exact_id",
    "generation_date",
    "prompt_id",
    "prompt_condition",
    "generation_channel",
    "model_family",
    "access_method",
    "wrapper_version",
    "system_prompt_visibility",
    "raw_payload_available",
    "sampling_parameters_known",
]

ORIGIN_CLASSES = {"H", "A", "A2H", "H2A", "HA", "HH", "T", "P"}


def read_rows(path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.info.append(msg)

    def show(self):
        for line in self.info:
            print(f"  {line}")
        for line in self.warnings:
            print(f"  ! {line}")
        for line in self.errors:
            print(f"  ОШИБКА: {line}")
        print()
        print(f"Итого: ошибок {len(self.errors)}, предупреждений {len(self.warnings)}")
        return 1 if self.errors else 0


def check_documents(docs, briefs, prompts, rep):
    if docs is None:
        rep.error(f"нет файла {DOCUMENTS.relative_to(ROOT)}")
        return
    if not docs:
        rep.note("реестр документов пуст — проверять нечего")
        return

    rep.note(f"документов в реестре: {len(docs)}")

    ids = Counter(row.get("document_id", "") for row in docs)
    for doc_id, count in ids.items():
        if count > 1:
            rep.error(f"document_id повторяется {count} раз: {doc_id}")
        if not doc_id:
            rep.error("есть строка без document_id")

    for row in docs:
        doc_id = row.get("document_id") or "<без id>"

        for field in REQUIRED_DOC_FIELDS:
            if not (row.get(field) or "").strip():
                rep.error(f"{doc_id}: пустое обязательное поле {field}")

        origin = (row.get("origin_class") or "").strip()
        if origin and origin not in ORIGIN_CLASSES:
            rep.error(f"{doc_id}: неизвестный origin_class '{origin}'")

        if origin == "A":
            for field in REQUIRED_AI_FIELDS:
                if not (row.get(field) or "").strip():
                    rep.error(f"{doc_id}: машинный текст без поля {field}")

        if origin == "H" and (row.get("consent_or_public_basis") or "").strip() == "":
            rep.error(f"{doc_id}: человеческий текст без основания использования")

        path_value = (row.get("file_path") or "").strip()
        if path_value and not (ROOT / path_value).exists():
            rep.error(f"{doc_id}: файл не найден — {path_value}")

        words = (row.get("word_count") or "").strip()
        if words.isdigit() and int(words) < 100:
            rep.warn(f"{doc_id}: {words} слов — короче порога надёжной оценки")

    if briefs:
        known_briefs = {row.get("brief_id") for row in briefs}
        for row in docs:
            brief = (row.get("brief_id") or "").strip()
            if brief and brief not in known_briefs:
                rep.error(f"{row.get('document_id')}: brief_id {brief} нет в реестре заданий")

    if prompts:
        known_prompts = {row.get("prompt_id") for row in prompts}
        for row in docs:
            prompt = (row.get("prompt_id") or "").strip()
            if prompt and prompt not in known_prompts:
                rep.error(f"{row.get('document_id')}: prompt_id {prompt} нет в реестре промптов")


def check_leakage_groups(docs, rep):
    """Один автор, тема или семейство версий не должны раздваиваться по группам утечки."""
    if not docs:
        return

    family_to_groups = defaultdict(set)
    for row in docs:
        family = (row.get("revision_family_id") or "").strip()
        group = (row.get("leakage_group") or "").strip()
        if family and group:
            family_to_groups[family].add(group)

    for family, groups in family_to_groups.items():
        if len(groups) > 1:
            rep.error(
                f"revision_family {family} размечено в разные leakage_group: {sorted(groups)}"
            )


def check_coverage(docs, rep):
    """Заполненность ячеек «задание × модель × промпт»."""
    ai_docs = [row for row in docs or [] if (row.get("origin_class") or "").strip() == "A"]
    if not ai_docs:
        return

    cells = Counter(
        (
            (row.get("brief_id") or "").strip(),
            (row.get("model_provider") or "").strip(),
            (row.get("prompt_condition") or "").strip(),
        )
        for row in ai_docs
    )
    rep.note(f"машинных текстов: {len(ai_docs)} в {len(cells)} ячейках")

    thin = [cell for cell, count in cells.items() if count < 2]
    if thin:
        rep.warn(f"ячеек с одним повтором: {len(thin)} (план — не менее двух)")

    by_origin = Counter((row.get("origin_class") or "").strip() for row in docs)
    rep.note("состав по классам: " + ", ".join(f"{k}={v}" for k, v in sorted(by_origin.items())))


def check_hashes(docs, hashes, verify, rep):
    if not docs:
        return
    if hashes is None:
        rep.error(f"нет файла {HASHES.relative_to(ROOT)}")
        return

    recorded = {row.get("document_id"): row.get("sha256") for row in hashes}
    for row in docs:
        doc_id = row.get("document_id")
        if doc_id not in recorded:
            rep.error(f"{doc_id}: нет записи в hashes.csv")
            continue
        if not verify:
            continue
        path = ROOT / (row.get("file_path") or "")
        if not path.exists():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != recorded[doc_id]:
            rep.error(f"{doc_id}: хеш файла не совпадает с записанным")


def check_scripts(docs, rep):
    """Язык текста и посторонние письменности.

    Проверка добавлена 2026-07-25 после того, как два англоязычных документа
    прожили в корпусе до расчёта признаков, а машинный текст со сбоем
    генерации — до расчёта лексики. Пороги диагностические: они отделяют
    порчу от греческих букв в формулах научных статей, где посторонние знаки
    входят в содержание.
    """
    for row in docs or []:
        path = ROOT / (row.get("file_path") or "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        letters = [char for char in text if char.isalpha()]
        if not letters:
            rep.error(f"{row.get('document_id')}: в файле нет букв")
            continue
        scripts = Counter()
        for char in letters:
            try:
                scripts[unicodedata.name(char).split()[0]] += 1
            except ValueError:
                scripts["UNKNOWN"] += 1
        cyrillic = scripts.get("CYRILLIC", 0) / len(letters)
        foreign = sum(
            count for name, count in scripts.items() if name not in ("CYRILLIC", "LATIN")
        ) / len(letters)
        if cyrillic < 0.5:
            rep.error(f"{row.get('document_id')}: доля кириллицы {cyrillic:.3f} — текст не на русском")
        elif foreign > 0.05:
            rep.warn(
                f"{row.get('document_id')}: посторонних письменностей {foreign:.1%} "
                f"({', '.join(f'{k} {v}' for k, v in scripts.most_common(3) if k not in ('CYRILLIC', 'LATIN'))})"
            )


def check_exclusions(docs, exclusions, rep):
    if not exclusions:
        return
    excluded = {row.get("document_id") for row in exclusions}
    live = {row.get("document_id") for row in docs or []}
    both = excluded & live
    if both:
        rep.error(f"документы одновременно в реестре и в логе исключений: {sorted(both)}")


def check_people(docs, people, rep):
    if not people or not docs:
        return
    known = {row.get("person_id") for row in people}
    for row in docs:
        if (row.get("origin_class") or "").strip() != "H":
            continue
        author = (row.get("author_or_model_id") or "").strip()
        if author and author not in known:
            rep.error(f"{row.get('document_id')}: автор {author} не найден в people-registry")

    counts = Counter(
        (row.get("author_or_model_id") or "").strip()
        for row in docs
        if (row.get("origin_class") or "").strip() == "H"
    )
    for author, count in counts.items():
        if count > 5:
            rep.warn(f"автор {author}: {count} текстов — план ограничивает 3–5")

    authors = len([a for a in counts if a])
    if authors and authors < 30:
        rep.warn(f"авторов человеческих текстов: {authors} — план требует 30+")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hashes", action="store_true", help="пересчитать SHA-256 файлов")
    args = parser.parse_args()

    print(f"Проверка реестров: {ROOT}")
    print()

    docs = read_rows(DOCUMENTS)
    briefs = read_rows(BRIEFS)
    prompts = read_rows(PROMPTS)
    hashes = read_rows(HASHES)
    people = read_rows(PEOPLE)
    exclusions = read_rows(EXCLUSIONS)

    rep = Report()
    check_documents(docs, briefs, prompts, rep)
    check_leakage_groups(docs, rep)
    check_coverage(docs, rep)
    check_hashes(docs, hashes, args.hashes, rep)
    check_scripts(docs, rep)
    check_exclusions(docs, exclusions, rep)
    check_people(docs, people, rep)

    return rep.show()


if __name__ == "__main__":
    sys.exit(main())
