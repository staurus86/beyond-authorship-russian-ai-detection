#!/usr/bin/env python3
"""Семантический слой признаков: M01, M02, M05, X05.

Процедура зафиксирована в `06-features/semantic-spec.md` до первого прогона.
Здесь только её реализация — расхождение между спецификацией и кодом считается
ошибкой кода.

Запуск из корня папки исследования:
    python 09-tools/extract_semantic.py --stage embed              # эмбеддинги в кэш
    python 09-tools/extract_semantic.py --stage embed --limit 20   # проба
    python 09-tools/extract_semantic.py --stage features           # признаки из кэша

Два этапа разделены намеренно: прогон модели на CPU стоит около 4.5 часа и не
должен повторяться при каждой правке формулы.

Границы предложений берутся из кэша `06-features/cache/stanza-v1`, а не
размечаются заново: семантика обязана стоять на тех же границах, что синтаксис.
"""

import argparse
import csv
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import retest_io

# Число потоков задаётся до импорта torch: после инициализации OMP вызов
# torch.set_num_threads уже не разгоняет пул до нужного размера. С потоками,
# выставленными после импорта, прогон шёл на 3.4 ядра из 16.
TORCH_THREADS = "16"
os.environ.setdefault("OMP_NUM_THREADS", TORCH_THREADS)
os.environ.setdefault("MKL_NUM_THREADS", TORCH_THREADS)

import numpy as np

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
EMBED_CACHE = ROOT / "06-features" / "cache" / "embed-v1"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"

EXTRACTOR_VERSION = "sem-v1"
# prep-v5, 2026-07-29: версия препроцессинга задаётся параметром, значение по
# умолчанию не меняется.
PREP_VERSION = "prep-v4"
STANZA_REVISION = "stanza 1.14.0/ru-syntagrus"
MODEL_NAME = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
# Ревизия входит в ключ кеша эмбеддингов.
EMBED_REVISION = f"{MODEL_NAME}@{MODEL_REVISION[:12]}, sentence-transformers 5.5.1"
MAX_SEQ_LENGTH = 512
BATCH_SIZE = 64
# Предложений в общей пачке перед вызовом модели. Больше пачка — плотнее
# упаковка по длине, но и потеря при обрыве прогона больше.
FLUSH_AT = 1024

# §2 спецификации: предложение короче трёх словных токенов меряет пунктуацию.
MIN_TOKENS = 3
# Признаки, которые пересчитывает этот скрипт.
OWNED = ("M01", "M02", "M05", "X05")

PUNCT_POS = "PUNCT"


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def sentence_text(sentence):
    """Текст предложения из токенов кэша Stanza. Склейка через пробел."""
    return " ".join(token["t"] for token in sentence)


def word_tokens(sentence):
    return [token for token in sentence if token["p"] != PUNCT_POS]


def normalize(text):
    """Для сопоставления предложения с абзацем: без пробелов и регистра."""
    return "".join(text.split()).casefold()


def usable_sentences(parsed):
    """Индексы и тексты предложений, проходящих порог MIN_TOKENS."""
    kept = []
    for index, sentence in enumerate(parsed["sentences"]):
        if len(word_tokens(sentence)) < MIN_TOKENS:
            continue
        kept.append((index, sentence_text(sentence)))
    return kept


def embed_stage(rows, limit, force=False):
    import torch
    from sentence_transformers import SentenceTransformer

    # Закреплено ради повторяемости: при разном числе потоков меняется порядок
    # редукции в сумме и повторный прогон перестаёт совпадать с прежним.
    torch.set_num_threads(int(TORCH_THREADS))
    EMBED_CACHE.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, device="cpu")
    model.max_seq_length = MAX_SEQ_LENGTH
    print(f"модель {MODEL_NAME} @ {MODEL_REVISION[:12]}, torch {torch.__version__}")
    print(f"max_seq_length={model.max_seq_length}, batch={BATCH_SIZE}, потоков {torch.get_num_threads()}")

    tokenizer = model.tokenizer
    stanza_index = fc.load_index(STANZA_CACHE)
    embed_index = fc.load_index(EMBED_CACHE)
    out_names = {}
    done = skipped_docs = truncated = 0
    started = datetime.now(timezone.utc)

    # Предложения копятся в общую пачку через границы документов: внутри вызова
    # encode библиотека сортирует по длине, и чем больше пачка, тем меньше
    # паддинга уходит впустую. Документ по 100 предложений даёт плотность
    # заметно хуже, чем пачка на несколько тысяч.
    pending_texts = []
    pending_owner = []  # (document_id, позиция в документе)
    plan = {}  # document_id -> индексы предложений

    def flush():
        nonlocal truncated
        if not pending_texts:
            return
        lengths = tokenizer(pending_texts, add_special_tokens=True, truncation=False)["input_ids"]
        truncated += sum(1 for item in lengths if len(item) > MAX_SEQ_LENGTH)
        vectors = model.encode(
            pending_texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype(np.float32)
        buckets = {}
        for (doc, slot), vector in zip(pending_owner, vectors):
            buckets.setdefault(doc, {})[slot] = vector
        for doc, slots in buckets.items():
            ordered = np.stack([slots[key] for key in sorted(slots)])
            name, input_sha = out_names[doc]
            np.savez_compressed(
                EMBED_CACHE / name,
                embeddings=ordered,
                sentence_index=np.array(plan[doc], dtype=np.int32),
            )
            fc.stamp(embed_index, doc, input_sha, PREP_VERSION, EMBED_REVISION, name)
            del plan[doc]
        fc.save_index(EMBED_CACHE, embed_index)
        pending_texts.clear()
        pending_owner.clear()

    for position, row in enumerate(rows, 1):
        doc_id = row["document_id"]
        # Вход эмбеддингов — тот же профиль prose, что у разбора: годность
        # записи решает его хеш, а не имя файла.
        input_sha = fc.sha_for(PREP_VERSION, "prose", doc_id)
        if input_sha is None:
            skipped_docs += 1
            continue
        if not force and fc.lookup(EMBED_CACHE, embed_index, doc_id, input_sha,
                                   EMBED_REVISION):
            done += 1
            continue
        cache_path = fc.lookup(STANZA_CACHE, stanza_index, doc_id, input_sha,
                               STANZA_REVISION)
        if cache_path is None:
            skipped_docs += 1
            continue
        out_path = EMBED_CACHE / fc.new_name(doc_id, input_sha, ".npz")
        out_names[doc_id] = (out_path.name, input_sha)
        with gzip.open(cache_path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)

        kept = usable_sentences(parsed)
        done += 1
        if not kept:
            np.savez_compressed(
                out_path,
                embeddings=np.zeros((0, 1024), dtype=np.float32),
                sentence_index=np.zeros((0,), dtype=np.int32),
            )
            fc.stamp(embed_index, doc_id, input_sha, PREP_VERSION, EMBED_REVISION,
                     out_path.name)
            fc.save_index(EMBED_CACHE, embed_index)
            continue

        plan[doc_id] = [index for index, _ in kept]
        for slot, (_, text) in enumerate(kept):
            pending_texts.append(text)
            pending_owner.append((doc_id, slot))

        if len(pending_texts) >= FLUSH_AT:
            flush()

        if position % 25 == 0 or position == len(rows):
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            left = elapsed / position * (len(rows) - position) / 60
            print(
                f"  {position}/{len(rows)} документов, "
                f"{elapsed / 60:.1f} мин прошло, осталось около {left:.0f} мин",
                flush=True,
            )

    flush()

    print(f"кэш эмбеддингов: {EMBED_CACHE.relative_to(ROOT)}")
    print(f"  документов в кэше: {done}")
    print(f"  предложений длиннее {MAX_SEQ_LENGTH} токенов (обрезаны): {truncated}")
    if skipped_docs:
        print(f"  ! нет разбора Stanza: {skipped_docs} документов")
    return 0


def cosine(a, b):
    return float(np.dot(a, b))


def unit(vectors):
    """Нормированный центроид набора векторов."""
    centroid = vectors.mean(axis=0)
    norm = np.linalg.norm(centroid)
    return centroid / norm if norm > 0 else centroid


def paragraph_map(parsed, manifest_row, kept_index):
    """Номер абзаца для каждого пригодного предложения.

    Сопоставление последовательным поиском по тексту без пробелов. Возвращает
    список той же длины, что kept_index: номер абзаца или None.
    """
    if manifest_row is None:
        return None, 0
    prose_path = ROOT / manifest_row["prose_path"]
    if not prose_path.exists():
        return None, 0
    paragraphs = [
        normalize(part) for part in prose_path.read_text(encoding="utf-8").split("\n\n") if part.strip()
    ]
    if len(paragraphs) < 2:
        return None, 0

    sentences = parsed["sentences"]
    assignment = []
    current = 0
    cursor = 0
    unmatched = 0
    for index in kept_index:
        needle = normalize(sentence_text(sentences[index]))
        placed = None
        probe, probe_cursor = current, cursor
        while probe < len(paragraphs):
            found = paragraphs[probe].find(needle, probe_cursor)
            if found >= 0:
                placed = probe
                current, cursor = probe, found + len(needle)
                break
            probe += 1
            probe_cursor = 0
        if placed is None:
            unmatched += 1
        assignment.append(placed)
    return assignment, unmatched


def document_features(vectors, parsed, manifest_row, kept_index):
    """Значения M01, M02, M05 и причины пропуска для одного документа."""
    values = {}
    skipped = {}
    unmatched = 0
    count = len(vectors)

    if count < 2:
        skipped["M01"] = "меньше двух предложений в профиле prose"
    else:
        pairs = [cosine(vectors[i], vectors[i + 1]) for i in range(count - 1)]
        spread = float(np.std(pairs, ddof=1)) if len(pairs) > 1 else None
        values["M01"] = ("Сходство соседних предложений", float(np.mean(pairs)), spread, "косинус")

    if count < 4:
        skipped["M05"] = "меньше четырёх предложений в профиле prose"
    else:
        half = count // 2
        first = unit(vectors[:half])
        second = unit(vectors[half:])
        values["M05"] = ("Тематическая прогрессия", 1.0 - cosine(first, second), None, "1 − косинус")

    assignment, unmatched = paragraph_map(parsed, manifest_row, kept_index)
    if assignment is None:
        skipped["M02"] = "меньше двух абзацев"
    else:
        buckets = {}
        for position, paragraph in enumerate(assignment):
            if paragraph is None:
                continue
            buckets.setdefault(paragraph, []).append(vectors[position])
        ordered = [unit(np.array(buckets[key])) for key in sorted(buckets)]
        if len(ordered) < 2:
            skipped["M02"] = "меньше двух абзацев с пригодными предложениями"
        else:
            pairs = [cosine(ordered[j], ordered[j + 1]) for j in range(len(ordered) - 1)]
            spread = float(np.std(pairs, ddof=1)) if len(pairs) > 1 else None
            values["M02"] = ("Сходство абзацев", float(np.mean(pairs)), spread, "косинус")

    return values, skipped, unmatched


def features_stage(rows, limit, out=None):
    manifest = {row["document_id"]: row for row in read_rows(MANIFEST)}
    registry = {row["document_id"]: row for row in read_rows(DOCUMENTS)}
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    records = []
    centroids = {}
    missing_cache = []
    unmatched_total = 0
    docs_with_unmatched = 0

    embed_index = fc.load_index(EMBED_CACHE)
    stanza_index = fc.load_index(STANZA_CACHE)
    for row in rows:
        doc_id = row["document_id"]
        input_sha = fc.sha_for(PREP_VERSION, "prose", doc_id)
        path = (fc.lookup(EMBED_CACHE, embed_index, doc_id, input_sha, EMBED_REVISION)
                if input_sha else None)
        parse_path = (fc.lookup(STANZA_CACHE, stanza_index, doc_id, input_sha,
                                STANZA_REVISION) if input_sha else None)
        if path is None or parse_path is None:
            missing_cache.append(doc_id)
            continue
        with np.load(path) as payload:
            vectors = payload["embeddings"]
            kept_index = payload["sentence_index"]

        with gzip.open(parse_path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)

        values, skipped, unmatched = document_features(
            vectors, parsed, manifest.get(doc_id), list(kept_index)
        )
        if unmatched:
            unmatched_total += unmatched
            docs_with_unmatched += 1
        if len(vectors):
            centroids[doc_id] = unit(vectors)

        for feature_id, (name, raw, normalized, measure) in values.items():
            records.append(make_record(doc_id, feature_id, name, raw, normalized, measure, computed_at))
        for feature_id, reason in skipped.items():
            records.append(make_record(doc_id, feature_id, "", None, None, "", computed_at, reason))

    # X05 считается после всех документов: признак междокументовый.
    by_topic = {}
    for doc_id in centroids:
        row = registry[doc_id]
        if row["origin_class"] != "A":
            continue
        by_topic.setdefault(row["topic_id"], []).append(doc_id)

    for row in rows:
        doc_id = row["document_id"]
        if row["origin_class"] != "A":
            records.append(
                make_record(
                    doc_id, "X05", "", None, None, "", computed_at,
                    "тема не задана: человеческая часть собрана из архивов без общего задания",
                )
            )
            continue
        peers = [other for other in by_topic.get(row["topic_id"], []) if other != doc_id]
        if doc_id not in centroids or not peers:
            records.append(
                make_record(doc_id, "X05", "", None, None, "", computed_at, "нет партнёров по теме")
            )
            continue
        scores = [cosine(centroids[doc_id], centroids[other]) for other in peers]
        records.append(
            make_record(
                doc_id, "X05", "Сходство машинных текстов внутри темы",
                float(np.mean(scores)), float(np.std(scores, ddof=1)) if len(scores) > 1 else None,
                "косинус", computed_at,
            )
        )

    # Проба на части корпуса матрицу не трогает: усечённый прогон заменил бы
    # строки всех документов строками нескольких.
    if out:
        written = retest_io.write_records(out, records)
        print(f"повторный прогон, матрица не изменена: {out}, строк {written}")
    elif limit:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)

    computed = sum(1 for record in records if not record["missing_reason"])
    print(f"семантический слой посчитан: строк {len(records)}, из них со значением {computed}")
    print(f"  документов с центроидом: {len(centroids)}")
    print(f"  тем с машинными документами: {len(by_topic)}")
    if docs_with_unmatched:
        print(
            f"  ! предложений, не сопоставленных абзацу: {unmatched_total} "
            f"в {docs_with_unmatched} документах"
        )
    if missing_cache:
        print(f"  ! нет эмбеддингов: {len(missing_cache)} документов, запустить --stage embed")
    return 0


def make_record(doc_id, feature_id, name, raw, normalized, measure, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": feature_id,
        "feature_name": name,
        "extractor_version": EXTRACTOR_VERSION,
        "preprocessing_profile": "prose",
        "raw_value": "" if raw is None else f"{raw:.6g}",
        "normalized_value": "" if normalized is None else f"{normalized:.6g}",
        "unit": measure,
        "genre_percentile": "",
        "missing_reason": reason,
        "computed_at": computed_at,
    }


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
        # Перцентиль внутри жанра — то же правило, что в feat-v1.
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

    backup = MATRIX.with_suffix(".csv.bak-before-sem-v1")
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")
    if dropped_stale:
        print(f"  снято строк документов вне реестра: {dropped_stale}")


def main():
    global PREP_VERSION, DERIVED, MANIFEST, MATRIX
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("embed", "features"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only")
    parser.add_argument("--ids-file", help="пересчитать только документы из файла, по идентификатору на строку")
    parser.add_argument("--force", action="store_true", help="пересчитать документы, уже лежащие в кэше")
    parser.add_argument("--out", help="записать результат в CSV вместо слияния в матрицу")
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга на входе")
    args = parser.parse_args()

    PREP_VERSION = args.prep_version
    DERIVED = ROOT / "04-corpus" / "derived" / PREP_VERSION
    MANIFEST = DERIVED / "manifest.csv"
    MATRIX = fc.matrix_path(PREP_VERSION, MATRIX)

    rows = read_rows(DOCUMENTS)
    if args.ids_file:
        wanted = {line.strip() for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if line.strip()}
        rows = [row for row in rows if row["document_id"] in wanted]
        print(f"пересчёт по списку: {len(rows)} документов")
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    if args.stage == "embed":
        return embed_stage(rows, args.limit, args.force)
    return features_stage(rows, args.limit, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
