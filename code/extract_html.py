#!/usr/bin/env python3
"""Извлечение текста и метаданных из сохранённых HTML-страниц (архив PI).

Берёт каталог с .html, вытаскивает основной текст, дату публикации, заголовок
и автора, фильтрует по дате и длине, пишет в реестр корпуса.

Дата ищется в трёх местах по порядку: meta[article:published_time],
JSON-LD datePublished, meta[shareaholic:article_published_time].
Страница без даты отбрасывается — дата подтверждается, а не угадывается.

Требует: pip install beautifulsoup4 lxml

    python 09-tools/extract_html.py --src 04-corpus/raw-human/_staurus_html --dry-run
    python 09-tools/extract_html.py --src 04-corpus/raw-human/_staurus_html --key author_archive
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
OUT_ROOT = ROOT / "04-corpus" / "raw-human"

CUTOFF = datetime(2022, 1, 1)
MIN_WORDS = 700
MAX_WORDS = 5000

# Блоки, которые не относятся к тексту статьи.
NOISE_SELECTORS = [
    "script", "style", "noscript", "nav", "footer", "aside", "form", "iframe",
    "#comments", ".comments-area", ".comment-respond", ".sidebar", ".widget",
    ".related", ".yarpp-related", ".navigation", ".breadcrumbs", ".share",
    ".social", ".post-navigation", ".author-box", ".advertisement",
]

CONTENT_SELECTORS = [
    "div.article-formatted-body", "#post-content-body",
    "article", "div.entry-content", "div.post-content", "div.td-post-content",
    "[itemprop='articleBody']", "div.post-body", "div.article-body",
    "div.post", "div.entry", "div.content", "main",
]

BLOCK_TAGS = ["p", "li", "h1", "h2", "h3", "h4", "h5", "blockquote", "pre", "td"]


def parse_iso(value):
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return None
    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def find_date(soup):
    """Возвращает (дата, чем подтверждена)."""
    meta = soup.select_one('meta[property="article:published_time"]')
    if meta and meta.get("content"):
        date = parse_iso(meta["content"])
        if date:
            return date, "meta article:published_time"

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
        if match:
            date = parse_iso(match.group(1))
            if date:
                return date, "JSON-LD datePublished"

    meta = soup.select_one('meta[name="shareaholic:article_published_time"]')
    if meta and meta.get("content"):
        date = parse_iso(meta["content"])
        if date:
            return date, "meta shareaholic:article_published_time"

    node = soup.select_one('time[datetime]')
    if node:
        date = parse_iso(node.get("datetime"))
        if date:
            return date, "time[datetime]"

    # Часть блогов не размечает дату вовсе — остаётся видимый текст в блоке даты.
    for element in soup.select('[class*="date" i], [class*="postdate" i], [class*="published" i], [class*="meta" i], [class*="time" i]'):
        date = parse_visible_date(element.get_text(" ", strip=True))
        if date:
            classes = ".".join(element.get("class") or ["?"])
            return date, f"видимый текст в .{classes}"

    # Дата просто в тексте страницы: «Опубликовано 15 марта 2016», «15.03.2016».
    head_text = visible_head(soup)
    date = parse_visible_date(head_text, scan_all=True)
    if date:
        return date, "видимый текст в начале страницы"

    return None, None


def visible_head(soup, limit=2500):
    """Верхняя часть видимого текста: заголовок и первые абзацы."""
    body = soup.body or soup
    return body.get_text(" ", strip=True)[:limit]


def date_from_url(url):
    """Адреса вида /2016/05/12/slug и /2016-05-12-slug."""
    if not url:
        return None
    match = re.search(r"/(19|20)(\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$)", url)
    if match:
        year = int(match.group(1) + match.group(2))
        month, day = int(match.group(3)), int(match.group(4))
        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    match = re.search(r"/(19|20)(\d{2})/(\d{1,2})/", url)
    if match:
        year = int(match.group(1) + match.group(2))
        month = int(match.group(3))
        try:
            return datetime(year, month, 1)
        except ValueError:
            return None

    return None


MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def parse_visible_date(text, scan_all=False):
    """Разбирает «12 января 2016» и «12.01.2016» из видимого текста.

    scan_all=False — короткий блок даты целиком.
    scan_all=True — длинный кусок страницы: берём первое полное совпадение.
    """
    if not text:
        return None
    if not scan_all and len(text) > 120:
        return None

    for match in re.finditer(r"(\d{1,2})\s+([А-Яа-яё]{3,10})\s+(\d{4})", text):
        day, month_word, year = match.groups()
        for stem, number in MONTHS.items():
            if month_word.lower().startswith(stem):
                candidate = _safe_date(int(year), number, int(day))
                if candidate:
                    return candidate

    for match in re.finditer(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text):
        day, month, year = (int(part) for part in match.groups())
        candidate = _safe_date(year, month, day)
        if candidate:
            return candidate

    for match in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text):
        year, month, day = (int(part) for part in match.groups())
        candidate = _safe_date(year, month, day)
        if candidate:
            return candidate

    return None


def _safe_date(year, month, day):
    """Отсекает мусорные совпадения: телефоны, номера версий, будущие даты."""
    if not 1995 <= year <= datetime.now().year:
        return None
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def find_title(soup):
    meta = soup.select_one('meta[property="og:title"]')
    if meta and meta.get("content"):
        return meta["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.select_one("h1")
    return h1.get_text(" ", strip=True) if h1 else None


def find_author(soup):
    meta = soup.select_one('meta[name="author"]')
    if meta and meta.get("content"):
        return meta["content"].strip()
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        match = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', raw)
        if match:
            return match.group(1).strip()
    node = soup.select_one('[rel="author"], .author-name, .entry-author')
    return node.get_text(" ", strip=True) if node else None


def extract_text(soup, leaf_only=False):
    """Самый крупный содержательный контейнер, очищенный от служебных блоков.

    `leaf_only=True` — правило коррекции prep-v5 от 2026-07-29, амендмент
    `02-preregistration/amendment-prep-v5-data-quality.md` §4. Без него
    `find_all(BLOCK_TAGS)` возвращает и контейнер, и вложенные в него блоки, а
    схлопывание соседних одинаковых строк этого не ловит: родительский `li` даёт
    строку «абзац1 абзац2», следом идут «абзац1» и «абзац2» — как строки они
    различны, а предложения внутри задваиваются. На `human_seo_drmax_0003` это
    давало 3819 слов при 1900 словах в самом контейнере.

    Значение по умолчанию сохраняет поведение сбора 2026-07-23…25, чтобы
    prep-v4 воспроизводился без изменений.
    """
    for selector in NOISE_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    best, best_len = None, 0
    for selector in CONTENT_SELECTORS:
        for node in soup.select(selector):
            length = len(node.get_text(" ", strip=True))
            if length > best_len:
                best, best_len = node, length
    if best is None:
        best = soup.body or soup

    blocks = []
    for node in best.find_all(BLOCK_TAGS):
        if leaf_only and node.find(BLOCK_TAGS):
            continue        # текст этого узла придёт от его листовых потомков
        text = node.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        if len(text.split()) >= 3:
            blocks.append(text)

    # Соседние одинаковые блоки появляются из вложенной вёрстки — схлопываем.
    deduped = []
    for block in blocks:
        if not deduped or block != deduped[-1]:
            deduped.append(block)

    collected = "\n\n".join(deduped)

    # Часть сайтов не оборачивает абзацы в <p>: текст висит прямо в контейнере.
    # Если по блокам собралось заметно меньше, берём контейнер целиком.
    container = best.get_text("\n", strip=True)
    if len(collected.split()) < 0.6 * len(container.split()):
        lines = [re.sub(r"\s+", " ", line).strip() for line in container.split("\n")]
        collected = "\n\n".join(line for line in lines if len(line.split()) >= 3)

    return collected


def process_file(path):
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    date, date_basis = find_date(soup)
    title = find_title(soup)
    author = find_author(soup)
    # Метаданные читаем до очистки, текст — после: decompose ломает разметку.
    text = extract_text(soup)
    words = len(text.split())

    return {
        "file": path,
        "date": date,
        "date_basis": date_basis,
        "title": title,
        "author": author,
        "text": text,
        "word_count": words,
    }


def verdict(item):
    if item["date"] is None:
        return "нет подтверждённой даты"
    if item["date"] >= CUTOFF:
        return f"опубликовано {item['date']:%Y-%m-%d}, позже отсечки"
    if item["word_count"] < MIN_WORDS:
        return f"{item['word_count']} слов, короче {MIN_WORDS}"
    if item["word_count"] > MAX_WORDS:
        return f"{item['word_count']} слов, длиннее {MAX_WORDS}"
    return None


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

    registry_rows, hash_rows, written = [], [], 0

    for index, item in enumerate(sorted(items, key=lambda i: i["date"]), start=1):
        doc_id = f"human_{args.genre}_{args.key}_{index:04d}"
        if doc_id in taken:
            print(f"  пропуск, уже в реестре: {doc_id}")
            continue

        text_path = target / f"{doc_id}.txt"
        text_path.write_text(item["text"], encoding="utf-8")
        (target / f"{doc_id}.json").write_text(
            json.dumps(
                {
                    "document_id": doc_id,
                    "source_file": item["file"].name,
                    "title": item["title"],
                    "author": item["author"] or args.author,
                    "publication_date": item["date"].strftime("%Y-%m-%d"),
                    "date_basis": item["date_basis"],
                    "date_second_confirmation": None,
                    "regulation_level": args.regulation_level,
                    "regulation_basis": args.regulation_basis,
                    "brief_available": None,
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
                "license_status": args.license_note,
                "consent_or_public_basis": args.legal_basis,
                "leakage_group": args.key,
                "revision_family_id": doc_id,
                "dedup_cluster_id": hashlib.sha256(
                    re.sub(r"\s+", " ", item["text"].lower()).encode("utf-8")
                ).hexdigest()[:16],
                "split_group_source": args.key,
                "split_group_author": item["author"] or args.author,
                "status": "collected",
                "notes": (
                    f"regulation_level={args.regulation_level}; {args.regulation_basis}; "
                    f"дата: {item['date_basis']}; второе подтверждение даты не проставлено"
                ),
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
        written += 1

    with REGISTRY.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fields).writerows(registry_rows)
    with HASHES.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(
            fh, fieldnames=["document_id", "file_path", "sha256", "bytes", "recorded_at"]
        ).writerows(hash_rows)

    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="каталог с .html")
    parser.add_argument("--key", default="author_archive", help="ключ источника")
    parser.add_argument("--genre", default="seo", choices=["seo", "analytics", "commercial", "news", "prose", "social", "tech"])
    parser.add_argument("--author", default="pi", help="автор по умолчанию, если не найден в разметке")
    parser.add_argument("--regulation-level", type=int, default=2)
    parser.add_argument(
        "--regulation-basis",
        default="собственный архив PI: известны автор и дата, форма задана редакционным шаблоном сайта",
    )
    parser.add_argument("--license-note", default="права принадлежат PI")
    parser.add_argument("--legal-basis", default="автор корпуса является правообладателем")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    src = ROOT / args.src if not Path(args.src).is_absolute() else Path(args.src)
    if not src.exists():
        raise SystemExit(f"нет каталога {src}")

    files = sorted(src.glob("*.html"))
    if not files:
        raise SystemExit(f"в {src} нет .html")

    print(f"Найдено файлов: {len(files)}\n")
    accepted, rejected = [], []

    for path in files:
        item = process_file(path)
        reason = verdict(item)
        date_text = item["date"].strftime("%Y-%m-%d") if item["date"] else "—"
        status = "ок" if reason is None else f"отброшен: {reason}"
        print(f"  {date_text}  {item['word_count']:>5} сл.  {status}")
        print(f"      {path.name[:78]}")
        (accepted if reason is None else rejected).append(item)

    print(f"\nПринято: {len(accepted)}, отброшено: {len(rejected)}")

    if args.dry_run:
        print("Сухой прогон: ничего не записано")
        return

    if not accepted:
        print("Записывать нечего")
        return

    written = write_documents(accepted, args, read_registry_fields())
    print(f"Записано в реестр: {written}")
    print("Дальше: python 09-tools/validate_registry.py")
    print("Не забыть: второе подтверждение даты по архивной копии для каждого документа")


if __name__ == "__main__":
    main()
