#!/usr/bin/env python3
"""Сбор судебных решений с sudact.ru — второй источник страты регламентированности 3.

Читает карту сайта (части .xml.gz), качает страницы решений, извлекает текст и
дату вынесения, фильтрует по дате и длине, снимает дубли, пишет в реестр.

Судебные акты — официальные документы государственных органов и объектами
авторского права не являются, поэтому корпус можно публиковать целиком.

robots.txt sudact.ru запрещает /doc/print/, /doc/send/, /doc/save/ и ajax-пути —
они не используются, качаются только обычные страницы решений.

    python 09-tools/collect_sudact.py --limit 60 --dry-run
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
OUT_DIR = ROOT / "04-corpus" / "raw-human" / "sudact"
CACHE_DIR = ROOT / "04-corpus" / "_archives" / "sudact_cache"

SITEMAP = "https://sudact.ru/sitemap.xml"
CUTOFF = datetime(2022, 1, 1)
MIN_WORDS = 700
MAX_WORDS = 5000
UA = "Mozilla/5.0 (research corpus collection; contact staurus86@gmail.com)"

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def fetch(url, delay, binary=False, attempts=2):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            time.sleep(delay)
            return data if binary else data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == attempts:
                print(f"    не скачано: {url} — {exc}")
                return None
            time.sleep(delay * 3)
    return None


def iter_sitemap_urls(delay, max_parts=1):
    index = fetch(SITEMAP, delay)
    if not index:
        return
    parts = re.findall(r"<loc>([^<]+)</loc>", index)[:max_parts]
    for part in parts:
        blob = fetch(part.strip(), delay, binary=True)
        if not blob:
            continue
        text = gzip.open(io.BytesIO(blob), "rt", encoding="utf-8", errors="replace").read()
        for loc in re.findall(r"<loc>([^<]+)</loc>", text):
            yield loc.strip()


def cached_page(url, delay):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".html")
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    html = fetch(url, delay)
    if html:
        path.write_text(html, encoding="utf-8")
    return html


def parse_decision(html):
    """Текст решения и дата вынесения."""
    soup = BeautifulSoup(html, "lxml")

    for noise in soup.select("script, style, nav, footer, aside, .breadcrumbs, .b-share, form"):
        noise.decompose()

    body = None
    for selector in [".doc_text", "#document", ".b-doc", "article", ".content"]:
        found = soup.select(selector)
        if found:
            body = max(found, key=lambda node: len(node.get_text(" ", strip=True)))
            break
    if body is None:
        body = soup.body or soup

    blocks = []
    for node in body.find_all(["p", "div", "li"], recursive=True):
        if node.find(["p", "div"], recursive=False):
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text.split()) >= 4:
            blocks.append(text)

    deduped = []
    for block in blocks:
        if not deduped or block != deduped[-1]:
            deduped.append(block)
    text = "\n\n".join(deduped)

    title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
    # Дата вынесения стоит в заголовке страницы по стандартной форме
    # «Решение от 4 марта 2025 г. по делу № 2-2291/2025». Поиск только по телу
    # документа давал ноль извлечённых дат на всех 25 проверенных страницах:
    # тело начинается с реквизитов суда, а дата уходит за границу окна.
    head = title_text + "\n" + text[:3000]
    date = None
    match = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s*(?:год|г)", head, re.I)
    if match:
        day, month_word, year = match.groups()
        number = MONTHS.get(month_word.lower())
        if number:
            try:
                date = datetime(int(year), number, int(day))
            except ValueError:
                date = None
    if date is None:
        match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", head)
        if match:
            day, month, year = (int(part) for part in match.groups())
            try:
                date = datetime(year, month, day)
            except ValueError:
                date = None

    title = soup.title.get_text(strip=True)[:200] if soup.title else None
    court = None
    match = re.search(r"([А-ЯЁ][^\n]{5,80}(?:суд|СУД)[^\n]{0,40})", head)
    if match:
        court = match.group(1).strip()[:120]

    return {"date": date, "text": text, "word_count": len(text.split()), "title": title, "court": court}


def shingles(text, size=5):
    words = re.sub(r"\s+", " ", text.lower()).split()
    return {
        hashlib.md5(" ".join(words[i : i + size]).encode("utf-8")).digest()[:8]
        for i in range(max(1, len(words) - size + 1))
    }


def is_duplicate(text, seen, threshold=0.75):
    """Порог ниже обычного: решения по типовым делам различаются только реквизитами."""
    current = shingles(text)
    for other in seen:
        union = len(current | other)
        if union and len(current & other) / union >= threshold:
            return True, None
    return False, current


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

    for index, item in enumerate(sorted(items, key=lambda i: i["date"]), start=1):
        doc_id = f"human_legal_sudact_{index:04d}"
        if doc_id in taken:
            continue

        text_path = OUT_DIR / f"{doc_id}.txt"
        text_path.write_text(item["text"], encoding="utf-8")
        (OUT_DIR / f"{doc_id}.json").write_text(
            json.dumps(
                {
                    "document_id": doc_id,
                    "url": item["url"],
                    "title": item["title"],
                    "court": item["court"],
                    "publication_date": item["date"].strftime("%Y-%m-%d"),
                    "date_basis": "дата вынесения в тексте решения",
                    "regulation_level": 3,
                    "regulation_basis": "судебный акт: структура и формулировки предписаны процессуальным законом",
                    "license_note": "официальный документ государственного органа, объектом авторского права не является",
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
                "genre": "legal",
                "origin_class": "H",
                "author_or_model_id": item["court"] or "court",
                "human_publication_date": item["date"].strftime("%Y-%m-%d"),
                "source_platform": "sudact",
                "word_count": item["word_count"],
                "char_count": len(item["text"]),
                "preprocessing_profile": "prose",
                "license_status": "официальный документ, вне авторского права",
                "consent_or_public_basis": "открытая публикация судебных актов",
                "leakage_group": "sudact",
                "revision_family_id": doc_id,
                "dedup_cluster_id": hashlib.sha256(
                    re.sub(r"\s+", " ", item["text"].lower()).encode("utf-8")
                ).hexdigest()[:16],
                "split_group_author": item["court"] or "court",
                "split_group_source": "sudact",
                "status": "collected",
                "notes": "regulation_level=3; судебный акт, формулировки предписаны процессуальным законом",
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
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--max-fetch", type=int, default=250)
    parser.add_argument("--step", type=int, default=37, help="шаг по карте, чтобы брать разные суды")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected, seen, stats, fetched = [], [], {}, 0

    def note(reason):
        stats[reason] = stats.get(reason, 0) + 1

    for position, url in enumerate(iter_sitemap_urls(args.delay)):
        if len(selected) >= args.limit or fetched >= args.max_fetch:
            break
        if position % args.step:
            continue
        if "/doc/" not in url:
            continue

        html = cached_page(url, args.delay)
        fetched += 1
        if not html:
            note("страница не скачана")
            continue

        item = parse_decision(html)
        if item["date"] is None:
            note("нет даты вынесения")
            continue
        if item["date"] >= CUTOFF:
            note("вынесено в 2022 или позже")
            continue
        if item["word_count"] < MIN_WORDS:
            note("короче 700 слов")
            continue
        if item["word_count"] > MAX_WORDS:
            note("длиннее 5000 слов")
            continue

        duplicate, sh = is_duplicate(item["text"], seen)
        if duplicate:
            note("типовое решение, дубль")
            continue
        seen.append(sh)

        item["url"] = url
        selected.append(item)
        print(f"  [{len(selected):>3}] {item['date']:%Y-%m-%d}  {item['word_count']:>5} сл.  {(item['court'] or '')[:52]}")

    print(f"\nСкачано страниц: {fetched}")
    for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  отсеяно, {reason}: {count}")
    print(f"Отобрано: {len(selected)}")

    if args.dry_run:
        print("Сухой прогон: ничего не записано")
        return
    if not selected:
        return

    print(f"Записано в реестр: {write_documents(selected, read_registry_fields())}")


if __name__ == "__main__":
    main()
