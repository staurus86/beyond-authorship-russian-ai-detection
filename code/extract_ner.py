#!/usr/bin/env python3
"""NER-слой: признак C01 и диагностика по типам сущностей.

Процедура зафиксирована в `06-features/ner-spec.md` до первого прогона на
корпусе. Здесь только её реализация — расхождение между спецификацией и кодом
считается ошибкой кода.

Запуск из корня папки исследования:
    python 09-tools/extract_ner.py --stage tag                # разметка в кэш
    python 09-tools/extract_ner.py --stage tag --limit 20     # проба
    python 09-tools/extract_ner.py --stage features           # C01 из кэша
    python 09-tools/extract_ner.py --stage pilot              # выборка на ручную проверку

Два этапа разделены по той же причине, что в семантическом слое: правка
формулы признака не должна стоить повторного прогона разметчика.

Текст берётся из профиля `full` манифеста prep-v4 — того же, по которому
считаются C02, C03, C05 и C06.
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
NER_CACHE = ROOT / "06-features" / "cache" / "ner-v1"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"
PILOT_IDS = ROOT / "06-features" / "pilot-1-ids.csv"

# Кэш спанов остаётся от ner-v1: правки версий v2 и v3 применяются при подсчёте,
# сама разметка не менялась и переразметки не требует.
EXTRACTOR_VERSION = "ner-v3"
# prep-v5, 2026-07-29: версия препроцессинга задаётся параметром, значение по
# умолчанию не меняется.
PREP_VERSION = "prep-v4"
NER_REVISION = "natasha 1.6.0/slovnet 0.6.0/navec 0.10.0"
PROFILE = "full"
OWNED = ("C01",)
SPAN_TYPES = ("PER", "LOC", "ORG")
# Спанов на жанр в выборку ручной проверки, §5 спецификации.
PILOT_SPANS_PER_GENRE = 20
PILOT_CONTEXT = 60

# §7 спецификации, правка первая: зона библиографии в счёт не идёт.
BIB_HEAD = re.compile(
    r"(список\s+(?:использованн\w+\s+)?(?:источник\w+|литератур\w+)"
    r"|библиографическ\w+\s+список|литература|библиография|references)",
    re.I,
)
BIB_MARK = re.compile(r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]?\.?|[—-]\s*[МСПб]{1,4}\.,|\d{4}\.\s")
BIB_TAIL_SHARE = 0.45
BIB_MIN_MARKS = 3
BIB_MIN_DENSITY = 1.5
# Список без заголовка: подряд идущие записи «1. Фамилия…» (`ner-v3`).
BIB_ENTRY = re.compile(r"[\s]\d{1,3}\.\s+[А-ЯЁA-Z]")
BIB_MIN_NUMBERED = 3
# Выходные данные издания. Нужны, чтобы отличить библиографию от нумерованного
# перечня в обычном тексте: новостная сводка с пунктами «7. Военным судом…» и
# инициалами фигурантов иначе отрезалась бы как список литературы.
BIB_IMPRINT = re.compile(
    r"[—-]\s*[А-ЯЁ]{1,4}\.,"
    r"|\d{4}\.\s*[—-]?\s*\d*\s*с\."
    r"|//"
    r"|Вып\.|Изд\.|Т\.\s?\d|С\.\s?\d+\s*[—–-]\s*\d+"
)
BIB_MIN_IMPRINT_DENSITY = 1.0

# §7 спецификации, правка вторая: пары типов, у которых разрыв означает одно
# составное название, а не два объекта подряд.
GLUE_PAIRS = {("ORG", "LOC"), ("ORG", "ORG")}

# Отчёты именуются по версии экстрактора: прежние остаются рядом для сверки.
TYPES_REPORT = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-types.csv"
PILOT_REPORT = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-pilot.md"


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_natasha():
    from natasha import Doc, MorphVocab, NewsEmbedding, NewsMorphTagger, NewsNERTagger, Segmenter

    embedding = NewsEmbedding()
    return {
        "Doc": Doc,
        "segmenter": Segmenter(),
        "morph": NewsMorphTagger(embedding),
        "ner": NewsNERTagger(embedding),
        "vocab": MorphVocab(),
    }


def tag_document(tools, text):
    """Спаны одного документа: тип, границы, поверхностная и нормальная форма."""
    doc = tools["Doc"](text)
    doc.segment(tools["segmenter"])
    doc.tag_morph(tools["morph"])
    doc.tag_ner(tools["ner"])
    spans = []
    for span in doc.spans:
        span.normalize(tools["vocab"])
        spans.append(
            {
                "type": span.type,
                "start": span.start,
                "stop": span.stop,
                "text": span.text,
                "normal": span.normal or "",
            }
        )
    return spans


def tag_stage(rows, manifest, force=False):
    tools = load_natasha()
    NER_CACHE.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    done = cached = no_text = 0
    spans_total = 0

    # Годность записи решает хеш входа, а не имя файла: у 68 документов prep-v5
    # текст изменился при прежнем document_id.
    index = fc.load_index(NER_CACHE)
    for position, row in enumerate(rows, 1):
        doc_id = row["document_id"]
        manifest_row = manifest.get(doc_id)
        if manifest_row is None:
            no_text += 1
            continue
        full_path = ROOT / manifest_row["full_path"]
        if not full_path.exists():
            no_text += 1
            continue
        input_sha = manifest_row["full_sha256"]
        if not force and fc.lookup(NER_CACHE, index, doc_id, input_sha, NER_REVISION):
            cached += 1
            continue

        spans = tag_document(tools, full_path.read_text(encoding="utf-8"))
        spans_total += len(spans)
        name = fc.new_name(doc_id, input_sha, ".json.gz")
        with gzip.open(NER_CACHE / name, "wt", encoding="utf-8") as fh:
            json.dump({"document_id": doc_id, "spans": spans}, fh, ensure_ascii=False)
        fc.stamp(index, doc_id, input_sha, PREP_VERSION, NER_REVISION, name)
        fc.save_index(NER_CACHE, index)
        done += 1

        if position % 200 == 0 or position == len(rows):
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            print(f"  {position}/{len(rows)} документов, {elapsed / 60:.1f} мин", flush=True)

    print(f"кэш разметки: {NER_CACHE.relative_to(ROOT)}")
    print(f"  размечено сейчас: {done}, взято из кэша: {cached}, спанов в новых: {spans_total}")
    if no_text:
        print(f"  ! нет текста профиля full: {no_text} документов")
    return 0


def bibliography_start(text):
    """Позиция начала списка литературы или None.

    Два способа. Первый — по заголовку: он ищется в последних BIB_TAIL_SHARE
    текста, и после него требуются маркеры библиографического описания — не
    меньше BIB_MIN_MARKS и не реже BIB_MIN_DENSITY на 100 слов хвоста. Без
    проверки маркерами слово «литература» в художественном тексте отрезало бы
    половину документа.

    Второй способ добавлен в `ner-v3`: часть журналов печатает список без
    заголовка, подряд идущими записями «1. …», «2. …». На таком документе —
    `cyberleninka_0004` — правило по заголовку не срабатывало, и восемь ошибок
    из пятнадцати пришлись на неотрезанную библиографию.
    """
    limit = len(text) * (1 - BIB_TAIL_SHARE)
    for match in BIB_HEAD.finditer(text):
        if match.start() < limit:
            continue
        tail = text[match.end():]
        words = max(1, len(tail.split()))
        marks = len(BIB_MARK.findall(tail))
        if marks >= BIB_MIN_MARKS and marks / words * 100 >= BIB_MIN_DENSITY:
            return match.start()

    # Номер записи ищется по тексту, а не по абзацам: в профиле `full` список
    # часто идёт сплошной строкой, и разбиение по пустой строке его не видит.
    starts = [match.start() + 1 for match in BIB_ENTRY.finditer(text) if match.start() + 1 >= limit]
    for index in range(len(starts) - BIB_MIN_NUMBERED + 1):
        tail = text[starts[index]:]
        words = max(1, len(tail.split()))
        if len(BIB_MARK.findall(tail)) / words * 100 < BIB_MIN_DENSITY:
            continue
        if len(BIB_IMPRINT.findall(tail)) / words * 100 >= BIB_MIN_IMPRINT_DENSITY:
            return starts[index]
    return None


def glue_spans(spans):
    """Смежные спаны составного названия считаются одним упоминанием.

    Возвращает список групп: каждая группа — один или несколько исходных
    спанов, слитых в одно упоминание. Тип упоминания берётся у первого спана
    группы: в паре ORG+LOC главное слово названия стоит первым.
    """
    groups = []
    for span in spans:
        if groups:
            previous = groups[-1][-1]
            adjacent = span["start"] - previous["stop"] == 1
            if adjacent and (previous["type"], span["type"]) in GLUE_PAIRS:
                groups[-1].append(span)
                continue
        groups.append([span])
    return groups


def document_counts(groups):
    """Счёт упоминаний по типам. Единица счёта — группа, а не исходный спан."""
    counts = {kind: 0 for kind in SPAN_TYPES}
    unique = {kind: set() for kind in SPAN_TYPES}
    unnormalized = 0
    for group in groups:
        kind = group[0]["type"]
        if kind not in counts:
            continue
        counts[kind] += 1
        key = " ".join(span["normal"] or span["text"] for span in group).casefold()
        unique[kind].add(key)
        if not group[0]["normal"]:
            unnormalized += 1
    return counts, unique, unnormalized


def features_stage(rows, manifest, limit, out=None):
    registry = {row["document_id"]: row for row in read_rows(DOCUMENTS)}
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    records = []
    diagnostics = []
    missing_cache = []
    zero_entities = 0
    bib_docs = 0
    bib_spans_dropped = 0
    glued = 0

    index = fc.load_index(NER_CACHE)
    for row in rows:
        doc_id = row["document_id"]
        manifest_row = manifest.get(doc_id)
        path = (fc.lookup(NER_CACHE, index, doc_id, manifest_row["full_sha256"],
                          NER_REVISION) if manifest_row else None)
        if path is None:
            missing_cache.append(doc_id)
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            spans = json.load(fh)["spans"]

        words = float(manifest_row["full_words"] or 0) if manifest_row else 0.0
        if words <= 0:
            records.append(
                make_record(doc_id, None, None, computed_at, "нет объёма профиля full")
            )
            continue

        # §7, правка первая: библиография не входит ни в счёт, ни в знаменатель.
        text = (ROOT / manifest_row["full_path"]).read_text(encoding="utf-8")
        cut = bibliography_start(text)
        if cut is not None:
            before = len(spans)
            spans = [span for span in spans if span["start"] < cut]
            bib_spans_dropped += before - len(spans)
            bib_docs += 1
            words -= len(text[cut:].split())
            if words <= 0:
                records.append(
                    make_record(doc_id, None, None, computed_at, "вне библиографии текста не осталось")
                )
                continue

        # §7, правка вторая: составное название — одно упоминание.
        groups = glue_spans(spans)
        glued += len(spans) - len(groups)

        counts, unique, unnormalized = document_counts(groups)
        total = sum(counts.values())
        per_1000 = total / words * 1000
        if total == 0:
            zero_entities += 1
        records.append(make_record(doc_id, total, per_1000, computed_at))

        diagnostic = {
            "document_id": doc_id,
            "origin_class": registry[doc_id]["origin_class"],
            "genre": registry[doc_id]["genre"],
            "full_words": int(words),
            "bibliography_cut": 1 if cut is not None else 0,
            "spans_total": total,
            "per_1000": f"{per_1000:.6g}",
            "unique_total": len(set().union(*unique.values())) if total else 0,
            "unnormalized": unnormalized,
        }
        for kind in SPAN_TYPES:
            diagnostic[f"{kind}_count"] = counts[kind]
            diagnostic[f"{kind}_per_1000"] = f"{counts[kind] / words * 1000:.6g}"
            diagnostic[f"{kind}_unique"] = len(unique[kind])
        diagnostics.append(diagnostic)

    if out:
        written = retest_io.write_records(out, records)
        print(f"повторный прогон, матрица не изменена: {out}, строк {written}")
    elif limit:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)
        write_diagnostics(diagnostics)

    computed = sum(1 for record in records if not record["missing_reason"])
    print(f"NER-слой посчитан: строк {len(records)}, из них со значением {computed}")
    print(f"  документов без единой сущности: {zero_entities}")
    print(f"  документов с отрезанной библиографией: {bib_docs}, снято спанов: {bib_spans_dropped}")
    print(f"  спанов слито в составные названия: {glued}")
    if missing_cache:
        print(f"  ! нет разметки: {len(missing_cache)} документов, запустить --stage tag")
    return 0


def make_record(doc_id, raw, normalized, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": "C01",
        "feature_name": "" if reason else "Именованные сущности",
        "extractor_version": EXTRACTOR_VERSION,
        "preprocessing_profile": PROFILE,
        "raw_value": "" if raw is None else f"{raw:.6g}",
        "normalized_value": "" if normalized is None else f"{normalized:.6g}",
        "unit": "" if reason else "на 1000 слов",
        "genre_percentile": "",
        "missing_reason": reason,
        "computed_at": computed_at,
    }


def write_diagnostics(diagnostics):
    if not diagnostics:
        return
    fields = list(diagnostics[0])
    with TYPES_REPORT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(diagnostics)
    print(f"диагностика по типам: {TYPES_REPORT.relative_to(ROOT)}, строк {len(diagnostics)}")


def merge_into_matrix(records, registry):
    """Замена строк OWNED в общей матрице. Документы вне реестра выбывают."""
    with SCHEMA.open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))

    kept = []
    dropped_stale = 0
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
        # Перцентиль внутри жанра — то же правило, что в feat-v1 и sem-v1.
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

    backup = MATRIX.with_suffix(".csv.bak-before-ner-v1")
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")
    if dropped_stale:
        print(f"  снято строк документов вне реестра: {dropped_stale}")


def pilot_stage(manifest):
    """Выборка спанов на ручную проверку — пункт 4 Pilot-1.

    Берётся первый документ пилота в каждом жанре и первые PILOT_SPANS_PER_GENRE
    его спанов подряд. Порядок задан файлом пилота, отбор внутри документа не
    делается: выбирать спаны глазами значило бы оценивать разметчик по тем
    случаям, которые я счёл интересными.
    """
    registry = {row["document_id"]: row for row in read_rows(DOCUMENTS)}
    pilot_ids = [row["document_id"] for row in read_rows(PILOT_IDS)]

    picked = {}
    for doc_id in pilot_ids:
        row = registry.get(doc_id)
        if row is None:
            continue
        picked.setdefault(row["genre"], doc_id)

    lines = [
        "# Проверка NER по жанрам — пункт 4 Pilot-1",
        "",
        f"Разметчик `ner-v1`, выборка от {datetime.now(timezone.utc).date()}. "
        f"По первому документу пилота на каждый жанр, первые {PILOT_SPANS_PER_GENRE} спанов подряд.",
        "",
        "Пометки в колонке «вердикт» ставятся вручную: `ок` — сущность найдена верно; "
        "`ложь` — сущность выдумана или тип неверен; `граница` — сущность есть, но границы спана "
        "захватили лишнее или потеряли часть.",
        "",
    ]

    for genre in sorted(picked):
        doc_id = picked[genre]
        path = NER_CACHE / f"{doc_id}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            spans = json.load(fh)["spans"]
        text = (ROOT / manifest[doc_id]["full_path"]).read_text(encoding="utf-8")

        # Выборка показывает ровно то, что попадает в счёт: библиография снята,
        # составные названия слиты.
        cut = bibliography_start(text)
        if cut is not None:
            spans = [span for span in spans if span["start"] < cut]
        groups = glue_spans(spans)

        lines.append(f"## {genre} — `{doc_id}`")
        lines.append("")
        note = ", библиография отрезана" if cut is not None else ""
        lines.append(
            f"Упоминаний в документе: {len(groups)}, исходных спанов: {len(spans)}, "
            f"слов: {manifest[doc_id]['full_words']}{note}."
        )
        lines.append("")
        lines.append("| № | тип | текст спана | контекст | вердикт |")
        lines.append("|---|---|---|---|---|")
        for number, group in enumerate(groups[:PILOT_SPANS_PER_GENRE], 1):
            start, stop = group[0]["start"], group[-1]["stop"]
            surface = text[start:stop]
            left = text[max(0, start - PILOT_CONTEXT) : start]
            right = text[stop : stop + PILOT_CONTEXT]
            context = f"…{left}⟦{surface}⟧{right}…".replace("\n", " ").replace("|", "¦")
            lines.append(
                f"| {number} | {group[0]['type']} | {surface.replace('|', '¦')} | {context} |  |"
            )
        lines.append("")

    PILOT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"выборка на проверку: {PILOT_REPORT.relative_to(ROOT)}, жанров {len(picked)}")
    return 0


def main():
    global PREP_VERSION, DERIVED, MANIFEST, MATRIX
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("tag", "features", "pilot"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only")
    parser.add_argument("--ids-file", help="пересчитать только документы из файла, по идентификатору на строку")
    parser.add_argument("--force", action="store_true", help="переразметить документы из кэша")
    parser.add_argument("--out", help="записать результат в CSV вместо слияния в матрицу")
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга на входе")
    args = parser.parse_args()

    PREP_VERSION = args.prep_version
    DERIVED = ROOT / "04-corpus" / "derived" / PREP_VERSION
    MANIFEST = DERIVED / "manifest.csv"
    MATRIX = fc.matrix_path(PREP_VERSION, MATRIX)

    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    rows = read_rows(DOCUMENTS)
    if args.ids_file:
        wanted = {line.strip() for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if line.strip()}
        rows = [row for row in rows if row["document_id"] in wanted]
        print(f"пересчёт по списку: {len(rows)} документов")
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    if args.stage == "tag":
        return tag_stage(rows, manifest, args.force)
    if args.stage == "pilot":
        return pilot_stage(manifest)
    return features_stage(rows, manifest, args.limit, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
