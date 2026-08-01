#!/usr/bin/env python3
"""Словарный дискурсивный слой: D01 и D06 (dlex-v1).

Процедура зафиксирована в `06-features/discourse-lexicon-spec.md` до прогона.
Здесь только её реализация — расхождение между спецификацией и кодом считается
ошибкой кода.

    python 09-tools/extract_discourse_lexicon.py             # весь корпус
    python 09-tools/extract_discourse_lexicon.py --limit 20  # проба, матрица не трогается
    python 09-tools/extract_discourse_lexicon.py --hits      # выборка на ручную проверку

Перечни взяты из двух академических источников и не составлялись автором:
Русская грамматика 1980, §2221, и Виноградов, «Русский язык», с. 604–606.
"""

import argparse
import csv
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import retest_io  # noqa: E402

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
DERIVED = ROOT / "04-corpus" / "derived" / "prep-v4"
MANIFEST = DERIVED / "manifest.csv"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"

EXTRACTOR_VERSION = "dlex-v1"
OWNED = ("D01", "D06")
HITS_REPORT = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.csv"
HITS_SAMPLE = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.md"
VERSIONS_CSV = ROOT / "06-features" / "dlex-versions.csv"

SAMPLE_SIZE = 200  # §4 спецификации: выборка на проверку омонимии
SAMPLE_SEED = 20260727

# --- перечни -------------------------------------------------------------
# §2.1 спецификации. Русская грамматика 1980, §2221, группа 2.
RG80_D01 = {
    "достоверность": [
        "наверное", "надеюсь", "думаю", "полагаю", "пожалуй", "кажется",
        "думается", "видимо", "по-видимому", "вероятно", "по всей вероятности",
        "может быть", "должно быть", "надо полагать", "как кажется", "видно",
        "как видно", "без сомнения", "конечно", "само собой", "разумеется",
        "само собой разумеется", "бесспорно", "действительно",
    ],
    "неопределённость": ["некоторым образом", "в каком-то смысле"],
    "допущение": ["положим", "предположим", "допустим", "пожалуй", "возможно",
                  "если хотите"],
}

# §2.2. Виноградов, разряд 5, с. 605.
VIN_D01 = {
    "логическая оценка": [
        "вероятно", "по всей вероятности", "понятно", "несомненно", "безусловно",
        "очевидно", "видимо", "по-видимому", "разумеется", "может быть",
        "действительно", "в самом деле", "подлинно",
    ],
}

# §3.1. Русская грамматика 1980, §2221, группа 4.
RG80_D06 = {
    "связи и место в тексте": [
        "кроме того", "к тому же", "в довершение всего", "вдобавок",
        "сверх всего", "притом", "следовательно", "стало быть", "тем более",
        "во-первых", "во-вторых", "в-третьих",
    ],
}

# §3.2. Виноградов, разряды 6 и 7, с. 606.
VIN_D06 = {
    "последовательность мыслей": [
        "значит", "стало быть", "кстати", "мало того", "кроме того",
        "сверх того", "помимо того", "в частности", "примерно", "например",
        "главное", "главное дело", "в конце концов",
    ],
    "числовой порядок": ["во-первых", "во-вторых", "в-третьих", "в-четвёртых"],
}


def flatten(groups):
    return {item for items in groups.values() for item in items}


LISTS = {
    "D01": {"rg80": flatten(RG80_D01), "vin": flatten(VIN_D01)},
    "D06": {"rg80": flatten(RG80_D06), "vin": flatten(VIN_D06)},
}
GROUPS = {"D01": {**RG80_D01, **VIN_D01}, "D06": {**RG80_D06, **VIN_D06}}

NORMALIZERS = {
    "D01": ("Модальные слова эпистемической оценки", 500),
    "D06": ("Signposting", 1000),
}

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def build_versions(feature_id):
    rg80, vin = LISTS[feature_id]["rg80"], LISTS[feature_id]["vin"]
    return {
        "union": rg80 | vin,
        "core": rg80 & vin,
        "rg80": rg80,
        "vin": vin,
    }


def compile_patterns(items):
    """Регулярные выражения по границам слова, длинные сочетания раньше коротких.

    Порядок важен: «по всей вероятности» должно поглотить своё вхождение до
    того, как «вероятно» попробует совпасть внутри него.
    """
    ordered = sorted(items, key=lambda s: (-len(s.split()), -len(s)))
    return [(item, re.compile(r"(?<![^\W\d_])" + re.escape(item) + r"(?![^\W\d_])"))
            for item in ordered]


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def prose_text(manifest_row):
    path = Path(manifest_row["prose_path"])
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").lower().replace("ё", "ё")
    return re.sub(r"\s+", " ", text)


def count_items(text, patterns):
    """Число вхождений и позиции. Найденное вычёркивается, чтобы длинное
    сочетание не считалось дважды — целиком и по своей части."""
    taken = []
    total = 0
    found = Counter()
    spans = []
    for item, pattern in patterns:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < b and a < end for a, b in taken):
                continue
            taken.append((start, end))
            total += 1
            found[item] += 1
            spans.append((item, start, end))
    return total, found, spans


def make_record(doc_id, feature_id, name, raw, normalized, unit, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": feature_id,
        "feature_name": "" if reason else name,
        "extractor_version": EXTRACTOR_VERSION,
        "preprocessing_profile": "prose",
        "raw_value": "" if raw is None else f"{raw:.6g}",
        "normalized_value": "" if normalized is None else f"{normalized:.6g}",
        "unit": "" if reason else unit,
        "genre_percentile": "",
        "missing_reason": reason,
        "computed_at": computed_at,
    }


def merge_into_matrix(records, registry):
    with SCHEMA.open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))
    kept = []
    with MATRIX.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["document_id"] not in registry:
                continue
            if row["feature_id"] in OWNED:
                continue
            kept.append(row)
    merged = kept + records

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

    backup = MATRIX.with_suffix(".csv.bak-before-dlex-v1")
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")


def sample_hits():
    """§4: выборка на ручную проверку омонимии."""
    rows = read_rows(HITS_REPORT)
    rng = random.Random(SAMPLE_SEED)
    picked = rng.sample(rows, min(SAMPLE_SIZE, len(rows)))
    lines = [f"# Выборка вхождений для проверки омонимии ({EXTRACTOR_VERSION})", "",
             f"Требование — `06-features/discourse-lexicon-spec.md`, §4. Отобрано "
             f"{len(picked)} вхождений из {len(rows)}, сид {SAMPLE_SEED}.", "",
             "Вердикт по каждому: верно — единица употреблена как модальное слово "
             "или указатель; ложно — омоним другой части речи.", "",
             "| № | Признак | Единица | Документ | Контекст | Вердикт |",
             "|---|---|---|---|---|---|"]
    for number, row in enumerate(picked, 1):
        context = row["context"].replace("|", "¦")
        lines.append(f"| {number} | {row['feature_id']} | {row['item']} | "
                     f"`{row['document_id']}` | {context} | |")
    HITS_SAMPLE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"выборка на проверку: {HITS_SAMPLE.relative_to(ROOT)}, строк {len(picked)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only")
    parser.add_argument("--hits", action="store_true")
    parser.add_argument("--out", help="записать результат в CSV вместо слияния в матрицу")
    args = parser.parse_args()

    if args.hits:
        return sample_hits()

    rows = read_rows(DOCUMENTS)
    registry = {row["document_id"]: row for row in rows}
    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    patterns = {fid: {v: compile_patterns(items) for v, items in build_versions(fid).items()}
                for fid in OWNED}
    for fid in OWNED:
        versions = build_versions(fid)
        print(f"{fid}: union {len(versions['union'])}, core {len(versions['core'])}, "
              f"rg80 {len(versions['rg80'])}, vin {len(versions['vin'])} единиц")

    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records, hits, version_rows = [], [], []
    totals = Counter()
    per_item = defaultdict(Counter)
    missing = 0

    for row in rows:
        doc_id = row["document_id"]
        manifest_row = manifest.get(doc_id)
        text = prose_text(manifest_row) if manifest_row else ""
        words = len(WORD_RE.findall(text))
        if not words:
            missing += 1
            for fid in OWNED:
                records.append(make_record(doc_id, fid, "", None, None, "", computed_at,
                                           "нет словных токенов в профиле prose"))
            continue

        for fid in OWNED:
            name, per = NORMALIZERS[fid]
            counts = {}
            for version, compiled in patterns[fid].items():
                total, found, spans = count_items(text, compiled)
                counts[version] = total
                if version == "union":
                    totals[fid] += total
                    per_item[fid].update(found)
                    for item, start, end in spans[:12]:
                        hits.append({
                            "feature_id": fid, "document_id": doc_id,
                            "origin_class": row["origin_class"], "genre": row["genre"],
                            "item": item,
                            "context": text[max(0, start - 60):end + 60].strip(),
                        })
            records.append(make_record(doc_id, fid, name, counts["union"],
                                       counts["union"] / words * per, f"на {per} слов",
                                       computed_at))
            version_rows.append({"document_id": doc_id, "feature_id": fid,
                                 "words": words, **{f"n_{v}": counts[v] for v in counts}})

    if args.out:
        written = retest_io.write_records(args.out, records)
        print(f"повторный прогон, матрица не изменена: {args.out}, строк {written}")
    elif args.limit or args.only:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)
        with HITS_REPORT.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["feature_id", "document_id",
                                                    "origin_class", "genre", "item", "context"])
            writer.writeheader()
            writer.writerows(hits)
        with VERSIONS_CSV.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["document_id", "feature_id", "words",
                                                    "n_union", "n_core", "n_rg80", "n_vin"])
            writer.writeheader()
            writer.writerows(version_rows)
        print(f"диагностика: {HITS_REPORT.relative_to(ROOT)}, строк {len(hits)}")
        print(f"версии перечней: {VERSIONS_CSV.relative_to(ROOT)}, строк {len(version_rows)}")

    print(f"словарный дискурсивный слой посчитан: строк {len(records)}")
    for fid in OWNED:
        print(f"  {fid}: вхождений {totals[fid]}, "
              f"чаще всего — {', '.join(f'{w} ({n})' for w, n in per_item[fid].most_common(5))}")
    if missing:
        print(f"  ! без словных токенов: {missing} документов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
