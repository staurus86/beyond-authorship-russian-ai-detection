#!/usr/bin/env python3
"""Разбор ошибок error-v1: отбор карточек и заполнение измеримых полей.

Спецификация отбора — `07-analysis/error-v1-spec.md`, зафиксирована до просмотра
документов. Форма карточки — `07-analysis/error-analysis-protocol.md`.

    python 09-tools/error_run.py

**Статус — exploratory.** Разбор идёт по оценкам процедуры 2, имеющей тот же
статус.

Скрипт не выносит вердиктов. Он отбирает случаи по заранее заданному правилу и
заполняет поля, выводимые из реестра и модели: сработавшие признаки, жанр,
режим, длину, источник. Поля «альтернативное объяснение», «реальная редакторская
проблема» и «вывод» остаются человеку и заполняются по тексту документа.

Модель переобучается теми же функциями `clf_run`, что и в замороженном прогоне;
полученные оценки сверяются с `fairness-v1-predictions.csv`, которые сами сверены
с `clf-v1-p2a-metrics.csv`. Расхождение выше TOL останавливает расчёт.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402

ROOT = clf.ROOT

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PREDICTIONS = ROOT / "07-analysis" / "fairness-v1-predictions.csv"
MANUAL = ROOT / "07-analysis" / "error-v1-manual.csv"
SCORE_V1 = ROOT / "07-analysis" / "score-v1-scores.csv"
NLL_V1 = ROOT / "07-analysis" / "nll-v1-scores.csv"
JUDGE_V1 = ROOT / "07-analysis" / "judge-v1-scores.csv"
OUT_CSV = ROOT / "07-analysis" / "error-analysis.csv"
OUT_CASES = ROOT / "07-analysis" / "error-v1-cases.md"
OUT_MANIFEST = ROOT / "07-analysis" / "error-v1-manifest.json"

# error-v2, 2026-07-29: разбор ошибок прогона на prep-v5. Значения по умолчанию
# не меняются; все четыре источника оценок берутся из одной серии.
SERIES = "error-v1"

MODEL, ESTIMAND = "main", "full"
TOL = 1e-9
N_FP, N_FN, N_DISAGREE = 50, 50, 30
QUOTA_SOURCE, QUOTA_CELL = 8, 15      # §3: квоты отбора
TOP_FEATURES = 5

FIELDS = ["case_id", "document_id", "error_class", "model_or_procedure", "predicted",
          "actual", "triggered_features", "alternative_explanation", "genre_norm",
          "prompt_effect", "length_effect", "source_artifact",
          "real_editorial_problem", "conclusion", "reviewed_by", "reviewed_at"]


def load_predictions():
    """Все записи первичной модели: пара документ × holdout."""
    return [r for r in csv.DictReader(PREDICTIONS.open(encoding="utf-8"))
            if r["model"] == MODEL and r["estimand"] == ESTIMAND]


def dedup(rows):
    """Дедупликация §3: одна запись на документ, holdout первый по алфавиту.

    Правка спецификации 2026-07-29, до просмотра карточек: первая редакция
    дедуплицировала до фильтра ошибок и теряла случаи, где документ ошибочен в
    одном holdout и верен в другом с более ранним именем. Так пропадали 44 из 44
    ложноотрицательных holdout_prompt_P3. Выбор среди holdout остаётся
    алфавитным, то есть не зависит от того, где ошибка выглядит убедительнее.
    """
    best = {}
    for r in rows:
        prev = best.get(r["document_id"])
        if prev is None or r["split"] < prev["split"]:
            best[r["document_id"]] = r
    return best


def round_robin(buckets, quota, limit):
    """Обход групп по кругу: по одному документу за проход, пока не наберём limit.

    Порядок групп — по убыванию числа кандидатов; внутри группы порядок задан
    вызывающим. Квота ограничивает вклад одной группы.
    """
    order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
    taken, out = defaultdict(int), []
    while len(out) < limit:
        moved = False
        for key in order:
            if len(out) >= limit or taken[key] >= quota:
                continue
            pool = buckets[key]
            if taken[key] < len(pool):
                out.append(pool[taken[key]])
                taken[key] += 1
                moved = True
        if not moved:
            break
    return out


def select_fp(preds, docs_by_id):
    errors = dedup([r for r in preds if r["false_positive"] == "1"])
    buckets = defaultdict(list)
    for doc_id, r in errors.items():
        buckets[docs_by_id[doc_id]["source_platform"]].append(r)
    for key in buckets:
        buckets[key].sort(key=lambda r: -float(r["score"]))
    return round_robin(buckets, QUOTA_SOURCE, N_FP), len(errors)


def select_fn(preds, docs_by_id):
    errors = dedup([r for r in preds
                    if r["origin_class"] == "A" and r["predicted_machine"] == "0"])
    buckets = defaultdict(list)
    for doc_id, r in errors.items():
        row = docs_by_id[doc_id]
        buckets[(row["generation_channel"], row["prompt_condition"])].append(r)
    for key in buckets:
        buckets[key].sort(key=lambda r: float(r["score"]))
    return round_robin(buckets, QUOTA_CELL, N_FN), len(errors)


def percentiles(values):
    """Перцентиль внутри корпуса: доля документов со значением не выше данного."""
    order = sorted(values.items(), key=lambda kv: kv[1])
    n = len(order)
    return {doc: (i + 1) / n for i, (doc, _) in enumerate(order)}


def select_disagreement(preds):
    """§3: размах перцентилей четырёх процедур после приведения знака."""
    proc1 = {r["document_id"]: float(r["index_common_plus_format"])
             for r in csv.DictReader(SCORE_V1.open(encoding="utf-8"))
             if r["index_common_plus_format"]}
    # у NLL знак обращается: ниже NLL — текст предсказуемее, то есть более AI-подобен
    proc3 = {r["document_id"]: -float(r["nll"])
             for r in csv.DictReader(NLL_V1.open(encoding="utf-8")) if r["nll"]}
    proc4 = {r["document_id"]: float(r["median"])
             for r in csv.DictReader(JUDGE_V1.open(encoding="utf-8"))
             if r["status"] == "ok" and r["median"]}
    proc2 = {doc: float(r["score"]) for doc, r in dedup(preds).items()}
    ranks = [percentiles(p) for p in (proc1, proc2, proc3, proc4)]
    common = set.intersection(*(set(r) for r in ranks))
    spread = {}
    for doc in common:
        vals = [r[doc] for r in ranks]
        spread[doc] = (max(vals) - min(vals), vals)
    top = sorted(spread.items(), key=lambda kv: -kv[1][0])[:N_DISAGREE]
    return [(doc, rng, vals) for doc, (rng, vals) in top]


def fit_for_split(split, docs_by_id, values, lengths):
    """Обучение той же моделью на том же train: нужны коэффициенты и масштаб.

    Используются функции `clf_run`, порядок вызовов повторяет `run_split`.
    """
    train_ids = [d for d in split["train"] if d in docs_by_id]
    test_ids = [d for d in split["test"] if d in docs_by_id]
    x_train, feats, levels = clf.design_for(MODEL, train_ids, values, lengths,
                                            docs_by_id, ESTIMAND)
    x_test, _, _ = clf.design_for(MODEL, test_ids, values, lengths, docs_by_id,
                                  ESTIMAND, levels=levels)
    x_train, x_test = clf.impute_with_train(x_train, x_test)
    y_train = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0
                        for d in train_ids])
    groups = [docs_by_id[d]["split_group_source"] or docs_by_id[d]["generation_channel"]
              for d in train_ids]
    # Разбиение подбора C берётся то же, что в разбираемом прогоне. Своё здесь
    # означало бы, что карточки объясняют ошибки другой модели: в серии v2
    # inner-fold-ы приходят из зарегистрированной схемы, а не строятся на месте.
    assignments = (None if clf.SERIES == "clf-v1"
                   else clf.load_p2a_assignments(split["split_name"]))
    c, _, _ = clf.pick_c(x_train, y_train, groups, assignments,
                         clf.base_series() == "clf-v2-valid")
    model = clf.make_model(c).fit(x_train, y_train)
    scaler = model.named_steps["scale"]
    coef = model.named_steps["lr"].coef_[0]
    scores = model.predict_proba(x_test)[:, 1]
    scaled = scaler.transform(x_test)
    return {"feats": feats, "coef": coef, "ids": test_ids, "scaled": scaled,
            "scores": scores}


def top_contributions(fit, doc_id):
    i = fit["ids"].index(doc_id)
    pairs = [(fit["feats"][j], fit["coef"][j] * fit["scaled"][i][j])
             for j in range(len(fit["feats"]))]
    pairs.sort(key=lambda kv: -abs(kv[1]))
    return "; ".join(f"{name} {value:+.2f}" for name, value in pairs[:TOP_FEATURES])


def length_note(row, low, high):
    words = int(row["word_count"] or 0)
    band = "короткие" if words <= low else "средние" if words <= high else "длинные"
    return f"{words} слов, терциль: {band}"


def genre_note(row):
    level = clf.LEVEL.search(row.get("notes") or "")
    return (f"{row['genre']}, уровень {level.group(1)}" if level
            else f"{row['genre']}, уровень не задан")


def switch_to_v2(revision=""):
    """Серия v2: предсказания, оценки процедур и матрица — из серии v2.

    Ревизия матрицы приходит суффиксом имени серии. Процедуры 1, 3 и 4 от неё не
    зависят и читаются те же: пересчитывается только процедура 2 и то, что от неё
    питается (амендмент feature-matrix-v5-r2).
    """
    global PREDICTIONS, SCORE_V1, NLL_V1, JUDGE_V1, OUT_CSV, OUT_CASES, OUT_MANIFEST
    upstream = f"clf-v2-valid-{revision}" if revision else "clf-v2-valid"
    clf.SERIES = upstream
    clf.switch_to_v2(upstream)
    analysis = ROOT / "07-analysis"
    stem = f"error-v2{revision}"
    PREDICTIONS = analysis / f"fairness-v2{revision}-predictions.csv"
    SCORE_V1 = analysis / "score-v2-scores.csv"
    NLL_V1 = analysis / "nll-v2-scores.csv"
    JUDGE_V1 = analysis / "judge-v2-scores.csv"
    OUT_CSV = analysis / f"{stem}-analysis.csv"
    OUT_CASES = analysis / f"{stem}-cases.md"
    OUT_MANIFEST = analysis / f"{stem}-manifest.json"


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES,
                        choices=["error-v1", "error-v2", "error-v2-r2b",
                                 "error-v2-r3"],
                        help="error-v1 — разбор прогона clf-v1 (по умолчанию); "
                             "error-v2 — разбор прогона clf-v2 на prep-v5; "
                             "error-v2-r2b — разбор прогона на матрице v5-r2")
    args = parser.parse_args()
    SERIES = args.series
    if SERIES != "error-v1":
        revision = SERIES[len("error-v2"):].lstrip("-")
        clf.reject_rolled_back(revision, SERIES)
        switch_to_v2(revision)
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.require(gate.STAGE_ANALYSIS, "error", revision=revision)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{SERIES}, запуск {stamp}")

    registry_rows = clf.read_rows(clf.DOCUMENTS, "utf-8-sig")
    docs_by_id = {r["document_id"]: r for r in registry_rows}
    values = clf.load_matrix(set(clf.FEATURES_CORE + clf.FEATURES_STRUCTURAL
                                 + [clf.FEATURE_M02]))
    lengths = clf.load_length_features()
    words = sorted(int(r["word_count"] or 0) for r in registry_rows
                   if r["origin_class"] == "H" and r["word_count"])
    low, high = words[len(words) // 3], words[2 * len(words) // 3]

    preds = load_predictions()
    fp_rows, fp_total = select_fp(preds, docs_by_id)
    fn_rows, fn_total = select_fn(preds, docs_by_id)
    disagree = select_disagreement(preds)
    print(f"  кандидатов: FP {fp_total}, FN {fn_total}")
    print(f"  отобрано: FP {len(fp_rows)}, FN {len(fn_rows)}, "
          f"расхождений {len(disagree)}")

    needed = {r["split"] for r in fp_rows + fn_rows}
    fits, checked = {}, 0
    for name in sorted(needed):
        matches = sorted(clf.SPLITS.glob(f"{name}_*.json"))
        if len(matches) != 1:
            raise SystemExit(f"манифест разбиения {name}: найдено {len(matches)} файлов")
        split = json.loads(matches[0].read_text(encoding="utf-8"))
        fit = fits[name] = fit_for_split(split, docs_by_id, values, lengths)
        index = {d: i for i, d in enumerate(fit["ids"])}
        for r in fp_rows + fn_rows:
            if r["split"] != name:
                continue
            got = float(fit["scores"][index[r["document_id"]]])
            if abs(got - float(r["score"])) > 1e-6:
                raise SystemExit(f"сверка: {r['document_id']} в {name}: "
                                 f"пересчитано {got}, записано {r['score']}")
            checked += 1
        print(f"  {name}: модель обучена, оценки сошлись")

    cards = []
    for kind, rows in (("FP", fp_rows), ("FN", fn_rows)):
        for n, r in enumerate(rows, 1):
            row = docs_by_id[r["document_id"]]
            cards.append({
                "case_id": f"{kind}-{n:03d}",
                "document_id": r["document_id"],
                "error_class": "FP" if kind == "FP" else "FN",
                "model_or_procedure": f"proc2 clf-v1 {MODEL}/{ESTIMAND}, {r['split']}",
                "predicted": "машина" if kind == "FP" else "человек",
                "actual": "человек" if kind == "FP" else "машина",
                "triggered_features": top_contributions(fits[r["split"]],
                                                        r["document_id"]),
                "alternative_explanation": "",
                "genre_norm": genre_note(row),
                "prompt_effect": (row["prompt_condition"] or "н/п"
                                  if row["origin_class"] == "A" else "н/п"),
                "length_effect": length_note(row, low, high),
                "source_artifact": (row["source_platform"]
                                    or row["generation_channel"]),
                "real_editorial_problem": "",
                "conclusion": "pending",
                "reviewed_by": "", "reviewed_at": "",
            })
    for n, (doc_id, spread, vals) in enumerate(disagree, 1):
        row = docs_by_id[doc_id]
        cards.append({
            "case_id": f"DIS-{n:03d}",
            "document_id": doc_id,
            "error_class": "disagreement",
            "model_or_procedure": "четыре процедуры, перцентили корпуса",
            "predicted": "; ".join(f"proc{i + 1} {v:.2f}" for i, v in enumerate(vals)),
            "actual": row["origin_class"],
            "triggered_features": f"размах перцентилей {spread:.2f}",
            "alternative_explanation": "",
            "genre_norm": genre_note(row),
            "prompt_effect": (row["prompt_condition"] or "н/п"
                              if row["origin_class"] == "A" else "н/п"),
            "length_effect": length_note(row, low, high),
            "source_artifact": row["source_platform"] or row["generation_channel"],
            "real_editorial_problem": "",
            "conclusion": "pending",
            "reviewed_by": "", "reviewed_at": "",
        })

    # Ручные вердикты живут в отдельном файле, иначе прогон затирал бы работу человека.
    manual = {}
    if MANUAL.exists():
        manual = {r["document_id"]: r
                  for r in csv.DictReader(MANUAL.open(encoding="utf-8"))}
    for card in cards:
        verdict = manual.get(card["document_id"])
        if verdict:
            for field in ("alternative_explanation", "real_editorial_problem",
                          "conclusion", "reviewed_by", "reviewed_at"):
                card[field] = verdict[field]
    print(f"  ручных вердиктов подхвачено: "
          f"{sum(1 for c in cards if c['conclusion'] != 'pending')}")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(cards)

    mode_rates, source_rates = error_rates(preds, docs_by_id)
    write_cases(cards, docs_by_id, stamp, checked, mode_rates, source_rates,
                fp_total, fn_total)

    OUT_MANIFEST.write_text(json.dumps({
        "created_at": stamp, "procedure": SERIES, "status": "exploratory",
        "spec": "07-analysis/error-v1-spec.md",
        "model": f"{MODEL}/{ESTIMAND}", "cards": len(cards),
        "candidates": {"false_positive": fp_total, "false_negative": fn_total},
        "scores_rechecked": checked, "tolerance": TOL,
        "not_executable": {
            "гибридные классы": "в корпусе отсутствуют",
            "нестабильные из стресс-тестов": "стресс-тесты не запускались",
            "несогласие разметчиков": "разметки людьми нет, заменено расхождением процедур",
        },
        "inputs": {name: clf.sha256_file(path) for name, path in [
            (PREDICTIONS.name, PREDICTIONS),
            ("documents-registry.csv", clf.DOCUMENTS),
            (clf.MATRIX.name, clf.MATRIX),
            ("error_run.py", Path(__file__)),
        ]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  карточек {len(cards)}, сверено оценок {checked}")
    print(f"  выход: {OUT_CSV.name}, {OUT_CASES.name}")

    if SERIES != "error-v1":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            SERIES,
            inputs=[PREDICTIONS, clf.DOCUMENTS, clf.MATRIX, SCORE_V1, NLL_V1, JUDGE_V1],
            outputs=[OUT_CSV, OUT_CASES, OUT_MANIFEST])
        print(f"  дочерний манифест: manifests-v2/{SERIES}-manifest.json")


def error_rates(preds, docs_by_id):
    """Доля ошибок по режиму задания и по источнику — на всех кандидатах, не на карточках."""
    fn = dedup([r for r in preds
                if r["origin_class"] == "A" and r["predicted_machine"] == "0"])
    fp = dedup([r for r in preds if r["false_positive"] == "1"])
    total_mode, total_source = defaultdict(int), defaultdict(int)
    for row in docs_by_id.values():
        if row["origin_class"] == "A":
            total_mode[row["prompt_condition"]] += 1
        else:
            total_source[row["source_platform"]] += 1
    by_mode = defaultdict(int)
    for doc in fn:
        by_mode[docs_by_id[doc]["prompt_condition"]] += 1
    by_source = defaultdict(int)
    for doc in fp:
        by_source[docs_by_id[doc]["source_platform"]] += 1
    return ([(mode, by_mode[mode], total_mode[mode]) for mode in sorted(total_mode)],
            sorted(((s, by_source[s], total_source[s]) for s in total_source
                    if by_source[s]), key=lambda t: -t[1] / t[2]))


def write_cases(cards, docs_by_id, stamp, checked, mode_rates, source_rates,
                fp_total, fn_total):
    by_class = defaultdict(list)
    for card in cards:
        by_class[card["error_class"]].append(card)
    lines = [
        f"# Разбор ошибок {SERIES}: карточки",
        "",
        f"Собрано {stamp} скриптом `09-tools/error_run.py` по спецификации "
        "`07-analysis/error-v1-spec.md`, зафиксированной до просмотра документов.",
        "",
        "**Статус — exploratory.** Отбор и измеримые поля заполнены расчётом; "
        "альтернативное объяснение и вывод заполняет человек по тексту документа. "
        f"Оценок сверено с прогоном `{PREDICTIONS.name}`: {checked}.",
        "",
        "Вклад признака — `коэффициент × стандартизованное значение`; знак «плюс» "
        "толкает документ к классу A.",
        "",
        "## Из чего отбирались карточки",
        "",
        f"Кандидатов: ложноположительных {fp_total}, ложноотрицательных {fn_total}. "
        "Карточек меньше там, где кандидатов не хватило на объём протокола при "
        "заданных квотах.",
        "",
        "### Ложноотрицательные по режиму задания",
        "",
        "| Режим | Пропущено моделью | Машинных документов | Доля |",
        "|---|---|---|---|",
    ]
    for mode, n, total in mode_rates:
        lines.append(f"| {mode} | {n} | {total} | {n / total:.1%} |")
    lines += [
        "",
        "Происхождение во всех трёх режимах одинаково: тексты порождены одними "
        "моделями по одним заданиям. Различается только формулировка ТЗ.",
        "",
        "### Ложноположительные по источнику",
        "",
        "| Источник | Обвинено | Документов источника | Доля |",
        "|---|---|---|---|",
    ]
    for source, n, total in source_rates:
        lines.append(f"| {source} | {n} | {total} | {n / total:.1%} |")
    lines.append("")
    titles = {"FP": "Ложноположительные: человеческий текст принят за машинный",
              "FN": "Ложноотрицательные: машинный текст принят за человеческий",
              "disagreement": "Расхождение процедур: размах перцентилей"}
    for cls in ("FP", "FN", "disagreement"):
        rows = by_class.get(cls, [])
        lines += [f"## {titles[cls]} — {len(rows)} карточек", "",
                  "| ID | Документ | Источник | Жанр | Длина | Признаки с наибольшим вкладом |",
                  "|---|---|---|---|---|---|"]
        for card in rows:
            lines.append(
                f"| {card['case_id']} | `{card['document_id']}` | "
                f"{card['source_artifact']} | {card['genre_norm']} | "
                f"{card['length_effect']} | {card['triggered_features']} |")
        lines.append("")
    lines += [
        "## Блоки протокола, не выполненные здесь",
        "",
        "| Блок | Почему |",
        "|---|---|",
        "| 30 ошибок на гибридных классах | гибридных классов в корпусе нет |",
        "| 30 нестабильных результатов | стресс-тесты не запускались |",
        "| 30 случаев несогласия разметчиков | разметки людьми нет по дизайну; "
        "заменено расхождением процедур |",
        "",
        "Ручной разбор с дословными фрагментами — `error-v1-manual.md`.",
    ]
    OUT_CASES.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
