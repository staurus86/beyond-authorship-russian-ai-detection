#!/usr/bin/env python3
"""Процедура 3, nll-v1: средний token NLL на открытой русскоязычной модели.

Спецификация — `07-analysis/proc3-zeroshot-spec.md`, статус и правило синтеза —
`procedures-2-4-registration.md`. Модель, устройство и выбор между large и small
приходят из манифеста preflight; здесь они не переопределяются.

    python 09-tools/preflight_procedures_2_4.py && python 09-tools/nll_zero_shot.py
    python 09-tools/nll_zero_shot.py --series nll-v2      # частичный пересчёт на prep-v5

Серия задаётся параметром, значения по умолчанию не меняются. В серии v2 вход —
профили prep-v5, и **пересчитываются только документы с изменившимся текстом**:
у остальных значение наследуется из nll-v1. Основание — `reuse-gate-v5.md`;
манифест серии v2 различает унаследованное, пересчитанное, исключённое и
sentinel-проверку.

**Статус — exploratory.** Величина одна: средний NLL на предсказываемый токен.
Перплексия отдельной метрикой не считается. Направление шкалы зафиксировано до
расчёта: ниже NLL — лучше предсказуем; при синтезе знак обращается.

**net — `not available`.** Удаление заголовков не равнозначно вычитанию
предписанной брифом структуры, имитация ради симметрии не создаётся.
"""

import csv
import hashlib
import json
import random
import statistics
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
PAIRS = ROOT / "07-analysis" / "score-v1-pairs.csv"
PREFLIGHT_MANIFEST = ROOT / "07-analysis" / "procedures-2-4-manifest.json"

OUT_SCORES = ROOT / "07-analysis" / "nll-v1-scores.csv"
OUT_O1 = ROOT / "07-analysis" / "nll-v1-o1-contrasts.csv"
OUT_MANIFEST = ROOT / "07-analysis" / "nll-v1-manifest.json"
OUT_REPORT = ROOT / "07-analysis" / "nll-v1-report.md"

# nll-v2, 2026-07-29: частичный пересчёт на prep-v5.
SERIES = "nll-v1"
PREP_MANIFEST_V4 = PREP_MANIFEST
SCORES_V1 = OUT_SCORES

WINDOW = 1024            # §4 спецификации
STRIDE = 512
RETEST_DOCS = 10         # §5: повторяемость проверяется на десяти документах
BOOTSTRAP = 5000
ALPHA_EACH = 0.05 / 2    # Бонферрони внутри семейства O1


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def load_config():
    """Модель, revision и устройство берутся из манифеста preflight, не отсюда."""
    manifest = json.loads(PREFLIGHT_MANIFEST.read_text(encoding="utf-8"))
    if not manifest.get("preflight_passed"):
        raise SystemExit("preflight не пройден — расчёт запрещён")
    cfg = manifest.get("nll")
    if not cfg or not cfg.get("model"):
        raise SystemExit("в манифесте preflight нет выбранной модели процедуры 3")
    return manifest, cfg


class Scorer:
    """Средний NLL документа. Каждый токен оценивается ровно один раз."""

    def __init__(self, model_name, revision, device, dtype):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name, revision=revision, dtype=dtype)
        self.model.to(device).eval()
        self.device = device
        self.bos_id = self.tokenizer.bos_token_id
        # §3: токенизатор BOS не ставит, поэтому он добавляется кодом явно.
        probe = self.tokenizer("проверка", add_special_tokens=True)["input_ids"]
        self.adds_bos = bool(probe) and probe[0] == self.bos_id

    def encode(self, text):
        """NFC, затем токены без служебных, затем BOS явно первым элементом."""
        ids = self.tokenizer(unicodedata.normalize("NFC", text),
                             add_special_tokens=False)["input_ids"]
        return [self.bos_id] + ids

    @torch.no_grad()
    def score(self, text):
        seq = self.encode(text)
        n_tokens = len(seq) - 1           # BOS вероятности не получает
        if n_tokens < 1:
            return None, 0, 0, set()
        logprob_sum, scored, windows = 0.0, set(), 0
        start = 0
        while True:
            window = seq[start:start + WINDOW]
            ids = torch.tensor([window], device=self.device)
            logits = self.model(ids).logits[0].float()
            logprobs = torch.log_softmax(logits, dim=-1)
            first_new = 1 if start == 0 else start + STRIDE
            last = start + len(window) - 1
            for pos in range(first_new, last + 1):
                i = pos - start - 1       # logits[i] предсказывает window[i+1]
                logprob_sum += logprobs[i, window[i + 1]].item()
                scored.add(pos)
            windows += 1
            if last >= n_tokens:
                break
            start += STRIDE
        return -logprob_sum / len(scored), len(scored), windows, scored


def bootstrap_ci(values, clusters, seed, reps=BOOTSTRAP):
    """Кластерный percentile bootstrap по заданию — как в процедуре 1."""
    by_cluster = defaultdict(list)
    for value, cluster in zip(values, clusters):
        by_cluster[cluster].append(value)
    keys = list(by_cluster)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        picked = [rng.choice(keys) for _ in keys]
        means.append(statistics.fmean([v for k in picked for v in by_cluster[k]]))
    means.sort()
    observed = statistics.fmean(values)
    beyond = (sum(1 for m in means if m <= 0) if observed > 0
              else sum(1 for m in means if m >= 0))
    return (means[int(0.025 * reps)], means[int(0.975 * reps) - 1],
            min(1.0, 2 * max(beyond, 1) / reps))


def switch_to_v2():
    """Входы и имена выходов серии v2. Файлы v1 не перезаписываются."""
    global PREP_MANIFEST, OUT_SCORES, OUT_O1, OUT_MANIFEST, OUT_REPORT
    PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v5" / "manifest.csv"
    analysis = ROOT / "07-analysis"
    OUT_SCORES = analysis / "nll-v2-scores.csv"
    OUT_O1 = analysis / "nll-v2-o1-contrasts.csv"
    OUT_MANIFEST = analysis / "nll-v2-manifest.json"
    OUT_REPORT = analysis / "nll-v2-report.md"


def inherited_from_v1():
    """Документы, у которых профиль prose не изменился, и их значения из nll-v1.

    Пересчитывать их означало бы тратить прогон модели на байт-идентичный вход.
    Проверка того, что это допустимо, — `reuse-gate-v5.md` и блок повторяемости
    ниже: он пересчитывает часть унаследованных документов и сверяет значения.
    """
    v4 = {r["document_id"]: r for r in read_rows(PREP_MANIFEST_V4)}
    v5 = {r["document_id"]: r for r in read_rows(PREP_MANIFEST)}
    scores = {r["document_id"]: r for r in read_rows(SCORES_V1)}
    inherited = {}
    for doc_id, row in v5.items():
        old_row = v4.get(doc_id)
        if (old_row and old_row["prose_sha256"] == row["prose_sha256"]
                and doc_id in scores and scores[doc_id]["nll"]):
            inherited[doc_id] = scores[doc_id]
    dropped = sorted(set(scores) - set(v5))
    return inherited, dropped


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES, choices=["nll-v1", "nll-v2"],
                        help="nll-v1 — замороженный прогон на prep-v4 (по умолчанию); "
                             "nll-v2 — частичный пересчёт на prep-v5")
    args = parser.parse_args()
    SERIES = args.series

    if SERIES == "nll-v2":
        switch_to_v2()
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.require(gate.STAGE_ANALYSIS, "proc3")

    manifest, cfg = load_config()
    seed = manifest["seed"]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    device, dtype_name = cfg.get("device", "cpu"), cfg.get("dtype", "float32")
    dtype = getattr(torch, dtype_name)
    print(f"nll-v1, запуск {stamp}\n  модель {cfg['model']} @ {cfg['revision'][:12]}, "
          f"{device}/{dtype_name}, seed {seed}")

    scorer = Scorer(cfg["model"], cfg["revision"], device, dtype)
    print(f"  токенизатор: bos_token_id {scorer.bos_id}, "
          f"ставит BOS сам — {'да' if scorer.adds_bos else 'нет'}")

    docs = sorted(read_rows(PREP_MANIFEST), key=lambda r: r["document_id"])
    # Имя отдельное: `dropped` ниже занято счётчиком выпавших пар контрастов O1.
    inherited, dropped_from_v1 = ({}, []) if SERIES == "nll-v1" else inherited_from_v1()
    if SERIES == "nll-v2":
        print(f"  документов {len(docs)}: унаследовано {len(inherited)}, "
              f"пересчитывается {len(docs) - len(inherited)}, "
              f"исключено из v1 {len(dropped_from_v1)}")
    rows_out, coverage_failures = [], []
    for n, row in enumerate(docs, 1):
        path = ROOT / row["prose_path"]
        text = path.read_text(encoding="utf-8")
        if sha256_file(path) != row["prose_sha256"]:
            raise SystemExit(f"хеш профиля не сходится: {row['document_id']}")
        if row["document_id"] in inherited:
            old_row = inherited[row["document_id"]]
            rows_out.append({"document_id": row["document_id"],
                             "origin_class": row["origin_class"],
                             "generation_channel": row["generation_channel"],
                             "nll": old_row["nll"],
                             "scored_tokens": int(old_row["scored_tokens"]),
                             "n_windows": int(old_row["n_windows"]),
                             "expected_tokens": int(old_row["expected_tokens"]),
                             "provenance": "унаследовано из nll-v1"})
            continue
        nll, scored, windows, positions = scorer.score(text)
        expected = len(scorer.encode(text)) - 1
        # §4: покрытие проверяется по каждому документу, расхождение останавливает расчёт
        if nll is None or scored == 0 or scored != expected or len(positions) != scored:
            coverage_failures.append({"document_id": row["document_id"],
                                      "scored": scored, "expected": expected})
        rows_out.append({"document_id": row["document_id"],
                         "origin_class": row["origin_class"],
                         "generation_channel": row["generation_channel"],
                         "nll": "" if nll is None else f"{nll:.6f}",
                         "scored_tokens": scored, "n_windows": windows,
                         "expected_tokens": expected,
                         "provenance": "пересчитано" if SERIES == "nll-v2" else ""})
        if n % 100 == 0 or n == len(docs):
            print(f"  {n}/{len(docs)}")

    if coverage_failures:
        for f in coverage_failures[:10]:
            print(f"  СБОЙ покрытия: {f}")
        raise SystemExit(f"покрытие не сошлось у {len(coverage_failures)} документов")

    with OUT_SCORES.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]))
        w.writeheader(); w.writerows(rows_out)
    print(f"  оценки: {OUT_SCORES.name}, строк {len(rows_out)}")

    # §5: повторяемость на десяти документах, ожидание — побитовое совпадение
    retest = []
    for row in docs[:RETEST_DOCS]:
        text = (ROOT / row["prose_path"]).read_text(encoding="utf-8")
        again, _, _, _ = scorer.score(text)
        first = float(next(r["nll"] for r in rows_out
                           if r["document_id"] == row["document_id"]))
        retest.append({"document_id": row["document_id"], "first": first,
                       "second": again, "delta": abs(first - again)})
    max_delta = max(r["delta"] for r in retest)
    # В серии v2 те же документы работают sentinel-проверкой наследования:
    # значение, взятое из nll-v1, сверяется со свежим прогоном модели.
    retest_inherited = sum(1 for r in retest if r["document_id"] in inherited)
    print(f"  повторяемость: max|Δ| = {max_delta:.3e} на {len(retest)} документах"
          + (f", из них унаследованных {retest_inherited}" if SERIES == "nll-v2" else ""))

    # контрасты O1: те же пары, что в процедуре 1
    nll_of = {r["document_id"]: float(r["nll"]) for r in rows_out if r["nll"]}
    pairs = read_rows(PAIRS)
    o1_rows = []
    for contrast in ("P3-P1", "P2-P1"):
        diffs, clusters, dropped = [], [], 0
        for pair in pairs:
            if pair["contrast"] != contrast:
                continue
            left, right = nll_of.get(pair["doc_left"]), nll_of.get(pair["doc_right"])
            if left is None or right is None:
                dropped += 1
                continue
            diffs.append(left - right)
            clusters.append(pair["brief_id"])
        mean = statistics.fmean(diffs)
        lo, hi, p = bootstrap_ci(diffs, clusters, seed)
        o1_rows.append({
            "estimand": "full", "contrast": contrast, "status": "exploratory",
            "n_pairs": len(diffs), "dropped_pairs": dropped,
            "n_clusters": len(set(clusters)),
            "mean_diff_nll": f"{mean:.4f}", "ci_low": f"{lo:.4f}", "ci_high": f"{hi:.4f}",
            "p_bootstrap": f"{p:.4f}", "alpha": f"{ALPHA_EACH:.4f}",
            "sign_after_convention": "+" if -mean > 0 else "-",
            "convention_note": "знак обращён: ниже NLL — более AI-подобно",
        })
        o1_rows.append({
            "estimand": "net", "contrast": contrast, "status": "not available",
            "n_pairs": "", "dropped_pairs": "", "n_clusters": "",
            "mean_diff_nll": "", "ci_low": "", "ci_high": "", "p_bootstrap": "",
            "alpha": "", "sign_after_convention": "",
            "convention_note": "у перплексии нет честного аналога net-estimand",
        })
    with OUT_O1.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(o1_rows[0]))
        w.writeheader(); w.writerows(o1_rows)
    print(f"  контрасты O1: {OUT_O1.name}, строк {len(o1_rows)}")

    out_manifest = {
        "created_at": stamp, "procedure": SERIES, "status": "exploratory",
        "seed": seed,
        "model": {"name": cfg["model"], "revision": cfg["revision"],
                  "device": device, "dtype": dtype_name,
                  "weights_sha256": cfg.get("weights_sha256", ""),
                  "bos_token_id": scorer.bos_id,
                  "tokenizer_adds_bos": scorer.adds_bos,
                  "torch": torch.__version__,
                  "transformers": __import__("transformers").__version__},
        "window": WINDOW, "stride": STRIDE,
        "inputs": {f"{PREP_MANIFEST.parent.name}/manifest.csv": sha256_file(PREP_MANIFEST),
                   "score-v1-pairs.csv": sha256_file(PAIRS)},
        "code_sha256": sha256_file(Path(__file__)),
        "retest": {"documents": len(retest), "max_abs_delta": max_delta,
                   "per_document": retest},
        "documents_scored": len(rows_out),
        "tokens_scored": sum(r["scored_tokens"] for r in rows_out),
    }
    if SERIES == "nll-v2":
        # Четыре категории обязательны: без них «пересчитано» и «взято как есть»
        # в одной таблице неразличимы.
        out_manifest["provenance"] = {
            "inherited_from_nll_v1": len(inherited),
            "recomputed": len(rows_out) - len(inherited),
            "excluded_present_in_v1": len(dropped_from_v1),
            "excluded_ids": dropped_from_v1,
            "sentinel_recomputed": retest_inherited,
            "sentinel_max_abs_delta": max_delta,
            "basis": "профиль prose байт-идентичен prep-v4; шлюз — reuse-gate-v5.md",
        }
        out_manifest["inputs"]["nll-v1-scores.csv"] = sha256_file(SCORES_V1)
    OUT_MANIFEST.write_text(json.dumps(out_manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"  манифест: {OUT_MANIFEST.name}")

    nlls = [float(r["nll"]) for r in rows_out if r["nll"]]
    report = [
        f"# Процедура 3, {SERIES}: результаты замороженного прогона", "",
        f"Запуск {stamp}. Модель `{cfg['model']}`, revision `{cfg['revision'][:12]}`, "
        f"{device}/{dtype_name}. Хеши входов и кода — `{OUT_MANIFEST.name}`.", "",
        "**Статус — exploratory.** Операционализация написана 2026-07-27, после "
        "просмотра результатов процедуры 1.", "",
        "Величина — средний NLL на предсказываемый токен. Перплексия отдельной "
        "метрикой не считается. Ниже NLL — текст лучше предсказуем моделью; "
        "при синтезе знак обращается.", "",
        "## Покрытие", "",
        f"Документов оценено {len(rows_out)}, токенов {sum(r['scored_tokens'] for r in rows_out)}. "
        f"У каждого документа число оценённых токенов совпало с ожидаемым; "
        f"повторно оценённых позиций нет.", "",
        f"Повторяемость на {len(retest)} документах: max|Δ| = {max_delta:.3e}.", "",
        *([f"**Пересчитано {len(rows_out) - len(inherited)} документов из "
           f"{len(rows_out)}, у остальных значение унаследовано из nll-v1.** "
           f"Основание — байт-идентичный профиль prose. Из {len(retest)} "
           f"документов блока повторяемости унаследованы {retest_inherited}: "
           f"для них сверка со свежим прогоном и есть проверка наследования. "
           f"Исключённых из v1 документов {len(dropped_from_v1)}.", ""]
          if SERIES == "nll-v2" else []),
        f"Распределение NLL по корпусу: медиана {statistics.median(nlls):.4f}, "
        f"среднее {statistics.fmean(nlls):.4f}, минимум {min(nlls):.4f}, "
        f"максимум {max(nlls):.4f}.", "",
        "## Контрасты O1", "",
        "| Estimand | Контраст | Пар | Кластеров | Средняя разница NLL | 95% CI | p | Знак после приведения |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in o1_rows:
        if r["estimand"] == "net":
            report.append(f"| net | {r['contrast']} | — | — | not available | — | — | — |")
            continue
        report.append(f"| full | {r['contrast']} | {r['n_pairs']} | {r['n_clusters']} | "
                      f"{r['mean_diff_nll']} | [{r['ci_low']}; {r['ci_high']}] | "
                      f"{r['p_bootstrap']} | {r['sign_after_convention']} |")
    report += ["", f"Кластерный percentile bootstrap по заданию, {BOOTSTRAP} повторов, "
                   f"α = {ALPHA_EACH} на тест.", "",
               "**net не операционализируется.** Удаление заголовков не равнозначно "
               "вычитанию предписанной брифом структуры: бриф диктует ещё порядок "
               "изложения, набор разделов и обязательные пункты.", ""]
    OUT_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}")

    if SERIES == "nll-v2":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            "proc3-v2", inputs=[PREP_MANIFEST, PAIRS, SCORES_V1],
            outputs=[OUT_SCORES, OUT_O1, OUT_MANIFEST, OUT_REPORT])
        print("  дочерний манифест: manifests-v2/proc3-v2-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
