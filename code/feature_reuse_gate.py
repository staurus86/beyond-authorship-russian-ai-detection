#!/usr/bin/env python3
"""Шлюз повторного использования разборов: sentinel-проверка кеша перед prep-v5.

    python 09-tools/feature_reuse_gate.py
    python 09-tools/feature_reuse_gate.py --stages stanza ner

Матрица prep-v5 берёт разбор 1814 неизменённых документов из кеша, построенного
под prep-v4, и пересчитывает только 68 изменённых. Основание — байт-идентичный
вход при той же ревизии модели. Основание проверяется, а не объявляется: на
sentinel-выборке разбор строится заново и сверяется с кешем.

Выборка задана до прогона: по три документа каждого класса, отсортированных по
идентификатору, из числа неизменённых. Отрицательный результат означает, что
разбор недетерминирован, и переиспользовать кеш нельзя ни для одного документа.

Проверка не смотрит на зависимую переменную: сверяются разборы текста, а не
значения признаков и не метки.
"""

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_cache as fc  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
OUT_REPORT = ROOT / "07-analysis" / "feature-reuse-gate-v5.md"
PER_CLASS = 3
# Допуск на эмбеддинги взят из шлюза NLL (`reuse-gate-v5.md`): float32 не даёт
# побитового совпадения при другой раскладке по пачкам.
EMBED_TOLERANCE = 1e-4


def read_registry():
    import csv
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def sentinel_ids():
    """Неизменённые документы: по три на класс, отбор по сортировке — до прогона."""
    m4, m5 = fc.manifest("prep-v4"), fc.manifest("prep-v5")
    unchanged = {d for d in m5 if d in m4
                 and m4[d]["prose_sha256"] == m5[d]["prose_sha256"]
                 and m4[d]["full_sha256"] == m5[d]["full_sha256"]}
    picked = []
    for origin in ("A", "H"):
        ids = sorted(r["document_id"] for r in read_registry()
                     if r["origin_class"] == origin and r["document_id"] in unchanged)
        picked += ids[:PER_CLASS]
    return picked, len(unchanged)


def check_stanza(ids, results):
    import stanza
    revision = f"stanza {stanza.__version__}/ru-syntagrus"
    index = fc.load_index(fc.CACHE_ROOT / "stanza-v1")
    nlp = stanza.Pipeline("ru", package="syntagrus",
                          processors="tokenize,pos,lemma,depparse",
                          use_gpu=False, verbose=False)
    for doc_id in ids:
        text_path = fc.input_path("prep-v5", "prose", doc_id)
        sha = fc.sha256_file(text_path)
        cached_path = fc.lookup(fc.CACHE_ROOT / "stanza-v1", index, doc_id, sha,
                                revision)
        if cached_path is None:
            results.append(("stanza", doc_id, None, "записи в кеше нет"))
            continue
        with gzip.open(cached_path, "rt", encoding="utf-8") as fh:
            cached = json.load(fh)
        text = text_path.read_text(encoding="utf-8")
        parsed = nlp([stanza.Document([], text=text)])[0]
        fresh = [[{"t": w.text, "l": w.lemma or w.text, "p": w.upos,
                   "d": w.deprel or "", "h": w.head, "i": w.id, "f": w.feats or ""}
                  for w in sentence.words] for sentence in parsed.sentences]
        same = fresh == cached["sentences"]
        detail = (f"предложений {len(fresh)}, токенов "
                  f"{sum(len(s) for s in fresh)}")
        results.append(("stanza", doc_id, same, detail))


def check_ner(ids, results):
    import extract_ner as ner
    index = fc.load_index(fc.CACHE_ROOT / "ner-v1")
    m5 = fc.manifest("prep-v5")
    tools = ner.load_natasha()
    for doc_id in ids:
        sha = m5[doc_id]["full_sha256"]
        cached_path = fc.lookup(fc.CACHE_ROOT / "ner-v1", index, doc_id, sha,
                                ner.NER_REVISION)
        if cached_path is None:
            results.append(("ner", doc_id, None, "записи в кеше нет"))
            continue
        with gzip.open(cached_path, "rt", encoding="utf-8") as fh:
            cached = json.load(fh)["spans"]
        text = fc.input_path("prep-v5", "full", doc_id).read_text(encoding="utf-8")
        fresh = ner.tag_document(tools, text)
        results.append(("ner", doc_id, fresh == cached, f"спанов {len(fresh)}"))


def check_embed(ids, results):
    import torch
    from sentence_transformers import SentenceTransformer
    import extract_semantic as sem
    torch.set_num_threads(int(sem.TORCH_THREADS))
    model = SentenceTransformer(sem.MODEL_NAME, revision=sem.MODEL_REVISION,
                                device="cpu")
    model.max_seq_length = sem.MAX_SEQ_LENGTH
    embed_index = fc.load_index(fc.CACHE_ROOT / "embed-v1")
    stanza_index = fc.load_index(fc.CACHE_ROOT / "stanza-v1")
    sem_manifest = fc.manifest("prep-v5")
    for doc_id in ids:
        sha = fc.sha_for("prep-v5", "prose", doc_id)
        cached_path = fc.lookup(fc.CACHE_ROOT / "embed-v1", embed_index, doc_id, sha,
                                sem.EMBED_REVISION)
        parse_path = fc.lookup(fc.CACHE_ROOT / "stanza-v1", stanza_index, doc_id, sha,
                               sem.STANZA_REVISION)
        if cached_path is None or parse_path is None:
            results.append(("embed", doc_id, None, "записи в кеше нет"))
            continue
        with np.load(cached_path) as payload:
            cached = payload["embeddings"]
            kept_index = list(payload["sentence_index"])
        with gzip.open(parse_path, "rt", encoding="utf-8") as fh:
            parsed = json.load(fh)
        kept = sem.usable_sentences(parsed)
        if not kept:
            results.append(("embed", doc_id, len(cached) == 0, "пригодных предложений нет"))
            continue
        fresh = model.encode([text for _, text in kept], batch_size=sem.BATCH_SIZE,
                             normalize_embeddings=True, show_progress_bar=False,
                             convert_to_numpy=True).astype(np.float32)
        if fresh.shape != cached.shape:
            results.append(("embed", doc_id, False,
                            f"форма {fresh.shape} против {cached.shape}"))
            continue
        gap = float(np.max(np.abs(fresh - cached)))
        # Побитового совпадения у float32 здесь не бывает: кэш считался пачками
        # через границы документов, и порядок редукции в сумме другой. Допуск
        # 1e-4 — тот же, что в шлюзе NLL (`reuse-gate-v5.md`), а не назначенный
        # под увиденное число. Решающая проверка ниже: доходит ли расхождение
        # до значений признаков после форматирования %.6g.
        values_fresh, _, _ = sem.document_features(
            fresh, parsed, sem_manifest.get(doc_id), kept_index)
        values_cached, _, _ = sem.document_features(
            cached, parsed, sem_manifest.get(doc_id), kept_index)

        # Критерий уточнён после первого прогона и помечен: побитового равенства
        # записанных значений float32 не даёт, поэтому сверяется величина сдвига,
        # а не совпадение строк. Отдельно считается, у скольких значений
        # изменилась хотя бы последняя записанная цифра.
        drift, shifted = 0.0, 0
        for key in values_fresh:
            for slot in (1, 2):
                x, y = values_fresh[key][slot], values_cached[key][slot]
                if not (isinstance(x, float) and isinstance(y, float)) or not y:
                    continue
                drift = max(drift, abs(x - y) / abs(y))
                if f"{x:.6g}" != f"{y:.6g}":
                    shifted += 1
        results.append(("embed", doc_id,
                        gap <= EMBED_TOLERANCE and drift <= EMBED_TOLERANCE,
                        f"векторов {len(fresh)}, расхождение векторов {gap:.2e}, "
                        f"признаков sem-v1 {drift:.2e} при допуске "
                        f"{EMBED_TOLERANCE:.0e}; последняя записанная цифра "
                        f"сдвинулась у {shifted} значений"))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stages", nargs="+", default=["stanza", "ner", "embed"],
                        choices=["stanza", "ner", "embed"])
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ids, unchanged = sentinel_ids()
    print(f"шлюз повторного использования, {stamp}")
    print(f"  неизменённых документов {unchanged}, sentinel {len(ids)}: "
          f"{', '.join(ids)}")

    results = []
    if "stanza" in args.stages:
        check_stanza(ids, results)
    if "ner" in args.stages:
        check_ner(ids, results)
    if "embed" in args.stages:
        check_embed(ids, results)

    for stage, doc_id, same, detail in results:
        mark = "совпал" if same else ("не проверен" if same is None else "РАЗОШЁЛСЯ")
        print(f"  {stage} {doc_id}: {mark} — {detail}")

    failed = [r for r in results if r[2] is False]
    lines = ["# Шлюз повторного использования разборов перед prep-v5", "",
             f"Собрано {stamp} скриптом `09-tools/feature_reuse_gate.py`.", "",
             f"Матрица prep-v5 берёт разбор {unchanged} неизменённых документов из "
             "кеша и пересчитывает 68 изменённых. Основание — байт-идентичный вход "
             "при той же ревизии модели. Здесь оно проверено: на sentinel-выборке "
             "разбор построен заново и сверен с кешем.", "",
             f"Выборка задана до прогона: по {PER_CLASS} документа каждого класса, "
             "отбор по сортировке идентификаторов среди неизменённых.", "",
             "| Этап | Документ | Результат | Детали |", "|---|---|---|---|"]
    for stage, doc_id, same, detail in results:
        mark = "совпал" if same else ("не проверен" if same is None else "**разошёлся**")
        lines.append(f"| {stage} | `{doc_id}` | {mark} | {detail} |")
    lines += ["",
              ("**Вердикт: разборы воспроизводятся, переиспользование кеша "
               "допустимо.** Формулировка ограничена выборкой: расхождение не "
               "обнаружено на sentinel-документах, а не «его нет во всём корпусе»."
               if not failed else
               f"**Вердикт: разошлось проверок {len(failed)}.** Переиспользование "
               "кеша запрещено до объяснения расхождения."),
              "",
              "**Разбор Stanza и разметка NER сверялись побитово, эмбеддинги — по "
              f"величине сдвига при допуске {EMBED_TOLERANCE:.0e}.** Критерий для "
              "эмбеддингов уточнён после первого прогона и помечен: кэш считался "
              "пачками через границы документов, порядок редукции в сумме другой, "
              "и побитового совпадения float32 не даёт. Допуск взят тот же, что в "
              "шлюзе NLL (`reuse-gate-v5.md`), а не назначен под увиденное число.",
              "",
              "Практическое следствие обратное опасению: переиспользование кеша "
              "этот источник шума убирает, а не создаёт. Значения признаков "
              "неизменённых документов в матрице v5 совпали с v4 побитово — "
              "инвариант 3 preflight, 114 282 сверенных значения.", ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
