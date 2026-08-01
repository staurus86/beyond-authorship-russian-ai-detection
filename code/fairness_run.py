#!/usr/bin/env python3
"""Fairness-аудит fairness-v1: FPR по уязвимым группам на оценках процедуры 2.

Спецификация — `07-analysis/fairness-v1-spec.md`, зафиксирована до расчёта.
Протокол групп — `07-analysis/fairness-audit.md`.

    python 09-tools/fairness_run.py
    python 09-tools/fairness_run.py --series fairness-v2   # аудит прогона clf-v2

**Статус — exploratory.** Аудит опирается на процедуру 2, которая сама получила
exploratory-статус, и группы определены после сбора корпуса.

Скрипт не переобучает модель по-своему: он импортирует `clf_run` и вызывает его
`run_split` без изменений, поэтому оценки те же, что в замороженном прогоне.
Замороженные файлы `clf-v1-*` не перезаписываются.

**Preflight с жёстким завершением.** До группировки воспроизведённые агрегаты
сверяются с `clf-v1-p2a-metrics.csv`. Расхождение больше TOL останавливает
расчёт: значит прогон не тот, и группировать его нельзя.
"""

import csv
import json
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402

ROOT = clf.ROOT

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FROZEN_P2A = ROOT / "07-analysis" / "clf-v1-p2a-metrics.csv"
OUT_PRED = ROOT / "07-analysis" / "fairness-v1-predictions.csv"
OUT_GROUPS = ROOT / "07-analysis" / "fairness-v1-groups.csv"
OUT_REPORT = ROOT / "07-analysis" / "fairness-v1-report.md"
OUT_MANIFEST = ROOT / "07-analysis" / "fairness-v1-manifest.json"

# fairness-v2, 2026-07-29: аудит прогона на prep-v5. Значения по умолчанию не
# меняются; сверка идёт с метриками той же серии, что и оценки.
SERIES = "fairness-v1"

# §2 спецификации: четыре модели, первичная — main/full.
PLAN = [("main", "full"), ("main", "net"),
        ("format-only", "full"), ("length-only", "full")]
PRIMARY = ("main", "full")
TOL = 1e-9                  # порог сверки с замороженным прогоном
THRESHOLD = 0.5             # тот же порог решения, что в metrics_block
SMALL_GROUP = 20            # §4: группа меньше — помечается «малая выборка»
OPERATIONAL_FPR = 0.05      # §5, критерий 2: мягкий операционный режим
BOOT = clf.BOOTSTRAP
SEED = clf.SEED

# Группы протокола, которых в корпусе нет. §3: перечисляются явно.
ABSENT_GROUPS = [
    ("тексты неносителей русского",
     "заявка в Russian Learner Corpus отправлена 2026-07-24, ответа нет"),
    ("техническая документация", "страта в корпус не набиралась"),
    ("студенческие работы", "страта в корпус не набиралась"),
    ("тексты после корректуры", "отдельной страты нет: признак не размечался"),
    ("авторы с простой лексикой", "признак не размечался, группа не определима"),
]


def cluster_of(row):
    return row["split_group_source"] or row["generation_channel"]


def tercile_bounds(registry_rows):
    """Границы длины считаются один раз по всей человеческой части (§3)."""
    words = sorted(int(r["word_count"] or 0) for r in registry_rows
                   if r["origin_class"] == "H" and r["word_count"])
    return words[len(words) // 3], words[2 * len(words) // 3]


def groups_of(row, low, high):
    """Группы документа. Человеческие документы, машинные в аудит не входят."""
    out = []
    if row["hh_subgroups"]:
        out.append("hh_all")
        out.append(row["hh_subgroups"])
    out.append(f"genre={row['genre']}")
    out.append(f"source={row['source_platform']}")
    words = int(row["word_count"] or 0)
    out.append("len_short" if words <= low else
               "len_mid" if words <= high else "len_long")
    return out


def boot_fpr(flags, clusters, seed=SEED, reps=BOOT):
    """FPR с кластерным bootstrap по источнику — та же схема, что в прогоне."""
    return clf.cluster_bootstrap_share(flags, clusters, seed, reps)


def boot_diff(in_flags, in_clusters, out_flags, out_clusters, seed=SEED, reps=BOOT):
    """Разность FPR группы и остального человеческого набора того же теста.

    Источники ресэмплятся одновременно для обеих частей: смещение общее, и
    независимый ресэмплинг завысил бы точность разности.
    """
    if not in_flags or not out_flags:
        return None, None, None
    inside, outside = defaultdict(list), defaultdict(list)
    for flag, cluster in zip(in_flags, in_clusters):
        inside[cluster].append(flag)
    for flag, cluster in zip(out_flags, out_clusters):
        outside[cluster].append(flag)
    keys = sorted(set(inside) | set(outside))
    rng = random.Random(seed)
    diffs = []
    for _ in range(reps):
        picked = [rng.choice(keys) for _ in keys]
        a = [v for k in picked for v in inside.get(k, [])]
        b = [v for k in picked for v in outside.get(k, [])]
        if a and b:
            diffs.append(st.fmean(a) - st.fmean(b))
    if not diffs:
        return None, None, None
    diffs.sort()
    point = st.fmean(in_flags) - st.fmean(out_flags)
    return (point, diffs[int(0.025 * len(diffs))],
            diffs[int(0.975 * len(diffs)) - 1])


def frozen_index():
    """Замороженные агрегаты P2a по ключу holdout × модель × estimand."""
    out = {}
    for r in clf.read_rows(FROZEN_P2A):
        out[(r["split"], r["model"], r["estimand"])] = r
    return out


def check_against_frozen(key, block, frozen):
    """Preflight §1: воспроизведённый блок обязан совпасть с записанным."""
    ref = frozen.get(key)
    if ref is None:
        raise SystemExit(f"preflight: в замороженном прогоне нет строки {key}")
    for field in ("auroc", "fpr", "fpr_hard_human", "fpr_HH-translation",
                  "fpr_HH-polished", "fpr_HH-formal-register"):
        got, want = block.get(field), ref.get(field)
        if got is None and not want:
            continue
        if got is None or not want:
            raise SystemExit(f"preflight: {key}, поле {field}: "
                             f"воспроизведено {got}, записано {want!r}")
        if abs(float(got) - float(want)) > TOL:
            raise SystemExit(f"preflight: {key}, поле {field}: "
                             f"воспроизведено {got}, записано {want}, "
                             f"расхождение {abs(float(got) - float(want)):.3e}")


def collect(docs_by_id, values, lengths, low, high):
    """Прогон по всем holdout и моделям плана: предсказания и группы."""
    splits = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(clf.SPLITS.glob("holdout_*.json"))]
    frozen = frozen_index()
    predictions, per_group = [], []
    checked = 0
    for split in splits:
        name = split["split_name"]
        for model_name, estimand in PLAN:
            result = clf.run_split(split, docs_by_id, values, lengths,
                                   model_name, estimand)
            if result is None:
                continue
            block, y_test, scores, rows = result
            check_against_frozen((name, model_name, estimand), block, frozen)
            checked += 1
            human = []
            for label, score, row in zip(y_test, scores, rows):
                flag = int(score > THRESHOLD)
                predictions.append({
                    "split": name, "model": model_name, "estimand": estimand,
                    "document_id": row["document_id"],
                    "origin_class": row["origin_class"],
                    "score": round(float(score), 6),
                    "predicted_machine": flag,
                    "false_positive": int(flag and label == 0),
                })
                if label == 0:
                    human.append((flag, cluster_of(row), row))
            per_group += group_rows(name, model_name, estimand, human, low, high)
        print(f"  {name}: сверено с замороженным прогоном")
    return predictions, per_group, checked, len(splits)


def group_rows(split_name, model_name, estimand, human, low, high):
    """Метрики §4 по каждой группе внутри одного теста."""
    if not human:
        return []
    members = defaultdict(list)
    for flag, cluster, row in human:
        for group in groups_of(row, low, high):
            members[group].append((flag, cluster))
    all_flags = [f for f, _, _ in human]
    all_clusters = [c for _, c, _ in human]
    overall, overall_lo, overall_hi = boot_fpr(all_flags, all_clusters)
    out = [{
        "split": split_name, "model": model_name, "estimand": estimand,
        "group": "весь человеческий тест", "n": len(all_flags),
        "fpr": overall, "fpr_ci_low": overall_lo, "fpr_ci_high": overall_hi,
        "delta": None, "delta_ci_low": None, "delta_ci_high": None, "note": "",
    }]
    for group, rows in sorted(members.items()):
        flags = [f for f, _ in rows]
        clusters = [c for _, c in rows]
        idx = {i for i, (_, _, row) in enumerate(human)
               if group in groups_of(row, low, high)}
        out_flags = [human[i][0] for i in range(len(human)) if i not in idx]
        out_clusters = [human[i][1] for i in range(len(human)) if i not in idx]
        fpr, lo, hi = boot_fpr(flags, clusters)
        delta, d_lo, d_hi = boot_diff(flags, clusters, out_flags, out_clusters)
        out.append({
            "split": split_name, "model": model_name, "estimand": estimand,
            "group": group, "n": len(flags),
            "fpr": fpr, "fpr_ci_low": lo, "fpr_ci_high": hi,
            "delta": delta, "delta_ci_low": d_lo, "delta_ci_high": d_hi,
            "note": "малая выборка" if len(flags) < SMALL_GROUP else "",
        })
    return out


def verdicts(per_group):
    """§5: два критерия, зафиксированные до расчёта, по первичной модели."""
    rows = [r for r in per_group
            if (r["model"], r["estimand"]) == PRIMARY and r["group"] != "весь человеческий тест"]
    by_group = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    out = []
    for group, items in sorted(by_group.items()):
        systematic = {r["split"] for r in items
                      if r["delta_ci_low"] is not None and r["delta_ci_low"] > 0}
        above = {r["split"] for r in items
                 if r["fpr_ci_high"] is not None and r["fpr_ci_high"] > OPERATIONAL_FPR}
        both = systematic & above
        out.append({
            "group": group, "holdouts": len(items),
            "systematic": len(systematic), "above_operational": len(above),
            "both": len(both),
            "flagged": len(both) * 2 >= len(items),
            "fpr_median": st.median([r["fpr"] for r in items if r["fpr"] is not None]),
            "delta_median": st.median([r["delta"] for r in items
                                       if r["delta"] is not None]),
            "small": all(r["note"] for r in items),
        })
    return out


def fmt(x, digits=3):
    return "—" if x is None else f"{x:.{digits}f}"


def duplicate_groups(registry_rows, low, high):
    """Группы с побайтово одинаковым составом документов.

    Человеческие жанры один в один ложатся на подгруппы hard-human и частью на
    источники (`clf-v1-split-degeneracy.md` §2). Такие группы в таблицах читаются
    как одна строка, а не как независимые проверки.
    """
    members = defaultdict(set)
    for row in registry_rows:
        if row["origin_class"] != "H":
            continue
        for group in groups_of(row, low, high):
            members[group].add(row["document_id"])
    seen, out = set(), []
    names = sorted(members)
    for i, a in enumerate(names):
        if a in seen or not members[a]:
            continue
        same = [b for b in names[i + 1:] if members[b] == members[a]]
        if same:
            out.append(([a] + same, len(members[a])))
            seen.update([a] + same)
    return out


def sources_within_genre(per_group, registry_rows, low, high):
    """FPR площадок внутри одного жанра: разброс между источниками одного жанра."""
    genre_of = {}
    for row in registry_rows:
        if row["origin_class"] == "H" and row["source_platform"]:
            genre_of[f"source={row['source_platform']}"] = row["genre"]
    medians = defaultdict(list)
    for r in per_group:
        if (r["model"], r["estimand"]) != PRIMARY or r["fpr"] is None:
            continue
        if r["group"].startswith("source=") or r["group"].startswith("genre="):
            medians[r["group"]].append((r["fpr"], r["split"]))
    out = defaultdict(list)
    for group, values in medians.items():
        if not group.startswith("source="):
            continue
        genre = genre_of.get(group)
        if genre:
            out[genre].append((group, st.median([v for v, _ in values]), len(values)))
    genre_median = {g.split("=", 1)[1]: st.median([v for v, _ in vals])
                    for g, vals in medians.items() if g.startswith("genre=")}
    return out, genre_median


def write_report(per_group, verdict_rows, low, high, checked, n_splits, stamp,
                 registry_rows):
    lines = []
    add = lines.append
    add(f"# Fairness-аудит {SERIES}: FPR по уязвимым группам")
    add("")
    add(f"Собрано {stamp} скриптом `09-tools/fairness_run.py` по спецификации "
        "`07-analysis/fairness-v1-spec.md`, зафиксированной до расчёта.")
    add("")
    add("**Статус — exploratory.** Аудит меряет поведение одной процедуры на одном "
        "корпусе; смещение не переносится на другие системы и другие данные.")
    add("")
    add(f"Preflight пройден: {checked} блоков из {n_splits} holdout × "
        f"{len(PLAN)} моделей сверены с `{FROZEN_P2A.name}` по AUROC, FPR и "
        f"FPR подгрупп hard-human, расхождение ниже {TOL:.0e}. Оценки те же, что в "
        "замороженном прогоне.")
    add("")
    add(f"Границы терцилей длины по человеческой части корпуса: короткие до {low} "
        f"слов, средние до {high}, длинные выше.")
    add("")

    add("## 1. Первичная модель: сводка по группам")
    add("")
    add("Медиана по 18 holdout. Критерии §5 спецификации: систематическое смещение — "
        "нижняя граница интервала разности выше нуля; превышение операционного "
        "уровня — верхняя граница интервала FPR выше 5%.")
    add("")
    add("| Группа | Holdout | Медиана FPR | Медиана Δ | Систематическое смещение | "
        "Выше 5% | Оба критерия | Вердикт |")
    add("|---|---|---|---|---|---|---|---|")
    for v in sorted(verdict_rows, key=lambda r: -r["fpr_median"]):
        verdict = ("**в статью: обвиняется системно**" if v["flagged"]
                   else "малая выборка" if v["small"] else "порог не пройден")
        add(f"| `{v['group']}` | {v['holdouts']} | {fmt(v['fpr_median'])} | "
            f"{fmt(v['delta_median'])} | {v['systematic']} | {v['above_operational']} | "
            f"{v['both']} | {verdict} |")
    add("")
    wide = [v for v in verdict_rows if v["flagged"] and v["holdouts"] >= n_splits / 2]
    narrow = [v for v in verdict_rows if v["flagged"] and v["holdouts"] < n_splits / 2]
    add("**Покрытие holdout у групп разное, и вердикт это не выравнивает.** Источник "
        "попадает в тест только тех разбиений, которые его выносят, поэтому доля "
        "«оба критерия из holdout» у группы с одним разбиением означает одно "
        "совпадение, а не устойчивость.")
    add("")
    add(f"Группы, прошедшие критерии на половине и более из {n_splits} holdout: "
        + (", ".join(f"`{v['group']}` ({v['both']} из {v['holdouts']})"
                     for v in sorted(wide, key=lambda r: -r["both"])) or "нет") + ".")
    add("")
    add("Группы, прошедшие критерии на малом числе разбиений: "
        + (", ".join(f"`{v['group']}` ({v['both']} из {v['holdouts']})"
                     for v in sorted(narrow, key=lambda r: -r["fpr_median"])) or "нет")
        + ". Их результат в статье подаётся как наблюдение, а не как измеренное "
          "смещение.")
    add("")

    dups = duplicate_groups(registry_rows, low, high)
    if dups:
        add("**Часть групп состоит из одних и тех же документов** и в таблице выше "
            "читается как одна строка, а не как независимые проверки:")
        add("")
        for names, size in dups:
            add(f"- {' ≡ '.join('`' + n + '`' for n in names)} — {size} документов;")
        add("")

    by_genre, genre_median = sources_within_genre(per_group, registry_rows, low, high)
    add("### Источники внутри жанра расходятся сильнее, чем жанры между собой")
    add("")
    add("| Жанр | Медиана FPR жанра | Площадки жанра: медиана FPR |")
    add("|---|---|---|")
    for genre in sorted(by_genre):
        cells = ", ".join(f"{g.split('=', 1)[1]} {fmt(v)} ({n} h/o)"
                          for g, v, n in sorted(by_genre[genre], key=lambda x: -x[1]))
        add(f"| {genre} | {fmt(genre_median.get(genre))} | {cells} |")
    add("")

    add("## 2. Первичная модель по каждому holdout: группы страты hard-human")
    add("")
    add("| Holdout | Группа | N | FPR | 95% CI | Δ с остальным тестом | 95% CI Δ |")
    add("|---|---|---|---|---|---|---|")
    for r in per_group:
        if (r["model"], r["estimand"]) != PRIMARY:
            continue
        if not (r["group"].startswith("HH-") or r["group"] in ("hh_all", "весь человеческий тест")):
            continue
        ci = (f"[{fmt(r['fpr_ci_low'])}; {fmt(r['fpr_ci_high'])}]"
              if r["fpr_ci_low"] is not None else "—")
        dci = (f"[{fmt(r['delta_ci_low'])}; {fmt(r['delta_ci_high'])}]"
               if r["delta_ci_low"] is not None else "—")
        add(f"| {r['split']} | `{r['group']}` | {r['n']} | {fmt(r['fpr'])} | {ci} | "
            f"{fmt(r['delta'])} | {dci} |")
    add("")

    add("## 3. Сравнение моделей: медиана FPR по holdout")
    add("")
    add("| Группа | main/full | main/net | format-only | length-only |")
    add("|---|---|---|---|---|")
    by_key = defaultdict(dict)
    for r in per_group:
        if r["fpr"] is None:
            continue
        by_key[r["group"]].setdefault((r["model"], r["estimand"]), []).append(r["fpr"])
    for group in sorted(by_key):
        cells = []
        for key in PLAN:
            vals = by_key[group].get(key)
            cells.append(fmt(st.median(vals)) if vals else "—")
        add(f"| `{group}` | " + " | ".join(cells) + " |")
    add("")

    add("## 4. Группы протокола, не представленные в корпусе")
    add("")
    add("| Группа | Почему отсутствует |")
    add("|---|---|")
    for name, reason in ABSENT_GROUPS:
        add(f"| {name} | {reason} |")
    add("")
    add("Отсутствие группы означает, что смещение на ней **не измерено**, а не что "
        "его нет. Цифра 61.22% FPR у неносителей из Liang et al. (2023) относится к "
        "английскому и здесь не воспроизводится.")
    add("")

    add("## 5. Что осталось открытым")
    add("")
    add("Численный порог отказа от применения в обвинительном сценарии в "
        "`fairness-audit.md` не заполнен. Назначать его после просмотра результатов "
        "нельзя, поэтому вердикты выше опираются на два статистических критерия §5 "
        "спецификации. Порог остаётся вопросом к PI.")
    add("")
    add("Разбор ложных срабатываний дословными фрагментами идёт в разбор ошибок по "
        "`error-analysis-protocol.md`, не сюда.")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def switch_to_v2(revision=""):
    """Серия v2: оценки берутся у clf-v2, сверка — с его же метриками.

    Ревизия матрицы приходит суффиксом имени серии: `fairness-v2-r2b` читает
    исправленную матрицу и метрики `clf-v2r2b`. Файлы прежней серии при этом не
    перезаписываются — у ревизии свои имена.
    """
    global FROZEN_P2A, OUT_PRED, OUT_GROUPS, OUT_REPORT, OUT_MANIFEST
    upstream = f"clf-v2-valid-{revision}" if revision else "clf-v2-valid"
    clf.SERIES = upstream
    clf.switch_to_v2(upstream)
    analysis = ROOT / "07-analysis"
    stem = f"fairness-v2{revision}"
    FROZEN_P2A = analysis / f"clf-v2{revision}-p2a-metrics.csv"
    OUT_PRED = analysis / f"{stem}-predictions.csv"
    OUT_GROUPS = analysis / f"{stem}-groups.csv"
    OUT_REPORT = analysis / f"{stem}-report.md"
    OUT_MANIFEST = analysis / f"{stem}-manifest.json"


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES,
                        choices=["fairness-v1", "fairness-v2", "fairness-v2-r2b",
                                 "fairness-v2-r3"],
                        help="fairness-v1 — аудит прогона clf-v1 (по умолчанию); "
                             "fairness-v2 — аудит прогона clf-v2 на prep-v5; "
                             "fairness-v2-r2b — аудит прогона на матрице v5-r2")
    args = parser.parse_args()
    SERIES = args.series
    if SERIES != "fairness-v1":
        revision = SERIES[len("fairness-v2"):].lstrip("-")
        clf.reject_rolled_back(revision, SERIES)
        switch_to_v2(revision)
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.require(gate.STAGE_ANALYSIS, "fairness", revision=revision)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{SERIES}, запуск {stamp}, seed {SEED}")

    registry_rows = clf.read_rows(clf.DOCUMENTS, "utf-8-sig")
    docs_by_id = {r["document_id"]: r for r in registry_rows}
    values = clf.load_matrix(set(clf.FEATURES_CORE + clf.FEATURES_STRUCTURAL
                                 + [clf.FEATURE_M02]))
    lengths = clf.load_length_features()
    low, high = tercile_bounds(registry_rows)
    print(f"  документов {len(docs_by_id)}, терцили длины: {low} / {high}")

    predictions, per_group, checked, n_splits = collect(
        docs_by_id, values, lengths, low, high)

    clf.write_csv(OUT_PRED, predictions)
    clf.write_csv(OUT_GROUPS, per_group)
    verdict_rows = verdicts(per_group)
    write_report(per_group, verdict_rows, low, high, checked, n_splits, stamp,
                 registry_rows)

    manifest = {
        "created_at": stamp, "procedure": SERIES, "status": "exploratory",
        "spec": "07-analysis/fairness-v1-spec.md",
        "seed": SEED, "threshold": THRESHOLD, "bootstrap": BOOT,
        "preflight_passed": True, "blocks_checked": checked,
        "tolerance": TOL,
        "tercile_bounds": {"short_max": low, "mid_max": high},
        "inputs": {name: clf.sha256_file(path) for name, path in [
            ("documents-registry.csv", clf.DOCUMENTS),
            (clf.MATRIX.name, clf.MATRIX),
            (FROZEN_P2A.name, FROZEN_P2A),
            ("clf_run.py", Path(clf.__file__)),
            ("fairness_run.py", Path(__file__)),
        ]},
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    flagged = [v["group"] for v in verdict_rows if v["flagged"]]
    print(f"  предсказаний {len(predictions)}, строк по группам {len(per_group)}")
    print(f"  групп с систематическим смещением: {len(flagged)}")
    for group in flagged:
        print(f"    {group}")
    print(f"  отчёт: {OUT_REPORT.name}")

    if SERIES != "fairness-v1":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            SERIES,
            inputs=[clf.DOCUMENTS, clf.MATRIX, FROZEN_P2A],
            outputs=[OUT_PRED, OUT_GROUPS, OUT_REPORT, OUT_MANIFEST])
        print(f"  дочерний манифест: manifests-v2/{SERIES}-manifest.json")


if __name__ == "__main__":
    main()
