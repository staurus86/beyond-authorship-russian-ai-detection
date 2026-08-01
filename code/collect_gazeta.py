#!/usr/bin/env python3
"""Сбор новостных текстов Gazeta.ru — третий источник страты 1.

Источник: датасет IlyaGusev/gazeta на HuggingFace (parquet, test+validation
сплиты, 13162 записей, 2020-2021). Карточка датасета через обычный API
huggingface.co недоступна анонимно, поэтому паркеты берутся по прямым ссылкам
`datasets-server.huggingface.co/parquet?dataset=...`. Train-сплит (61 тыс.
записей, 240+ Мб) не используется — test+validation с запасом хватает квоты.

Проверка 2026-07-24 (archive-sources-spec.md): 21% записей в 700-5000 слов,
даты корректные (поле `date`, полный timestamp), все раньше 2022-01-01.

Требует: pyarrow (уже установлен).

    python 09-tools/collect_gazeta.py --dry-run
    python 09-tools/collect_gazeta.py
"""

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
OUT_DIR = ROOT / "04-corpus" / "raw-human" / "gazeta"
PARQUET_FILES = [
    ROOT / "04-corpus" / "_archives" / "gazeta" / "test-0000.parquet",
    ROOT / "04-corpus" / "_archives" / "gazeta" / "validation-0000.parquet",
]

CUTOFF = datetime(2022, 1, 1)
MIN_WORDS = 700
MAX_WORDS = 5000
QUOTA = 60
SOURCE_KEY = "gazeta"
GENRE_BLOCK = "news"
REGULATION_LEVEL = 1
REGULATION_BASIS = "интернет-издание с редакционной политикой, формат новостной заметки и аналитической статьи"
LICENSE_NOTE = (
    "gazeta.ru через датасет IlyaGusev/gazeta (HuggingFace), "
    "только некоммерческое использование, права на тексты принадлежат gazeta.ru"
)


def cyrillic_share(text):
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё") / len(letters)


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def shingles(text, size=5):
    words = re.sub(r"\s+", " ", text.lower()).split()
    return {
        hashlib.md5(" ".join(words[i : i + size]).encode("utf-8")).digest()[:8]
        for i in range(max(1, len(words) - size + 1))
    }


def is_duplicate(text, seen, threshold=0.8):
    current = shingles(text)
    for other in seen:
        union = len(current | other)
        if union and len(current & other) / union >= threshold:
            return True, None
    return False, current


def iter_records():
    for path in PARQUET_FILES:
        if not path.exists():
            raise SystemExit(
                f"нет файла {path}\n"
                f"скачайте паркет: curl -sSL -o {path} "
                f"\"https://huggingface.co/datasets/IlyaGusev/gazeta/resolve/refs%2Fconvert%2Fparquet/default/"
                f"{'test' if 'test' in path.name else 'validation'}/0000.parquet\""
            )
        table = pq.read_table(path, columns=["text", "title", "date", "url"])
        for row in table.to_pylist():
            yield row


def read_registry_fields():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def existing_ids():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return {row.get("document_id") for row in csv.DictReader(fh)}


def write_documents(items, fields):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    taken = existing_ids()
    stamp = datetime.now().strftime("%Y-%m-%d")
    registry_rows, hash_rows = [], []

    for index, item in enumerate(items, start=1):
        doc_id = f"human_{GENRE_BLOCK}_{SOURCE_KEY}_{index:04d}"
        if doc_id in taken:
            continue

        text_path = OUT_DIR / f"{doc_id}.txt"
        text_path.write_text(item["text"], encoding="utf-8")
        (OUT_DIR / f"{doc_id}.json").write_text(
            json.dumps(
                {
                    "document_id": doc_id,
                    "source_key": SOURCE_KEY,
                    "title": item["title"],
                    "url": item["url"],
                    "publication_date": item["date_parsed"].strftime("%Y-%m-%d"),
                    "regulation_level": REGULATION_LEVEL,
                    "regulation_basis": REGULATION_BASIS,
                    "license_note": LICENSE_NOTE,
                    "collected_at": stamp,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        rel_path = text_path.relative_to(ROOT).as_posix()
        digest = hashlib.sha256(text_path.read_bytes()).hexdigest()

        row = {field: "" for field in fields}
        row.update(
            {
                "document_id": doc_id,
                "file_path": rel_path,
                "sha256": digest,
                "language": "ru",
                "genre": GENRE_BLOCK,
                "origin_class": "H",
                "author_or_model_id": f"unknown@{SOURCE_KEY}",
                "human_publication_date": item["date_parsed"].strftime("%Y-%m-%d"),
                "source_platform": SOURCE_KEY,
                "word_count": item["word_count"],
                "char_count": len(item["text"]),
                "preprocessing_profile": "prose",
                "license_status": LICENSE_NOTE,
                "consent_or_public_basis": "исследовательское использование, набор HuggingFace IlyaGusev/gazeta",
                "leakage_group": SOURCE_KEY,
                "revision_family_id": doc_id,
                "dedup_cluster_id": hashlib.sha256(
                    re.sub(r"\s+", " ", item["text"].lower()).encode("utf-8")
                ).hexdigest()[:16],
                "split_group_author": f"unknown@{SOURCE_KEY}",
                "split_group_source": SOURCE_KEY,
                "status": "collected",
                "notes": f"regulation_level={REGULATION_LEVEL}; {REGULATION_BASIS}",
            }
        )
        registry_rows.append(row)
        hash_rows.append(
            {"document_id": doc_id, "file_path": rel_path, "sha256": digest,
             "bytes": text_path.stat().st_size, "recorded_at": stamp}
        )

    with REGISTRY.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fields).writerows(registry_rows)
    with HASHES.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(
            fh, fieldnames=["document_id", "file_path", "sha256", "bytes", "recorded_at"]
        ).writerows(hash_rows)

    return len(registry_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quota", type=int, default=QUOTA)
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    candidates, seen, stats, scanned = [], [], {}, 0

    def note(reason):
        stats[reason] = stats.get(reason, 0) + 1

    for record in iter_records():
        scanned += 1
        text = (record.get("text") or "").strip()
        if not text:
            note("пустой текст")
            continue

        words = len(text.split())
        if words < MIN_WORDS:
            note("короче 700 слов")
            continue
        if words > MAX_WORDS:
            note("длиннее 5000 слов")
            continue

        if cyrillic_share(text) < 0.5:
            note("не русский")
            continue

        date = parse_date(record.get("date"))
        if date is None:
            note("дата не распознана")
            continue
        if date >= CUTOFF:
            note("издано в 2022 или позже")
            continue

        duplicate, sh = is_duplicate(text, seen)
        if duplicate:
            note("дубль")
            continue
        seen.append(sh)

        record["text"] = text
        record["word_count"] = words
        record["date_parsed"] = date
        candidates.append(record)

    print(f"Просмотрено записей: {scanned}")
    for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  отсеяно, {reason}: {count}")
    print(f"Прошло фильтры: {len(candidates)}")

    rng = random.Random(args.seed)
    rng.shuffle(candidates)
    selected = candidates[: args.quota]
    print(f"Отобрано: {len(selected)} из квоты {args.quota}")
    if len(selected) < args.quota:
        print("  ! квота не набрана")

    if args.dry_run:
        print("Сухой прогон: ничего не записано")
        return
    if not selected:
        return

    print(f"Записано в реестр: {write_documents(selected, read_registry_fields())}")


if __name__ == "__main__":
    main()
