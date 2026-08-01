#!/usr/bin/env python3
"""Четвёртая проверка допуска s01: M01 не растёт.

    python 09-tools/sensitivity_m01_check.py

Критерий §3 амендмента `amendment-sensitivity-truncation.md`: медиана Δz по
панели не положительна, и ни у одного документа рост не превышает 0.5 σ.
Проверка стоит отдельно от трёх остальных именно потому, что M01 и отличает эту
проверку от t10 и t11: у них дублирование поднимало разброс на 7.86 σ.

**Что считается.** M01 в модели — стандартное отклонение косинусов соседних пар
предложений (`extract_semantic.document_features`, строки 290–292: среднее идёт в
`raw_value`, разброс в `normalized_value`, а `clf_run.load_matrix` читает
`normalized_value or raw_value`). Здесь повторяется та же формула на текстах s01.

Значения оригиналов не пересчитываются: они уже лежат в `feature-matrix-v5.csv`,
посчитанные тем же кодом на тех же профилях `prep-v5/prose`.

**Канонический режим обязателен.** Эмбеддинги bge-m3 воспроизводятся побитово
только в один поток с `use_deterministic_algorithms` и батчем 1
(`stress-embed-nondeterminism.md`). Скрипт перезапускает себя сам, как это делает
`stress_run_p1`.
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median, pstdev

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "09-tools"

CANONICAL_FLAG = "STRESS_EMBED_CANONICAL"
CANONICAL_ENV = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "TOKENIZERS_PARALLELISM": "false",
}

PANEL = ROOT / "07-analysis" / "stress-panel-v1.csv"
MATRIX = ROOT / "06-features" / "feature-matrix-v5.csv"
S01_PROSE = ROOT / "04-corpus" / "derived" / "sensitivity-v1" / "s01" / "prose"
OUT_CSV = ROOT / "07-analysis" / "sensitivity-v1-m01.csv"
OUT_JSON = ROOT / "07-analysis" / "sensitivity-v1-m01.json"

FEATURE = "M01"
MEDIAN_MAX = 0.0
DOC_MAX = 0.5

_EMBED_MODEL_NAME = "BAAI/bge-m3"
_EMBED_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
_EMBED_MAX_SEQ = 512


def relaunch_canonical():
    """Перезапуск в каноническом окружении: переменные ставятся до импорта torch."""
    env = dict(os.environ)
    env.update(CANONICAL_ENV)
    env[CANONICAL_FLAG] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    print("перезапуск в каноническом окружении …", flush=True)
    raise SystemExit(subprocess.run([sys.executable, str(Path(__file__))],
                                    env=env).returncode)


def load_matrix_m01():
    """Значения M01 всех документов и параметры стандартизации по корпусу."""
    values = {}
    with MATRIX.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["feature_id"] != FEATURE:
                continue
            raw = r["normalized_value"] or r["raw_value"]
            if raw:
                values[r["document_id"]] = float(raw)
    series = list(values.values())
    return values, fmean(series), pstdev(series)


def main():
    if os.environ.get(CANONICAL_FLAG) != "1":
        relaunch_canonical()

    wrong = {k: os.environ.get(k) for k, v in CANONICAL_ENV.items()
             if os.environ.get(k) != v}
    if wrong:
        raise SystemExit(f"окружение не каноническое: {wrong}")

    sys.path.insert(0, str(TOOLS))
    import numpy as np
    import stanza
    import torch
    from sentence_transformers import SentenceTransformer
    import extract_features as ef
    import extract_semantic as es

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(0)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"проверка M01 для s01, {stamp}")

    revision = f"stanza {stanza.__version__}/ru-syntagrus"
    if revision != ef.STANZA_REVISION:
        raise SystemExit(f"ревизия разбора {revision} против {ef.STANZA_REVISION}")

    with PANEL.open(encoding="utf-8") as fh:
        panel = [r["document_id"] for r in csv.DictReader(fh)]
    base, mean, sd = load_matrix_m01()
    print(f"  панель {len(panel)}, M01 в матрице {len(base)}, σ корпуса {sd:.6f}")
    if sd == 0:
        raise SystemExit("ОСТАНОВ: нулевой разброс M01 по корпусу, Δz не определён")

    nlp = stanza.Pipeline("ru", package="syntagrus",
                          processors="tokenize,pos,lemma,depparse",
                          use_gpu=False, verbose=False)
    model = SentenceTransformer(_EMBED_MODEL_NAME, revision=_EMBED_MODEL_REVISION,
                                device="cpu")
    model.max_seq_length = _EMBED_MAX_SEQ
    model.eval()
    print(f"  модель {_EMBED_MODEL_NAME}@{_EMBED_MODEL_REVISION[:12]}, "
          f"потоков {torch.get_num_threads()}, батч 1", flush=True)

    rows, skipped = [], []
    for i, doc_id in enumerate(panel, start=1):
        path = S01_PROSE / f"{doc_id}.txt"
        if not path.exists() or doc_id not in base:
            skipped.append(doc_id)
            continue
        text = path.read_text(encoding="utf-8")
        doc = nlp(stanza.Document([], text=text))
        payload = {"sentences": [[{"t": w.text, "l": w.lemma or w.text,
                                   "p": w.upos, "d": w.deprel or "",
                                   "h": w.head, "i": w.id, "f": w.feats or ""}
                                  for w in s.words] for s in doc.sentences]}
        kept = es.usable_sentences(payload)
        texts = [t for _, t in kept]
        if len(texts) < 2:
            skipped.append(doc_id)
            continue

        vectors = model.encode(texts, batch_size=1, convert_to_numpy=True,
                               normalize_embeddings=True, show_progress_bar=False)
        # Та же формула, что в extract_semantic.document_features, строки 290-292.
        pairs = [float(np.dot(vectors[j], vectors[j + 1]))
                 for j in range(len(vectors) - 1)]
        spread = float(np.std(pairs, ddof=1)) if len(pairs) > 1 else None
        if spread is None:
            skipped.append(doc_id)
            continue

        rows.append({
            "document_id": doc_id,
            "m01_baseline": round(base[doc_id], 8),
            "m01_s01": round(spread, 8),
            "delta": round(spread - base[doc_id], 8),
            "delta_z": round((spread - base[doc_id]) / sd, 6),
            "sentences": len(texts),
        })
        if i % 10 == 0 or i == len(panel):
            print(f"    обработано {i} из {len(panel)}", flush=True)

    if not rows:
        raise SystemExit("ОСТАНОВ: ни одного документа не посчитано")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    deltas = [r["delta_z"] for r in rows]
    med = median(deltas)
    worst = max(deltas)
    grew = [r for r in rows if r["delta_z"] > DOC_MAX]
    passed = med <= MEDIAN_MAX and not grew

    result = {
        "check": "m01_not_growing",
        "transform_id": "s01",
        "amendment": "02-preregistration/amendment-sensitivity-truncation.md",
        "criterion": {"median_delta_z_max": MEDIAN_MAX, "per_document_max": DOC_MAX},
        "documents": len(rows),
        "skipped": skipped,
        "median_delta_z": round(med, 6),
        "max_delta_z": round(worst, 6),
        "min_delta_z": round(min(deltas), 6),
        "documents_above_doc_max": [r["document_id"] for r in grew],
        "passed": passed,
        "corpus_sigma": round(sd, 8),
        "feature_semantics": "M01 = SD косинусов соседних пар (normalized_value)",
        "comparison_t11": "у t11 медиана Δz равна +7.86",
        "created_at": stamp,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"  документов {len(rows)}, пропущено {len(skipped)}")
    print(f"  медиана Δz {med:+.4f}, максимум {worst:+.4f}, минимум {min(deltas):+.4f}")
    print(f"  проверка {'пройдена' if passed else 'ПРОВАЛЕНА'}")
    print(f"  записано: {OUT_CSV.name}, {OUT_JSON.name}")

    if not passed:
        raise SystemExit("ОСТАНОВ: критерий §3 не выполнен, ослаблять его нельзя — "
                         "развилка выносится PI")


if __name__ == "__main__":
    main()
