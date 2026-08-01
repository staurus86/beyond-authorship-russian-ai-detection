#!/usr/bin/env python3
"""Межтекстовый слой: X01 (повтор предложений) и X02 (общий скелет H2).

Процедура зафиксирована в `06-features/intertext-spec.md` до первого прогона.
Здесь только её реализация — расхождение между спецификацией и кодом считается
ошибкой кода.

Запуск из корня папки исследования:
    python 09-tools/extract_intertext.py              # весь корпус
    python 09-tools/extract_intertext.py --limit 50   # проба, матрица не трогается
    python 09-tools/extract_intertext.py --hits       # выборка на ручную проверку

Сравнение идёт внутри темы и внутри класса: вопрос признака — насколько авторы
одной группы повторяют друг друга.
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

import retest_io

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_cache as fc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
DERIVED = ROOT / "04-corpus" / "derived" / "prep-v4"
MANIFEST = DERIVED / "manifest.csv"
STANZA_CACHE = ROOT / "06-features" / "cache" / "stanza-v1"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"

EXTRACTOR_VERSION = "x-v1"
# prep-v5, 2026-07-29: версия препроцессинга задаётся параметром, значение по
# умолчанию не меняется.
PREP_VERSION = "prep-v4"
OWNED = ("X01", "X02")
HITS_REPORT = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.csv"
HITS_SAMPLE = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.md"

PUNCT_POS = "PUNCT"
# §2 спецификации: короткое предложение совпадает у разных авторов по причинам,
# к повтору текста отношения не имеющим.
MIN_SENTENCE_WORDS = 5
# §3: заголовок короче двух слов не считается.
MIN_HEADING_WORDS = 2
HEADING_MAX_WORDS = 12
MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
EDGE_PUNCT = " \t.,;:!?…—–-«»\"'()[]"
HITS_PER_GROUP = 10


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def normalize(text):
    """§2. Схлопнутые пробелы, нижний регистр, снятая краевая пунктуация."""
    return re.sub(r"\s+", " ", text).strip(EDGE_PUNCT).casefold()


def sentences_of(doc_id):
    """Пригодные предложения документа из кэша Stanza."""
    path = STANZA_CACHE / f"{doc_id}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        parsed = json.load(fh)
    kept = []
    for sentence in parsed["sentences"]:
        words = [token for token in sentence if token["p"] != PUNCT_POS]
        if len(words) < MIN_SENTENCE_WORDS:
            continue
        kept.append(" ".join(token["t"] for token in sentence))
    return kept


def headings_of(manifest_row):
    """Заголовки документа по правилу §2.25 препроцессинга."""
    if manifest_row is None:
        return []
    path = ROOT / manifest_row["full_path"]
    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = MD_HEADING.match(stripped)
        if match:
            found.append(match.group(1))
            continue
        words = stripped.split()
        if len(words) < HEADING_MAX_WORDS and not stripped.endswith((".", "!", "?", "…", ":", ";")):
            found.append(stripped)
    return [item for item in found if len(item.split()) >= MIN_HEADING_WORDS]


def group_key(row):
    """Тема и класс: сравнение идёт внутри обоих."""
    if not row["topic_id"]:
        return None
    return (row["topic_id"], row["origin_class"])


def make_record(doc_id, feature_id, name, raw, normalized_value, unit, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": feature_id,
        "feature_name": "" if reason else name,
        "extractor_version": EXTRACTOR_VERSION,
        "preprocessing_profile": "prose" if feature_id == "X01" else "full",
        "raw_value": "" if raw is None else f"{raw:.6g}",
        "normalized_value": "" if normalized_value is None else f"{normalized_value:.6g}",
        "unit": "" if reason else unit,
        "genre_percentile": "",
        "missing_reason": reason,
        "computed_at": computed_at,
    }


def merge_into_matrix(records, registry):
    with SCHEMA.open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))

    kept, dropped_stale = [], 0
    with MATRIX.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["document_id"] not in registry:
                dropped_stale += 1
                continue
            if row["feature_id"] in OWNED:
                continue
            kept.append(row)

    merged = kept + records

    if fc.percentiles_inline(PREP_VERSION):
        pools = {}
        for record in merged:
            if record["normalized_value"] == "" and record["raw_value"] == "":
                continue
            genre = registry[record["document_id"]]["genre"]
            value = float(record["normalized_value"] or record["raw_value"])
            pools.setdefault((record["feature_id"], genre), []).append(value)
        for key in pools:
            pools[key].sort()
        for record in merged:
            if record["normalized_value"] == "" and record["raw_value"] == "":
                continue
            genre = registry[record["document_id"]]["genre"]
            pool = pools[(record["feature_id"], genre)]
            value = float(record["normalized_value"] or record["raw_value"])
            rank = sum(1 for item in pool if item < value)
            record["genre_percentile"] = f"{rank / len(pool):.4f}" if pool else ""

    backup = MATRIX.with_suffix(".csv.bak-before-x-v1")
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")
    if dropped_stale:
        print(f"  снято строк документов вне реестра: {dropped_stale}")


def sample_hits():
    """Выборка на ручную проверку — §5 спецификации."""
    rows = read_rows(HITS_REPORT)
    registry = {row["document_id"]: row for row in read_rows(DOCUMENTS)}
    by_group = defaultdict(list)
    for row in rows:
        by_group[(row["topic_id"], row["origin_class"], row["feature_id"])].append(row)

    lines = [
        "# Совпадения между документами — выборка на ручную проверку",
        "",
        f"Экстрактор `{EXTRACTOR_VERSION}`, выборка от {datetime.now(timezone.utc).date()}. "
        f"По {HITS_PER_GROUP} совпадений на группу «тема × класс × признак».",
        "",
        "Вердикт: `ок` — совпадение содержательное; `служебное` — совпал шаблон площадки "
        "или канала, а не текст автора.",
        "",
    ]
    for key in sorted(by_group):
        topic, origin, feature = key
        picked = by_group[key][:HITS_PER_GROUP]
        lines.append(f"## {feature} · тема {topic} · класс {origin} — совпадений {len(by_group[key])}")
        lines.append("")
        lines.append("| № | документ | совпал с | текст | один автор | вердикт |")
        lines.append("|---|---|---|---|---|---|")
        for number, row in enumerate(picked, 1):
            same = "да" if registry[row["document_id"]]["split_group_author"] == registry[row["partner_id"]]["split_group_author"] else ""
            text = row["text"].replace("|", "¦")[:150]
            lines.append(
                f"| {number} | `{row['document_id']}` | `{row['partner_id']}` | {text} | {same} |  |"
            )
        lines.append("")
    HITS_SAMPLE.write_text("\n".join(lines), encoding="utf-8")
    print(f"выборка на проверку: {HITS_SAMPLE.relative_to(ROOT)}")
    return 0


def main():
    global PREP_VERSION, DERIVED, MANIFEST, MATRIX
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--hits", action="store_true")
    parser.add_argument("--out", help="записать результат в CSV вместо слияния в матрицу; "
                                      "подвыборки у слоя нет, группа сравнения требует всего корпуса")
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга на входе")
    args = parser.parse_args()

    PREP_VERSION = args.prep_version
    DERIVED = ROOT / "04-corpus" / "derived" / PREP_VERSION
    MANIFEST = DERIVED / "manifest.csv"
    MATRIX = fc.matrix_path(PREP_VERSION, MATRIX)

    if args.hits:
        return sample_hits()

    rows = read_rows(DOCUMENTS)
    registry = {row["document_id"]: row for row in rows}
    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    if args.limit:
        rows = rows[: args.limit]

    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Первый проход: собрать нормализованные предложения и заголовки по группам.
    groups = defaultdict(list)
    sentences, headings = {}, {}
    for row in rows:
        key = group_key(row)
        if key is None:
            continue
        doc_id = row["document_id"]
        found = sentences_of(doc_id)
        if found is None:
            continue
        sentences[doc_id] = [(normalize(text), text) for text in found]
        headings[doc_id] = [(normalize(text), text) for text in headings_of(manifest.get(doc_id))]
        groups[key].append(doc_id)

    print(f"групп «тема × класс»: {len(groups)}, документов в них: {sum(len(v) for v in groups.values())}")

    records, hits = [], []
    stats = Counter()

    for row in rows:
        doc_id = row["document_id"]
        key = group_key(row)
        if key is None or doc_id not in sentences:
            for feature_id in OWNED:
                records.append(make_record(
                    doc_id, feature_id, "", None, None, "", computed_at,
                    "тема не задана: сравнивать не с чем",
                ))
            continue
        peers = [other for other in groups[key] if other != doc_id]
        if not peers:
            for feature_id in OWNED:
                records.append(make_record(
                    doc_id, feature_id, "", None, None, "", computed_at, "в группе нет партнёров",
                ))
            continue

        for feature_id, source, name in (
            ("X01", sentences, "Повтор предложений между документами"),
            ("X02", headings, "Общий скелет H2"),
        ):
            own = source[doc_id]
            if not own:
                records.append(make_record(
                    doc_id, feature_id, "", None, None, "", computed_at,
                    "нет пригодных предложений" if feature_id == "X01" else "заголовков нет",
                ))
                continue
            peer_index = defaultdict(list)
            for other in peers:
                for norm, _ in source[other]:
                    peer_index[norm].append(other)
            matched = 0
            for norm, surface in own:
                partners = peer_index.get(norm)
                if not partners:
                    continue
                matched += 1
                stats[feature_id] += 1
                hits.append({
                    "topic_id": row["topic_id"], "origin_class": row["origin_class"],
                    "feature_id": feature_id, "document_id": doc_id,
                    "partner_id": partners[0], "text": surface[:300],
                })
            share = matched / len(own)
            records.append(make_record(doc_id, feature_id, name, share, matched, "доля", computed_at))

    if args.out:
        written = retest_io.write_records(args.out, records)
        print(f"повторный прогон, матрица не изменена: {args.out}, строк {written}")
    elif args.limit:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)
        with HITS_REPORT.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["topic_id", "origin_class", "feature_id", "document_id", "partner_id", "text"]
            )
            writer.writeheader()
            writer.writerows(hits)
        print(f"диагностика совпадений: {HITS_REPORT.relative_to(ROOT)}, строк {len(hits)}")

    print(f"межтекстовый слой посчитан: строк {len(records)}")
    print(f"  X01 совпавших предложений: {stats['X01']}")
    print(f"  X02 совпавших заголовков: {stats['X02']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
