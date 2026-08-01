#!/usr/bin/env python3
"""Тематический сбор человеческих статей из веб-архива — подгруппа topic_matched.

Процедура зафиксирована в `04-corpus/topic-matched-spec.md` до сбора. Берутся
закрытые отраслевые блоги: домен не отвечает, но снапшоты лежат в Wayback
Machine. Список URL приходит из CDX API, страница качается в форме
`web/<timestamp>id_/<url>` — без тулбара архива.

Тексты чужие: в открытый корпус не публикуются, идут метаданные, признаки и
ссылка на снапшот.

    python 09-tools/collect_wayback.py --domain devaka.ru --dry-run
    python 09-tools/collect_wayback.py --domain devaka.ru --per-topic 3
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
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

_spec = importlib.util.spec_from_file_location("extract_html", ROOT / "09-tools" / "extract_html.py")
_extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_extract)

# Правила чистки берутся из препроцессора, а не переписываются здесь: длина
# проверяется по тому тексту, который останется после `prep-v3`. Без этого в
# корпус снова попали бы статьи, где порог набирается ветками комментариев.
_prep_spec = importlib.util.spec_from_file_location("prep", ROOT / "09-tools" / "prep.py")
_prep = importlib.util.module_from_spec(_prep_spec)
_prep_spec.loader.exec_module(_prep)

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
OUT_ROOT = ROOT / "04-corpus" / "raw-human"
CACHE_ROOT = ROOT / "04-corpus" / "_archives" / "wayback_cache"

CDX = "http://web.archive.org/cdx/search/cdx"
SNAPSHOT = "https://web.archive.org/web/{timestamp}id_/{url}"
CUTOFF = datetime(2022, 1, 1)
MIN_WORDS = 700
# Во сколько раз сырой текст должен превышать порог, чтобы профиль prose его прошёл.
PROSE_MARGIN = 1.35
MAX_WORDS = 5000
UA = "Mozilla/5.0 (research corpus collection; contact staurus86@gmail.com)"

# §3 спецификации: тема считается названной, если её слуг стоит в адресе статьи.
# Слуги подобраны по темам брифов `03-briefs/briefs.json`, жанр seo.
TOPIC_SLUGS = {
    "b001": ["robots-txt", "robots.txt", "robots_txt", "meta-robots"],
    "b002": ["sitemap"],
    "b003": ["redirect", "301", "302", "redirekt"],
    "b004": ["canonical", "kanonich"],
    "b005": ["speed", "skorost", "pagespeed", "zagruzk"],
    "b006": ["microdata", "schema", "mikrorazmetk", "structured"],
    "b007": ["perelinkovk", "internal-link", "vnutrennie-ssylk"],
    "b008": ["dubl", "duplicate"],
    "b009": ["paginac", "pagination"],
    "b010": ["hreflang", "multilingual", "yazykov"],
    "b011": ["404", "broken-link", "bitye-ssylk"],
    "b012": ["log-analys", "logi-servera", "server-log", "analiz-logov"],
    "b013": ["crawl-budget", "kraulingov"],
    "b014": ["mobile", "mobiln", "adaptiv"],
    # Слуг «https» намеренно не используется: он стоит в протоколе любого адреса.
    "b015": ["ssl-sertifikat", "pereezd-na-https", "perehod-na-https", "https-migration", "zaschischenn"],
}
TOPIC_RE = {key: re.compile("|".join(re.escape(s) for s in slugs), re.I) for key, slugs in TOPIC_SLUGS.items()}


def fetch(url, delay, attempts=3, timeout=90):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
            time.sleep(delay)
            return data.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == attempts:
                print(f"    не скачано: {url[:90]} — {exc}")
                return None
            time.sleep(delay * attempt * 2)
    return None


def cdx_urls(domain, delay, limit, any_topic=False):
    """Адреса статей домена, где в самом адресе стоит слуг одной из тем."""
    pattern = "|".join(slug for slugs in TOPIC_SLUGS.values() for slug in slugs)
    params = {
        "url": f"{domain}*",
        "output": "json",
        "from": "2010",
        "to": "2021",
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "limit": str(limit),
    }
    query = urllib.parse.urlencode(params)
    query += "&filter=mimetype:text/html"
    if not any_topic:
        query += "&filter=original:.*(" + re.sub(r"[^a-z0-9|._-]", "", pattern.lower()) + ").*"
    payload = fetch(f"{CDX}?{query}", delay)
    if not payload:
        return []
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError:
        print("    CDX вернул не JSON")
        return []
    return [(row[1], row[2]) for row in rows[1:]]


def topic_of(url):
    """Тема по слугу в пути адреса. Адрес, задевший две темы, отбрасывается.

    Ищется только в пути и запросе: протокол и домен слуги содержать не могут,
    иначе `https` в схеме адреса отнёс бы к теме переезда на HTTPS весь сайт.
    """
    parts = urllib.parse.urlsplit(url)
    target = f"{parts.path}?{parts.query}" if parts.query else parts.path
    matched = [key for key, regex in TOPIC_RE.items() if regex.search(target)]
    return matched[0] if len(matched) == 1 else None


def cached_snapshot(timestamp, url, domain, delay):
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    folder = CACHE_ROOT / domain.replace(".", "_")
    folder.mkdir(exist_ok=True)
    name = hashlib.sha1(f"{timestamp}{url}".encode("utf-8")).hexdigest()[:20] + ".html"
    path = folder / name
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    html = fetch(SNAPSHOT.format(timestamp=timestamp, url=url), delay)
    if html:
        path.write_text(html, encoding="utf-8")
    return html


def parse_snapshot(html, url, timestamp):
    """Текст, заголовок, автор и дата публикации из снапшота."""
    import warnings

    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(html, "lxml")
    # h1 у части блогов — название сайта, поэтому заголовок берётся из title и
    # чистится от хвоста площадки после разделителя.
    title = ""
    for node in (soup.select_one("h1.entry-title, h1.post-title, article h1"),
                 soup.select_one("title"), soup.select_one("h1")):
        if node:
            title = re.split(r"\s+[|—–-]\s+", node.get_text(" ", strip=True))[0].strip()
            if len(title) > 12:
                break
    text = _extract.extract_text(soup)
    if not text:
        return None

    published = _extract.date_from_url(url)
    basis = "адрес статьи" if published else ""
    if published is None:
        node = soup.select_one("time[datetime], meta[property='article:published_time']")
        raw = (node.get("datetime") or node.get("content")) if node else None
        if raw:
            try:
                published = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
                basis = "разметка страницы"
            except ValueError:
                published = None
    snapshot_date = datetime.strptime(timestamp[:8], "%Y%m%d")
    if published is None:
        # §5 спецификации: снапшот задаёт верхнюю границу даты публикации.
        published = snapshot_date
        basis = "время снапшота — верхняя граница"

    # Длина считается по тексту после чистки §2.26: ветка комментариев и
    # служебные строки площадки в порог не засчитываются.
    stats = Counter()
    clean = _prep.strip_platform_lines(_prep.strip_comment_thread(text, stats), stats)

    return {
        "title": title,
        "text": text,
        "author": _extract.find_author(soup) or "",
        "published": published,
        "date_basis": basis,
        "snapshot_date": snapshot_date,
        "word_count": len(text.split()),
        "clean_word_count": len(clean.split()),
        "comments_cut_chars": stats["comments_cut_chars"],
    }


def cyrillic_share(text):
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if "а" <= char.lower() <= "я" or char.lower() == "ё") / len(letters)


def shingles(text, size=5):
    words = re.sub(r"\s+", " ", text.lower()).split()
    return {hash(tuple(words[i : i + size])) for i in range(max(0, len(words) - size + 1))}


def is_duplicate(text, seen, threshold=0.8):
    current = shingles(text)
    if not current:
        return True, current
    for other in seen:
        if not other:
            continue
        overlap = len(current & other) / min(len(current), len(other))
        if overlap >= threshold:
            return True, current
    return False, current


def existing_ids():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return {row["document_id"] for row in csv.DictReader(fh)}


def next_index(key):
    folder = OUT_ROOT / key
    if not folder.exists():
        return 1
    used = [
        int(match.group(1))
        for path in folder.glob(f"human_seo_{key}_*.txt")
        if (match := re.search(r"_(\d{4})\.txt$", path.name))
    ]
    return max(used) + 1 if used else 1


def write_documents(items, key, domain):
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        fields = next(csv.reader(fh))
    folder = OUT_ROOT / key
    folder.mkdir(parents=True, exist_ok=True)
    taken = existing_ids()
    stamp = datetime.now().strftime("%Y-%m-%d")
    start = next_index(key)
    registry_rows, hash_rows = [], []

    for offset, item in enumerate(items):
        doc_id = f"human_seo_{key}_{start + offset:04d}"
        if doc_id in taken:
            continue
        text_path = folder / f"{doc_id}.txt"
        text_path.write_text(item["text"], encoding="utf-8")
        (folder / f"{doc_id}.json").write_text(
            json.dumps(
                {
                    "document_id": doc_id,
                    "url": item["url"],
                    "snapshot": SNAPSHOT.format(timestamp=item["timestamp"], url=item["url"]),
                    "title": item["title"],
                    "author": item["author"],
                    "topic_id": item["topic"],
                    "publication_year": item["published"].year,
                    "date_basis": item["date_basis"],
                    "date_second_confirmation": f"снапшот {item['snapshot_date'].date()}",
                    "regulation_level": 2,
                    "regulation_basis": "отраслевой блог: формат задан площадкой, не стандартом",
                    "license_note": "текст чужой, в открытый корпус не публикуется; ссылка на снапшот",
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
                "genre": "seo",
                "topic_id": item["topic"],
                "origin_class": "H",
                "author_or_model_id": item["author"] or f"unknown@{domain}",
                "human_publication_date": item["published"].strftime("%Y-%m-%d"),
                "source_platform": key,
                "word_count": item["word_count"],
                "char_count": len(item["text"]),
                "preprocessing_profile": "prose",
                "license_status": "чужой текст, публикуются метаданные и признаки",
                "consent_or_public_basis": "открытая веб-публикация, копия в Wayback Machine",
                "leakage_group": f"{key}:{domain}",
                "revision_family_id": doc_id,
                "dedup_cluster_id": hashlib.sha256(
                    re.sub(r"\s+", " ", item["text"].lower()).encode("utf-8")
                ).hexdigest()[:16],
                "split_group_author": item["author"] or f"unknown@{domain}",
                "split_group_source": key,
                "status": "collected",
                "notes": f"regulation_level=2; topic_matched; тема {item['topic']}; снапшот {item['timestamp']}",
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", required=True, help="домен закрытого блога, например devaka.ru")
    parser.add_argument("--key", help="ключ источника, по умолчанию домен без точек")
    parser.add_argument("--per-topic", type=int, default=3, help="максимум документов одной темы от источника")
    parser.add_argument("--cdx-limit", type=int, default=400)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--max-docs", type=int,
                        help="сколько документов взять с этого домена всего")
    parser.add_argument("--any-topic", action="store_true",
                        help="брать любые статьи блога, не только по темам брифов")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    key = args.key or args.domain.replace(".", "_").replace("-", "_")
    print(f"веб-архив: {args.domain}, ключ {key}, до {args.per_topic} документов на тему\n")

    entries = cdx_urls(args.domain, args.delay, args.cdx_limit, args.any_topic)
    print(f"адресов с темой в CDX: {len(entries)}")

    # Защита от дублей идёт по всем человеческим документам жанра seo, а не
    # только по своей папке: снапшот живого домена легко повторит статью,
    # собранную раньше по карте сайта, и ключи источников у них разные.
    seen = []
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["origin_class"] != "H" or row["genre"] != "seo":
                continue
            path = ROOT / row["file_path"]
            if path.exists():
                seen.append(shingles(path.read_text(encoding="utf-8", errors="replace")))
    print(f"в защите от дублей: {len(seen)} человеческих seo-документов")

    selected, per_topic, stats = [], {}, {}

    def note(reason):
        stats[reason] = stats.get(reason, 0) + 1

    for timestamp, url in entries:
        if args.max_docs and len(selected) >= args.max_docs:
            note("набран лимит по домену")
            break
        topic = topic_of(url)
        if topic is None and not args.any_topic:
            note("адрес не отнести к одной теме")
            continue
        if topic is not None and per_topic.get(topic, 0) >= args.per_topic:
            note("лимит на тему")
            continue
        html = cached_snapshot(timestamp, url, args.domain, args.delay)
        if not html:
            note("снапшот не скачан")
            continue
        parsed = parse_snapshot(html, url, timestamp)
        if parsed is None:
            note("нет блока с текстом")
            continue
        # Запас к порогу: профиль `prose` короче сырого текста на списки,
        # заголовки и разметку. Без запаса документ проходит здесь и падает
        # ниже порога после препроцессинга — так вышло у 14 из первых 103.
        if parsed["clean_word_count"] < MIN_WORDS * PROSE_MARGIN:
            note("короче порога с запасом на профиль prose")
            continue
        if parsed["word_count"] > MAX_WORDS:
            note("длиннее 5000 слов")
            continue
        if cyrillic_share(parsed["text"]) < 0.5:
            note("не русский")
            continue
        if parsed["published"] >= CUTOFF:
            note("опубликовано в 2022 или позже")
            continue
        if parsed["text"].lstrip()[:1].islower():
            note("текст начинается с середины предложения")
            continue
        duplicate, sh = is_duplicate(parsed["text"], seen)
        if duplicate:
            note("дубль")
            continue
        seen.append(sh)

        parsed.update({"url": url, "timestamp": timestamp, "topic": topic or ""})
        selected.append(parsed)
        if topic is not None:
            per_topic[topic] = per_topic.get(topic, 0) + 1
        print(f"  [{len(selected):>3}] {topic or '—':4s} {parsed['published'].year} "
              f"{parsed['clean_word_count']:>5} сл.  {parsed['title'][:58]}")

    print(f"\nОтобрано: {len(selected)}, тем затронуто: {len(per_topic)}")
    for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  отсеяно, {reason}: {count}")

    if args.dry_run:
        print("Сухой прогон: ничего не записано")
        return 0
    if not selected:
        return 0
    print(f"Записано в реестр: {write_documents(selected, key, args.domain)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
