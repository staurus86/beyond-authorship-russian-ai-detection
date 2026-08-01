#!/usr/bin/env python3
"""Сбор переводных текстов из Russian Learner Translator Corpus в страту hard-human.

RusLTC — студенческие переводы английских текстов на русский, кафедра перевода
ТюмГУ и партнёры, 2009–2019. Для нас это переводной дискурс: синтаксические
кальки, буквализмы, следование чужой структуре. Ровно тот материал, на котором
детекторы машинного текста ошибаются, — а `FPR на hard-human` числится
первичной метрикой в `07-analysis/metrics-spec.md`.

**Главное ограничение отбора.** Один английский оригинал переводят до двадцати
студентов. Такие переводы делят содержание, факты и порядок изложения, то есть
независимыми наблюдениями не являются. Поэтому из каждого оригинала берётся
ровно один перевод: 104 оригинала в пуле длинных текстов дают потолок в 104
документа без единой пары с общим содержанием.

Лицензия корпуса — CC BY-SA 4.0, тексты можно публиковать в открытом корпусе
с указанием источника и сохранением лицензии.

Подтверждение даты двумя способами: год выполнения из метаданных файла
(`.head.txt`) и дата публикации самого корпуса — архив сформирован и
опубликован до 2022 года, описан в работах 2014–2021.

    python 09-tools/collect_rusltc.py --dry-run
    python 09-tools/collect_rusltc.py --limit 60
"""

import argparse
import csv
import hashlib
import random
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04-corpus" / "_archives" / "rusltc" / "rltc"
OUT = ROOT / "04-corpus" / "external-hard-human" / "rusltc"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"

SEED = 20260724
MIN_WORDS = 700
MAX_WORDS = 5000
NAME = re.compile(r"^RU_(?P<section>\d+)_(?P<source>\d+)(?:_(?P<variant>\d+))?$")

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def read_head(stem):
    """Метаданные перевода: год, жанр, режим, вуз.

    Порядок полей в .head.txt не фиксирован между секциями корпуса, поэтому
    год ищется как любое четырёхзначное число, а вуз — как последняя
    непустая строка. Остальное складывается в примечание целиком.
    """
    path = SRC / f"{stem}.head.txt"
    if not path.exists():
        return {"year": "", "institution": "", "raw": ""}
    lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    years = [l for l in lines if re.fullmatch(r"(19|20)\d{2}", l)]
    return {
        "year": years[0] if years else "",
        "institution": lines[-1] if lines else "",
        "raw": " | ".join(lines),
    }


def candidates():
    pool = []
    for path in sorted(SRC.glob("RU_*.txt")):
        if path.name.endswith(".head.txt"):
            continue
        match = NAME.match(path.stem)
        if not match:
            continue
        # RU_{секция}_{оригинал} без третьего номера — это русский исходник для
        # перевода на английский, а не студенческий перевод. В head у таких
        # файлов стоит Source. Попади они в набор, страта переводов получила бы
        # обычные русские тексты, написанные носителями и без калек
        if not match.group("variant"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        words = len(text.split())
        if not MIN_WORDS <= words <= MAX_WORDS:
            continue
        head = read_head(path.stem)
        if head["year"] and int(head["year"]) >= 2022:
            continue
        if head["institution"] == "Source":
            continue
        # без года дату не подтвердить вторым способом — требование §3 спеки
        if not head["year"]:
            continue
        pool.append({
            "path": path, "stem": path.stem, "words": words, "text": text,
            "source_id": match.group("source"), "section": match.group("section"),
            **head,
        })
    return pool


def pick_one_per_source(pool, limit):
    """По одному переводу с оригинала, порядок и выбор — сидом."""
    rng = random.Random(SEED)
    by_source = defaultdict(list)
    for item in pool:
        by_source[item["source_id"]].append(item)
    chosen = [rng.choice(sorted(v, key=lambda x: x["stem"])) for _, v in sorted(by_source.items())]
    rng.shuffle(chosen)
    return chosen[:limit] if limit else chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=60, help="сколько документов взять, 0 — все")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SRC.exists():
        raise SystemExit(f"нет распакованного корпуса: {SRC}\nскачать rltc.tar.gz и распаковать в _archives/rusltc/")

    pool = candidates()
    picked = pick_one_per_source(pool, args.limit)

    print(f"Кандидатов {MIN_WORDS}–{MAX_WORDS} слов: {len(pool)}")
    print(f"Уникальных оригиналов: {len({p['source_id'] for p in pool})}")
    print(f"Отобрано (по одному с оригинала): {len(picked)}\n")

    if args.dry_run:
        for item in picked[:10]:
            print(f"  {item['stem']}: {item['words']} слов, {item['year']}, {item['institution']}")
        if len(picked) > 10:
            print(f"  … ещё {len(picked) - 10}")
        print("\nСухой прогон: ничего не записано")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    fields = next(csv.reader(REGISTRY.open(encoding="utf-8-sig")))
    taken = {r["document_id"] for r in csv.DictReader(REGISTRY.open(encoding="utf-8-sig"))}
    stamp = datetime.now().strftime("%Y-%m-%d")

    reg_rows, hash_rows = [], []
    for index, item in enumerate(sorted(picked, key=lambda x: x["stem"]), start=1):
        doc_id = f"human_hard_rusltc_{index:04d}"
        if doc_id in taken:
            continue
        target = OUT / f"{doc_id}.txt"
        target.write_text(item["text"] + "\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()

        row = {field: "" for field in fields}
        row.update({
            "document_id": doc_id,
            "file_path": target.relative_to(ROOT).as_posix(),
            "sha256": digest,
            "language": "ru",
            "genre": "translation",
            "origin_class": "H",
            "author_or_model_id": f"студент-переводчик {item['institution'] or 'вуз не указан'}",
            "human_publication_date": f"{item['year']}-01-01" if item["year"] else "",
            "source_platform": "rusltc",
            "word_count": item["words"],
            "char_count": len(item["text"]),
            "preprocessing_profile": "prose",
            "license_status": "CC BY-SA 4.0",
            "consent_or_public_basis": "открытая публикация корпуса RusLTC под CC BY-SA 4.0",
            # оригинал — группа утечки: разные студенты переводят один текст
            "leakage_group": f"rusltc:source-{item['source_id']}",
            "revision_family_id": doc_id,
            "dedup_cluster_id": hashlib.sha256(
                re.sub(r"\s+", " ", item["text"].lower()).encode("utf-8")
            ).hexdigest()[:16],
            "split_group_author": f"rusltc:{item['institution'] or 'unknown'}",
            "split_group_source": f"rusltc:source-{item['source_id']}",
            "split_group_topic": f"rusltc:source-{item['source_id']}",
            "status": "collected",
            "notes": (
                f"hard_human=yes; subset=translation; исходный файл {item['stem']}; "
                f"перевод с английского, оригинал общий с другими переводами того же source-id, "
                f"взят один; метаданные RusLTC: {item['raw']}; "
                f"дата подтверждена годом выполнения и датой публикации корпуса до 2022"
            ),
        })
        reg_rows.append(row)
        hash_rows.append({
            "document_id": doc_id, "file_path": row["file_path"], "sha256": digest,
            "bytes": target.stat().st_size, "recorded_at": stamp,
        })

    with REGISTRY.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fields).writerows(reg_rows)
    with HASHES.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(
            fh, fieldnames=["document_id", "file_path", "sha256", "bytes", "recorded_at"]
        ).writerows(hash_rows)

    print(f"Записано в реестр: {len(reg_rows)}")
    print(f"Файлы: {OUT.relative_to(ROOT)}")
    print("Дальше: python 09-tools/validate_registry.py")


if __name__ == "__main__":
    main()
