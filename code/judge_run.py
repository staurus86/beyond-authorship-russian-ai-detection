#!/usr/bin/env python3
"""Процедура 4, judge-v1: модель-судья, три вызова на документ, медиана.

Спецификация — `07-analysis/proc4-judge-spec.md`, статус и правило синтеза —
`procedures-2-4-registration.md`. Модель, параметры вызова и объём приходят из
манифеста preflight и здесь не переопределяются.

    python 09-tools/preflight_procedures_2_4.py && python 09-tools/judge_run.py
    python 09-tools/judge_run.py --series judge-v2     # частичный пересчёт на prep-v5

Серия задаётся параметром, значения по умолчанию не меняются. В серии v2 судья
вызывается **только для документов с изменившимся текстом**: у остальных оценки
трёх сидов наследуются из judge-v1. Основание — `reuse-gate-v5.md`; манифест
серии v2 различает унаследованное, пересчитанное, исключённое и sentinel.

**Статус — exploratory.** Шкала измеряет выраженность стилевых признаков, а не
вероятность машинного происхождения. Перевод в вероятность и пороги вида
«выше 60 — машина» запрещены.

**net — `not available`.** Судья читает текст целиком; удаление разметки изменило
бы не только структурный вклад, но и связность. Имитация не создаётся.

Прогон идёт одной командой. Обрыв связи или остановка машины не отменяют
сделанного: сырые ответы пишутся построчно, и повторный запуск продолжает с
места обрыва, не пересчитывая уже полученные вызовы.
"""

import csv
import hashlib
import json
import random
import statistics
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
PAIRS = ROOT / "07-analysis" / "score-v1-pairs.csv"
SPEC = ROOT / "07-analysis" / "proc4-judge-spec.md"
PREFLIGHT_MANIFEST = ROOT / "07-analysis" / "procedures-2-4-manifest.json"

OUT_RAW = ROOT / "07-analysis" / "judge-v1-raw.jsonl"
OUT_SCORES = ROOT / "07-analysis" / "judge-v1-scores.csv"
OUT_AGREEMENT = ROOT / "07-analysis" / "judge-v1-agreement.md"
OUT_O1 = ROOT / "07-analysis" / "judge-v1-o1-contrasts.csv"
OUT_MANIFEST = ROOT / "07-analysis" / "judge-v1-manifest.json"

# judge-v2, 2026-07-29: частичный пересчёт на prep-v5.
SERIES = "judge-v1"
PREP_MANIFEST_V4 = PREP_MANIFEST
RAW_V1 = OUT_RAW
SENTINEL_DOCS = 3        # унаследованных документов, пересчитываемых для сверки

OLLAMA = "http://localhost:11434/api/generate"
BOOTSTRAP = 5000
ALPHA_EACH = 0.05 / 2
WIDE_RANGE = 20          # §5: доля документов с размахом свыше 20 пунктов


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def judge_prompt():
    """§2: промпт берётся из спецификации дословно, а не переписывается здесь."""
    return SPEC.read_text(encoding="utf-8").split("```")[1].strip("\n")


def load_config():
    manifest = json.loads(PREFLIGHT_MANIFEST.read_text(encoding="utf-8"))
    if not manifest.get("preflight_passed"):
        raise SystemExit("preflight не пройден — расчёт запрещён")
    cfg = manifest.get("judge")
    if not cfg or not cfg.get("model"):
        raise SystemExit("в манифесте preflight нет артефакта судьи")
    return manifest, cfg


def call_judge(cfg, prompt, seed):
    body = {
        "model": cfg["model"], "prompt": prompt, "stream": False,
        "format": cfg["response_schema"],
        "options": {"temperature": cfg["sampling"]["temperature"],
                    "top_p": cfg["sampling"]["top_p"],
                    "top_k": cfg["sampling"]["top_k"],
                    "seed": seed, "num_ctx": cfg["num_ctx"],
                    "num_predict": cfg["num_predict"]},
    }
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as fh:
        return json.load(fh)


def parse(response):
    """§4: разбор ответа. Возвращает (score или None, статус)."""
    raw = (response.get("response") or "").strip()
    if not raw:
        return None, "refusal"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, "invalid_json"
    score = parsed.get("score")
    if not isinstance(score, int):
        return None, "invalid_json"
    if not 0 <= score <= 100:
        return None, "out_of_range"
    return score, "ok"


def icc21(rows):
    """ICC(2,1): двусторонняя случайная модель, единичная мера, три прогона.

    Считается только по документам с полным набором трёх оценок — иначе строки
    ANOVA несопоставимы. Число таких документов публикуется рядом со значением.
    """
    table = [r for r in rows if len(r) == 3]
    n, k = len(table), 3
    if n < 2:
        return None, n
    grand = statistics.fmean(v for r in table for v in r)
    row_means = [statistics.fmean(r) for r in table]
    col_means = [statistics.fmean(r[j] for r in table) for j in range(k)]
    ss_rows = k * sum((m - grand) ** 2 for m in row_means)
    ss_cols = n * sum((m - grand) ** 2 for m in col_means)
    ss_total = sum((v - grand) ** 2 for r in table for v in r)
    ss_error = ss_total - ss_rows - ss_cols
    ms_rows = ss_rows / (n - 1)
    ms_cols = ss_cols / (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))
    denom = ms_rows + (k - 1) * ms_error + k * (ms_cols - ms_error) / n
    return (ms_rows - ms_error) / denom if denom else None, n


def bootstrap_ci(values, clusters, seed, reps=BOOTSTRAP):
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


def done_calls():
    """Уже выполненные вызовы из сырого журнала — для продолжения после обрыва."""
    if not OUT_RAW.exists():
        return {}
    done = {}
    with OUT_RAW.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            done[(rec["document_id"], rec["seed"])] = rec
    return done


def switch_to_v2():
    """Входы и имена выходов серии v2. Файлы v1 не перезаписываются."""
    global PREP_MANIFEST, OUT_RAW, OUT_SCORES, OUT_AGREEMENT, OUT_O1, OUT_MANIFEST
    PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v5" / "manifest.csv"
    analysis = ROOT / "07-analysis"
    OUT_RAW = analysis / "judge-v2-raw.jsonl"
    OUT_SCORES = analysis / "judge-v2-scores.csv"
    OUT_AGREEMENT = analysis / "judge-v2-agreement.md"
    OUT_O1 = analysis / "judge-v2-o1-contrasts.csv"
    OUT_MANIFEST = analysis / "judge-v2-manifest.json"


def inherited_from_v1():
    """Оценки судьи для документов, у которых профиль prose не изменился.

    Записи берутся из сырого журнала judge-v1: там сохранён ответ на каждый
    вызов. Унаследованные вызовы в журнал v2 не дописываются — иначе они
    выглядели бы как выполненные в этом прогоне.
    """
    v4 = {r["document_id"]: r for r in read_rows(PREP_MANIFEST_V4)}
    v5 = {r["document_id"]: r for r in read_rows(PREP_MANIFEST)}
    same = {d for d, row in v5.items()
            if d in v4 and v4[d]["prose_sha256"] == row["prose_sha256"]}
    by_doc, seen = defaultdict(dict), set()
    with RAW_V1.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(rec["document_id"])
            if rec["document_id"] in same and rec["status"] == "ok":
                by_doc[rec["document_id"]][rec["seed"]] = rec["score"]
    dropped_from_v1 = sorted(seen - set(v5))
    return dict(by_doc), dropped_from_v1


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES,
                        choices=["judge-v1", "judge-v2"],
                        help="judge-v1 — замороженный прогон на prep-v4 (по умолчанию); "
                             "judge-v2 — частичный пересчёт на prep-v5")
    args = parser.parse_args()
    SERIES = args.series

    if SERIES == "judge-v2":
        switch_to_v2()
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.require(gate.STAGE_ANALYSIS, "proc4")

    manifest, cfg = load_config()
    prompt_template = judge_prompt()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seeds = cfg["seeds"]
    docs = sorted(read_rows(PREP_MANIFEST), key=lambda r: r["document_id"])
    print(f"{SERIES}, запуск {stamp}\n  модель {cfg['model']} @ {cfg['digest'][:12]}, "
          f"ollama {cfg['ollama_version']}, num_ctx {cfg['num_ctx']}")
    print(f"  документов {len(docs)}, вызовов {len(docs) * len(seeds)}, "
          f"сиды {seeds}")

    done = done_calls()
    if done:
        print(f"  в журнале уже {len(done)} вызовов — они не повторяются")

    inherited, dropped_from_v1 = ({}, []) if SERIES == "judge-v1" else inherited_from_v1()
    # Sentinel: несколько унаследованных документов всё же прогоняются заново,
    # и их оценки сверяются с унаследованными.
    sentinel = sorted(inherited)[:SENTINEL_DOCS] if SERIES == "judge-v2" else []
    if SERIES == "judge-v2":
        print(f"  документов {len(docs)}: унаследовано {len(inherited)}, "
              f"вызывается судья для {len(docs) - len(inherited)}, "
              f"исключено из v1 {len(dropped_from_v1)}, sentinel {len(sentinel)}")

    raw_fh = OUT_RAW.open("a", encoding="utf-8")
    per_doc = defaultdict(dict)
    sentinel_results = []
    for key, rec in done.items():
        if rec["status"] != "ok":
            continue
        if key[0] in sentinel:
            # Ответ sentinel-документа в журнале — свидетельство сверки, а не
            # рабочее значение. Иначе повторный запуск подменял бы им
            # унаследованное и терял бы само доказательство наследования.
            sentinel_results.append({
                "document_id": key[0], "seed": key[1],
                "inherited": inherited[key[0]].get(key[1]),
                "recomputed": rec["score"], "source": "журнал прогона"})
            continue
        per_doc[key[0]][key[1]] = rec["score"]
    for doc_id, scores in inherited.items():
        for seed, score in scores.items():
            per_doc[doc_id].setdefault(seed, score)

    for n, row in enumerate(docs, 1):
        path = ROOT / row["prose_path"]
        if sha256_file(path) != row["prose_sha256"]:
            raise SystemExit(f"хеш профиля не сходится: {row['document_id']}")
        text = path.read_text(encoding="utf-8")
        prompt = prompt_template.replace("<<<TEXT>>>", text)
        if row["document_id"] in inherited and row["document_id"] not in sentinel:
            continue
        for seed in seeds:
            if (row["document_id"], seed) in done:
                continue
            response = call_judge(cfg, prompt, seed)
            score, status = parse(response)
            if status == "invalid_json":
                # §4: один повтор с тем же seed, затем окончательный invalid
                response = call_judge(cfg, prompt, seed)
                score, status = parse(response)
                if status == "invalid_json":
                    status = "invalid"
            rec = {"document_id": row["document_id"], "seed": seed,
                   "score": score, "status": status,
                   "response": response.get("response"),
                   "prompt_eval_count": response.get("prompt_eval_count"),
                   "eval_count": response.get("eval_count"),
                   "total_duration_ns": response.get("total_duration")}
            raw_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            raw_fh.flush()
            if status == "ok":
                if row["document_id"] in sentinel:
                    # Унаследованное значение остаётся рабочим, свежее идёт в сверку.
                    sentinel_results.append({
                        "document_id": row["document_id"], "seed": seed,
                        "inherited": inherited[row["document_id"]].get(seed),
                        "recomputed": score, "source": "вызов этого прогона"})
                    continue
                per_doc[row["document_id"]][seed] = score
        if n % 50 == 0 or n == len(docs):
            print(f"  {n}/{len(docs)}")
    raw_fh.close()

    rows_out, triples = [], []
    for row in docs:
        got = per_doc.get(row["document_id"], {})
        values = [got[s] for s in seeds if s in got]
        if len(values) >= 2:
            median = statistics.median(values)
            spread = max(values) - min(values)
            status = "ok" if len(values) == 3 else "partial_two_of_three"
        else:
            median, spread = None, None
            status = "missing_fewer_than_two_valid"
        if len(values) == 3:
            triples.append(values)
        rows_out.append({
            "provenance": ("" if SERIES == "judge-v1" else
                           "унаследовано из judge-v1"
                           if row["document_id"] in inherited else "пересчитано"),
            "document_id": row["document_id"], "origin_class": row["origin_class"],
            "generation_channel": row["generation_channel"],
            "score_seed1": got.get(seeds[0], ""), "score_seed2": got.get(seeds[1], ""),
            "score_seed3": got.get(seeds[2], ""),
            "n_valid": len(values),
            "median": "" if median is None else f"{median:.1f}",
            "range": "" if spread is None else spread,
            "status": status,
        })
    with OUT_SCORES.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]))
        w.writeheader(); w.writerows(rows_out)
    print(f"  оценки: {OUT_SCORES.name}, строк {len(rows_out)}")

    # §5: медиана не должна скрывать нестабильность
    spreads = [r["range"] for r in rows_out if r["range"] != ""]
    exact = sum(1 for t in triples if len(set(t)) == 1)
    icc, icc_n = icc21(triples)
    agreement = {
        "documents_with_three": len(triples),
        "exact_match_share": exact / len(triples) if triples else None,
        "mean_range": statistics.fmean(spreads) if spreads else None,
        "median_range": statistics.median(spreads) if spreads else None,
        "share_range_over_20": (sum(1 for s in spreads if s > WIDE_RANGE) / len(spreads)
                                if spreads else None),
        "icc_2_1": icc, "icc_documents": icc_n,
    }
    print(f"  согласие: точных совпадений {agreement['exact_match_share']:.3f}, "
          f"средний размах {agreement['mean_range']:.2f}, ICC(2,1) "
          f"{'—' if icc is None else f'{icc:.3f}'}")

    median_of = {r["document_id"]: float(r["median"]) for r in rows_out if r["median"]}
    pairs = read_rows(PAIRS)
    seed_boot = manifest["seed"]
    o1_rows = []
    for contrast in ("P3-P1", "P2-P1"):
        diffs, clusters, dropped = [], [], 0
        for pair in pairs:
            if pair["contrast"] != contrast:
                continue
            left = median_of.get(pair["doc_left"])
            right = median_of.get(pair["doc_right"])
            if left is None or right is None:
                dropped += 1
                continue
            diffs.append(left - right)
            clusters.append(pair["brief_id"])
        mean = statistics.fmean(diffs)
        lo, hi, p = bootstrap_ci(diffs, clusters, seed_boot)
        o1_rows.append({
            "estimand": "full", "contrast": contrast, "status": "exploratory",
            "n_pairs": len(diffs), "dropped_pairs": dropped,
            "n_clusters": len(set(clusters)),
            "mean_diff": f"{mean:.4f}", "ci_low": f"{lo:.4f}", "ci_high": f"{hi:.4f}",
            "p_bootstrap": f"{p:.4f}", "alpha": f"{ALPHA_EACH:.4f}",
            "note": "шкала выраженности стилевых признаков, не вероятность происхождения",
        })
        o1_rows.append({
            "estimand": "net", "contrast": contrast, "status": "not available",
            "n_pairs": "", "dropped_pairs": "", "n_clusters": "", "mean_diff": "",
            "ci_low": "", "ci_high": "", "p_bootstrap": "", "alpha": "",
            "note": "суждение о стиле не разлагается на структурную и нестилевую части",
        })
    with OUT_O1.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(o1_rows[0]))
        w.writeheader(); w.writerows(o1_rows)
    print(f"  контрасты O1: {OUT_O1.name}, строк {len(o1_rows)}")

    statuses = defaultdict(int)
    for rec in done_calls().values():
        statuses[rec["status"]] += 1
    out_manifest = {
        "created_at": stamp, "procedure": SERIES, "status": "exploratory",
        "judge": cfg,
        "prompt_sha256": hashlib.sha256(prompt_template.encode("utf-8")).hexdigest(),
        "inputs": {f"{PREP_MANIFEST.parent.name}/manifest.csv": sha256_file(PREP_MANIFEST),
                   "score-v1-pairs.csv": sha256_file(PAIRS),
                   "proc4-judge-spec.md": sha256_file(SPEC)},
        "code_sha256": sha256_file(Path(__file__)),
        "call_statuses": dict(statuses),
        "agreement": agreement,
        "documents_scored": sum(1 for r in rows_out if r["median"]),
        "documents_missing": sum(1 for r in rows_out if not r["median"]),
    }
    if SERIES == "judge-v2":
        mismatched = [r for r in sentinel_results if r["inherited"] != r["recomputed"]]
        out_manifest["provenance"] = {
            "inherited_from_judge_v1": len(inherited),
            "recomputed": len(rows_out) - len(inherited),
            "excluded_present_in_v1": len(dropped_from_v1),
            "excluded_ids": dropped_from_v1,
            "sentinel_documents": sentinel,
            "sentinel_calls": len(sentinel_results),
            "sentinel_mismatched": len(mismatched),
            "sentinel_detail": sentinel_results,
            "basis": "профиль prose байт-идентичен prep-v4; шлюз — reuse-gate-v5.md",
        }
        out_manifest["inputs"]["judge-v1-raw.jsonl"] = sha256_file(RAW_V1)
    OUT_MANIFEST.write_text(json.dumps(out_manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"  манифест: {OUT_MANIFEST.name}")

    wide = agreement["share_range_over_20"] or 0
    report = [
        f"# Процедура 4, {SERIES}: согласие трёх прогонов", "",
        f"Запуск {stamp}. Судья `{cfg['model']}`, digest `{cfg['digest'][:12]}`, "
        f"ollama {cfg['ollama_version']}, температура "
        f"{cfg['sampling']['temperature']}, три сида {seeds}.", "",
        "**Статус — exploratory.** Шкала измеряет выраженность стилевых признаков, "
        "а не вероятность машинного происхождения.", "",
        "## Показатели согласия", "",
        "| Показатель | Значение |", "|---|---|",
        f"| документов с тремя валидными оценками | {agreement['documents_with_three']} |",
        f"| доля точных совпадений трёх оценок | {agreement['exact_match_share']:.3f} |",
        f"| средний размах | {agreement['mean_range']:.2f} |",
        f"| медианный размах | {agreement['median_range']:.1f} |",
        f"| доля документов с размахом свыше {WIDE_RANGE} пунктов | {wide:.3f} |",
        f"| ICC(2,1) | {'не считался' if icc is None else f'{icc:.3f}'} "
        f"(по {icc_n} документам) |", "",
        "## Статусы вызовов", "",
        "| Статус | Вызовов |", "|---|---|",
    ]
    report += [f"| {k} | {v} |" for k, v in sorted(statuses.items())]
    report += ["", "## Контрасты O1", "",
               "| Estimand | Контраст | Пар | Кластеров | Средняя разница | 95% CI | p |",
               "|---|---|---|---|---|---|---|"]
    for r in o1_rows:
        if r["estimand"] == "net":
            report.append(f"| net | {r['contrast']} | — | — | not available | — | — |")
            continue
        report.append(f"| full | {r['contrast']} | {r['n_pairs']} | {r['n_clusters']} | "
                      f"{r['mean_diff']} | [{r['ci_low']}; {r['ci_high']}] | "
                      f"{r['p_bootstrap']} |")
    if agreement["mean_range"] and agreement["mean_range"] > WIDE_RANGE:
        report += ["", f"**Средний размах превысил {WIDE_RANGE} пунктов.** Результат "
                       "публикуется с пометкой о низкой воспроизводимости оценки судьи; "
                       "шум измерения сопоставим с ожидаемым эффектом."]
    report += ["", "Кластерный percentile bootstrap по заданию, "
                   f"{BOOTSTRAP} повторов, α = {ALPHA_EACH} на тест.", ""]
    if SERIES == "judge-v2":
        mismatched = [r for r in sentinel_results if r["inherited"] != r["recomputed"]]
        report += [
            f"**Судья вызван для {len(rows_out) - len(inherited)} документов из "
            f"{len(rows_out)}, у остальных оценки трёх сидов унаследованы из "
            f"judge-v1.** Основание — байт-идентичный профиль prose. Sentinel: "
            f"{len(sentinel)} унаследованных документа прогнаны заново, "
            f"{len(sentinel_results)} вызовов, разошлось {len(mismatched)}. "
            f"Исключённых из v1 документов {len(dropped_from_v1)}.", ""]
    OUT_AGREEMENT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"  согласие: {OUT_AGREEMENT.name}")

    if SERIES == "judge-v2":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            "proc4-v2", inputs=[PREP_MANIFEST, PAIRS, SPEC, RAW_V1],
            outputs=[OUT_SCORES, OUT_O1, OUT_MANIFEST, OUT_AGREEMENT, OUT_RAW])
        print("  дочерний манифест: manifests-v2/proc4-v2-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
