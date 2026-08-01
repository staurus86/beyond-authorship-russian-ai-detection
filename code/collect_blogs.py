#!/usr/bin/env python3
"""Сбор статей отраслевых блогов по карте сайта — страта регламентированности 2.

Читает sitemap.xml, качает страницы, извлекает текст и дату публикации из
разметки, фильтрует по дате и длине, снимает дубли, пишет в реестр.

Тексты чужие: в открытый корпус не публикуются, идут только метаданные,
признаки и ссылки на источник.

    python 09-tools/collect_blogs.py --sitemap https://example.ru/post-sitemap.xml --key example --limit 40 --dry-run
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# Извлечение текста и даты общее с обработкой сохранённых страниц.
_spec = importlib.util.spec_from_file_location("extract_html", ROOT / "09-tools" / "extract_html.py")
_extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_extract)

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
OUT_ROOT = ROOT / "04-corpus" / "raw-human"
CACHE_ROOT = ROOT / "04-corpus" / "_archives" / "blog_cache"

CUTOFF = datetime(2022, 1, 1)
MIN_WORDS = 700
MAX_WORDS = 5000
UA = "Mozilla/5.0 (research corpus collection; contact staurus86@gmail.com)"


def fetch(url, delay, attempts=2):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read()
            time.sleep(delay)
            return data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == attempts:
                print(f"    не скачано: {url} — {exc}")
                return None
            time.sleep(delay * 2)
    return None


def read_sitemap(url, delay):
    """Возвращает список (loc, lastmod). Понимает CDATA и вложенные индексы."""
    xml = fetch(url, delay)
    if not xml:
        return []

    if "<sitemapindex" in xml:
        children = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", xml, re.S)
        entries = []
        for child in children:
            child = child.strip()
            # Blogger отдаёт части индекса как sitemap.xml?page=2 — расширение не проверяем.
            if "sitemap" in child.lower() or child.endswith(".xml"):
                entries.extend(read_sitemap(child, delay))
        return entries

    entries = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", block, re.S)
        lastmod = re.search(r"<lastmod>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</lastmod>", block, re.S)
        if loc:
            entries.append((loc.group(1).strip(), lastmod.group(1).strip() if lastmod else None))
    return entries


def cached_page(url, key, delay):
    cache = CACHE_ROOT / key
    cache.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".html"
    path = cache / name
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    html = fetch(url, delay)
    if html:
        path.write_text(html, encoding="utf-8")
    return html


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


def read_registry_fields():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def existing_ids():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return {row.get("document_id") for row in csv.DictReader(fh)}


def write_documents(items, args, fields):
    target = OUT_ROOT / args.key
    target.mkdir(parents=True, exist_ok=True)
    taken = existing_ids()
    stamp = datetime.now().strftime("%Y-%m-%d")
    registry_rows, hash_rows = [], []

    for index, item in enumerate(sorted(items, key=lambda i: i["date"]), start=1):
        doc_id = f"human_{args.genre}_{args.key}_{index:04d}"
        if doc_id in taken:
            continue

        text_path = target / f"{doc_id}.txt"
        text_path.write_text(item["text"], encoding="utf-8")
        (target / f"{doc_id}.json").write_text(
            json.dumps(
                {
                    "document_id": doc_id,
                    "url": item["url"],
                    "title": item["title"],
                    "author": item["author"] or args.author,
                    "publication_date": item["date"].strftime("%Y-%m-%d"),
                    "date_basis": item["date_basis"],
                    "date_second_confirmation": item.get("lastmod"),
                    "regulation_level": 2,
                    "regulation_basis": args.regulation_basis,
                    "license_note": "права принадлежат автору сайта; полный текст в открытый корпус не публикуется",
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
                "genre": args.genre,
                "origin_class": "H",
                "author_or_model_id": item["author"] or args.author,
                "human_publication_date": item["date"].strftime("%Y-%m-%d"),
                "source_platform": args.key,
                "word_count": item["word_count"],
                "char_count": len(item["text"]),
                "preprocessing_profile": "prose",
                "license_status": "права автора сайта, публикация полного текста запрещена",
                "consent_or_public_basis": "исследовательское использование, публикуются метаданные и признаки",
                "leakage_group": args.key,
                "revision_family_id": doc_id,
                "dedup_cluster_id": hashlib.sha256(
                    re.sub(r"\s+", " ", item["text"].lower()).encode("utf-8")
                ).hexdigest()[:16],
                "split_group_author": item["author"] or args.author,
                "split_group_source": args.key,
                "status": "collected",
                "notes": f"regulation_level=2; {args.regulation_basis}; дата: {item['date_basis']}",
            }
        )
        registry_rows.append(row)
        hash_rows.append(
            {
                "document_id": doc_id,
                "file_path": rel_path,
                "sha256": digest,
                "bytes": text_path.stat().st_size,
                "recorded_at": stamp,
            }
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
    parser.add_argument("--sitemap", required=True)
    parser.add_argument("--key", required=True, help="ключ источника, он же имя папки")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--genre", default="seo")
    parser.add_argument("--author", default="", help="автор блога, если не найден в разметке")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--max-fetch", type=int, default=250, help="сколько страниц максимум качать")
    parser.add_argument(
        "--regulation-basis",
        default="отраслевой блог с устойчивым редакционным шаблоном: рубрикация, врезки, единый формат материала",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = read_sitemap(args.sitemap, args.delay)
    print(f"В карте сайта адресов: {len(entries)}")
    if not entries:
        raise SystemExit("карта сайта пуста или недоступна")

    # Свежие правки не значат свежую публикацию, но старый lastmod — надёжный признак старой статьи.
    entries.sort(key=lambda e: (e[1] or ""))

    selected, seen_shingles = [], []
    stats, fetched = {}, 0

    def note(reason):
        stats[reason] = stats.get(reason, 0) + 1

    # Служебные адреса: архивы, метки, поиск, пагинация. Статей там нет.
    junk = re.compile(r"(_archive\.html|/search|[?&]updated-|/label/|/page/\d+|/tag/|/category/|/author/)", re.I)

    for url, lastmod in entries:
        if len(selected) >= args.limit or fetched >= args.max_fetch:
            break
        if junk.search(url):
            note("служебный адрес")
            continue
        if url.rstrip("/").endswith(args.sitemap.split("//")[1].split("/")[0].rstrip("/")):
            continue  # главная страница

        html = cached_page(url, args.key, args.delay)
        fetched += 1
        if not html:
            note("страница не скачана")
            continue

        item = _parse_with_extract(html, url)

        if item["date"] is None:
            note("нет даты в разметке")
            continue
        if item["date"] >= CUTOFF:
            note("опубликовано в 2022 или позже")
            continue
        if item["word_count"] < MIN_WORDS:
            note("короче 700 слов")
            continue
        if item["word_count"] > MAX_WORDS:
            note("длиннее 5000 слов")
            continue

        duplicate, sh = is_duplicate(item["text"], seen_shingles)
        if duplicate:
            note("дубль")
            continue
        seen_shingles.append(sh)

        item["url"] = url
        item["lastmod"] = lastmod
        selected.append(item)
        print(f"  [{len(selected):>3}] {item['date']:%Y-%m-%d}  {item['word_count']:>5} сл.  {(item['title'] or '')[:58]}")

    print(f"\nСкачано страниц: {fetched}")
    for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  отсеяно, {reason}: {count}")
    print(f"Отобрано: {len(selected)}")

    if args.dry_run:
        print("Сухой прогон: ничего не записано")
        return
    if not selected:
        print("Записывать нечего")
        return

    written = write_documents(selected, args, read_registry_fields())
    print(f"Записано в реестр: {written}")


def looks_like_listing(soup):
    """Лента постов и страницы рубрик.

    По датам судить нельзя: в комментариях их бывают десятки, и обычная статья
    ошибочно уходит в отсев. Считаем только заголовки материалов.
    """
    if len(soup.select("article")) > 2:
        return True
    if len(soup.select("h1")) > 2:
        return True
    return False


def _parse_with_extract(html, url=None):
    """Разбор страницы функциями extract_html без обращения к диску."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    if looks_like_listing(soup):
        return {"date": None, "date_basis": "листинг, не статья", "title": None,
                "author": None, "text": "", "word_count": 0}
    date, basis = _extract.find_date(soup)

    # Последний шанс: дата в адресе страницы, /2016/05/12/slug.
    if date is None and url:
        date = _extract.date_from_url(url)
        basis = "дата в адресе страницы" if date else basis
    title = _extract.find_title(soup)
    author = _extract.find_author(soup)
    text = _extract.extract_text(soup)
    return {
        "date": date,
        "date_basis": basis,
        "title": title,
        "author": author,
        "text": text,
        "word_count": len(text.split()),
    }


if __name__ == "__main__":
    main()
