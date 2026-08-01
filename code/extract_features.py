#!/usr/bin/env python3
"""Расчёт признаков по кодбуку 06-features/codebook.md.

Запуск из корня папки исследования:
    python 09-tools/extract_features.py --stage parse       # разбор Stanza в кэш
    python 09-tools/extract_features.py --stage features    # признаки из кэша
    python 09-tools/extract_features.py --stage features --limit 20

Два этапа разделены намеренно: разбор стоит около 1.7 секунды на документ и
не должен пересчитываться при каждой правке формулы признака.

Вход — производные версии `prep-v4` (09-tools/prep.py), а не сырые файлы.
Профили не смешиваются: лексика, синтаксис и ритм считаются на `prose`,
плотность чисел и форматные признаки — на `full` (§1 preprocessing-spec.md).

Выход — `06-features/feature-matrix.csv` в длинном формате по схеме
`06-features/feature-matrix-schema.csv`: строка на пару документ × признак.

Чего этот скрипт не считает и почему. Признаки, определяемые словарём
маркеров (L07, L09, D01, D02, D06, D07), не считаются вовсе. Рецензируемого
частотного списка русских лексических маркеров не существует
(`ai-human-style-lab-v0.2.0/russian-language-notes.md`, §5), а список,
составленный после сбора корпуса, — это ровно «признаки придуманы после
результатов» из таблицы рисков. Такие признаки попадают в матрицу со
статусом missing и ждут фиксации списка амендментом. То же для признаков с
ручной схемой (M03, M06, C07, D03), семантики на эмбеддингах (M01, M02, M05),
NER (C01) и межтекстовых (X01–X05): у каждого свой этап.
"""

import argparse
import csv
import gzip
import json
import math
import re
import statistics
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
DERIVED = ROOT / "04-corpus" / "derived" / "prep-v4"
MANIFEST = DERIVED / "manifest.csv"
CACHE = ROOT / "06-features" / "cache" / "stanza-v1"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"

EXTRACTOR_VERSION = "feat-v1"
PREP_VERSION = "prep-v4"
# Ревизия разбора входит в ключ кеша. Значение проверено запросом к пакету
# 2026-07-29; несовпадение с установленной версией останавливает разбор.
STANZA_REVISION = "stanza 1.14.0/ru-syntagrus"

# Порог кодбука §10: на текстах короче 100 слов признаки не считаются.
MIN_WORDS = 100
# Документов в пакете разбора Stanza.
BATCH = 8

# Знаменательные части речи для лексической плотности (L05).
CONTENT_POS = {"NOUN", "VERB", "ADJ", "ADV", "PROPN"}
# Служебные для L06.
FUNCTION_POS = {"ADP", "CCONJ", "SCONJ", "PART", "PRON", "DET", "AUX"}
# Придаточные для S05.
CLAUSE_DEPRELS = {"acl", "advcl", "ccomp", "xcomp", "csubj", "acl:relcl", "csubj:pass"}
# Суффиксы отглагольных существительных для L08. Это морфологический список,
# а не словарь маркеров: он проверяется по лемме и не зависит от темы текста.
NOMINALIZATION = ("ние", "ение", "ание", "тие", "ация", "изация", "ость", "ство")

NUMBER = re.compile(r"\b\d[\d\s,.]*\b")
URL = re.compile(r"https?://\S+|www\.\S+")
DATE = re.compile(
    r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"
    r"|\b\d{1,2}\s+(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
    r"|\b(19|20)\d{2}\s*(год|г\.)",
    re.IGNORECASE,
)
QUOTE_SPAN = re.compile(r"[«\"]([^«»\"]{20,})[»\"]")
EM_DASH = re.compile(r"[—–]")
COLON = re.compile(r":")

# Признаки, которые этот экстрактор не считает, с причиной.
DEFERRED = {
    "L07": "словарь абстрактности не зафиксирован",
    "L09": "словарь оценочной лексики не зафиксирован",
    "C01": "NER считается отдельным этапом (Natasha/Slovnet)",
    "C04": "список единиц измерения не зафиксирован",
    "C07": "ручная схема",
    "D01": "список хеджей не зафиксирован амендментом",
    "D02": "список приблизителей не зафиксирован амендментом",
    "D03": "ручная схема",
    "D04": "правило распознавания триколона не зафиксировано",
    "D05": "требует различения риторического и обычного вопроса",
    "D06": "список signposting не зафиксирован амендментом",
    "D07": "список конструкций баланса не зафиксирован амендментом",
    "D08": "ручная схема",
    "D09": "составная шкала 0–3, ручная схема",
    "M01": "эмбеддинги, отдельный этап",
    "M02": "эмбеддинги, отдельный этап",
    "M03": "ручная схема",
    "M04": "ручная схема",
    "M05": "эмбеддинги, отдельный этап",
    "M06": "ручная проверка",
    "F04": "список плейсхолдеров не зафиксирован",
    "F05": "паттерны служебной разметки не зафиксированы",
    "F06": "требует сопоставления с ролью канала",
    "F07": "требует сопоставления с заданием",
    "F08": "проверяется по смыслу задания, не поиском строки",
    "X01": "межтекстовый признак, отдельный этап",
    "X02": "межтекстовый признак, отдельный этап",
    "X03": "межтекстовый признак, отдельный этап",
    "X04": "межтекстовый признак, отдельный этап",
    "X05": "межтекстовый признак, отдельный этап",
    "Y01": "требует прогона нейтрального рерайта",
}


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def set_prep_version(version):
    """Переключение версии препроцессинга: вход, манифест и файл выхода.

    prep-v4 остаётся значением по умолчанию и пишет в ту же матрицу, что и
    раньше. Для остальных версий выход — `features-normalized-<версия>.csv`, а
    жанровые перцентили в матрицу не пишутся: они корпус-зависимы и считаются
    отдельным этапом `compute_percentiles.py` (решение PI 2026-07-29).
    """
    global PREP_VERSION, DERIVED, MANIFEST, MATRIX
    PREP_VERSION = version
    DERIVED = ROOT / "04-corpus" / "derived" / version
    MANIFEST = DERIVED / "manifest.csv"
    if version != "prep-v4":
        MATRIX = ROOT / "06-features" / f"features-normalized-{version}.csv"


# --- этап 1: разбор ------------------------------------------------------


def parse_stage(rows, limit, only, force=False):
    import stanza

    CACHE.mkdir(parents=True, exist_ok=True)
    nlp = stanza.Pipeline(
        "ru", package="syntagrus", processors="tokenize,pos,lemma,depparse",
        use_gpu=False, verbose=False,
    )
    revision = f"stanza {stanza.__version__}/ru-syntagrus"
    if revision != STANZA_REVISION:
        raise SystemExit(f"установлена {revision}, в ключе кеша {STANZA_REVISION}: "
                         "запись новой ревизии смешалась бы со старой")
    index = fc.load_index(CACHE)
    print(f"Разбор Stanza: пайплайн ru/syntagrus загружен, ревизия {revision}")

    # Годность записи кеша решает вход, а не имя файла: при коррекции prep-v5 у
    # 68 документов текст изменился, а document_id остался прежним.
    pending, skipped = [], 0
    for row in rows:
        doc_id = row["document_id"]
        path = DERIVED / "prose" / f"{doc_id}.txt"
        if not path.exists():
            continue
        input_sha = fc.sha256_file(path)
        if not force and fc.lookup(CACHE, index, doc_id, input_sha, revision):
            skipped += 1
            continue
        pending.append((doc_id, path, input_sha))
    print(f"К разбору: {len(pending)}, годных записей в кэше: {skipped}")

    done = 0
    for start in range(0, len(pending), BATCH):
        batch = pending[start : start + BATCH]
        texts = [path.read_text(encoding="utf-8") for _, path, _ in batch]
        # Пакетный вызов вместо поштучного: на замере 10 документов дал
        # 2.23 против 2.73 секунды на документ.
        parsed_batch = nlp([stanza.Document([], text=text) for text in texts])

        for (doc_id, _, input_sha), text, parsed in zip(batch, texts, parsed_batch):
            payload = {
                "document_id": doc_id,
                "prep_version": PREP_VERSION,
                "profile": "prose",
                "stanza": stanza.__version__,
                "sentences": [
                    [
                        {"t": w.text, "l": w.lemma or w.text, "p": w.upos, "d": w.deprel or "", "h": w.head, "i": w.id,
                         "f": w.feats or ""}
                        for w in sentence.words
                    ]
                    for sentence in parsed.sentences
                ],
                "paragraphs": [len(part.split()) for part in text.split("\n\n") if part.strip()],
            }
            name = fc.new_name(doc_id, input_sha, ".json.gz")
            with gzip.open(CACHE / name, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            fc.stamp(index, doc_id, input_sha, PREP_VERSION, revision, name)
            done += 1
        fc.save_index(CACHE, index)
        print(f"  разобрано {done} из {len(pending)}", flush=True)
    fc.save_index(CACHE, index)
    print(f"Готово: разобрано {done}, взято из кэша {skipped}")
    return 0


# --- лексическое разнообразие -------------------------------------------


def mtld(tokens, threshold=0.720):
    """MTLD по леммам, двусторонний прогон (McCarthy & Jarvis, 2010)."""
    if len(tokens) < MIN_WORDS:
        return None

    def run(sequence):
        factors, types, count = 0.0, set(), 0
        for token in sequence:
            types.add(token)
            count += 1
            if len(types) / count <= threshold:
                factors += 1
                types, count = set(), 0
        if count:
            ratio = len(types) / count
            factors += (1 - ratio) / (1 - threshold) if threshold < 1 else 0
        return len(sequence) / factors if factors else float(len(sequence))

    return (run(tokens) + run(list(reversed(tokens)))) / 2


def mattr(tokens, window=50):
    if len(tokens) < window:
        return None
    values = []
    types = Counter(tokens[:window])
    values.append(len(types) / window)
    for index in range(window, len(tokens)):
        out_token = tokens[index - window]
        types[out_token] -= 1
        if types[out_token] == 0:
            del types[out_token]
        types[tokens[index]] += 1
        values.append(len(types) / window)
    return sum(values) / len(values)


def hdd(tokens, sample=42):
    """HD-D: ожидаемая доля типов в случайной выборке заданного объёма."""
    if len(tokens) < sample:
        return None
    total = len(tokens)
    counts = Counter(tokens)
    contribution = 0.0
    for count in counts.values():
        # Вероятность, что тип не попал в выборку.
        if total - count < sample:
            probability = 0.0
        else:
            probability = math.exp(
                math.lgamma(total - count + 1) - math.lgamma(total - count - sample + 1)
                - math.lgamma(total + 1) + math.lgamma(total - sample + 1)
            )
        contribution += (1 - probability) / sample
    return contribution


# --- этап 2: признаки ----------------------------------------------------


def tree_depth(sentence):
    """Глубина дерева зависимостей. Сломанное дерево — цикл или потерянный
    head — не считается: возвращается None, документ получает missing."""
    heads = {word["i"]: word["h"] for word in sentence}
    depth_of = {}

    def depth(node, seen):
        if node == 0:
            return 0
        if node in depth_of:
            return depth_of[node]
        if node in seen or node not in heads:
            return None
        seen.add(node)
        parent = depth(heads[node], seen)
        if parent is None:
            return None
        depth_of[node] = parent + 1
        return depth_of[node]

    values = []
    for word in sentence:
        value = depth(word["i"], set())
        if value is None:
            return None
        values.append(value)
    return values


def document_features(parsed, manifest_row):
    """Все признаки одного документа. Ключ — feature_id кодбука."""
    sentences = parsed["sentences"]
    words = [w for sentence in sentences for w in sentence if w["p"] != "PUNCT"]
    lemmas = [w["l"].lower() for w in words]
    total = len(words)

    out, skipped = {}, {}
    if total < MIN_WORDS:
        return out, {"*": f"документ короче {MIN_WORDS} слов"}

    per_1000 = 1000 / total
    lengths = [len([w for w in sentence if w["p"] != "PUNCT"]) for sentence in sentences]
    lengths = [length for length in lengths if length]

    # 1. Лексика
    out["L01"] = ("MTLD по леммам", mtld(lemmas), None, "фактор")
    out["L02"] = ("MATTR по леммам, окно 50", mattr(lemmas), None, "доля")
    out["L03"] = ("HD-D", hdd(lemmas), None, "доля")
    hapax = sum(1 for _, count in Counter(lemmas).items() if count == 1)
    out["L04"] = ("Доля hapax legomena", hapax, hapax / len(set(lemmas)) if lemmas else None, "доля от типов")
    content = sum(1 for w in words if w["p"] in CONTENT_POS)
    out["L05"] = ("Лексическая плотность", content, content / total, "доля")
    function = sum(1 for w in words if w["p"] in FUNCTION_POS)
    out["L06"] = ("Частота служебных слов", function, function * per_1000, "на 1000 слов")
    nominal = sum(1 for w in words if w["p"] == "NOUN" and w["l"].lower().endswith(NOMINALIZATION))
    out["L08"] = ("Номинализация", nominal, nominal * 100 / total, "на 100 слов")

    # 2. Синтаксис
    out["S01"] = ("Средняя длина предложения", statistics.mean(lengths), None, "слов")
    if len(lengths) > 1:
        deviation = statistics.stdev(lengths)
        out["S02"] = ("SD длины предложения", deviation, deviation / statistics.mean(lengths), "слов / CV")
    else:
        skipped["S02"] = "меньше двух предложений"

    depths, distances, broken = [], [], 0
    for sentence in sentences:
        values = tree_depth(sentence)
        if values is None:
            broken += 1
            continue
        depths.extend(values)
        for word in sentence:
            if word["h"]:
                distances.append(abs(word["i"] - word["h"]))
    if depths:
        out["S03"] = ("Глубина дерева зависимостей, среднее", statistics.mean(depths), max(depths), "узлов / максимум")
        out["S04"] = ("MDD, средняя дистанция зависимости", statistics.mean(distances), None, "токенов")
    else:
        skipped["S03"] = "все деревья сломаны"
        skipped["S04"] = "все деревья сломаны"
    out["S10"] = ("Доля сломанных деревьев", broken, broken / len(sentences) if sentences else None, "доля предложений")

    clauses = sum(1 for sentence in sentences for w in sentence if w["d"] in CLAUSE_DEPRELS)
    out["S05"] = ("Придаточные", clauses, clauses / len(sentences), "на предложение")

    impersonal = 0
    passive = 0
    for sentence in sentences:
        deprels = {w["d"] for w in sentence}
        root = next((w for w in sentence if w["d"] == "root"), None)
        if root and root["p"] in {"VERB", "AUX"} and not ({"nsubj", "nsubj:pass"} & deprels):
            impersonal += 1
        if ({"nsubj:pass", "aux:pass", "obl:agent"} & deprels) or any("Voice=Pass" in w["f"] for w in sentence):
            passive += 1
    out["S06"] = ("Безличные конструкции", impersonal, impersonal * 100 / len(sentences), "на 100 предложений")
    out["S07"] = ("Пассивный залог", passive, passive / len(sentences), "доля предложений")

    starts = Counter()
    for sentence in sentences:
        content_words = [w["l"].lower() for w in sentence if w["p"] != "PUNCT"]
        if len(content_words) >= 2:
            starts[tuple(content_words[:2])] += 1
    repeated = sum(count for pair, count in starts.items() if count > 1)
    out["S08"] = ("Повтор начал предложений", repeated, repeated / len(sentences), "доля предложений")

    pos_sequence = [w["p"] for sentence in sentences for w in sentence]
    trigrams = [tuple(pos_sequence[i : i + 3]) for i in range(len(pos_sequence) - 2)]
    if trigrams:
        unique = len(set(trigrams))
        out["S09"] = ("Повтор POS-триграмм", len(trigrams) - unique, 1 - unique / len(trigrams), "доля повторных")

    # 3. Ритм
    if len(lengths) > 1:
        out["R01"] = ("CV длины предложений", statistics.stdev(lengths) / statistics.mean(lengths), None, "CV")
        out["R04"] = ("Разброс длин предложений", max(lengths) - min(lengths), None, "слов")
        band = sum(1 for length in lengths if 10 <= length <= 20)
        out["R05"] = ("Доля предложений 10–20 слов", band, band / len(lengths), "доля")
    paragraphs = [value for value in parsed.get("paragraphs", []) if value]
    if len(paragraphs) > 1:
        out["R02"] = ("CV длины абзацев", statistics.stdev(paragraphs) / statistics.mean(paragraphs), None, "CV")
        out["R03"] = ("Слов в абзаце, среднее", statistics.mean(paragraphs), statistics.stdev(paragraphs), "слов / SD")
    else:
        skipped["R02"] = "меньше двух абзацев"
        skipped["R03"] = "меньше двух абзацев"

    # Форматные и структурные признаки берутся из манифеста препроцессинга:
    # они по определению живут в профиле full, а не в связной прозе.
    if manifest_row:
        full_words = float(manifest_row["full_words"] or 0)
        if full_words:
            scale = 1000 / full_words
            for feature_id, name, column in (
                ("R07", "Частота списков", "list_items"),
                ("F01", "Жирные фрагменты", "full_bold_spans"),
                ("R06", "Заголовки", "heading_md"),
            ):
                value = float(manifest_row.get(column) or 0)
                out[feature_id] = (name, value, value * scale, "на 1000 слов профиля full")
            dropped = 1 - float(manifest_row["prose_words"] or 0) / full_words
            out["P01"] = ("Доля текста вне связной прозы", dropped, None, "доля профиля full")

    return out, skipped


def surface_features(text, total_words):
    """Признаки, считаемые по строке профиля full: числа, даты, ссылки,
    цитаты, типографика. Разбор для них не нужен."""
    if not total_words:
        return {}
    per_1000 = 1000 / total_words
    numbers = len(NUMBER.findall(text))
    dates = len(DATE.findall(text))
    urls = len(URL.findall(text))
    quotes = len(QUOTE_SPAN.findall(text))
    dashes = len(EM_DASH.findall(text))
    colons = len(COLON.findall(text))
    return {
        "C02": ("Числа", numbers, numbers * per_1000, "на 1000 слов"),
        "C03": ("Даты", dates, dates * per_1000, "на 1000 слов"),
        "C05": ("Ссылки", urls, urls * per_1000, "на 1000 слов"),
        "C06": ("Прямые цитаты", quotes, quotes * per_1000, "на 1000 слов"),
        "F02": ("Длинные тире", dashes, dashes * per_1000, "на 1000 слов"),
        "F03": ("Двоеточия", colons, colons * per_1000, "на 1000 слов"),
    }


def features_stage(rows, limit, only, out=None):
    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    registry = {row["document_id"]: row for row in rows}

    records = []
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    missing_cache, stale_cache = [], []
    index = fc.load_index(CACHE)
    revision = STANZA_REVISION

    for row in rows:
        doc_id = row["document_id"]
        # Разбор адресуется хешем входа своей версии препроцессинга: запись,
        # построенная на другом тексте, молча не берётся.
        input_file = DERIVED / "prose" / f"{doc_id}.txt"
        if not input_file.exists():
            missing_cache.append(doc_id)
            continue
        cache_path = fc.lookup(CACHE, index, doc_id, fc.sha256_file(input_file),
                               revision)
        if cache_path is None:
            stale_cache.append(doc_id)
            continue
        with gzip.open(cache_path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)

        manifest_row = manifest.get(doc_id)
        values, skipped = document_features(parsed, manifest_row)

        if manifest_row:
            full_path = ROOT / manifest_row["full_path"]
            if full_path.exists():
                values.update(
                    surface_features(full_path.read_text(encoding="utf-8"), float(manifest_row["full_words"] or 0))
                )

        for feature_id, (name, raw, normalized, unit) in sorted(values.items()):
            profile = "full" if feature_id in {"R06", "R07", "F01", "F02", "F03", "C02", "C03", "C05", "C06", "P01"} else "prose"
            records.append(
                {
                    "document_id": doc_id,
                    "feature_id": feature_id,
                    "feature_name": name,
                    "extractor_version": EXTRACTOR_VERSION,
                    "preprocessing_profile": profile,
                    "raw_value": "" if raw is None else f"{raw:.6g}" if isinstance(raw, float) else raw,
                    "normalized_value": "" if normalized is None else f"{normalized:.6g}" if isinstance(normalized, float) else normalized,
                    "unit": unit,
                    "genre_percentile": "",
                    "missing_reason": "",
                    "computed_at": computed_at,
                }
            )
        for feature_id, reason in sorted(skipped.items()):
            records.append(
                {
                    "document_id": doc_id, "feature_id": feature_id, "feature_name": "",
                    "extractor_version": EXTRACTOR_VERSION, "preprocessing_profile": "prose",
                    "raw_value": "", "normalized_value": "", "unit": "",
                    "genre_percentile": "", "missing_reason": reason, "computed_at": computed_at,
                }
            )
        for feature_id, reason in sorted(DEFERRED.items()):
            records.append(
                {
                    "document_id": doc_id, "feature_id": feature_id, "feature_name": "",
                    "extractor_version": EXTRACTOR_VERSION, "preprocessing_profile": "",
                    "raw_value": "", "normalized_value": "", "unit": "",
                    "genre_percentile": "", "missing_reason": reason, "computed_at": computed_at,
                }
            )

    # Перцентиль внутри жанра: считается после всех документов, иначе шкала
    # зависела бы от порядка обработки. С prep-v5 этап вынесен в
    # compute_percentiles.py — перцентиль зависит от состава корпуса, и при
    # частичном пересчёте пул здесь оказался бы неполным.
    # Проверка идёт до записи: матрица без части документов не должна появиться
    # на диске даже на секунду — её успел бы прочитать следующий этап.
    if stale_cache:
        raise SystemExit(
            f"разбор в кэше построен на другом тексте у {len(stale_cache)} документов "
            f"({', '.join(stale_cache[:5])}…): запустить --stage parse "
            f"--prep-version {PREP_VERSION}")

    if PREP_VERSION == "prep-v4":
        by_feature_genre = {}
        for record in records:
            if record["normalized_value"] == "" and record["raw_value"] == "":
                continue
            genre = registry[record["document_id"]]["genre"]
            key = (record["feature_id"], genre)
            value = float(record["normalized_value"] or record["raw_value"])
            by_feature_genre.setdefault(key, []).append(value)
        for key in by_feature_genre:
            by_feature_genre[key].sort()
        for record in records:
            if record["normalized_value"] == "" and record["raw_value"] == "":
                continue
            genre = registry[record["document_id"]]["genre"]
            pool = by_feature_genre[(record["feature_id"], genre)]
            value = float(record["normalized_value"] or record["raw_value"])
            rank = sum(1 for item in pool if item < value)
            record["genre_percentile"] = f"{rank / len(pool):.4f}" if pool else ""

    # Порядок колонок задан схемой 06-features/feature-matrix-schema.csv.
    # С ключом --out записи уходят в отдельный файл: повторный прогон на
    # подвыборке не должен переписывать матрицу строками нескольких документов.
    target = Path(out) if out else MATRIX
    with (ROOT / "06-features" / "feature-matrix-schema.csv").open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    documents = len({record["document_id"] for record in records})
    computed = {record["feature_id"] for record in records if not record["missing_reason"]}
    print(f"Матрица признаков: {target}")
    print(f"  документов: {documents}, строк: {len(records)}")
    print(f"  посчитано признаков: {len(computed)} — {', '.join(sorted(computed))}")
    print(f"  отложено до фиксации словарей и отдельных этапов: {len(DEFERRED)}")
    if missing_cache:
        print(f"  ! нет разбора в кэше: {len(missing_cache)} документов, запустить --stage parse")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("parse", "features"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only")
    parser.add_argument("--ids-file", help="пересчитать только документы из файла, по идентификатору на строку")
    parser.add_argument("--force", action="store_true", help="переразобрать документы, уже лежащие в кэше")
    parser.add_argument("--out", help="записать результат в CSV вместо матрицы")
    # prep-v5, 2026-07-29: версия препроцессинга задаётся параметром. Значение по
    # умолчанию не меняется — без флага скрипт воспроизводит prep-v4.
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга на входе")
    args = parser.parse_args()

    set_prep_version(args.prep_version)

    rows = read_rows(DOCUMENTS)
    if args.ids_file:
        wanted = {line.strip() for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if line.strip()}
        rows = [row for row in rows if row["document_id"] in wanted]
        print(f"пересчёт по списку: {len(rows)} документов")
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    if args.stage == "parse":
        return parse_stage(rows, args.limit, args.only, args.force)
    return features_stage(rows, args.limit, args.only, args.out)


if __name__ == "__main__":
    sys.exit(main())
