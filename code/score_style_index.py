#!/usr/bin/env python3
"""Индекс стиля score-v1: один замороженный прогон, пять зарегистрированных вариантов.

Процедура — `07-analysis/scoring-spec.md`, покрытие конструкции —
`construct-coverage.md`, идентифицируемость — `design-support-table.md`.
Конфигурация и хеши входов — `07-analysis/score-v1-manifest.json`, записан
preflight-скриптом.

    python 09-tools/preflight_score.py && python 09-tools/score_style_index.py
    python 09-tools/score_style_index.py --series score-v2   # пересчёт на prep-v5

Серия задаётся параметром, значения по умолчанию не меняются: без флага скрипт
воспроизводит score-v1. В серии v2 вход — `feature-matrix-v5.csv`, допуск берётся
у `preflight_v2_run.require`, результаты пишутся под именами `score-v2-*`.

**Статус — exploratory operationalized style index.** Не роботность, не вероятность
машинного происхождения, порогов вида «60% машинности» не применяется.

O2 и O3 считаются только как описательные наблюдаемые контрасты: жанр в них
неотделим от уровня регламентированности и от происхождения.
"""

import csv
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
MANIFEST = ROOT / "07-analysis" / "score-v1-manifest.json"
PAIRS = ROOT / "07-analysis" / "score-v1-pairs.csv"

OUT_SCORES = ROOT / "07-analysis" / "score-v1-scores.csv"
OUT_O1 = ROOT / "07-analysis" / "score-v1-o1-contrasts.csv"
OUT_DESC = ROOT / "07-analysis" / "score-v1-descriptive-o2-o3.csv"
OUT_MISSING = ROOT / "07-analysis" / "score-v1-missingness.csv"
OUT_REPORT = ROOT / "07-analysis" / "score-v1-report.md"

# score-v2, 2026-07-29: пересчёт на prep-v5. Значения по умолчанию не меняются.
SERIES = "score-v1"

BOOTSTRAP = 5000
ALPHA_FAMILY = 0.05
N_CONFIRMATORY = 2          # P3−P1 главный, P2−P1 дополнительный
ALPHA_EACH = ALPHA_FAMILY / N_CONFIRMATORY

# §4.1 спецификации: сокращённый индекс. Веса — из матрицы скилла, домен «Смешанный».
COMMON = {
    "Lexical Richness": (0.30, {"L01": "up", "L02": "up", "L03": "up",
                                "L04": "down", "L05": "down"}),
    "Information-theoretic": (0.10, {"S09": "up", "M05": "down"}),
    "Dependencies": (0.07, {"S03": "down", "S04": "down", "S05": "down"}),
    "Semantic": (0.04, {"M01": "up", "M02": "up"}),
    "Surface": (0.03, {"R01": "down", "S02": "down", "R04": "down", "C02": "down"}),
    "Named Entities": (0.02, {"C01": "down"}),
    "Readability": (0.01, {"S01": "up"}),
}
# §4.2: format-sensitive score, отдельный выход.
FORMAT = {"Structural Markers": (0.12, {"F01": "up", "R06": "up", "R07": "up", "P01": "up"})}

# §4.3: исключены как неоперационализированные либо отсутствующие. Веса НЕ
# перераспределяются — перераспределение выдало бы неизмеренное за учтённое.
EXCLUDED = {
    "Morphological": "механизм — сужение разброса, признаки меряют уровень",
    "POS": "распределение POS-тегов не считалось",
    "Emotion": "механизм — сужение разброса, признак меряет уровень",
    "Psycholinguistic": "механизм — сужение разброса, признак меряет уровень",
    "Knockoff": "требует прогона нейтрального рерайта, Y01 не рассчитан",
}
# §6: пропуск равен метке класса либо принадлежности к пилоту.
EXCLUDED_FEATURES = {"X01", "X02", "X05", "F08", "M03"}

LEVEL = re.compile(r"regulation_level=(\d)")


def level_of(row):
    m = LEVEL.search(row.get("notes") or "")
    return m.group(1) if m else ""


def read_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def tercile_score(percentile, direction):
    """§3: перцентиль → 0 / 0.5 / 1, с учётом направления признака."""
    value = 0.0 if percentile < 1 / 3 else (0.5 if percentile < 2 / 3 else 1.0)
    return value if direction == "up" else 1.0 - value


def document_scores(percentiles, groups):
    """Оценка по каждой категории и взвешенная сумма. Возвращает также, из
    скольких признаков посчитана категория — без этого число нечитаемо."""
    per_category, used = {}, {}
    total_weight = 0.0
    total = 0.0
    for category, (weight, features) in groups.items():
        values = [tercile_score(percentiles[f], d)
                  for f, d in features.items() if f in percentiles]
        used[category] = len(values)
        if not values:
            per_category[category] = None
            continue
        score = sum(values) / len(values)
        per_category[category] = score
        total += weight * score
        total_weight += weight
    normalized = total / total_weight * 100 if total_weight else None
    return normalized, per_category, used, total_weight


def bootstrap_ci(values, clusters, seed, reps=BOOTSTRAP):
    """Кластерный бутстрап по заданию: пары одного brief_id зависимы."""
    by_cluster = defaultdict(list)
    for value, cluster in zip(values, clusters):
        by_cluster[cluster].append(value)
    keys = list(by_cluster)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        picked = [rng.choice(keys) for _ in keys]
        pool = [v for k in picked for v in by_cluster[k]]
        means.append(statistics.fmean(pool))
    means.sort()
    lo = means[int(0.025 * reps)]
    hi = means[int(0.975 * reps) - 1]
    # Двусторонний эмпирический p: доля бутстрап-средних, лежащих по другую
    # сторону нуля от наблюдённого среднего, удвоенная. Ноль в числителе
    # заменяется на 1/reps — «p меньше разрешения бутстрапа», а не «p = 0».
    observed = statistics.fmean(values)
    beyond = sum(1 for m in means if m <= 0) if observed > 0 else \
             sum(1 for m in means if m >= 0)
    p = min(1.0, 2 * max(beyond, 1) / reps)
    return lo, hi, p


def switch_to_v2():
    """Вход и имена выходов серии v2. Файлы v1 не перезаписываются."""
    global MATRIX, OUT_SCORES, OUT_O1, OUT_DESC, OUT_MISSING, OUT_REPORT
    MATRIX = ROOT / "06-features" / "feature-matrix-v5.csv"
    analysis = ROOT / "07-analysis"
    OUT_SCORES = analysis / "score-v2-scores.csv"
    OUT_O1 = analysis / "score-v2-o1-contrasts.csv"
    OUT_DESC = analysis / "score-v2-descriptive-o2-o3.csv"
    OUT_MISSING = analysis / "score-v2-missingness.csv"
    OUT_REPORT = analysis / "score-v2-report.md"


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES, choices=["score-v1", "score-v2"],
                        help="score-v1 — замороженный прогон на prep-v4 (по умолчанию); "
                             "score-v2 — пересчёт на prep-v5")
    args = parser.parse_args()
    SERIES = args.series

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    seed = manifest["config"]["seed"]
    if SERIES == "score-v2":
        switch_to_v2()
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.require(gate.STAGE_ANALYSIS, "proc1")
    elif not manifest.get("preflight_passed"):
        print("preflight не пройден — расчёт запрещён")
        return 1
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{SERIES}, запуск {stamp}, seed {seed}")

    registry = {r["document_id"]: r for r in read_rows(DOCUMENTS, "utf-8-sig")}

    # перцентили по нужным признакам
    needed = set()
    for groups in (COMMON, FORMAT):
        for _, features in groups.values():
            needed |= set(features)
    percentiles = defaultdict(dict)
    missing_rows = []
    for r in read_rows(MATRIX):
        fid = r["feature_id"]
        if fid not in needed or fid in EXCLUDED_FEATURES:
            continue
        if r["genre_percentile"]:
            percentiles[r["document_id"]][fid] = float(r["genre_percentile"])
        else:
            missing_rows.append({"document_id": r["document_id"], "feature_id": fid,
                                 "reason": r["missing_reason"] or "перцентиль не посчитан"})

    # оценки на документ
    scores = {}
    rows_out = []
    for doc, row in registry.items():
        p = percentiles.get(doc, {})
        common, cat_c, used_c, w_c = document_scores(p, COMMON)
        fmt, cat_f, used_f, w_f = document_scores(p, FORMAT)
        both = None
        if common is not None and fmt is not None:
            both = (common * w_c + fmt * w_f) / (w_c + w_f)
        scores[doc] = {"common": common, "format": fmt, "both": both}
        rows_out.append({
            "document_id": doc, "origin_class": row["origin_class"], "genre": row["genre"],
            "prompt_condition": row["prompt_condition"], "regulation_level": level_of(row),
            "index_common": "" if common is None else f"{common:.4f}",
            "score_format": "" if fmt is None else f"{fmt:.4f}",
            "index_common_plus_format": "" if both is None else f"{both:.4f}",
            "n_features_common": sum(used_c.values()),
            "n_features_format": sum(used_f.values()),
            "excluded_categories": ";".join(EXCLUDED),
        })
    with OUT_SCORES.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]))
        w.writeheader(); w.writerows(rows_out)
    with OUT_MISSING.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["document_id", "feature_id", "reason"])
        w.writeheader(); w.writerows(missing_rows)
    print(f"  оценки: {OUT_SCORES.name}, строк {len(rows_out)}")
    print(f"  пропуски: {OUT_MISSING.name}, строк {len(missing_rows)}")

    # O1 — парные контрасты
    pairs = read_rows(PAIRS)
    o1_rows = []
    for variant, key in (("O1-full", "both"), ("O1-net", "common")):
        for contrast in ("P3-P1", "P2-P1"):
            diffs, clusters, dropped = [], [], 0
            for pair in pairs:
                if pair["contrast"] != contrast:
                    continue
                left = scores.get(pair["doc_left"], {}).get(key)
                right = scores.get(pair["doc_right"], {}).get(key)
                if left is None or right is None:
                    dropped += 1
                    continue
                diffs.append(left - right)
                clusters.append(pair["brief_id"])
            if not diffs:
                continue
            mean = statistics.fmean(diffs)
            lo, hi, p = bootstrap_ci(diffs, clusters, seed)
            status = ("confirmatory" if (variant == "O1-full" and contrast == "P3-P1")
                      else "зарегистрированный дополнительный" if variant == "O1-full"
                      else "sensitivity")
            o1_rows.append({
                "variant": variant, "contrast": contrast, "status": status,
                "n_pairs": len(diffs), "dropped_pairs": dropped,
                "mean_diff": f"{mean:.4f}", "ci_low": f"{lo:.4f}", "ci_high": f"{hi:.4f}",
                "p_bootstrap": f"{p:.4f}", "alpha": f"{ALPHA_EACH:.4f}",
                "significant_at_alpha": str(p < ALPHA_EACH and status != "sensitivity"),
            })
    with OUT_O1.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(o1_rows[0]))
        w.writeheader(); w.writerows(o1_rows)
    print(f"  контрасты O1: {OUT_O1.name}, строк {len(o1_rows)}")

    # O2 и O3 — только описательные наблюдаемые контрасты
    desc = []
    by_group = defaultdict(list)
    for doc, row in registry.items():
        value = scores[doc]["common"]
        if value is None:
            continue
        if row["origin_class"] == "H":
            by_group[("O2", f"{row['genre']} / уровень {level_of(row) or '—'}")].append(value)
        if row["origin_class"] == "A" and row["prompt_condition"] == "P1":
            by_group[("O3", "AI P1, составная группа: seo+analytics+commercial")].append(value)
    for (outcome, group), values in sorted(by_group.items()):
        desc.append({
            "outcome": outcome, "group": group, "n": len(values),
            "mean": f"{statistics.fmean(values):.4f}",
            "median": f"{statistics.median(values):.4f}",
            "sd": f"{statistics.stdev(values):.4f}" if len(values) > 1 else "",
            "status": "описательное наблюдение, жанр неотделим от фактора",
        })
    with OUT_DESC.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(desc[0]))
        w.writeheader(); w.writerows(desc)
    print(f"  описательные O2/O3: {OUT_DESC.name}, строк {len(desc)}")

    # отчёт
    out = [f"# Индекс стиля {SERIES}: результаты замороженного прогона", "",
           f"Запуск {stamp}, seed {seed}. Конфигурация и хеши входов — "
           f"`07-analysis/{MANIFEST.name}`, вход — `{MATRIX.name}`.", "",
           "**Статус индекса — exploratory operationalized style index.** Это не роботность "
           "и не вероятность машинного происхождения; пороги вида «60% машинности» "
           "не применяются.", "",
           "**Содержательная валидность ограничена:** из тринадцати категорий скилла "
           "полностью операционализированы две — Lexical Richness и Surface. Пять покрыты "
           "частично, четыре не операционализированы, одна вынесена отдельно, одна "
           "отсутствует. Разбор — `07-analysis/construct-coverage.md`. Ограничение "
           "действует независимо от того, насколько сильными окажутся различия.", "",
           "## Исключённые категории", "",
           "| Категория | Причина |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in EXCLUDED.items()]
    out += ["", "Веса исключённых категорий **не перераспределены** между оставшимися.", "",
            "## O1 — confirmatory", "",
            "| Вариант | Контраст | Статус | Пар | Средняя разница | 95% CI | p | α |",
            "|---|---|---|---|---|---|---|---|"]
    for r in o1_rows:
        out.append(f"| {r['variant']} | {r['contrast']} | {r['status']} | {r['n_pairs']} | "
                   f"{r['mean_diff']} | [{r['ci_low']}; {r['ci_high']}] | {r['p_bootstrap']} | {r['alpha']} |")
    out += ["", f"Поправка на множественность: Бонферрони внутри семейства O1, "
                f"α = {ALPHA_EACH} на тест. Кластерный бутстрап по заданию, {BOOTSTRAP} повторов.", "",
            "## O2 и O3 — описательные наблюдаемые контрасты", "",
            "Подтверждающими сравнениями не являются. Формулировки «эффект уровня "
            "регламентированности», «различие из-за происхождения», «гипотеза подтверждена» "
            "к ним неприменимы: жанр неотделим от фактора в обоих случаях.", "",
            "**O2** — различия между жанрово-уровневыми группами, где жанр и уровень "
            "неразделимы. **O3** — различие между `human science level 3` и составной "
            "группой `AI P1`, смешанное с жанром.", "",
            "| Исход | Группа | N | Среднее | Медиана | SD |", "|---|---|---|---|---|---|"]
    for r in desc:
        out.append(f"| {r['outcome']} | {r['group']} | {r['n']} | {r['mean']} | "
                   f"{r['median']} | {r['sd']} |")
    out += ["", "p-value для O2 и O3 не считались.", ""]
    OUT_REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}")

    if SERIES == "score-v2":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            "proc1-v2", inputs=[DOCUMENTS, MATRIX, PAIRS],
            outputs=[OUT_SCORES, OUT_O1, OUT_DESC, OUT_MISSING, OUT_REPORT])
        print("  дочерний манифест: manifests-v2/proc1-v2-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
