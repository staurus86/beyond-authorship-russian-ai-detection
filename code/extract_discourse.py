#!/usr/bin/env python3
"""Дискурсивный слой: D04 (триколоны) и D05 (вопросы автора).

Процедура зафиксирована в `06-features/discourse-spec.md` до первого прогона.
Здесь только её реализация — расхождение между спецификацией и кодом считается
ошибкой кода.

Запуск из корня папки исследования:
    python 09-tools/extract_discourse.py              # весь корпус
    python 09-tools/extract_discourse.py --limit 20   # проба, матрица не трогается
    python 09-tools/extract_discourse.py --hits       # выборка на ручную проверку

Разбор берётся из кэша `06-features/cache/stanza-v1`, профиль `prose`.
"""

import argparse
import csv
import gzip
import json
import re
import sys
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

EXTRACTOR_VERSION = "disc-v1"
# prep-v5, 2026-07-29: версия препроцессинга задаётся параметром, значение по
# умолчанию не меняется.
PREP_VERSION = "prep-v4"
STANZA_REVISION = "stanza 1.14.0/ru-syntagrus"
OWNED = ("D04", "D05")
HITS_REPORT = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.csv"
HITS_SAMPLE = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.md"

PUNCT_POS = "PUNCT"
CONJ_DEPREL = "conj"
# §2 спецификации: триколон — ряд ровно из трёх однородных членов.
TRICOLON_CONJ = 2
# §3: заголовок по правилу §2.25 препроцессинга.
HEADING_MAX_WORDS = 12
QUOTE_OPEN = "«\"„“"
QUOTE_CLOSE = "»\"“”"
PILOT_PER_GENRE = 20
CONTEXT = 70


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def sentence_text(sentence):
    return " ".join(token["t"] for token in sentence)


def word_tokens(sentence):
    return [token for token in sentence if token["p"] != PUNCT_POS]


def tricolons(sentence):
    """Ряды ровно из трёх однородных членов одной части речи.

    Возвращает список рядов, каждый — тройка словоформ по порядку в
    предложении. Сама тройка нужна и для счёта, и для диагностики: по одному
    предложению целиком вердикт «ряд из трёх однородных» не поставить.
    """
    children = {}
    for token in sentence:
        if token["d"] == CONJ_DEPREL:
            children.setdefault(token["h"], []).append(token)
    by_index = {token["i"]: token for token in sentence}

    found = []
    for head_index, conjuncts in children.items():
        if len(conjuncts) != TRICOLON_CONJ:
            continue
        head = by_index.get(head_index)
        if head is None:
            continue
        parts = [head] + conjuncts
        if len({part["p"] for part in parts}) != 1:
            continue
        found.append([part["t"] for part in sorted(parts, key=lambda item: item["i"])])
    return found


def is_question(sentence):
    for token in reversed(sentence):
        if token["t"].strip():
            return token["t"].strip().endswith("?")
    return False


def in_direct_speech(text):
    """Предложение целиком внутри кавычек либо открывается тире реплики."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[0] in QUOTE_OPEN and stripped[-1] in QUOTE_CLOSE + "?!.":
        return True
    return stripped[0] in "—–-"


def looks_like_heading(text):
    words = text.split()
    return len(words) < HEADING_MAX_WORDS and not text.strip().endswith((".", "!", "…"))


def document_features(parsed):
    """Счётчики D04 и D05 и записи для диагностики."""
    sentences = parsed["sentences"]
    words = sum(len(word_tokens(sentence)) for sentence in sentences)
    tricolon_count = 0
    questions = 0
    in_speech = 0
    in_heading = 0
    hits = []

    for sentence in sentences:
        text = sentence_text(sentence)
        rows = tricolons(sentence)
        tricolon_count += len(rows)
        for parts in rows:
            hits.append(("D04", " · ".join(parts) + "  ⟵  " + text[:180], "", ""))
        if is_question(sentence):
            questions += 1
            speech = in_direct_speech(text)
            heading = looks_like_heading(text)
            in_speech += bool(speech)
            in_heading += bool(heading)
            hits.append(("D05", text[:240], "да" if speech else "", "да" if heading else ""))

    return {
        "words": words,
        "D04": tricolon_count,
        "D05": questions,
        "questions_in_speech": in_speech,
        "questions_in_heading": in_heading,
        "hits": hits,
    }


def make_record(doc_id, feature_id, name, raw, normalized, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": feature_id,
        "feature_name": "" if reason else name,
        "extractor_version": EXTRACTOR_VERSION,
        "preprocessing_profile": "prose",
        "raw_value": "" if raw is None else f"{raw:.6g}",
        "normalized_value": "" if normalized is None else f"{normalized:.6g}",
        "unit": "" if reason else "на 1000 слов",
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

    backup = MATRIX.with_suffix(".csv.bak-before-disc-v1")
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")
    if dropped_stale:
        print(f"  снято строк документов вне реестра: {dropped_stale}")


def sample_hits(limit_per_genre=PILOT_PER_GENRE):
    """Выборка на ручную проверку — §4 спецификации."""
    rows = read_rows(HITS_REPORT)
    registry = {row["document_id"]: row for row in read_rows(DOCUMENTS)}
    pilot = [row["document_id"] for row in read_rows(ROOT / "06-features" / "pilot-1-ids.csv")]

    picked = {}
    for doc_id in pilot:
        row = registry.get(doc_id)
        if row is not None:
            picked.setdefault(row["genre"], doc_id)

    lines = [
        "# Срабатывания дискурсивного слоя — выборка на ручную проверку",
        "",
        f"Экстрактор `{EXTRACTOR_VERSION}`, выборка от {datetime.now(timezone.utc).date()}. "
        f"По первому документу пилота на каждый жанр, до {limit_per_genre} срабатываний на признак.",
        "",
        "D04: `ок` — ряд из трёх однородных; `ложь` — связь соединила разное; "
        "`граница` — ряд длиннее трёх.",
        "D05: `автор` — вопрос обращён к читателю; `персонаж` — вопрос в прямой речи или диалоге; "
        "`цитата` — вопрос принадлежит источнику.",
        "",
    ]
    for feature_id in OWNED:
        lines.append(f"# {feature_id}")
        lines.append("")
        for genre in sorted(picked):
            doc_id = picked[genre]
            sel = [r for r in rows if r["document_id"] == doc_id and r["feature_id"] == feature_id]
            lines.append(f"## {feature_id} · {genre} — `{doc_id}`, срабатываний {len(sel)}")
            lines.append("")
            if not sel:
                lines.extend(["Срабатываний нет.", ""])
                continue
            lines.append("| № | фрагмент | прямая речь | заголовок | вердикт |")
            lines.append("|---|---|---|---|---|")
            for number, row in enumerate(sel[:limit_per_genre], 1):
                fragment = row["fragment"].replace("|", "¦")[:200]
                lines.append(
                    f"| {number} | {fragment} | {row['in_speech']} | {row['in_heading']} |  |"
                )
            lines.append("")
    HITS_SAMPLE.write_text("\n".join(lines), encoding="utf-8")
    print(f"выборка на проверку: {HITS_SAMPLE.relative_to(ROOT)}")
    return 0


def main():
    global PREP_VERSION, DERIVED, MANIFEST, MATRIX
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only")
    parser.add_argument("--hits", action="store_true")
    parser.add_argument("--ids-file", help="считать только документы из файла, по идентификатору на строку")
    parser.add_argument("--out", help="записать результат в CSV вместо слияния в матрицу")
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
    if args.ids_file:
        wanted = set(retest_io.read_ids(args.ids_file))
        rows = [row for row in rows if row["document_id"] in wanted]
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records, hits = [], []
    missing_cache = []
    totals = {"D04": 0, "D05": 0, "speech": 0, "heading": 0}
    docs_with = {"D04": 0, "D05": 0}

    # Разбор адресуется хешем входа своей версии препроцессинга.
    index = fc.load_index(STANZA_CACHE)
    for row in rows:
        doc_id = row["document_id"]
        input_sha = fc.sha_for(PREP_VERSION, "prose", doc_id)
        path = (fc.lookup(STANZA_CACHE, index, doc_id, input_sha, STANZA_REVISION)
                if input_sha else None)
        if path is None:
            missing_cache.append(doc_id)
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)

        result = document_features(parsed)
        words = result["words"]
        if words <= 0:
            for feature_id in OWNED:
                records.append(make_record(doc_id, feature_id, "", None, None, computed_at,
                                           "нет словных токенов в профиле prose"))
            continue

        for feature_id, name in (("D04", "Триколоны"), ("D05", "Вопросы автора")):
            count = result[feature_id]
            totals[feature_id] += count
            docs_with[feature_id] += bool(count)
            records.append(
                make_record(doc_id, feature_id, name, count, count / words * 1000, computed_at)
            )
        totals["speech"] += result["questions_in_speech"]
        totals["heading"] += result["questions_in_heading"]

        for feature_id, fragment, speech, heading in result["hits"]:
            hits.append({
                "document_id": doc_id, "origin_class": row["origin_class"],
                "genre": row["genre"], "feature_id": feature_id,
                "fragment": fragment, "in_speech": speech, "in_heading": heading,
            })

    if args.out:
        written = retest_io.write_records(args.out, records)
        print(f"повторный прогон на {len(rows)} документах, матрица не изменена: {args.out}, строк {written}")
    elif args.limit or args.only:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)
        with HITS_REPORT.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["document_id", "origin_class", "genre", "feature_id",
                                "fragment", "in_speech", "in_heading"]
            )
            writer.writeheader()
            writer.writerows(hits)
        print(f"диагностика: {HITS_REPORT.relative_to(ROOT)}, строк {len(hits)}")

    print(f"дискурсивный слой посчитан: строк {len(records)}")
    print(f"  D04 триколонов: {totals['D04']}, документов с ними {docs_with['D04']}")
    print(f"  D05 вопросов: {totals['D05']}, документов с ними {docs_with['D05']}")
    print(f"     из них внутри прямой речи {totals['speech']}, в заголовках {totals['heading']}")
    if missing_cache:
        print(f"  ! нет разбора: {len(missing_cache)} документов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
