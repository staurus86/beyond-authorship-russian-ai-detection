#!/usr/bin/env python3
"""Калибровка процедуры 2: Brier, ECE и кривая risk-coverage.

    python 09-tools/calibration_run.py

Спецификация — `02-preregistration/amendment-calibration-spec.md`, утверждена PI
2026-08-01 до расчёта.

**Режим — read-only.** Модели не переобучаются, ни один прогон не повторяется.
`clf_run.py` и `fairness_run.py` не запускаются и **не импортируются** — условие
PI, потому что хеши обоих файлов расходятся с записанными в манифестах после
правки от 1 августа. Скрипт читает только CSV.

Статусы величин, §6a спецификации: Brier — зарегистрированная метрика; ECE, MCE и
risk-coverage — зарегистрированные названия с post hoc уточнённой
операционализацией, поэтому описательные; 5 и 20 бинов — sensitivity; худший
holdout — описательный экстремум; свёртка к документу — sensitivity ансамблевой
свёртки.
"""

import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PREDICTIONS = ROOT / "07-analysis" / "fairness-v2-predictions.csv"
FROZEN_METRICS = ROOT / "07-analysis" / "clf-v2-p2a-metrics.csv"
OUT_CSV = ROOT / "07-analysis" / "calibration-v1-by-holdout.csv"
OUT_REPORT = ROOT / "07-analysis" / "calibration-v1-report.md"
OUT_MANIFEST = ROOT / "07-analysis" / "calibration-v1-manifest.json"

MODEL, ESTIMAND = "main", "full"
EXPECTED_SHA = "c5f2d10e55bb8f63f0e6e70225b414b89cd959d6086fa8adafc76474239d378d"
EXPECTED_ROWS = 12112
EXPECTED_SPLITS = 18
BINS_MAIN = 10
BINS_SENSITIVITY = (5, 20)
COVERAGE_TARGETS = (1.00, 0.90, 0.80, 0.70, 0.50)
FPR_TOL = 1e-12


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_predictions():
    rows = []
    with PREDICTIONS.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["model"] == MODEL and r["estimand"] == ESTIMAND:
                rows.append({
                    "split": r["split"],
                    "document_id": r["document_id"],
                    "origin_class": r["origin_class"],
                    "p": float(r["score"]),
                    "decision": int(r["predicted_machine"]),
                    "y": 1 if r["origin_class"] == "A" else 0,
                })
    return rows


def load_frozen():
    out = {}
    with FROZEN_METRICS.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("model") == MODEL and r.get("estimand") == ESTIMAND:
                out[r["split"]] = {"n": int(r["n"]), "fpr": float(r["fpr"])}
    return out


def preflight(rows, frozen):
    """Семь проверок §5a. Возвращает отчёт; при провале останавливает расчёт."""
    checks = {}
    got_sha = sha256(PREDICTIONS)
    checks["input_sha256"] = {"expected": EXPECTED_SHA, "got": got_sha,
                              "passed": got_sha == EXPECTED_SHA}
    checks["rows"] = {"expected": EXPECTED_ROWS, "got": len(rows),
                      "passed": len(rows) == EXPECTED_ROWS}

    splits = sorted({r["split"] for r in rows})
    checks["splits"] = {"expected": EXPECTED_SPLITS, "got": len(splits),
                        "passed": len(splits) == EXPECTED_SPLITS}

    pairs = [(r["split"], r["document_id"]) for r in rows]
    checks["unique_pairs"] = {"duplicates": len(pairs) - len(set(pairs)),
                              "passed": len(pairs) == len(set(pairs))}

    bad_p = [r for r in rows if not 0.0 <= r["p"] <= 1.0]
    checks["probability_range"] = {"violations": len(bad_p), "passed": not bad_p}

    bad_dec = [r for r in rows if r["decision"] != int(r["p"] >= 0.5)]
    checks["decision_consistency"] = {"violations": len(bad_dec), "passed": not bad_dec}

    by_split = defaultdict(list)
    for r in rows:
        by_split[r["split"]].append(r)
    mism = []
    for s, items in by_split.items():
        exp = frozen.get(s)
        if exp is None:
            mism.append({"split": s, "reason": "нет в замороженных метриках"})
            continue
        humans = [x for x in items if x["origin_class"] == "H"]
        fpr = sum(1 for x in humans if x["decision"] == 1) / len(humans) if humans else None
        if len(items) != exp["n"] or fpr is None or abs(fpr - exp["fpr"]) > FPR_TOL:
            mism.append({"split": s, "n_got": len(items), "n_expected": exp["n"],
                         "fpr_got": fpr, "fpr_expected": exp["fpr"]})
    checks["frozen_metrics_match"] = {"mismatched_splits": mism, "passed": not mism}

    failed = [k for k, v in checks.items() if not v["passed"]]
    return checks, failed


def brier(items):
    return fmean((r["p"] - r["y"]) ** 2 for r in items)


def bin_assignment(items, n_bins):
    """Бины по §2a: группа равных вероятностей целиком идёт в один бин.

    Бин группы определяется серединой её рангового интервала:
    bin = min(B-1, floor(B * midpoint_rank / n)). Пустые бины допускаются.
    """
    ordered = sorted(items, key=lambda r: r["p"])
    n = len(ordered)
    groups = defaultdict(list)
    for r in ordered:
        groups[r["p"]].append(r)

    bins = defaultdict(list)
    rank = 0
    for p in sorted(groups):
        members = groups[p]
        midpoint = rank + len(members) / 2
        b = min(n_bins - 1, int(n_bins * midpoint / n))
        bins[b].extend(members)
        rank += len(members)
    return bins


def ece_mce(items, n_bins):
    """ECE = Σ (n_b/n)·|mean(y_b) − mean(p_b)|; MCE — максимум по непустым бинам."""
    bins = bin_assignment(items, n_bins)
    n = len(items)
    ece = 0.0
    mce = 0.0
    for b, members in bins.items():
        if not members:
            continue
        gap = abs(fmean(r["y"] for r in members) - fmean(r["p"] for r in members))
        ece += len(members) / n * gap
        mce = max(mce, gap)
    return ece, mce, len(bins)


def risk_coverage(items):
    """Кривая risk-coverage. Confidence = max(p, 1−p), решение при p ≥ 0.5.

    При совпадении confidence на границе включается вся группа ties, поэтому
    публикуется фактически достигнутое покрытие, а не целевое.
    """
    ranked = sorted(items, key=lambda r: -max(r["p"], 1 - r["p"]))
    n = len(ranked)
    out = []
    for target in COVERAGE_TARGETS:
        k = max(1, round(target * n))
        # добираем всю группу равной уверенности на границе
        if k < n:
            edge = max(ranked[k - 1]["p"], 1 - ranked[k - 1]["p"])
            while k < n and abs(max(ranked[k]["p"], 1 - ranked[k]["p"]) - edge) < 1e-15:
                k += 1
        kept = ranked[:k]
        errors = sum(1 for r in kept if r["decision"] != r["y"])
        out.append({"target_coverage": target,
                    "achieved_coverage": k / n,
                    "n_kept": k,
                    "risk": errors / k})
    return out


def document_level(rows):
    """Sensitivity ансамблевой свёртки: усреднение вероятностей по eligible-моделям."""
    by_doc = defaultdict(list)
    for r in rows:
        by_doc[r["document_id"]].append(r)
    items = []
    for doc, group in by_doc.items():
        items.append({"document_id": doc,
                      "p": fmean(r["p"] for r in group),
                      "y": group[0]["y"],
                      "decision": int(fmean(r["p"] for r in group) >= 0.5),
                      "origin_class": group[0]["origin_class"]})
    return items


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"калибровка процедуры 2, {stamp}")

    rows = load_predictions()
    frozen = load_frozen()
    print(f"  предсказаний {MODEL}/{ESTIMAND}: {len(rows)}, holdout в метриках: {len(frozen)}")

    checks, failed = preflight(rows, frozen)
    for name, res in checks.items():
        print(f"  preflight {name}: {'ok' if res['passed'] else 'ПРОВАЛ'}")
    if failed:
        OUT_MANIFEST.write_text(json.dumps(
            {"status": "blocked", "failed_checks": failed, "checks": checks,
             "created_at": stamp}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(f"ОСТАНОВ: preflight провален — {failed}. Расчёт не выполнялся, "
                         f"диагностика в {OUT_MANIFEST.name}")

    by_split = defaultdict(list)
    for r in rows:
        by_split[r["split"]].append(r)

    per_holdout = []
    for split in sorted(by_split):
        items = by_split[split]
        e10, m10, nb10 = ece_mce(items, BINS_MAIN)
        e5, _, nb5 = ece_mce(items, BINS_SENSITIVITY[0])
        e20, _, nb20 = ece_mce(items, BINS_SENSITIVITY[1])
        rc = risk_coverage(items)
        row = {
            "split_name": split,
            "n": len(items),
            "n_machine": sum(1 for r in items if r["y"] == 1),
            "brier": round(brier(items), 8),
            "ece_10": round(e10, 8),
            "mce_10": round(m10, 8),
            "bins_nonempty_10": nb10,
            "ece_5": round(e5, 8),
            "bins_nonempty_5": nb5,
            "ece_20": round(e20, 8),
            "bins_nonempty_20": nb20,
        }
        for point in rc:
            tag = f"{int(point['target_coverage'] * 100)}"
            row[f"risk_at_{tag}"] = round(point["risk"], 8)
            row[f"coverage_at_{tag}"] = round(point["achieved_coverage"], 6)
        per_holdout.append(row)

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(per_holdout[0]))
        writer.writeheader()
        writer.writerows(per_holdout)

    def macro(field):
        return fmean(r[field] for r in per_holdout)

    def worst(field):
        row = max(per_holdout, key=lambda r: r[field])
        return {"split_name": row["split_name"], "value": row[field]}

    doc_items = document_level(rows)
    doc_brier = brier(doc_items)
    doc_ece, doc_mce, doc_bins = ece_mce(doc_items, BINS_MAIN)

    # Pooled-кривая по всем строкам сразу. Основной остаётся macro §3: pooled
    # взвешивает документы числом holdout, в котором они оказались, и приводится
    # только как описательный контраст.
    pooled = {f"at_{int(p['target_coverage'] * 100)}": p for p in risk_coverage(rows)}

    summary = {
        "brier_macro": macro("brier"),
        "ece_10_macro": macro("ece_10"),
        "mce_10_macro": macro("mce_10"),
        "ece_5_macro": macro("ece_5"),
        "ece_20_macro": macro("ece_20"),
        "worst_brier": worst("brier"),
        "worst_ece_10": worst("ece_10"),
        "worst_mce_10": worst("mce_10"),
        "risk_macro": {f"at_{int(t*100)}": macro(f"risk_at_{int(t*100)}")
                       for t in COVERAGE_TARGETS},
        "coverage_macro": {f"at_{int(t*100)}": macro(f"coverage_at_{int(t*100)}")
                           for t in COVERAGE_TARGETS},
        "risk_pooled": {k: v["risk"] for k, v in pooled.items()},
        "coverage_pooled": {k: v["achieved_coverage"] for k, v in pooled.items()},
        "document_level_sensitivity": {
            "documents": len(doc_items),
            "brier": doc_brier,
            "ece_10": doc_ece,
            "mce_10": doc_mce,
            "bins_nonempty_10": doc_bins,
        },
    }

    write_report(per_holdout, summary, checks, stamp)

    manifest = {
        "series": "calibration-v1",
        "spec": "02-preregistration/amendment-calibration-spec.md",
        "mode": "read-only; clf_run.py и fairness_run.py не запускались и не импортировались",
        "source": PREDICTIONS.name,
        "source_sha256": sha256(PREDICTIONS),
        "frozen_metrics": FROZEN_METRICS.name,
        "model": MODEL, "estimand": ESTIMAND,
        "statuses": {
            "brier": "зарегистрированная метрика",
            "ece_mce": "название зарегистрировано, операционализация уточнена post hoc до расчёта — описательные",
            "risk_coverage": "то же — описательное",
            "bins_5_20": "sensitivity",
            "worst_holdout": "описательный экстремум по каждой метрике отдельно",
            "document_level": "sensitivity ансамблевой свёртки",
        },
        "bins_main": BINS_MAIN,
        "bins_sensitivity": list(BINS_SENSITIVITY),
        "coverage_targets": list(COVERAGE_TARGETS),
        "preflight": checks,
        "summary": summary,
        "code_sha256": {Path(__file__).name: sha256(Path(__file__))},
        "provenance_limitation": ("хеш файла предсказаний в манифест прогона не записан; "
                                  "побитовая неизменность с момента исходного прогона не "
                                  "доказана, доказано соответствие замороженным метрикам и "
                                  "совпадение решений с базовыми вероятностями стресс-P2 r11"),
        "precision_limitation": ("fairness_run.py:183 пишет round(score, 6); расхождение с "
                                 "неокруглёнными базовыми вероятностями стресс-P2 не выше "
                                 "4.999e-07, все 12112 решений совпадают"),
        "created_at": stamp,
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    print(f"  Brier macro: {summary['brier_macro']:.6f}")
    print(f"  ECE(10) macro: {summary['ece_10_macro']:.6f}, "
          f"худший — {summary['worst_ece_10']['split_name']} "
          f"{summary['worst_ece_10']['value']:.6f}")
    print(f"  записано: {OUT_CSV.name}, {OUT_REPORT.name}, {OUT_MANIFEST.name}")


def write_report(per_holdout, summary, checks, stamp):
    lines = [
        "# Калибровка процедуры 2",
        "",
        f"Собрано {stamp} скриптом `09-tools/calibration_run.py`. Спецификация — "
        "`02-preregistration/amendment-calibration-spec.md`, утверждена до расчёта.",
        "",
        "**Режим — read-only.** Модели не переобучались, `clf_run.py` и "
        "`fairness_run.py` не запускались и не импортировались.",
        "",
        "## Статусы величин",
        "",
        "| Величина | Статус |",
        "|---|---|",
        "| Brier score | зарегистрированная метрика |",
        "| ECE, MCE | название зарегистрировано, операционализация уточнена post hoc до расчёта — **описательные** |",
        "| Risk–coverage | то же — **описательное** |",
        "| 5 и 20 бинов | **sensitivity** |",
        "| Худший holdout | **описательный экстремум** отдельно по каждой метрике |",
        "| Свёртка к документу | **sensitivity ансамблевой свёртки** |",
        "",
        "## Сводка, macro по 18 holdout с равным весом",
        "",
        "| Величина | Значение |",
        "|---|---|",
        f"| Brier | {summary['brier_macro']:.6f} |",
        f"| ECE, 10 бинов | {summary['ece_10_macro']:.6f} |",
        f"| MCE, 10 бинов | {summary['mce_10_macro']:.6f} |",
        f"| ECE, 5 бинов (sensitivity) | {summary['ece_5_macro']:.6f} |",
        f"| ECE, 20 бинов (sensitivity) | {summary['ece_20_macro']:.6f} |",
        "",
        "## Описательные экстремумы",
        "",
        "| Метрика | Худший holdout | Значение |",
        "|---|---|---|",
        f"| Brier | {summary['worst_brier']['split_name']} | {summary['worst_brier']['value']:.6f} |",
        f"| ECE(10) | {summary['worst_ece_10']['split_name']} | {summary['worst_ece_10']['value']:.6f} |",
        f"| MCE(10) | {summary['worst_mce_10']['split_name']} | {summary['worst_mce_10']['value']:.6f} |",
        "",
        "## Risk–coverage, macro",
        "",
        "Confidence = `max(p, 1−p)`, решение при `p ≥ 0.5`. При совпадении "
        "confidence на границе включается вся группа ties, поэтому покрытие "
        "приводится фактически достигнутое. Оптимальный порог не выбирается.",
        "",
        "| Целевое покрытие | Достигнутое | Risk |",
        "|---|---|---|",
    ]
    for t in COVERAGE_TARGETS:
        tag = f"at_{int(t * 100)}"
        lines.append(f"| {t:.0%} | {summary['coverage_macro'][tag]:.4f} | "
                     f"{summary['risk_macro'][tag]:.6f} |")

    lines += [
        "",
        "### Pooled-кривая, описательный контраст",
        "",
        "Та же кривая по всем строкам сразу, без деления на holdout. Зарегистрированный "
        "вариант — macro выше; pooled взвешивает документ числом holdout, в которые он "
        "попал, и приводится только для сравнения.",
        "",
        "| Целевое покрытие | Достигнутое | Risk pooled | Risk macro |",
        "|---|---|---|---|",
    ]
    for t in COVERAGE_TARGETS:
        tag = f"at_{int(t * 100)}"
        lines.append(f"| {t:.0%} | {summary['coverage_pooled'][tag]:.4f} | "
                     f"{summary['risk_pooled'][tag]:.6f} | "
                     f"{summary['risk_macro'][tag]:.6f} |")

    doc = summary["document_level_sensitivity"]
    lines += [
        "",
        "## Sensitivity ансамблевой свёртки",
        "",
        "Вероятности документа усреднены по всем eligible-моделям, затем посчитаны "
        "Brier и ECE. Основной единицей анализа это не является.",
        "",
        f"- документов: {doc['documents']}",
        f"- Brier: {doc['brier']:.6f}",
        f"- ECE(10): {doc['ece_10']:.6f}, MCE(10): {doc['mce_10']:.6f}, "
        f"непустых бинов: {doc['bins_nonempty_10']}",
        "",
        "## По holdout",
        "",
        "| Holdout | n | Brier | ECE(10) | MCE(10) | Непустых бинов | ECE(5) | ECE(20) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in per_holdout:
        lines.append(f"| {r['split_name']} | {r['n']} | {r['brier']:.6f} | "
                     f"{r['ece_10']:.6f} | {r['mce_10']:.6f} | {r['bins_nonempty_10']} | "
                     f"{r['ece_5']:.6f} | {r['ece_20']:.6f} |")

    lines += [
        "",
        "## Preflight",
        "",
        "| Проверка | Итог |",
        "|---|---|",
    ]
    for name, res in checks.items():
        lines.append(f"| {name} | {'пройдена' if res['passed'] else 'ПРОВАЛЕНА'} |")

    lines += [
        "",
        "## Ограничения",
        "",
        "**Провенанс.** Хеш файла предсказаний в манифест исходного прогона не "
        "записан: побитовая неизменность с момента прогона не доказана. Доказано "
        "соответствие замороженным метрикам классификатора — 18 holdout из 18 по "
        "числу строк и FPR — и совпадение всех 12 112 решений с базовыми "
        "вероятностями стресс-P2 ревизии r11.",
        "",
        "**Точность источника.** `fairness_run.py:183` пишет `round(score, 6)`. "
        "Расхождение с неокруглёнными базовыми вероятностями не превышает 4.999e-07, "
        "решения совпадают полностью. Округление создаёт дополнительные совпадения "
        "вероятностей и тем меняет состав групп ties.",
        "",
        "**Вырожденное разбиение.** Оценки насыщены у краёв шкалы, поэтому группа "
        "равных вероятностей может превышать размер бина втрое. Такая группа целиком "
        "занимает свой бин, соседние остаются пустыми и в ECE не входят: колонка "
        "«непустых бинов» показывает фактическое число.",
        "",
        "**Балл не является вероятностью происхождения.** "
        "`statistical-analysis-plan.md` строка 105 запрещает такой перевод без "
        "репрезентативного калибровочного корпуса, целевой популяции, проверки "
        "дрейфа и опубликованного профиля ошибок. Расчёт описывает поведение оценок "
        "внутри корпуса.",
        "",
    ]
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
