#!/usr/bin/env python3
"""M03: доля предложений без нового информационного элемента.

Схема разметки и процедура зафиксированы в `06-features/m03-spec.md` до того,
как размечено хотя бы одно предложение.

Ручная разметка лежит в `06-features/m03-manual.csv` и покрывает 20 документов
development set. Автоматический прокси считается на всём корпусе и калибруется
по этой разметке.

    python 09-tools/extract_m03.py --calibrate   # сверка прокси с разметкой
    python 09-tools/extract_m03.py               # признак в матрицу
    python 09-tools/extract_m03.py --check-file  # 50 предложений на проверку PI
"""

import argparse
import csv
import gzip
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_cache as fc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
STANZA_CACHE = ROOT / "06-features" / "cache" / "stanza-v1"
NER_CACHE = ROOT / "06-features" / "cache" / "ner-v1"
MANUAL = ROOT / "06-features" / "m03-manual.csv"
MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"
CHECK_FILE = ROOT / "06-features" / "m03-check-for-pi.csv"
CALIBRATION = ROOT / "06-features" / "m03-calibration.md"

# prep-v5, 2026-07-29: версия препроцессинга задаётся параметром, значение по
# умолчанию не меняется.
PREP_VERSION = "prep-v4"
MANUAL_VERSION = "m03-v1"
PROXY_VERSION = "m03-proxy-v1"
OWNED = ("M03",)

PUNCT_POS = "PUNCT"
FUNCTION_POS = {"ADP", "AUX", "CCONJ", "SCONJ", "DET", "PART", "PRON", "PUNCT", "SYM", "X"}
MIN_LEMMA_LEN = 4
MIN_SENTENCE_WORDS = 3
NUMBER = re.compile(r"\d")
CHECK_SIZE = 50
CHECK_SEED = 20260725
HEADING_MAX_WORDS = 12
MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def sentence_flags(doc_id):
    """Для каждого пригодного предложения: индекс, текст, вердикт прокси.

    §7 спецификации: предложение относится к «без нового», если все его
    содержательные леммы уже встречались раньше в документе, в нём нет числа и
    нет именованной сущности.
    """
    path = STANZA_CACHE / f"{doc_id}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        parsed = json.load(fh)

    spans = []
    ner_path = NER_CACHE / f"{doc_id}.json.gz"
    if ner_path.exists():
        with gzip.open(ner_path, "rt", encoding="utf-8") as fh:
            spans = [span["text"].casefold() for span in json.load(fh)["spans"]]

    seen = set()
    result = []
    for index, sentence in enumerate(parsed["sentences"]):
        words = [token for token in sentence if token["p"] != PUNCT_POS]
        if len(words) < MIN_SENTENCE_WORDS:
            continue
        text = " ".join(token["t"] for token in sentence)
        lemmas = {
            (token.get("l") or token["t"]).casefold()
            for token in words
            if token["p"] not in FUNCTION_POS and len((token.get("l") or token["t"])) >= MIN_LEMMA_LEN
        }
        fresh = lemmas - seen
        has_number = bool(NUMBER.search(text))
        lowered = text.casefold()
        has_entity = any(span and span in lowered for span in spans)
        without_new = not fresh and not has_number and not has_entity
        result.append((index, text, without_new))
        seen |= lemmas
    return result


def calibrate():
    """Сверка прокси с ручной разметкой — §7 спецификации."""
    manual = read_rows(MANUAL)
    by_doc = {}
    for row in manual:
        by_doc.setdefault(row["document_id"], {})[int(row["sentence_index"])] = row

    matrix = Counter()
    examples = {"ложно без нового": [], "пропущено без нового": []}
    for doc_id, marked in by_doc.items():
        flags = sentence_flags(doc_id)
        if flags is None:
            continue
        for index, text, proxy_without_new in flags:
            row = marked.get(index)
            if row is None:
                continue
            manual_without_new = row["verdict"] == "без нового"
            key = (manual_without_new, proxy_without_new)
            matrix[key] += 1
            if proxy_without_new and not manual_without_new and len(examples["ложно без нового"]) < 5:
                examples["ложно без нового"].append(text[:150])
            if manual_without_new and not proxy_without_new and len(examples["пропущено без нового"]) < 5:
                examples["пропущено без нового"].append(text[:150])

    tp = matrix[(True, True)]
    fp = matrix[(False, True)]
    fn = matrix[(True, False)]
    tn = matrix[(False, False)]
    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    agreement = (tp + tn) / total if total else 0.0

    lines = [
        "# Калибровка прокси M03 по ручной разметке",
        "",
        f"Сверено {total} предложений — те же, что размечены вручную в `m03-manual.csv`.",
        "",
        "| | прокси: без нового | прокси: новое |",
        "|---|---|---|",
        f"| **разметка: без нового** | {tp} | {fn} |",
        f"| **разметка: новое** | {fp} | {tn} |",
        "",
        f"Совпадение вердиктов: **{agreement:.1%}**. Точность прокси на классе «без нового»: "
        f"**{precision:.1%}**, полнота: **{recall:.1%}**.",
        "",
        "## Где прокси ошибается",
        "",
        "**Считает «без нового» то, что несёт новое.** Обычно это предложение, целиком собранное "
        "из уже встречавшихся слов, но в новом сочетании.",
        "",
    ]
    for text in examples["ложно без нового"]:
        lines.append(f"- «{text}»")
    lines += [
        "",
        "**Пропускает настоящие повторы.** Перефразирование другими словами прокси не видит: "
        "для него «файл управляет обходом» и «этот документ регулирует сканирование» — разные предложения.",
        "",
    ]
    for text in examples["пропущено без нового"]:
        lines.append(f"- «{text}»")
    lines += [
        "",
        "## Что из этого следует",
        "",
        "Прокси не заменяет схему. Он показывает, насколько далеко уходит автоматика, и потому "
        "остаётся exploratory, тогда как первичным исходом служит ручная доля на двадцати документах.",
    ]
    CALIBRATION.write_text("\n".join(lines), encoding="utf-8")
    print(f"калибровка: {CALIBRATION.relative_to(ROOT)}")
    print(f"  совпадение {agreement:.1%}, точность {precision:.1%}, полнота {recall:.1%}")
    print(f"  матрица: TP={tp} FP={fp} FN={fn} TN={tn}")
    return 0


def context_of(doc_id, sentence_index, manifest):
    """Предшествующий контекст: все предложения prose до проверяемого и заголовок над ним.

    Схема §2 требует судить о новизне относительно всего предыдущего текста
    документа, а §3 отдельно упоминает повтор тезиса заголовка. Без обеих
    величин перепроверка вырождается в оценку отдельной фразы, и согласие,
    посчитанное по ней, ничего не значит.
    """
    path = STANZA_CACHE / f"{doc_id}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        parsed = json.load(fh)
    before = []
    for index, sentence in enumerate(parsed["sentences"]):
        if index >= sentence_index:
            break
        words = [token for token in sentence if token["p"] != PUNCT_POS]
        if len(words) < MIN_SENTENCE_WORDS:
            continue
        before.append(" ".join(token["t"] for token in sentence))

    target = " ".join(
        token["t"] for token in parsed["sentences"][sentence_index]
    )
    heading = find_heading(doc_id, target, manifest)
    return " ".join(before), heading


def find_heading(doc_id, sentence_text, manifest):
    """Последний заголовок профиля full перед предложением."""
    row = manifest.get(doc_id)
    if row is None:
        return ""
    path = ROOT / row["full_path"]
    if not path.exists():
        return ""
    needle = "".join(sentence_text.split()).casefold()
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "".join(stripped.split()).casefold().find(needle[:80]) >= 0 and len(needle) > 20:
            return current
        match = MD_HEADING.match(stripped)
        if match:
            current = match.group(1)
            continue
        words = stripped.split()
        if len(words) < HEADING_MAX_WORDS and not stripped.endswith((".", "!", "?", "…", ":", ";")):
            current = stripped
    # Предложение в профиле full не найдено: вернуть накопленный заголовок значило
    # бы выдать последний заголовок документа за предшествующий.
    return "— не определён —"


def build_check_file():
    """50 предложений на слепую перепроверку PI — §6 спецификации.

    Файл не содержит меток ассистента: слепота проверки сохраняется. Зато он
    содержит весь предшествующий контекст — без него схему применить нельзя.
    """
    manual = read_rows(MANUAL)
    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    rng = random.Random(CHECK_SEED)
    picked = rng.sample(manual, CHECK_SIZE)

    with CHECK_FILE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["document_id", "sentence_index", "previous_heading",
                        "previous_context", "text", "verdict", "disputed"],
        )
        writer.writeheader()
        for row in picked:
            context, heading = context_of(row["document_id"], int(row["sentence_index"]), manifest)
            writer.writerow({
                "document_id": row["document_id"],
                "sentence_index": row["sentence_index"],
                "previous_heading": heading,
                "previous_context": context,
                "text": row["text"],
                "verdict": "",
                "disputed": "",
            })
    print(f"файл для проверки PI: {CHECK_FILE.relative_to(ROOT)}, {CHECK_SIZE} предложений без меток")
    return 0


def make_record(doc_id, share, count, version, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": "M03",
        "feature_name": "" if reason else "Предложения без нового информационного элемента",
        "extractor_version": version,
        "preprocessing_profile": "prose",
        "raw_value": "" if share is None else f"{share:.6g}",
        "normalized_value": "" if count is None else f"{count:.6g}",
        "unit": "" if reason else "доля предложений",
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
            if row["document_id"] not in registry or row["feature_id"] in OWNED:
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

    backup = MATRIX.with_suffix(".csv.bak-before-m03")
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")


def main():
    global PREP_VERSION, MANIFEST, MATRIX
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--check-file", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга на входе")
    args = parser.parse_args()

    PREP_VERSION = args.prep_version
    MANIFEST = ROOT / "04-corpus" / "derived" / PREP_VERSION / "manifest.csv"
    MATRIX = fc.matrix_path(PREP_VERSION, MATRIX)

    if args.calibrate:
        return calibrate()
    if args.check_file:
        return build_check_file()

    rows = read_rows(DOCUMENTS)
    registry = {row["document_id"]: row for row in rows}
    if args.limit:
        rows = rows[: args.limit]

    manual = read_rows(MANUAL)
    manual_docs = {}
    for row in manual:
        manual_docs.setdefault(row["document_id"], []).append(row["verdict"] == "без нового")

    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records, proxy_shares = [], []
    missing = 0

    for row in rows:
        doc_id = row["document_id"]
        if doc_id in manual_docs:
            flags = manual_docs[doc_id]
            records.append(make_record(
                doc_id, sum(flags) / len(flags), sum(flags), MANUAL_VERSION, computed_at
            ))
            continue
        # Прокси в матрицу не пишется: обе проверенные редакции — лексическая и
        # семантическая — дали F1 = 0.25 против ручной разметки. Слабый прокси
        # хуже пропуска: он создаёт видимость измерения там, где измерения нет.
        missing += 1
        records.append(make_record(
            doc_id, None, None, PROXY_VERSION, computed_at,
            "требует ручной разметки: автоматический прокси проверен и отклонён",
        ))

    if args.limit:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)

    ordered = sorted(proxy_shares)
    print(f"M03 записан: строк {len(records)}")
    print(f"  ручная разметка: {len(manual_docs)} документов")
    if ordered:
        print(f"  прокси: {len(ordered)} документов, медиана {ordered[len(ordered) // 2]:.4f}, "
              f"мин {ordered[0]:.4f}, макс {ordered[-1]:.4f}")
    if missing:
        print(f"  ! без значения: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
