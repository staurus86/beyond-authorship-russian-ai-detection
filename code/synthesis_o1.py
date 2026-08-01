#!/usr/bin/env python3
"""Синтез контрастов O1 по четырём процедурам: таблица, а не единое число.

Правило синтеза — `07-analysis/procedures-2-4-registration.md` §4, зафиксировано
2026-07-27 до запуска процедур 2–4. Конвенция направления шкал — из манифеста
preflight, где у NLL стоит `invert`.

    python 09-tools/synthesis_o1.py

**Код написан 2026-07-28, когда процедуры 1 и 2 уже отдали результат, а 3 и 4
считались.** Форма таблицы и правила пометок выбраны без доступа к половине
строк — именно поэтому он пишется сейчас, а не после завершения пакета.

**Правка от того же дня, после результата процедуры 3.** Пометка §4.3
«согласовано по направлению, но статистически неразличимо» ставилась на строку
по одному признаку — интервал накрывает ноль. Так она утверждала согласованность
там, где знаки могли и разойтись. Теперь статус строки говорит только про
интервал, а согласованность оценивается при сравнении строк между собой.
Критерий не изменился, изменилась формулировка.

Чего скрипт не делает, по §4.5 и §4.2:
  - не считает голосование «две из трёх»;
  - не считает сводный метааналитический эффект;
  - не сводит full и net в одну оценку;
  - не конструирует net там, где процедура его не операционализирует.
"""

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PREFLIGHT_MANIFEST = ROOT / "07-analysis" / "procedures-2-4-manifest.json"
OUT_CSV = ROOT / "07-analysis" / "synthesis-o1.csv"
OUT_REPORT = ROOT / "07-analysis" / "synthesis-o1-report.md"
OUT_MANIFEST = ROOT / "07-analysis" / "synthesis-o1-manifest.json"

# synthesis-v2, 2026-07-29: сводка по прогонам на prep-v5. Значения по умолчанию
# не меняются; смешивать строки серий v1 и v2 в одной таблице запрещено.
SERIES = "synthesis-v1"

# Источники строк. Ключи полей различаются, потому что шкалы различаются.
SOURCES = [
    {"procedure": "1 — индекс стиля score-v1",
     "file": "score-v1-o1-contrasts.csv",
     "scale": "proc1_common", "unit": "пункты индекса 0–100",
     "input_type": "матрица признаков, веса заданы заранее",
     "estimand_field": "variant", "estimand_map": {"O1-full": "full", "O1-net": "net"},
     "mean": "mean_diff", "p": "p_bootstrap", "clusters": None, "pairs": "n_pairs",
     "population": "весь корпус, 45 заданий"},
    {"procedure": "2 — классификатор P2b",
     "file": "clf-v1-p2b-o1-contrasts.csv",
     "scale": "p2b_score", "unit": "вероятность класса A, 0–1",
     "input_type": "та же матрица признаков, веса обучены по данным",
     "estimand_field": "estimand", "estimand_map": None,
     "mean": "mean_diff_prob", "p": "p_wild_cluster", "clusters": "n_clusters",
     "pairs": "n_pairs", "population": "только жанр seo, 15 заданий"},
    {"procedure": "3 — zero-shot nll-v1",
     "file": "nll-v1-o1-contrasts.csv",
     "scale": "proc3_nll", "unit": "средний token NLL",
     "input_type": "сырой текст профиля prose",
     "estimand_field": "estimand", "estimand_map": None,
     "mean": "mean_diff_nll", "p": "p_bootstrap", "clusters": "n_clusters",
     "pairs": "n_pairs", "population": "весь корпус, 45 заданий"},
    {"procedure": "4 — модель-судья judge-v1",
     "file": "judge-v1-o1-contrasts.csv",
     "scale": "proc4_judge", "unit": "шкала рубрики 0–100",
     "input_type": "сырой текст профиля prose",
     "estimand_field": "estimand", "estimand_map": None,
     "mean": "mean_diff", "p": "p_bootstrap", "clusters": "n_clusters",
     "pairs": "n_pairs", "population": "весь корпус, 45 заданий"},
]

CONTRASTS = ["P3-P1", "P2-P1"]
ESTIMANDS = ["full", "net"]
ALPHA = 0.05 / 2   # Бонферрони внутри семейства O1, как в процедуре 1


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def direction_label(mean, lo, hi, factor, p_value=None):
    """Знак после приведения к общей конвенции и статус различимости.

    Статус описывает одну строку, а не её отношение к соседним: интервал либо
    накрывает ноль, либо нет. Пометка §4.3 «согласовано по направлению, но
    статистически неразличимо» ставится ниже, при сравнении строк между собой,
    и только когда знаки действительно совпали.
    """
    if mean is None:
        return "", "не посчитано"
    adjusted = mean * factor
    sign = "+" if adjusted > 0 else ("−" if adjusted < 0 else "0")
    if lo is None or hi is None:
        return sign, "интервал не посчитан"
    lo_adj, hi_adj = sorted((lo * factor, hi * factor))
    if lo_adj <= 0 <= hi_adj:
        return sign, "интервал накрывает ноль, статистически неразличимо"
    # Интервал строится на 95%, а порог значимости после поправки Бонферрони —
    # 0.025. Строка может не накрывать ноль и всё равно не проходить порог.
    if p_value is not None and p_value > ALPHA:
        return sign, (f"95% интервал ноль не накрывает, но p = {p_value:.4f} выше "
                      f"α = {ALPHA} после поправки Бонферрони")
    return sign, "интервал ноль не накрывает"


def compare_signs(rows, estimand, contrast):
    """Сравнение строк между собой: §4.3 и §4.4.

    Пометка «согласовано по направлению, но статистически неразличимо» относится
    к отношению строк, а не к отдельной строке, поэтому ставится здесь.
    """
    got = [r for r in rows if r["estimand"] == estimand and r["contrast"] == contrast
           and r["sign_after_convention"]]
    if not got:
        return ["Ни одна процедура этот estimand не операционализирует либо прогон "
                "не завершён."]
    signs = {r["sign_after_convention"] for r in got}
    listed = ", ".join(f"{r['procedure'].split(' — ')[0]}: {r['sign_after_convention']}"
                       for r in got)
    out = [f"Знаки после приведения к конвенции — {listed}."]
    weak = [r for r in got if r["status"].startswith("интервал накрывает ноль")]
    if len(signs) == 1:
        out.append(f"Направление совпало у всех {len(got)} посчитанных процедур.")
        if weak:
            names = ", ".join(r["procedure"].split(" — ")[0] for r in weak)
            out.append(f"Интервал накрывает ноль у процедуры {names} — этот вклад "
                       "читается как согласованный по направлению, но статистически "
                       "неразличимый, и с остальными строками не смешивается."
                       if len(weak) == 1 else
                       f"Интервал накрывает ноль у процедур {names} — их вклад "
                       "читается как согласованный по направлению, но статистически "
                       "неразличимый, и с остальными строками не смешивается.")
    else:
        out.append("**Знаки разошлись.** По §4.4 это самостоятельный результат, а не "
                   "повод искать ошибку в одной из процедур: входы различаются "
                   "матрицей против текста и заданными весами против обученных.")
        if weak:
            names = ", ".join(r["procedure"].split(" — ")[0] for r in weak)
            out.append(f"Интервал накрывает ноль у процедуры {names}: её знак от нуля "
                       "неотличим и в расхождение вносит слабый вклад."
                       if len(weak) == 1 else
                       f"Интервал накрывает ноль у процедур {names}: их знаки от нуля "
                       "неотличимы и в расхождение вносят слабый вклад.")
    if len(got) < 4:
        out.append(f"Посчитано {len(got)} процедур из четырёх.")
    return out


def switch_to_v2(revision=""):
    """Серия v2: все четыре источника — из прогонов на prep-v5.

    Ревизия матрицы меняет только вклад процедуры 2: индекс, NLL и судья её не
    читают и остаются прежними файлами (амендмент feature-matrix-v5-r2).
    """
    global OUT_CSV, OUT_REPORT, OUT_MANIFEST
    analysis = ROOT / "07-analysis"
    stem = f"synthesis-o1-v2{revision}"
    OUT_CSV = analysis / f"{stem}.csv"
    OUT_REPORT = analysis / f"{stem}-report.md"
    OUT_MANIFEST = analysis / f"{stem}-manifest.json"
    replacement = {"score-v1-o1-contrasts.csv": "score-v2-o1-contrasts.csv",
                   "clf-v1-p2b-o1-contrasts.csv":
                       f"clf-v2{revision}-p2b-o1-contrasts.csv",
                   "nll-v1-o1-contrasts.csv": "nll-v2-o1-contrasts.csv",
                   "judge-v1-o1-contrasts.csv": "judge-v2-o1-contrasts.csv"}
    for source in SOURCES:
        source["file"] = replacement[source["file"]]
        source["procedure"] = source["procedure"].replace("-v1", "-v2")


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES,
                        choices=["synthesis-v1", "synthesis-v2",
                                 "synthesis-v2-r2b",
                                 "synthesis-v2-r3"],
                        help="synthesis-v1 — сводка прогонов серии v1 (по умолчанию); "
                             "synthesis-v2 — сводка прогонов на prep-v5; "
                             "synthesis-v2-r2b — с процедурой 2 на матрице v5-r2")
    args = parser.parse_args()
    SERIES = args.series
    if SERIES != "synthesis-v1":
        revision = SERIES[len("synthesis-v2"):].lstrip("-")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import clf_run  # noqa: PLC0415 — только ради общего списка ревизий
        clf_run.reject_rolled_back(revision, SERIES)
        switch_to_v2(revision)
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.require(gate.STAGE_ANALYSIS, "synthesis", revision=revision)

    manifest = json.loads(PREFLIGHT_MANIFEST.read_text(encoding="utf-8"))
    convention = manifest["scale_convention"]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"синтез O1 ({SERIES}), запуск {stamp}")

    rows_out, inputs, missing = [], {}, []
    for source in SOURCES:
        path = ROOT / "07-analysis" / source["file"]
        factor = -1.0 if convention[source["scale"]]["direction"] == "invert" else 1.0
        if not path.exists():
            missing.append(source["procedure"])
            for estimand in ESTIMANDS:
                for contrast in CONTRASTS:
                    rows_out.append({
                        "procedure": source["procedure"], "estimand": estimand,
                        "contrast": contrast, "input_type": source["input_type"],
                        "population": source["population"], "unit": source["unit"],
                        "n_pairs": "", "n_clusters": "", "mean_diff_raw": "",
                        "convention_factor": int(factor), "sign_after_convention": "",
                        "ci_low_raw": "", "ci_high_raw": "", "p_value": "",
                        "status": "прогон не завершён",
                    })
            continue
        inputs[source["file"]] = sha256_file(path)
        for row in read_rows(path):
            estimand = row[source["estimand_field"]]
            if source["estimand_map"]:
                estimand = source["estimand_map"].get(estimand, estimand)
            if estimand not in ESTIMANDS or row["contrast"] not in CONTRASTS:
                continue
            mean = number(row.get(source["mean"]))
            lo, hi = number(row.get("ci_low")), number(row.get("ci_high"))
            sign, verdict = direction_label(mean, lo, hi, factor,
                                            number(row.get(source["p"])))
            status = row.get("status", "")
            if status == "not available":
                verdict = "not available: estimand процедурой не операционализируется"
            rows_out.append({
                "procedure": source["procedure"], "estimand": estimand,
                "contrast": row["contrast"], "input_type": source["input_type"],
                "population": source["population"], "unit": source["unit"],
                "n_pairs": row.get(source["pairs"], ""),
                "n_clusters": row.get(source["clusters"] or "", ""),
                "mean_diff_raw": "" if mean is None else f"{mean:.4f}",
                "convention_factor": int(factor),
                "sign_after_convention": sign,
                "ci_low_raw": "" if lo is None else f"{lo:.4f}",
                "ci_high_raw": "" if hi is None else f"{hi:.4f}",
                "p_value": row.get(source["p"], ""),
                "status": verdict,
            })

    order = {p["procedure"]: i for i, p in enumerate(SOURCES)}
    rows_out.sort(key=lambda r: (ESTIMANDS.index(r["estimand"]),
                                 CONTRASTS.index(r["contrast"]),
                                 order[r["procedure"]]))
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]))
        w.writeheader(); w.writerows(rows_out)
    print(f"  таблица: {OUT_CSV.name}, строк {len(rows_out)}")
    if missing:
        print(f"  не посчитаны: {', '.join(missing)}")

    report = [
        "# Синтез контрастов O1: таблица по четырём процедурам", "",
        f"Собрано {stamp} скриптом `09-tools/synthesis_o1.py`. Правило синтеза "
        "зафиксировано в `procedures-2-4-registration.md` §4 до запуска процедур "
        "2–4; код написан 2026-07-28, когда процедуры 3 и 4 ещё считались.", "",
        "**Единого числа здесь нет и не будет.** Голосование «две из трёх» не "
        "применяется, сводный метааналитический эффект не рассчитывается: "
        "процедуры измеряют разное на общей выборке, и усреднение их оценок "
        "интерпретации не имеет.", "",
        "**Величины несопоставимы между строками.** Пункты индекса, вероятность "
        "класса, средний NLL и шкала рубрики — четыре разные шкалы. Сравнивается "
        "знак после приведения к конвенции «больше значит более AI-подобно» и то, "
        "накрывает ли интервал ноль.", "",
        "**Процедуры 1 и 2 — одно семейство свидетельств.** Обе агрегируют одну "
        "матрицу признаков на одних документах, различаясь лишь способом получения "
        "весов. Их совпадение считается одним источником, а не двумя.", "",
        "**Процедура 2 считает контраст на другой популяции:** только жанр `seo`, "
        "120 пар и 15 кластеров-заданий против 359–360 пар и 45 кластеров у "
        "остальных. Это не те же пары.", "",
    ]
    for estimand in ESTIMANDS:
        report += [f"## Estimand {estimand}", ""]
        for contrast in CONTRASTS:
            report += [f"### Контраст {contrast}", "",
                       "| Процедура | Вход | Популяция | Пар | Эффект | 95% CI | p | Знак | Статус |",
                       "|---|---|---|---|---|---|---|---|---|"]
            for r in rows_out:
                if r["estimand"] != estimand or r["contrast"] != contrast:
                    continue
                ci = (f"[{r['ci_low_raw']}; {r['ci_high_raw']}]"
                      if r["ci_low_raw"] else "—")
                report.append(
                    f"| {r['procedure']} | {r['input_type']} | {r['population']} | "
                    f"{r['n_pairs'] or '—'} | {r['mean_diff_raw'] or '—'} {r['unit']} | "
                    f"{ci} | {r['p_value'] or '—'} | {r['sign_after_convention'] or '—'} | "
                    f"{r['status']} |")
            report += [""] + compare_signs(rows_out, estimand, contrast) + [""]
    report += [
        "## Как читаются расхождения", "",
        "**Расхождение знаков между процедурами — самостоятельный результат**, а не "
        "повод искать ошибку в одной из них. Публикуется с указанием, чем "
        "различаются входы: матрица против текста, заданные веса против обученных.", "",
        "**Расхождение между estimands — другое явление.** Оно уже наблюдалось в "
        "процедуре 1 и объяснено декомпозицией: вклад структуры вдвое-втрое больше "
        "стилевого и определяет знак.", "",
        "**Совпадение процедур не является репликацией.** Все они применены к "
        "одному корпусу, поэтому термин «воспроизведение» не используется — только "
        "сходимость операционализаций. Настоящее воспроизведение потребует нового "
        "bridge set, требования к нему — в `design-support-table.md`.", "",
        "**Ни одна процедура не исключается из сводки** по причине несогласия с "
        "остальными; выбирать «лучше работающую» после просмотра запрещено §4.6.", "",
    ]
    OUT_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}")

    meta = {"created_at": stamp, "series": SERIES,
            "rule": "procedures-2-4-registration.md §4",
            "scale_convention": convention, "inputs": inputs,
            "not_finished": missing, "code_sha256": sha256_file(Path(__file__))}
    OUT_MANIFEST.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    if SERIES != "synthesis-v1":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            SERIES,
            inputs=[ROOT / "07-analysis" / s["file"] for s in SOURCES],
            outputs=[OUT_CSV, OUT_REPORT, OUT_MANIFEST])
        print(f"  дочерний манифест: manifests-v2/{SERIES}-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
