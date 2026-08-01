#!/usr/bin/env python3
"""Стресс-тест, процедура 1: индекс стиля на 660 преобразованных текстах.

    python 09-tools/stress_run_p1.py --stage texts      # препроцессинг
    python 09-tools/stress_run_p1.py --stage parse      # разбор Stanza
    python 09-tools/stress_run_p1.py --stage embed      # эмбеддинги для M01/M02/M05
    python 09-tools/stress_run_p1.py --stage ner        # NER для C01
    python 09-tools/stress_run_p1.py --stage score      # признаки и индекс

Панель — `07-analysis/stress-panel-v1.csv`, 60 документов, зафиксирована до
прогона. Преобразования — одиннадцать выполнимых из `stress_transforms`.

**Шкала не пересобирается.** Перцентиль преобразованного документа считается
против неизменного жанрового пула серии v2: преобразованные значения в пул не
добавляются, исходное значение документа из пула не убирается, правила ties и
пропусков наследуются.

**Признаки с полным покрытием 18/4.** Этапы embed и ner вычисляют M01/M02/M05
(эмбеддинги BAAI/bge-m3, те же, что в основном корпусе) и C01 (Natasha NER).
Без них score получает 14/18 common-признаков и знаменатель 0.51 вместо 0.57;
delta несопоставима с baseline.

**Baseline пересчитывается, а не читается из score-v2.** Обе стороны сравнения
проходят одну функцию расчёта признаков и одну функцию агрегации, иначе delta
смешивала бы эффект преобразования с разницей численных процедур. `score-v2`
остаётся основным результатом и не меняется; расхождение с ним пишется в
`stress-p1-r4-baseline-vs-v2.csv`.

**Эмбеддинги считаются в каноническом детерминированном режиме** — один поток,
batch_size=1. Адрес записи в кэше — хеш модельного входа, поэтому ячейка с
неизменённым входом берёт вектор оригинала, а не считает свой.

Препроцессинг идёт тем же кодом, что и корпус: `prep.process` вызывается
напрямую, поэтому профили строятся по замороженным правилам, а счётчики
форматных признаков сохраняются в манифест панели.
"""

import argparse
import csv
import gzip
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import feature_cache as fc  # noqa: E402
import prep  # noqa: E402
import extract_features as ef  # noqa: E402
import extract_semantic as es  # noqa: E402
import extract_ner as en  # noqa: E402
import score_style_index as sc  # noqa: E402
import stress_transforms as st  # noqa: E402
import stress_paths as sp  # noqa: E402
from lifecycle_gate import lifecycle_path, check_previous_lifecycle  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PANEL = ROOT / "07-analysis" / "stress-panel-v1.csv"
MATRIX_V5 = ROOT / "06-features" / "feature-matrix-v5.csv"
SCORES_V2 = ROOT / "07-analysis" / "score-v2-scores.csv"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
CACHE = ROOT / "06-features" / "cache" / "stress-stanza-v2"
# Разбор оригиналов лежит в кэше корпуса: baseline считается тем же кодом и в
# том же режиме, что преобразованные ячейки.
CORPUS_STANZA = ROOT / "06-features" / "cache" / "stanza-v1"
CORPUS_NER = ROOT / "06-features" / "cache" / "ner-v1"
PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v5" / "manifest.csv"
STRESS_EMBED_CACHE = ROOT / "06-features" / "cache" / "stress-embed-v2"
STRESS_NER_CACHE = ROOT / "06-features" / "cache" / "stress-ner-v2"
# Каталог входов и метка ревизии заданы в stress_paths и больше нигде: 30 июля
# дублирование этой константы по скриптам стоило 2ч45м прогона по устаревшим
# текстам (амендмент r5, изменение 2).
TEXTS = sp.TEXTS
PANEL_MANIFEST = sp.MANIFEST
ORIG_PROSE = ROOT / "04-corpus" / "derived" / "prep-v5" / "prose"
ORIG_FULL = ROOT / "04-corpus" / "derived" / "prep-v5" / "full"
# Расхождение пересчитанного baseline с основной серией v2. Файл диагностический:
# score-v2 остаётся основным результатом и не меняется (решение PI 2026-07-30).
OUT_BASELINE = sp.analysis("p1", "baseline-vs-v2.csv")

# Ревизия r5, решение PI 2026-07-31: t14 переведено в not executable, знаменатель
# стал 10, ячеек 600. Ревизия r4 сохраняется целиком и не перезаписывается —
# результаты по десяти преобразованиям в ней остаются диагностикой. Кеши остаются
# *-v2: записи адресуются хешем входа, а тексты десяти преобразований не менялись,
# поэтому пересчитывать нечего.
PREV_JSON = ROOT / "07-analysis" / "stress-p1-r4-manifest.json"
OUT_CSV = sp.analysis("p1", "scores.csv")
OUT_JSON = sp.analysis("p1", "manifest.json")

# Шлюз завершения: знаменатель common — сумма весов всех семи категорий COMMON.
DENOM_COMMON_EXPECTED = 0.57
DENOM_FORMAT_EXPECTED = 0.12
DENOM_TOL = 1e-9
# Полное покрытие — 18 common-признаков; 17 допустимо только при валидном
# пропуске M02 (признак вне основного варианта, proc2-classifier-spec.md §3.3).
FEATURES_COMMON_FULL = 18
FEATURES_COMMON_MIN_VALID = 17
FEATURES_FORMAT_EXPECTED = 4
# applied_no_change: профиль prose идентичен оригиналу. Диагностика, не блокирует:
# индекс читает ещё профиль full и счётчики препроцессинга, и они меняются
# независимо от prose (stress-p1-r3-gate.md).
INDEX_SENTINEL_TOL = 1e-4
# input_unchanged: совпал весь вход признаков — prose, full и счётчики. Тогда
# индекс обязан совпасть тождественно: ячейка берёт из кэша тот же вектор, что
# оригинал, и все прочие признаки считаются из тех же байтов.
INPUT_SENTINEL_TOL = 1e-9
# Счётчики препроцессинга, которые читает экстрактор: R06, R07 и F01.
PANEL_COUNTER_KEYS = ("heading_md", "list_items", "full_bold_spans")

# Те же константы, что в extract_semantic.py / extract_ner.py — изменить там,
# изменить здесь. Сверка по хешу кода в манифесте.
_TORCH_THREADS = "16"
os.environ.setdefault("OMP_NUM_THREADS", _TORCH_THREADS)
os.environ.setdefault("MKL_NUM_THREADS", _TORCH_THREADS)
_EMBED_MODEL_NAME = "BAAI/bge-m3"
_EMBED_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
# Ревизия r3: канонический детерминированный режим. Строка входит в проверку
# годности записи, поэтому записи прежнего режима под ней не подхватываются.
_EMBED_REVISION = (
    f"{_EMBED_MODEL_NAME}@{_EMBED_MODEL_REVISION[:12]}, sentence-transformers 5.5.1, "
    f"deterministic cpu/1thread/bs1"
)
_EMBED_MAX_SEQ = 512
_EMBED_BATCH = 1               # канонический режим: батч не влияет на padding
_EMBED_MIN_TOKENS = 3          # §2 семантической спецификации
# Все входы лежат под одним ключом и адресуются хешем самого входа: номер
# преобразования в адрес не входит, одинаковые входы считаются один раз.
_EMBED_KEY = "model-input"
_EMBED_CANONICAL_FLAG = "STRESS_EMBED_CANONICAL"
_EMBED_CANONICAL_ENV = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "TOKENIZERS_PARALLELISM": "false",
}
_NER_REVISION = "natasha 1.6.0/slovnet 0.6.0/navec 0.10.0"
_NER_PREP_VERSION = sp.PREP_VERSION


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_csv(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def panel_rows():
    registry = {r["document_id"]: r for r in read_csv(REGISTRY, "utf-8-sig")}
    return [registry[r["document_id"]] for r in read_csv(PANEL)]


def source_text(row):
    """Исходник документа — тот же, что подавался в prep-v5.

    Кодировка та же, что в `prep.py:682` — `utf-8`, а не `utf-8-sig`. Разница не
    косметическая: prep-v5 оставляет BOM в тексте, и профили 38 документов корпуса
    начинаются с него. Чтение через `utf-8-sig` снимало бы BOM и разводило профиль
    панели с baseline, против которого считается delta.
    """
    return fc.source_file(row, "prep-v5").read_text(encoding="utf-8",
                                                    errors="replace")


# Колонки манифеста панели: те же счётчики, что пишет prep-v5. Три из них —
# heading_md, list_items, full_bold_spans — читает extract_features для R06, R07
# и F01; остальные сохраняются, чтобы повторный сбор панели не потребовался.
PANEL_STATS_KEYS = (
    "heading_md", "heading_plain", "list_items", "table_rows", "rules",
    "code_blocks", "paragraph", "prose_bold_spans", "prose_code_spans",
    "prose_links", "full_bold_spans", "full_code_spans", "full_links",
    "boilerplate_lines_removed", "hyphen_joins", "line_is_paragraph",
    "list_leads_removed",
)


def build_texts(rows):
    """660 преобразованных текстов, их профили и манифест счётчиков.

    Преобразование применяется к исходнику, профили строит штатный
    `prep.process` — тот же код, что собрал корпус. Оболочка источника снимается
    тем же `collect_boilerplate`: без неё профили 6 документов панели из 60
    расходились с prep-v5 (stress-format-gate.md).

    `stats` сохраняется в манифест. F01, R06 и R07 экстрактор читает из колонок
    препроцессинга, а не из текста профиля: `strip_inline` снимает `**` при
    рендеринге, а `segment` пишет заголовки и пункты списка без маркеров, поэтому
    из готового профиля они невосстановимы.
    """
    boilerplate = prep.collect_boilerplate(prep.read_rows(REGISTRY))
    print(f"  оболочка источников: {sum(len(v) for v in boilerplate.values())} "
          f"строк у {len(boilerplate)} источников", flush=True)
    TEXTS.mkdir(parents=True, exist_ok=True)
    made = 0
    records = []
    for row in rows:
        raw = source_text(row)
        source = (row["source_platform"] or row["generation_channel"] or "unknown")
        for number in sorted(st.TRANSFORMS):
            text = st.apply_transform(number, raw, ROOT)
            rendered, stats = prep.process(text, boilerplate.get(source))
            record = {"document_id": row["document_id"],
                      "transformation_id": number}
            for profile in ("prose", "full"):
                out = TEXTS / f"t{number:02d}" / profile
                out.mkdir(parents=True, exist_ok=True)
                path = out / f"{row['document_id']}.txt"
                path.write_text(rendered[profile], encoding="utf-8", newline="\n")
                record[f"{profile}_path"] = str(
                    path.relative_to(ROOT)).replace("\\", "/")
                record[f"{profile}_sha256"] = sha256_bytes(
                    rendered[profile].encode("utf-8"))
                record[f"{profile}_words"] = len(rendered[profile].split())
            for key in PANEL_STATS_KEYS:
                record[key] = stats.get(key, 0)
            records.append(record)
            made += 1
    with PANEL_MANIFEST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f"  манифест панели: {PANEL_MANIFEST.relative_to(ROOT)}, "
          f"строк {len(records)}")
    return made


def parse_stage(rows):
    import stanza
    revision = f"stanza {stanza.__version__}/ru-syntagrus"
    if revision != ef.STANZA_REVISION:
        raise SystemExit(f"ревизия разбора {revision} против {ef.STANZA_REVISION}")
    CACHE.mkdir(parents=True, exist_ok=True)
    index = fc.load_index(CACHE)
    nlp = stanza.Pipeline("ru", package="syntagrus",
                          processors="tokenize,pos,lemma,depparse",
                          use_gpu=False, verbose=False)
    pending = []
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            path = TEXTS / f"t{number:02d}" / "prose" / f"{row['document_id']}.txt"
            if not path.exists():
                continue
            key = f"t{number:02d}:{row['document_id']}"
            sha = fc.sha256_file(path)
            if fc.lookup(CACHE, index, key, sha, revision):
                continue
            pending.append((key, path, sha))
    print(f"  к разбору {len(pending)}, годных записей {660 - len(pending)}")
    done = 0
    for start in range(0, len(pending), ef.BATCH):
        batch = pending[start:start + ef.BATCH]
        texts = [p.read_text(encoding="utf-8") for _, p, _ in batch]
        parsed = nlp([stanza.Document([], text=t) for t in texts])
        for (key, _, sha), text, doc in zip(batch, texts, parsed):
            payload = {"document_id": key, "prep_version": _NER_PREP_VERSION,
                       "profile": "prose", "stanza": stanza.__version__,
                       "sentences": [[{"t": w.text, "l": w.lemma or w.text,
                                       "p": w.upos, "d": w.deprel or "",
                                       "h": w.head, "i": w.id, "f": w.feats or ""}
                                      for w in s.words] for s in doc.sentences],
                       "paragraphs": [len(p.split()) for p in text.split("\n\n")
                                      if p.strip()]}
            name = fc.new_name(key.replace(":", "_"), sha, ".json.gz")
            with gzip.open(CACHE / name, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            fc.stamp(index, key, sha, _NER_PREP_VERSION, revision, name)
            done += 1
        fc.save_index(CACHE, index)
        if done % 80 == 0 or start + ef.BATCH >= len(pending):
            print(f"    разобрано {done} из {len(pending)}", flush=True)
    return done


def model_input(parsed):
    """Точный вход модели: пригодные предложения и их индексы в разборе."""
    kept = es.usable_sentences(parsed)
    return [text for _, text in kept], [int(idx) for idx, _ in kept]


def model_input_digest(texts, index):
    """SHA256 модельного входа: сами строки, их порядок и позиции в разборе.

    Номер преобразования в адрес не входит. Две ячейки с одинаковым входом
    получают одну запись, поэтому у ячейки, где преобразование не изменило
    prose, вектор буквально тот же, что у оригинала, и delta равна нулю
    тождественно, а не в пределах допуска.
    """
    payload = json.dumps({"texts": texts, "index": index},
                         ensure_ascii=False, sort_keys=True)
    return sha256_bytes(payload.encode("utf-8"))


def require_canonical_env():
    """Канонический режим фиксируется до импорта torch, иначе OMP уже поднят."""
    if os.environ.get(_EMBED_CANONICAL_FLAG) != "1":
        raise SystemExit(
            f"этап embed требует канонического окружения ({_EMBED_CANONICAL_FLAG}=1); "
            f"main() перезапускает процесс сам — вызывайте через CLI")
    wrong = {name: os.environ.get(name)
             for name, value in _EMBED_CANONICAL_ENV.items()
             if os.environ.get(name) != value}
    if wrong:
        raise SystemExit(f"окружение не каноническое: {wrong}, "
                         f"ожидалось {_EMBED_CANONICAL_ENV}")


def embed_stage(rows):
    """Эмбеддинги BGE-M3 для M01/M02/M05 в каноническом детерминированном режиме.

    Считаются оба конца сравнения: 60 оригинальных документов prep-v5 и 660
    преобразованных ячеек. Baseline и transformed проходят одну численную
    процедуру — иначе delta смешивает эффект преобразования с разницей режимов.

    Режим: один поток BLAS и torch, `use_deterministic_algorithms`, batch_size=1.
    Проверено двумя независимыми процессами: sha256 массива и M01/M02/M05
    совпадают побитово. Прежний режим (16 потоков, батч 64) давал разные массивы
    на одном и том же входе у 43 групп из 50 — stress-embed-nondeterminism.md.
    """
    require_canonical_env()
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(0)
    STRESS_EMBED_CACHE.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(_EMBED_MODEL_NAME, revision=_EMBED_MODEL_REVISION,
                                device="cpu")
    model.max_seq_length = _EMBED_MAX_SEQ
    model.eval()
    print(f"модель {_EMBED_MODEL_NAME}@{_EMBED_MODEL_REVISION[:12]}, "
          f"torch {torch.__version__}, потоков {torch.get_num_threads()}, "
          f"batch {_EMBED_BATCH}, deterministic")

    stress_index = fc.load_index(CACHE)
    corpus_index = fc.load_index(CORPUS_STANZA)
    embed_index = fc.load_index(STRESS_EMBED_CACHE)

    # Сбор входов: сначала оригиналы, затем ячейки. Разбор оригинала лежит в
    # кэше корпуса и адресуется document_id, разбор ячейки — в кэше панели.
    sources = []
    for row in rows:
        doc_id = row["document_id"]
        orig = ORIG_PROSE / f"{doc_id}.txt"
        if orig.exists():
            sources.append(("orig", doc_id, doc_id, orig, CORPUS_STANZA,
                            corpus_index))
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            doc_id = row["document_id"]
            prose = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
            if prose.exists():
                sources.append((f"t{number:02d}", doc_id,
                                f"t{number:02d}:{doc_id}", prose, CACHE,
                                stress_index))

    unique, missing_parse = {}, 0
    for label, doc_id, key, prose, cache_dir, index in sources:
        parsed_path = fc.lookup(cache_dir, index, key, fc.sha256_file(prose),
                                ef.STANZA_REVISION)
        if parsed_path is None:
            missing_parse += 1
            continue
        with gzip.open(parsed_path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)
        texts, plan = model_input(parsed)
        unique.setdefault(model_input_digest(texts, plan), (texts, plan))

    print(f"  входов всего: {len(sources)}, разбор отсутствует у {missing_parse}, "
          f"уникальных модельных входов: {len(unique)}")

    todo = [digest for digest in unique
            if fc.lookup(STRESS_EMBED_CACHE, embed_index, _EMBED_KEY, digest,
                         _EMBED_REVISION) is None]
    print(f"  в кэше уже есть: {len(unique) - len(todo)}, считать: {len(todo)}",
          flush=True)

    done = truncated = 0
    started = datetime.now(timezone.utc)
    for position, digest in enumerate(sorted(todo), 1):
        texts, plan = unique[digest]
        name = f"embed__{digest[:32]}.npz"
        if not texts:
            vectors = np.zeros((0, 1024), dtype=np.float32)
        else:
            lengths = model.tokenizer(texts, add_special_tokens=True,
                                      truncation=False)["input_ids"]
            truncated += sum(1 for ids in lengths if len(ids) > _EMBED_MAX_SEQ)
            with torch.inference_mode():
                vectors = model.encode(
                    texts, batch_size=_EMBED_BATCH, normalize_embeddings=True,
                    show_progress_bar=False, convert_to_numpy=True,
                ).astype(np.float32)
        np.savez_compressed(
            STRESS_EMBED_CACHE / name,
            embeddings=vectors,
            sentence_index=np.array(plan, dtype=np.int32),
        )
        fc.stamp(embed_index, _EMBED_KEY, digest, _NER_PREP_VERSION,
                 _EMBED_REVISION, name)
        fc.save_index(STRESS_EMBED_CACHE, embed_index)
        done += 1
        if position % 20 == 0 or position == len(todo):
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            left = elapsed / position * (len(todo) - position) / 60
            print(f"  {position}/{len(todo)}, {elapsed/60:.1f} мин, "
                  f"~{left:.0f} осталось", flush=True)

    print(f"кэш эмбеддингов: {STRESS_EMBED_CACHE.relative_to(ROOT)}")
    print(f"  записано: {done}, предложений обрезано: {truncated}")
    return done


def ner_stage(rows):
    """Этап 4: NER-разметка Natasha для C01 на 660 стресс-текстах."""
    STRESS_NER_CACHE.mkdir(parents=True, exist_ok=True)
    tools = en.load_natasha()
    ner_index = fc.load_index(STRESS_NER_CACHE)
    done = skipped = 0
    started = datetime.now(timezone.utc)

    total = len(rows) * len(st.TRANSFORMS)
    position = 0
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            position += 1
            doc_id = row["document_id"]
            key = f"t{number:02d}:{doc_id}"
            full = TEXTS / f"t{number:02d}" / "full" / f"{doc_id}.txt"
            if not full.exists():
                skipped += 1
                continue
            sha = fc.sha256_file(full)
            if fc.lookup(STRESS_NER_CACHE, ner_index, key, sha, _NER_REVISION):
                done += 1
                continue
            text = full.read_text(encoding="utf-8")
            spans = en.tag_document(tools, text)
            name = fc.new_name(key.replace(":", "_"), sha, ".json.gz")
            with gzip.open(STRESS_NER_CACHE / name, "wt", encoding="utf-8") as fh:
                json.dump({"document_id": key, "spans": spans}, fh,
                          ensure_ascii=False)
            fc.stamp(ner_index, key, sha, _NER_PREP_VERSION, _NER_REVISION, name)
            fc.save_index(STRESS_NER_CACHE, ner_index)
            done += 1
            if position % 100 == 0 or position == total:
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                print(f"  {position}/{total}, {elapsed/60:.1f} мин", flush=True)

    print(f"кэш NER: {STRESS_NER_CACHE.relative_to(ROOT)}")
    print(f"  готово: {done}, пропущено: {skipped}")
    return done


def frozen_pools():
    """Жанровые пулы серии v2: значения признаков по правилу percentile-v1.0."""
    genre_of = {r["document_id"]: r["genre"]
                for r in read_csv(REGISTRY, "utf-8-sig")}
    pools = defaultdict(list)
    with MATRIX_V5.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            value = r["normalized_value"] or r["raw_value"]
            genre = genre_of.get(r["document_id"])
            if value == "" or genre is None:
                continue
            pools[(r["feature_id"], genre)].append(float(value))
    for key in pools:
        pools[key].sort()
    return pools


def percentile(pools, feature_id, genre, value):
    """Ранг против замороженного пула. Преобразованное значение в пул не входит."""
    pool = pools.get((feature_id, genre))
    if not pool:
        return None
    rank = sum(1 for item in pool if item < value)
    return rank / len(pool)


def check_completion_gate(out_rows, expected_count):
    """Шлюз завершения P1: возвращает (passed: bool, detail: str, checks: dict).

    Условия (все обязательны):
    1. Присутствуют все 660 ячеек.
    2. features_common = 18, либо 17 при валидном пропуске M02.
    3. features_format = 4.
    4. Знаменатель common везде 0.57, format везде 0.12.
    5. Отсутствуют выпавшие категории (все 7 COMMON + 1 FORMAT посчитаны).
    6. applied_no_change дают Δ = 0 в допуске INDEX_SENTINEL_TOL.
    7. Прежняя ревизия объявлена заменённой или негодной отдельной записью
       жизненного цикла, и новый файл её не перезаписывает.
    """
    checks = {}
    fails = []

    # 1. Полнота
    checks["rows"] = {"got": len(out_rows), "expected": expected_count}
    if len(out_rows) != expected_count:
        fails.append(f"строк {len(out_rows)}, ожидалось {expected_count}")

    # 2-3. Покрытие признаков
    # str() обязателен: в памяти поля целые, из CSV приходят строками.
    bad_common, bad_format, m02_skips = [], [], 0
    for r in out_rows:
        fc_n = int(r["features_common"])
        ff_n = int(r["features_format"])
        if fc_n == FEATURES_COMMON_FULL:
            pass
        elif (fc_n == FEATURES_COMMON_MIN_VALID
              and str(r.get("m02_missing")) == "1"):
            m02_skips += 1
        else:
            bad_common.append(r)
        if ff_n != FEATURES_FORMAT_EXPECTED:
            bad_format.append(r)
    checks["features_common"] = {
        "full_18": sum(1 for r in out_rows
                       if int(r["features_common"]) == FEATURES_COMMON_FULL),
        "valid_17_m02_missing": m02_skips,
        "invalid": len(bad_common),
    }
    checks["features_format"] = {"invalid": len(bad_format)}
    if bad_common:
        worst = sorted({int(r["features_common"]) for r in bad_common})
        fails.append(f"{len(bad_common)} строк с недопустимым features_common "
                     f"(значения {worst}); 17 допустимо только при m02_missing=1")
    if bad_format:
        fails.append(f"{len(bad_format)} строк с features_format != "
                     f"{FEATURES_FORMAT_EXPECTED}")

    # 4. Знаменатели
    bad_denom_c = [r for r in out_rows
                   if r["weight_common"] == ""
                   or abs(float(r["weight_common"]) - DENOM_COMMON_EXPECTED) > DENOM_TOL]
    bad_denom_f = [r for r in out_rows
                   if r["weight_format"] == ""
                   or abs(float(r["weight_format"]) - DENOM_FORMAT_EXPECTED) > DENOM_TOL]
    checks["denominator_common"] = {"expected": DENOM_COMMON_EXPECTED,
                                    "invalid": len(bad_denom_c)}
    checks["denominator_format"] = {"expected": DENOM_FORMAT_EXPECTED,
                                    "invalid": len(bad_denom_f)}
    if bad_denom_c:
        got = sorted({r["weight_common"] for r in bad_denom_c})[:5]
        fails.append(f"{len(bad_denom_c)} строк со знаменателем common != "
                     f"{DENOM_COMMON_EXPECTED} (встречено {got})")
    if bad_denom_f:
        fails.append(f"{len(bad_denom_f)} строк со знаменателем format != "
                     f"{DENOM_FORMAT_EXPECTED}")

    # 5. Выпавшие категории
    dropped = [r for r in out_rows if r["dropped_categories"] != ""]
    checks["dropped_categories"] = {"rows": len(dropped)}
    if dropped:
        names = sorted({c for r in dropped
                        for c in r["dropped_categories"].split(";") if c})
        fails.append(f"{len(dropped)} строк с выпавшими категориями: "
                     + ", ".join(names[:5]))

    # 6. applied_no_change — диагностика, не блокирует. Совпадение одного профиля
    #    prose не означает, что вход признаков не менялся: full читают
    #    surface-признаки, счётчики препроцессинга — форматные.
    anc = [r for r in out_rows if str(r["applied_no_change"]) == "1"]
    anc_bad = [r for r in anc
               if r["delta"] != "" and abs(float(r["delta"])) > INDEX_SENTINEL_TOL]
    checks["applied_no_change"] = {"rows": len(anc), "out_of_tolerance": len(anc_bad),
                                   "tolerance": INDEX_SENTINEL_TOL,
                                   "blocking": False,
                                   "note": "prose совпал; full и счётчики могли измениться"}

    # 6b. input_unchanged — блокирующий инвариант. Совпал весь вход признаков,
    #     значит индекс обязан совпасть тождественно, а не в пределах допуска.
    iu = [r for r in out_rows if str(r.get("input_unchanged")) == "1"]
    iu_bad = [r for r in iu
              if r["delta"] == ""
              or abs(float(r["delta"])) > INPUT_SENTINEL_TOL]
    checks["input_unchanged"] = {"rows": len(iu), "out_of_tolerance": len(iu_bad),
                                 "tolerance": INPUT_SENTINEL_TOL, "blocking": True}
    if not iu:
        fails.append("ни одной ячейки с неизменённым входом: инвариант "
                     "input_unchanged не проверен")
    if iu_bad:
        worst = max((abs(float(r["delta"])) for r in iu_bad if r["delta"] != ""),
                    default=float("inf"))
        fails.append(f"{len(iu_bad)} из {len(iu)} input_unchanged вне допуска "
                     f"{INPUT_SENTINEL_TOL}: max|Δ| = {worst:.6f}")

    # 7. Прежняя ревизия объявлена заменённой или негодной, и не перезаписана.
    #
    # Статус читается из неизменяемого sidecar рядом с манифестом, а не из
    # самого манифеста: его хеш входит в чужие манифесты, и правка задним числом
    # сломала бы их воспроизводимость. Дополнение к амендменту r5 от 2026-07-31.
    prev_ok, prev_note = check_previous_lifecycle(PREV_JSON)
    checks["previous_run"] = {"file": PREV_JSON.name,
                              "sidecar": lifecycle_path(PREV_JSON).name,
                              "accepted": prev_ok, "note": prev_note,
                              "overwritten": PREV_JSON == OUT_JSON}
    if PREV_JSON == OUT_JSON:
        fails.append("новый манифест перезаписывает прежний прогон")
    elif not prev_ok:
        fails.append(f"прежняя ревизия {PREV_JSON.name}: {prev_note}")

    if fails:
        return False, "; ".join(fails), checks
    return True, (f"completed: {len(out_rows)} строк, "
                  f"features_common 18 у {checks['features_common']['full_18']}"
                  + (f" / 17 при m02_missing у {m02_skips}" if m02_skips else "")
                  + f", знаменатели {DENOM_COMMON_EXPECTED}/{DENOM_FORMAT_EXPECTED}, "
                  f"0 выпавших категорий, {len(iu)} input_unchanged с нулевой "
                  f"delta, {len(anc)} applied_no_change (из них вне допуска "
                  f"{len(anc_bad)}, не блокирует), "
                  f"прежний прогон {prev_note}"), checks


def unit_values(parsed, full_text, manifest_row, embed_index, ner_cache,
                ner_index, ner_key, full_sha):
    """Все признаки одной единицы — ячейки панели либо оригинального документа.

    Обе стороны сравнения проходят эту функцию: иначе baseline считался бы одной
    численной процедурой, а transformed другой, и delta смешивала бы эффект
    преобразования с разницей процедур (требование PI 2026-07-30).
    """
    values, _ = ef.document_features(parsed, manifest_row)
    values.update(ef.surface_features(full_text,
                                      float(manifest_row["full_words"])))

    # Адрес записи — хеш модельного входа, номер преобразования в него не входит:
    # ячейка с неизменённым prose берёт ту же запись, что оригинал.
    embed_path = fc.lookup(STRESS_EMBED_CACHE, embed_index, _EMBED_KEY,
                           model_input_digest(*model_input(parsed)),
                           _EMBED_REVISION)
    if embed_path is not None:
        with np.load(embed_path) as payload:
            vectors = payload["embeddings"]
            kept_index = list(payload["sentence_index"])
        sem_vals, _, _ = es.document_features(vectors, parsed, manifest_row,
                                              kept_index)
        values.update(sem_vals)

    ner_path = fc.lookup(ner_cache, ner_index, ner_key, full_sha, _NER_REVISION)
    if ner_path is not None:
        with gzip.open(ner_path, "rt", encoding="utf-8") as fh:
            spans = json.load(fh)["spans"]
        words_c01 = float(manifest_row["full_words"])
        cut = en.bibliography_start(full_text)
        if cut is not None:
            spans = [s for s in spans if s["start"] < cut]
            words_c01 -= len(full_text[cut:].split())
        if words_c01 > 0:
            groups_c01 = en.glue_spans(spans)
            counts_c01, _, _ = en.document_counts(groups_c01)
            total_c01 = sum(counts_c01.values())
            values["C01"] = ("Именованные сущности", total_c01,
                             total_c01 / words_c01 * 1000, "на 1000 слов")
    return values


def unit_index(values, genre, pools, needed):
    """Перцентили, категорийные баллы и индекс. Пул жанра остаётся замороженным."""
    percentiles = {}
    for feature_id, payload in values.items():
        if feature_id not in needed:
            continue
        raw, normalized = payload[1], payload[2]
        value = normalized if normalized is not None else raw
        if value is None:
            continue
        p = percentile(pools, feature_id, genre, float(value))
        if p is not None:
            percentiles[feature_id] = p
    common, _, used_c, w_c = sc.document_scores(percentiles, sc.COMMON)
    fmt, _, used_f, w_f = sc.document_scores(percentiles, sc.FORMAT)
    both = ((common * w_c + fmt * w_f) / (w_c + w_f)
            if common is not None and fmt is not None else None)
    # Категория выпала, если из неё не посчитан ни один признак — тогда её вес
    # не попадает в знаменатель и delta несопоставима.
    dropped = [name for name, n in list(used_c.items()) + list(used_f.items())
               if n == 0]
    return {
        "index": both,
        "features_common": sum(used_c.values()),
        "features_format": sum(used_f.values()),
        "weight_common": w_c,
        "weight_format": w_f,
        "dropped": dropped,
        "m02_missing": int("M02" not in percentiles),
    }


def baseline_units(rows, pools, needed, embed_index):
    """Индекс 60 оригиналов, пересчитанный тем же кодом и в том же режиме.

    Значения score-v2 для сравнения не годятся: они посчитаны в прежнем
    недетерминированном режиме эмбеддингов, и delta против них мешала бы эффект
    преобразования с разницей режимов. score-v2 остаётся основным результатом и
    не меняется; расхождение с ним пишется в OUT_BASELINE.
    """
    prep_manifest = {r["document_id"]: r for r in read_csv(PREP_MANIFEST)}
    corpus_stanza = fc.load_index(CORPUS_STANZA)
    corpus_ner = fc.load_index(CORPUS_NER)
    out, missing = {}, []
    for row in rows:
        doc_id = row["document_id"]
        prose, full = ORIG_PROSE / f"{doc_id}.txt", ORIG_FULL / f"{doc_id}.txt"
        mrow = prep_manifest.get(doc_id)
        if mrow is None or not prose.exists() or not full.exists():
            missing.append(doc_id)
            continue
        parsed_path = fc.lookup(CORPUS_STANZA, corpus_stanza, doc_id,
                                fc.sha256_file(prose), ef.STANZA_REVISION)
        if parsed_path is None:
            missing.append(doc_id)
            continue
        with gzip.open(parsed_path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)
        full_text = full.read_text(encoding="utf-8")
        manifest_row = {
            "prose_words": mrow["prose_words"],
            "full_words": mrow["full_words"],
            "full_path": mrow["full_path"],
            "prose_path": mrow["prose_path"],
            "heading_md": mrow["heading_md"],
            "list_items": mrow["list_items"],
            "full_bold_spans": mrow["full_bold_spans"],
        }
        values = unit_values(parsed, full_text, manifest_row, embed_index,
                             CORPUS_NER, corpus_ner, doc_id,
                             mrow["full_sha256"])
        out[doc_id] = unit_index(values, row["genre"], pools, needed)
    if missing:
        print(f"  baseline не посчитан у {len(missing)}: {missing[:5]}")
    return out


def score_stage(rows):
    pools = frozen_pools()
    index = fc.load_index(CACHE)
    embed_index = fc.load_index(STRESS_EMBED_CACHE)
    ner_index = fc.load_index(STRESS_NER_CACHE)
    needed = set()
    for groups in (sc.COMMON, sc.FORMAT):
        for _, features in groups.values():
            needed |= set(features)

    # Baseline пересчитывается в том же режиме, что ячейки. score-v2 читается
    # только для отчёта о расхождении и основным результатом остаётся он.
    baseline = baseline_units(rows, pools, needed, embed_index)
    print(f"  baseline пересчитан: {len(baseline)}/{len(rows)} документов")
    v2 = {r["document_id"]: r for r in read_csv(SCORES_V2)}
    with OUT_BASELINE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["document_id", "index_recomputed", "index_score_v2",
                         "delta", "features_common", "features_format"])
        diffs = []
        for doc_id in sorted(baseline):
            new_value = baseline[doc_id]["index"]
            old_raw = v2.get(doc_id, {}).get("index_common_plus_format")
            old_value = float(old_raw) if old_raw else None
            gap = (new_value - old_value
                   if new_value is not None and old_value is not None else None)
            if gap is not None:
                diffs.append(abs(gap))
            writer.writerow([
                doc_id,
                "" if new_value is None else f"{new_value:.4f}",
                "" if old_value is None else f"{old_value:.4f}",
                "" if gap is None else f"{gap:.4f}",
                baseline[doc_id]["features_common"],
                baseline[doc_id]["features_format"],
            ])
    if diffs:
        print(f"  расхождение с score-v2: max|Δ| = {max(diffs):.4f}, "
              f"ненулевых {sum(1 for d in diffs if d > 1e-9)} из {len(diffs)}"
              f" → {OUT_BASELINE.name}")

    # SHA256 оригинальных prose-текстов: преобразование может не изменить текст
    orig_sha = {}
    for row in rows:
        orig = ORIG_PROSE / f"{row['document_id']}.txt"
        if orig.exists():
            orig_sha[row["document_id"]] = fc.sha256_file(orig)
    print(f"  orig_sha загружен: {len(orig_sha)}/{len(rows)} документов")

    # Полный вход признаков оригинала: профиль prose, профиль full и счётчики
    # препроцессинга. Совпадения одного prose мало — surface-признаки читают
    # full, форматные читают счётчики, и оба меняются независимо от prose.
    orig_full_sha, prep_manifest = {}, {}
    for r in read_csv(PREP_MANIFEST):
        prep_manifest[r["document_id"]] = r
    for row in rows:
        orig = ORIG_FULL / f"{row['document_id']}.txt"
        if orig.exists():
            orig_full_sha[row["document_id"]] = fc.sha256_file(orig)

    # Счётчики препроцессинга ячейки: F01, R06 и R07 экстрактор читает из них,
    # а не из текста профиля. Без манифеста они молча становились нулями.
    panel_stats = {(r["document_id"], int(r["transformation_id"])): r
                   for r in read_csv(PANEL_MANIFEST)}
    print(f"  манифест панели: {len(panel_stats)} ячеек")

    out_rows = []
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            doc_id = row["document_id"]
            key = f"t{number:02d}:{doc_id}"
            prose = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
            full = TEXTS / f"t{number:02d}" / "full" / f"{doc_id}.txt"
            if not prose.exists():
                continue
            prose_sha = fc.sha256_file(prose)
            path = fc.lookup(CACHE, index, key, prose_sha, ef.STANZA_REVISION)
            if path is None:
                continue
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                parsed = json.load(fh)
            full_text = full.read_text(encoding="utf-8")
            stats_row = panel_stats.get((doc_id, number))
            if stats_row is None:
                raise SystemExit(
                    f"нет строки манифеста панели для {key}: пересоберите "
                    f"панель (--stage texts) до расчёта")
            manifest_row = {
                "prose_words": len(prose.read_text(encoding="utf-8").split()),
                "full_words": len(full_text.split()),
                "full_path": str(full.relative_to(ROOT)).replace("\\", "/"),
                "prose_path": str(prose.relative_to(ROOT)).replace("\\", "/"),
                "heading_md": stats_row["heading_md"],
                "list_items": stats_row["list_items"],
                "full_bold_spans": stats_row["full_bold_spans"],
            }
            full_sha = fc.sha256_file(full)
            values = unit_values(parsed, full_text, manifest_row, embed_index,
                                 STRESS_NER_CACHE, ner_index, key, full_sha)
            unit = unit_index(values, row["genre"], pools, needed)
            both = unit["index"]
            w_c, w_f = unit["weight_common"], unit["weight_format"]
            base_value = baseline.get(doc_id, {}).get("index")
            delta = (both - base_value
                     if both is not None and base_value is not None else None)
            out_rows.append({
                "original_document_id": doc_id,
                "transformation_id": number,
                "transformation": st.TRANSFORMS[number][0],
                "meaning_preserving": int(number in st.MEANING_PRESERVING),
                "origin_class": row["origin_class"], "genre": row["genre"],
                "prompt_condition": row["prompt_condition"],
                "generation_channel": row["generation_channel"],
                "index_baseline": "" if base_value is None else f"{base_value:.4f}",
                "index_transformed": "" if both is None else f"{both:.4f}",
                "delta": "" if delta is None else f"{delta:.4f}",
                "abs_delta": "" if delta is None else f"{abs(delta):.4f}",
                "unstable": "" if delta is None else int(abs(delta) > 5.0),
                "features_common": unit["features_common"],
                "features_format": unit["features_format"],
                "weight_common": "" if w_c is None else f"{w_c:.4f}",
                "weight_format": "" if w_f is None else f"{w_f:.4f}",
                "dropped_categories": ";".join(unit["dropped"]),
                "m02_missing": unit["m02_missing"],
                "applied_no_change": int(prose_sha == orig_sha.get(doc_id)),
                "input_unchanged": int(
                    prose_sha == orig_sha.get(doc_id)
                    and full_sha == orig_full_sha.get(doc_id)
                    and all(int(float(stats_row[k] or 0))
                            == int(float(prep_manifest[doc_id][k] or 0))
                            for k in PANEL_COUNTER_KEYS)),
            })
        print(f"  преобразование {number}: строк {len(out_rows)}", flush=True)

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    scored = [r for r in out_rows if r["delta"] != ""]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ── Шлюз завершения ───────────────────────────────────────────────────────
    expected = len(rows) * len(st.TRANSFORMS)
    gate_ok, gate_detail, checks = check_completion_gate(out_rows, expected)
    status_str = "completed" if gate_ok else "incomplete"

    OUT_JSON.write_text(json.dumps({
        "created_at": stamp, "procedure": "P1", "revision": "r2",
        "status": status_str, "gate_detail": gate_detail, "gate_checks": checks,
        "panel": PANEL.name,
        "documents": len(rows), "transformations": sorted(st.TRANSFORMS),
        "not_executable": st.NOT_EXECUTABLE,
        "denominator": len(st.TRANSFORMS),
        "threshold": 5.0,
        "percentile_reference": "замороженный жанровый пул серии v2; "
                                "преобразованные значения в пул не добавляются",
        "rows": len(out_rows), "rows_scored": len(scored),
        "supersedes": {
            "file": PREV_JSON.name,
            "lifecycle_file": lifecycle_path(PREV_JSON).name,
            "accepted": checks["previous_run"]["accepted"],
            "note_from_lifecycle": checks["previous_run"]["note"],
            "reason": "t14 переведено в not executable амендментом r5: "
                      "знаменатель 10, ячеек 600",
            "detail": "stress-t14-cliche-source-defect.md",
            "note": "прежняя ревизия сохранена и не перезаписана; её статус "
                    "жизненного цикла лежит в отдельной неизменяемой записи",
        },
        "inputs": {p.name: fc.sha256_file(p) for p in (PANEL, MATRIX_V5, SCORES_V2)},
        "code_sha256": {name: fc.sha256_file(ROOT / "09-tools" / name)
                        for name in ("stress_run_p1.py", "stress_transforms.py",
                                     "stress_panel.py")},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  записано: {OUT_CSV.name}, строк {len(out_rows)}, с оценкой {len(scored)}")
    print(f"  шлюз завершения: {status_str} — {gate_detail}")
    print(f"  манифест: {OUT_JSON.name}")
    return 0 if gate_ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage",
                        choices=("texts", "parse", "embed", "ner", "score"),
                        required=True)
    args = parser.parse_args()

    # Потоки BLAS фиксируются переменными окружения до импорта torch, поэтому
    # канонический режим нельзя включить изнутри уже запущенного процесса.
    # Перезапуск делает режим свойством этапа, а не памяти оператора.
    if args.stage == "embed" and os.environ.get(_EMBED_CANONICAL_FLAG) != "1":
        env = dict(os.environ)
        env.update(_EMBED_CANONICAL_ENV)
        env[_EMBED_CANONICAL_FLAG] = "1"
        print("перезапуск в каноническом окружении: "
              + ", ".join(f"{k}={v}" for k, v in _EMBED_CANONICAL_ENV.items()),
              flush=True)
        return subprocess.call([sys.executable, str(Path(__file__).resolve()),
                                "--stage", "embed"], env=env)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = panel_rows()
    print(f"стресс-тест P1, стадия {args.stage}, {stamp}")
    print(f"  панель {len(rows)} документов × {len(st.TRANSFORMS)} преобразований")
    if args.stage == "texts":
        print(f"  построено текстов: {build_texts(rows)}")
    elif args.stage == "parse":
        print(f"  разобрано: {parse_stage(rows)}")
    elif args.stage == "embed":
        print(f"  эмбеддингов: {embed_stage(rows)}")
    elif args.stage == "ner":
        print(f"  NER-размечено: {ner_stage(rows)}")
    else:
        return score_stage(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
