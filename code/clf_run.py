#!/usr/bin/env python3
"""Процедура 2, clf-v1: P2a corpus-discrimination baseline и P2b source-disjoint SEO.

Спецификация — `07-analysis/proc2-classifier-spec.md`, метрики — `metrics-spec.md`,
разбиения — `07-analysis/splits/`, статус и правило синтеза —
`procedures-2-4-registration.md`.

    python 09-tools/preflight_procedures_2_4.py && python 09-tools/clf_run.py
    python 09-tools/clf_run.py --series clf-v2      # пересчёт на prep-v5

Серия задаётся параметром, значения по умолчанию не меняются: без флага скрипт
воспроизводит clf-v1. В серии v2 входы — `feature-matrix-v5.csv` и `prep-v5`,
разбиения P2b **переносятся** из v1 (`splits-v5/p2b-*-carried.json`), допуск
берётся у `preflight_v2_run.require`, результаты пишутся под именами `clf-v2-*`.

**Статус — exploratory.** P2a — корпусный baseline, подверженный жанровому и
источниковому конфаундингу; формулировка «точность обнаружения машинного текста»
к нему неприменима. P2b проверяет не происхождение, а перенос различения за
пределы конкретных идентификаторов источников.

Все преобразования — стандартизация, импутация, подбор регуляризации — идут
внутри соответствующего train. Источник никогда не пересекает train и test.
"""

import csv
import hashlib
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, matthews_corrcoef, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preflight_procedures_2_4 import build_outer_folds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
PAIRS = ROOT / "07-analysis" / "score-v1-pairs.csv"
SPLITS = ROOT / "07-analysis" / "splits"
PREFLIGHT_MANIFEST = ROOT / "07-analysis" / "procedures-2-4-manifest.json"

# clf-v2, 2026-07-29: пересчёт на prep-v5. Значения по умолчанию не меняются —
# `python 09-tools/clf_run.py` обязан воспроизводить clf-v1 строка в строку.
SERIES = "clf-v1"
CARRIED_OUTER = ROOT / "07-analysis" / "splits-v5" / "p2b-outer-folds-carried.json"
CARRIED_INNER = ROOT / "07-analysis" / "splits-v5" / "p2b-inner-folds-carried.json"
CARRIED_P2A = ROOT / "07-analysis" / "splits-v5" / "p2a-inner-folds-carried.json"
VALID_P2A = ROOT / "07-analysis" / "splits-v5" / "p2a-inner-folds-valid.json"
SCHEMES_SHA = ROOT / "07-analysis" / "clf-v2-schemes.sha256.md"
_P2A_CARRIED = None
# Ревизия матрицы и схема вложенного CV — разные вещи. Имя серии несёт обе:
# `clf-v2-legacy-r2` — схема A на матрице v5-r2. Сравнивать с именем схемы нужно
# базовую часть, иначе суффикс ревизии молча уводит прогон на чужую схему.
SERIES_REVISIONS = ("-r2", "-r2b", "-r3")
# Ревизии, отменённые откатом 1 августа 2026: все они читают
# `feature-matrix-v5-r2.csv`, собранную пересчётом D04 и D05 по профилям
# prep-v4. Исходная матрица v5 корректна, что доказано побитовым совпадением
# пересчёта по prep-v5 (3764 значения из 3764). Серии сохраняются в списке
# допустимых имён, чтобы прежние манифесты читались, но запускать их нельзя.
ROLLED_BACK_REVISIONS = ("r2", "r2b", "r3")
ROLLBACK_DECISION = "07-analysis/rollback-decision-2026-08-01-d04-d05.json"


def reject_rolled_back(revision, series):
    """Отказ запускать серию на откатанной матрице."""
    if revision in ROLLED_BACK_REVISIONS:
        raise SystemExit(
            f"серия {series} отменена откатом: она читает "
            f"feature-matrix-v5-r2.csv, где D04 и D05 посчитаны по профилям "
            f"prep-v4. Основание — {ROLLBACK_DECISION}. Действующая матрица — "
            f"feature-matrix-v5.csv, действующие результаты — серии без суффикса")


def base_series(series=None):
    """Имя серии без суффикса ревизии матрицы: только схема inner CV."""
    name = SERIES if series is None else series
    for suffix in SERIES_REVISIONS:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name

OUT_P2A = ROOT / "07-analysis" / "clf-v1-p2a-metrics.csv"
OUT_P2A_SLICES = ROOT / "07-analysis" / "clf-v1-p2a-slices.csv"
OUT_P2B = ROOT / "07-analysis" / "clf-v1-p2b-metrics.csv"
OUT_P2B_O1 = ROOT / "07-analysis" / "clf-v1-p2b-o1-contrasts.csv"
OUT_MANIFEST = ROOT / "07-analysis" / "clf-v1-manifest.json"
OUT_REPORT = ROOT / "07-analysis" / "clf-v1-report.md"

# §6: первичные признаки кодбука со значениями. X01, X02, X05, F08 и M03 исключены —
# их пропуск равен метке класса либо принадлежности к пилоту.
FEATURES_CORE = ["L01", "L02", "L04", "L05", "S01", "S02", "S03", "S06", "S08",
                 "R01", "M01", "D04", "D05", "C01", "C02", "F04", "F05", "F06"]
# §5: структурные четыре — те же, что вынесены в format-компонент процедуры 1.
FEATURES_STRUCTURAL = ["F01", "R06", "R07", "P01"]
# §3.3: M02 вне основного варианта, sensitivity с импутацией медианой train.
FEATURE_M02 = "M02"

C_GRID = [0.01, 0.1, 1.0, 10.0]     # сетка регуляризации, зафиксирована до расчёта
INNER_FOLDS = 3
NEG_CONTROL_PERMUTATIONS = 20       # post hoc 2026-07-28, см. run_negative_control
SEED = 20260727
BOOTSTRAP = 5000
WILD_REPS = 5000
LEVEL = re.compile(r"regulation_level=(\d)")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def stratum_of(row):
    """Срез для macro-метрик: у человека — уровень регламентированности,
    у машины — режим задания. Документы без уровня идут своей стратой."""
    if row["origin_class"] == "A":
        return f"A/{row['prompt_condition']}"
    m = LEVEL.search(row.get("notes") or "")
    return f"H/уровень {m.group(1)}" if m else "H/без уровня"


def load_matrix(feature_ids):
    values = defaultdict(dict)
    for r in csv.DictReader(MATRIX.open(encoding="utf-8")):
        fid = r["feature_id"]
        if fid not in feature_ids:
            continue
        raw = r["normalized_value"] or r["raw_value"]
        if raw:
            values[r["document_id"]][fid] = float(raw)
    return values


def load_length_features():
    """length-only baseline: log(word_count), предложения, абзацы, заголовки.

    Число предложений в prep-манифесте не хранится и выводится из S01 — средней
    длины предложения; остальные три величины берутся из манифеста напрямую.
    """
    s01 = {d: v["S01"] for d, v in load_matrix({"S01"}).items() if "S01" in v}
    out = {}
    for r in read_rows(PREP_MANIFEST):
        words = int(r["prose_words"] or 0)
        mean_sent = s01.get(r["document_id"]) or 0
        out[r["document_id"]] = [
            np.log(max(words, 1)),
            words / mean_sent if mean_sent else 0.0,
            float(r["paragraph"] or 0),
            float(r["heading_md"] or 0) + float(r["heading_plain"] or 0),
        ]
    return out


def one_hot(values, levels=None):
    """Уровни задаются обучающей частью. Невиданный уровень в test даёт нулевую
    строку: расширять кодировку по test означало бы заглядывать в него."""
    if levels is None:
        levels = sorted(set(values))
    index = {v: i for i, v in enumerate(levels)}
    matrix = np.zeros((len(values), len(levels)))
    for i, v in enumerate(values):
        if v in index:
            matrix[i, index[v]] = 1.0
    return matrix, levels


def make_model(c):
    return Pipeline([("scale", StandardScaler()),
                     ("lr", LogisticRegression(C=c, penalty="l2", max_iter=5000,
                                               class_weight="balanced",
                                               random_state=SEED))])


def load_carried_inner(fold_index, fold):
    """Перенесённое соответствие группа → inner fold для одного внешнего fold-а.

    Читается **только** `splits-v5/p2b-inner-folds-carried.json`: собственное
    разбиение в серии v2 не строится, иначе смена tuning split смешалась бы с
    коррекцией корпуса (`inner-folds-carry.md`). Проверки идут до расчёта.
    """
    if not CARRIED_INNER.exists():
        raise SystemExit(f"нет {CARRIED_INNER.name}: перенесённые inner-разбиения "
                         "обязательны, строить своё запрещено")
    data = json.loads(CARRIED_INNER.read_text(encoding="utf-8"))
    if data.get("inner_folds") != INNER_FOLDS:
        raise SystemExit(f"{CARRIED_INNER.name}: inner_folds "
                         f"{data.get('inner_folds')}, ожидалось {INNER_FOLDS}")
    entry = data["folds"].get(str(fold_index))
    if entry is None:
        raise SystemExit(f"{CARRIED_INNER.name}: нет назначений для outer {fold_index}")
    if entry["held_out_channel"] != fold["ai"]:
        raise SystemExit(f"outer {fold_index}: удержанный канал {fold['ai']}, "
                         f"в переносе {entry['held_out_channel']}")
    assignments = entry["assignments"]
    leaked = sorted({fold["ai"], *fold["human"]} & set(assignments))
    if leaked:
        raise SystemExit(f"outer {fold_index}: в inner-назначениях присутствуют "
                         f"группы внешнего теста: {', '.join(leaked)}")
    if sorted(set(assignments.values())) != list(range(INNER_FOLDS)):
        raise SystemExit(f"outer {fold_index}: номера inner fold-ов "
                         f"{sorted(set(assignments.values()))}")
    return assignments


def check_schemes():
    """Сверка хешей амендмента и обеих схем разбиения, зафиксированных до прогона.

    PI, 2026-07-29: «амендмент и обе схемы нужно захешировать до запуска любого
    v2-варианта». Расхождение означает, что схему правили после регистрации, и
    расчёт останавливается.
    """
    if not SCHEMES_SHA.exists():
        raise SystemExit(f"нет {SCHEMES_SHA.name}: схемы не зафиксированы")
    text = SCHEMES_SHA.read_text(encoding="utf-8")
    targets = {
        "amendment-clf-v2-inner-cv.md":
            ROOT / "02-preregistration" / "amendment-clf-v2-inner-cv.md",
        CARRIED_P2A.name: CARRIED_P2A,
        VALID_P2A.name: VALID_P2A,
    }
    drift = [name for name, path in targets.items()
             if sha256_file(path) not in text]
    if drift:
        raise SystemExit("хеш не совпадает с зафиксированным: " + ", ".join(drift))


def load_p2a_assignments(split_name):
    """Соответствие группа → inner fold для holdout-разбиения P2a.

    Схема A (`clf-v2-legacy`) — перенос из clf-v1: `p2a-inner-folds-carried.json`.
    Собран на составе до исключения 34 документов, потому что при пересборке 131
    назначение из 388 сменило бы fold.

    Схема B (`clf-v2-valid`) — `p2a-inner-folds-valid.json`: у семи holdout
    разбиение перестроено так, чтобы validation каждого fold-а был двухклассовым.
    Обе схемы зарегистрированы `amendment-clf-v2-inner-cv.md` до расчёта.
    """
    global _P2A_CARRIED
    path = CARRIED_P2A if base_series() == "clf-v2-legacy" else VALID_P2A
    if _P2A_CARRIED is None:
        if not path.exists():
            raise SystemExit(f"нет {path.name}: разбиение подбора C обязано "
                             "приходить из зарегистрированной схемы")
        data = json.loads(path.read_text(encoding="utf-8"))
        if base_series() == "clf-v2-legacy" and data.get("inner_folds") != INNER_FOLDS:
            raise SystemExit(f"{path.name}: inner_folds "
                             f"{data.get('inner_folds')}, ожидалось {INNER_FOLDS}")
        _P2A_CARRIED = data["splits"]
    entry = _P2A_CARRIED.get(split_name)
    if entry is None:
        raise SystemExit(f"{path.name}: нет назначений для {split_name}")
    assignments = entry["assignments"]
    expected = entry.get("n_folds", INNER_FOLDS)
    if sorted(set(assignments.values())) != list(range(expected)):
        raise SystemExit(f"{split_name}: номера inner fold-ов "
                         f"{sorted(set(assignments.values()))}, ожидалось {expected}")
    return assignments


def carried_folds(y, groups, assignments, strict=True):
    """Индексы train/validation по перенесённому назначению групп.

    Группа без назначения останавливает расчёт: подставить ей fold по месту
    означало бы построить разбиение заново.

    `strict=False` — только для негативного контроля: там метки переставлены
    нарочно, и одноклассовый validation говорит о перестановке, а не о дефекте
    разбиения. Такой fold пропускается, как это делала редакция clf-v1.
    """
    missing = sorted(set(groups) - set(assignments))
    if missing:
        raise SystemExit("нет перенесённого inner fold у групп train: "
                         + ", ".join(missing[:10])
                         + (" …" if len(missing) > 10 else ""))
    # Число fold-ов задаётся самой схемой: у `holdout_source` их два, и причина
    # записана в амендменте до расчёта.
    folds = []
    for index in range(max(assignments[g] for g in groups) + 1):
        va = np.array([i for i, g in enumerate(groups) if assignments[g] == index])
        tr = np.array([i for i, g in enumerate(groups) if assignments[g] != index])
        if not len(va) or not len(tr):
            raise SystemExit(f"inner fold {index} пуст с одной из сторон")
        if strict and len(set(y[va])) < 2:
            raise SystemExit(f"inner fold {index}: в validation один класс — "
                             "подбор регуляризации по нему невозможен")
        if strict and len(set(y[tr])) < 2:
            raise SystemExit(f"inner fold {index}: в train один класс")
        folds.append((tr, va))
    return folds


def pick_c(x, y, groups, assignments=None, strict=True):
    """Вложенный подбор силы регуляризации. Метрика — ROC AUC, задана до расчёта.

    `assignments` — перенесённое соответствие группа → inner fold. Задано:
    разбиение берётся только из него, fallback на `GroupKFold` запрещён.

    Третьим значением возвращается учёт fold-ов: сколько заявлено и сколько
    пригодилось. В clf-v1 одноклассовый validation пропускался молча, и семь
    holdout из восемнадцати подбирали C по двум fold-ам вместо трёх, не сообщая
    об этом (`amendment-clf-v2-inner-cv.md`).
    """
    if assignments is not None:
        inner = carried_folds(y, groups, assignments, strict)
    else:
        n_groups = len(set(groups))
        if n_groups < 2:
            return C_GRID[len(C_GRID) // 2], None, {"total": 0, "used": 0}
        splitter = GroupKFold(n_splits=min(INNER_FOLDS, n_groups))
        inner = list(splitter.split(x, y, groups))
    usable = [i for i, (tr, va) in enumerate(inner)
              if len(set(y[tr])) > 1 and len(set(y[va])) > 1]
    accounting = {"total": len(inner), "used": len(usable),
                  "skipped_single_class": [i for i in range(len(inner))
                                           if i not in usable]}
    best, best_score = None, -np.inf
    for c in C_GRID:
        scores = []
        for tr, va in inner:
            if len(set(y[tr])) < 2 or len(set(y[va])) < 2:
                continue
            model = make_model(c).fit(x[tr], y[tr])
            scores.append(roc_auc_score(y[va], model.predict_proba(x[va])[:, 1]))
        if scores and statistics.fmean(scores) > best_score:
            best, best_score = c, statistics.fmean(scores)
    return (best if best is not None else C_GRID[len(C_GRID) // 2],
            None if best_score == -np.inf else round(best_score, 4),
            accounting)


def tpr_at_fpr(y, scores, target):
    """TPR в рабочей точке: порог берётся по человеческой части, TPR — по машинной."""
    human = sorted((s for s, label in zip(scores, y) if label == 0), reverse=True)
    if not human:
        return None
    k = int(np.floor(target * len(human)))
    threshold = human[k] if k < len(human) else human[-1]
    machine = [s for s, label in zip(scores, y) if label == 1]
    if not machine:
        return None
    return sum(1 for s in machine if s > threshold) / len(machine)


def cluster_bootstrap_share(flags, clusters, seed, reps=BOOTSTRAP):
    """Доля с кластерным bootstrap-интервалом — для FPR по источникам."""
    by_cluster = defaultdict(list)
    for flag, cluster in zip(flags, clusters):
        by_cluster[cluster].append(flag)
    keys = list(by_cluster)
    if not keys:
        return None, None, None
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        picked = [rng.choice(keys) for _ in keys]
        pool = [v for k in picked for v in by_cluster[k]]
        means.append(statistics.fmean(pool))
    means.sort()
    return (statistics.fmean(flags), means[int(0.025 * reps)],
            means[int(0.975 * reps) - 1])


def metrics_block(y, scores, rows, threshold=0.5):
    """Первичные метрики и обязательные срезы из metrics-spec.md §1 и §1.1."""
    y = np.asarray(y)
    scores = np.asarray(scores)
    pred = (scores > threshold).astype(int)
    out = {
        "n": len(y), "n_machine": int(y.sum()), "n_human": int((1 - y).sum()),
        "tpr_at_1pct_fpr": tpr_at_fpr(y, scores, 0.01),
        "tpr_at_5pct_fpr": tpr_at_fpr(y, scores, 0.05),
        "mcc": matthews_corrcoef(y, pred) if len(set(y)) > 1 else None,
        "balanced_accuracy": balanced_accuracy_score(y, pred) if len(set(y)) > 1 else None,
        "auroc": roc_auc_score(y, scores) if len(set(y)) > 1 else None,
    }
    human_idx = [i for i, label in enumerate(y) if label == 0]
    if human_idx:
        flags = [int(pred[i]) for i in human_idx]
        clusters = [rows[i]["split_group_source"] or rows[i]["generation_channel"]
                    for i in human_idx]
        fpr, lo, hi = cluster_bootstrap_share(flags, clusters, SEED)
        out.update({"fpr": fpr, "fpr_ci_low": lo, "fpr_ci_high": hi})
        hh = [i for i in human_idx if rows[i]["hh_subgroups"]]
        out["fpr_hard_human"] = (statistics.fmean([int(pred[i]) for i in hh])
                                 if hh else None)
        for subgroup in sorted({rows[i]["hh_subgroups"] for i in hh}):
            members = [i for i in hh if rows[i]["hh_subgroups"] == subgroup]
            out[f"fpr_{subgroup}"] = statistics.fmean([int(pred[i]) for i in members])
    return out


def stratum_slices(y, scores, rows, split_name, model_name, estimand):
    """Метрики каждой страты отдельно, macro по стратам и худшая страта."""
    by_stratum = defaultdict(list)
    for i, row in enumerate(rows):
        by_stratum[stratum_of(row)].append(i)
    slices, macro_pool = [], defaultdict(list)
    for stratum, idx in sorted(by_stratum.items()):
        sub = metrics_block([y[i] for i in idx], [scores[i] for i in idx],
                            [rows[i] for i in idx])
        sub.update({"split": split_name, "model": model_name, "estimand": estimand,
                    "slice": stratum})
        slices.append(sub)
        for key in ("mcc", "balanced_accuracy", "auroc", "fpr"):
            if sub.get(key) is not None:
                macro_pool[key].append(sub[key])
    macro = {"split": split_name, "model": model_name, "estimand": estimand,
             "slice": "macro по стратам", "n": len(y)}
    worst = {"split": split_name, "model": model_name, "estimand": estimand,
             "slice": "худшая страта", "n": len(y)}
    for key, values in macro_pool.items():
        macro[key] = statistics.fmean(values)
        worst[key] = min(values) if key != "fpr" else max(values)
    return slices + [macro, worst]


def design_for(model_name, docs, values, lengths, registry, estimand, levels=None):
    """Матрица предикторов модели. Возвращает X, список признаков и уровни кодировки.

    Уровни категориальных предикторов приходят из train и переиспользуются на test.
    """
    if model_name == "length-only":
        return np.array([lengths[d] for d in docs]), [
            "log_word_count", "n_sentences", "n_paragraphs", "n_headings"], None
    if model_name == "genre-only":
        matrix, levels = one_hot([registry[d]["genre"] for d in docs], levels)
        return matrix, [f"genre={v}" for v in levels], levels
    if model_name == "source-only":
        matrix, levels = one_hot([registry[d]["split_group_source"]
                                  or registry[d]["generation_channel"] for d in docs],
                                 levels)
        return matrix, [f"source={v}" for v in levels], levels
    if model_name == "format-only":
        feats = FEATURES_STRUCTURAL
    elif estimand == "net":
        feats = FEATURES_CORE
    else:
        feats = FEATURES_CORE + FEATURES_STRUCTURAL
    if model_name == "main+M02":
        feats = feats + [FEATURE_M02]
    return (np.array([[values[d].get(f, np.nan) for f in feats] for d in docs]),
            list(feats), None)


def impute_with_train(x_train, x_test):
    """§3.3: медиана считается на train fold и переносится на test."""
    medians = np.nanmedian(x_train, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)
    return (np.where(np.isnan(x_train), medians, x_train),
            np.where(np.isnan(x_test), medians, x_test))


def run_split(split, docs_by_id, values, lengths, model_name, estimand,
              shuffle=False, shuffle_seed=SEED):
    train_ids = [d for d in split["train"] if d in docs_by_id]
    test_ids = [d for d in split["test"] if d in docs_by_id]
    if not train_ids or not test_ids:
        return None
    x_train, feats, levels = design_for(model_name, train_ids, values, lengths,
                                        docs_by_id, estimand)
    x_test, _, _ = design_for(model_name, test_ids, values, lengths, docs_by_id,
                              estimand, levels=levels)
    x_train, x_test = impute_with_train(x_train, x_test)
    y_train = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0
                        for d in train_ids])
    y_test = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0
                       for d in test_ids])
    if shuffle:
        # negative control: метки переставляются на уровне кластера-источника
        clusters = sorted({docs_by_id[d]["split_group_source"]
                           or docs_by_id[d]["generation_channel"] for d in train_ids})
        labels = {c: y_train[[i for i, d in enumerate(train_ids)
                              if (docs_by_id[d]["split_group_source"]
                                  or docs_by_id[d]["generation_channel"]) == c]][0]
                  for c in clusters}
        shuffled = list(labels.values())
        random.Random(shuffle_seed).shuffle(shuffled)
        remap = dict(zip(clusters, shuffled))
        y_train = np.array([remap[docs_by_id[d]["split_group_source"]
                                  or docs_by_id[d]["generation_channel"]]
                            for d in train_ids])
    if len(set(y_train)) < 2:
        return None
    # Тест с одним классом: AUROC, MCC и TPR неопределимы, FPR определим и нужен —
    # это цена ложного обвинения на невиданном человеческом жанре. Решение принято
    # 2026-07-28 после первого прогона, строки помечаются post hoc.
    single_class_test = len(set(y_test)) < 2
    groups = [docs_by_id[d]["split_group_source"] or docs_by_id[d]["generation_channel"]
              for d in train_ids]
    assignments = (None if SERIES == "clf-v1"
                   else load_p2a_assignments(split["split_name"]))
    # Схема A воспроизводит v1 и пропускает одноклассовый validation; схема B
    # его не допускает по построению, поэтому проверка остаётся жёсткой.
    strict = base_series() == "clf-v2-valid" and not shuffle
    c, inner_auc, folds_used = pick_c(x_train, y_train, groups, assignments, strict)
    model = make_model(c).fit(x_train, y_train)
    scores = model.predict_proba(x_test)[:, 1]
    rows = [docs_by_id[d] for d in test_ids]
    block = metrics_block(y_test, scores, rows)
    block.update({"split": split["split_name"], "model": model_name,
                  "estimand": estimand, "C": c, "inner_auc": inner_auc,
                  "n_features": len(feats),
                  "status": ("post hoc: тест одноклассовый, определим только FPR"
                             if single_class_test else "зарегистрирован до прогона")})
    if SERIES != "clf-v1":
        block["inner_folds_total"] = folds_used["total"]
        block["inner_folds_used"] = folds_used["used"]
        block["inner_folds_skipped"] = ";".join(
            str(i) for i in folds_used.get("skipped_single_class", []))
    # Диагностика решения PI от 2026-07-29: рядом с рабочим значением пишется то,
    # которое дало бы разбиение, построенное заново. Перестановочные прогоны
    # негативного контроля в диагностику не входят — там метки не настоящие.
    if SERIES != "clf-v1" and not shuffle:
        c_rebuilt, _, _ = pick_c(x_train, y_train, groups)
        block["c_rebuilt"] = c_rebuilt
        block["c_changed_by_rebuild"] = int(c_rebuilt != c)
    return block, y_test, scores, rows


def run_negative_control(split, docs_by_id, values, lengths):
    """Негативный контроль как серия кластерных перестановок.

    Решение принято 2026-07-28 после первого прогона и помечено post hoc.
    Одна перестановка при четырёх машинных кластерах против 124 человеческих не
    убирает сигнал, а инвертирует его: веса классов вытягивают четыре ошибочно
    помеченных кластера в эталон, и модель уверенно учит обратную зависимость.
    AUROC уходил к 0.01 вместо 0.5, то есть число читалось как сильный эффект
    наоборот, а не как отсутствие сигнала. Серия перестановок даёт распределение,
    по которому видно, где лежит нулевая гипотеза.
    """
    aurocs = []
    for i in range(NEG_CONTROL_PERMUTATIONS):
        result = run_split(split, docs_by_id, values, lengths, "main", "full",
                           shuffle=True, shuffle_seed=SEED + i)
        if result and result[0]["auroc"] is not None:
            aurocs.append(result[0]["auroc"])
    if not aurocs:
        return None
    return {"split": split["split_name"], "model": "negative-control",
            "estimand": "full", "n_permutations": len(aurocs),
            "auroc": statistics.fmean(aurocs),
            "auroc_sd": statistics.stdev(aurocs) if len(aurocs) > 1 else None,
            "auroc_min": min(aurocs), "auroc_max": max(aurocs),
            "status": "post hoc: серия кластерных перестановок вместо одной"}


def run_p2a(docs_by_id, values, lengths):
    """P2a: целевая переменная — происхождение, весь корпус, групповые holdout."""
    print("\nP2a — corpus-discrimination baseline")
    splits = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(SPLITS.glob("holdout_*.json"))]
    metrics_rows, slice_rows = [], []
    plan = [("main", "full"), ("main", "net"), ("length-only", "full"),
            ("genre-only", "full"), ("source-only", "full"), ("format-only", "full"),
            ("main+M02", "full")]
    for split in splits:
        for model_name, estimand in plan:
            result = run_split(split, docs_by_id, values, lengths, model_name, estimand)
            if result is None:
                continue
            block, y_test, scores, rows = result
            metrics_rows.append(block)
            slice_rows += stratum_slices(y_test, scores, rows, split["split_name"],
                                         model_name, estimand)
        control = run_negative_control(split, docs_by_id, values, lengths)
        if control:
            metrics_rows.append(control)
        print(f"  {split['split_name']}: моделей посчитано "
              f"{sum(1 for r in metrics_rows if r['split'] == split['split_name'])}")
    return metrics_rows, slice_rows


def wild_cluster_p(diffs, clusters, seed, reps=WILD_REPS):
    """Wild cluster bootstrap с весами Радемахера и кластерный sign-flip.

    Обязательная sensitivity §4: при 15 кластерах обычный percentile bootstrap
    занижает интервалы.
    """
    by_cluster = defaultdict(list)
    for value, cluster in zip(diffs, clusters):
        by_cluster[cluster].append(value)
    keys = list(by_cluster)
    observed_mean = statistics.fmean(diffs)

    def cluster_t(values_by_cluster):
        """t с кластерно-робастной дисперсией среднего и поправкой на малое G.

        Нулевая кластерная дисперсия означает, что среднее не колеблется между
        кластерами, — это бесконечная t, а не нулевая. Прежняя редакция считала
        такой случай отсутствием эффекта.
        """
        flat = [v for k in keys for v in values_by_cluster[k]]
        n, g = len(flat), len(keys)
        if g < 2:
            return 0.0
        mean = statistics.fmean(flat)
        sum_sq = sum(sum(v - mean for v in values_by_cluster[k]) ** 2 for k in keys)
        se = np.sqrt(g / (g - 1) * sum_sq / n ** 2)
        if se == 0:
            return 0.0 if mean == 0 else np.inf * np.sign(mean)
        return mean / se

    observed_t = cluster_t(by_cluster)
    rng = random.Random(seed)
    hits_t, hits_sign = 0, 0
    for _ in range(reps):
        signs = {k: rng.choice((-1, 1)) for k in keys}
        flipped = {k: [v * signs[k] for v in by_cluster[k]] for k in keys}
        if abs(cluster_t(flipped)) >= abs(observed_t):
            hits_t += 1
        if abs(statistics.fmean([v for k in keys for v in flipped[k]])) >= abs(observed_mean):
            hits_sign += 1
    return (max(hits_t, 1) / reps, max(hits_sign, 1) / reps)


def cluster_percentile_ci(diffs, clusters, seed, reps=BOOTSTRAP):
    by_cluster = defaultdict(list)
    for value, cluster in zip(diffs, clusters):
        by_cluster[cluster].append(value)
    keys = list(by_cluster)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        picked = [rng.choice(keys) for _ in keys]
        means.append(statistics.fmean([v for k in picked for v in by_cluster[k]]))
    means.sort()
    return means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def run_p2b(docs_by_id, values, lengths, registry_rows):
    """P2b: четыре внешних fold-а по AI-каналам, три внутренних, контрасты O1."""
    print("\nP2b — source-disjoint SEO corpus baseline")
    seo = [r for r in registry_rows if r["genre"] == "seo"]
    if SERIES == "clf-v1":
        folds, _ = build_outer_folds(seo, SEED)  # noqa: PLR2004
    else:
        # Внешние fold-ы тоже переносятся, а не строятся: при пересборке 34 из
        # ~120 источников сменили бы сторону (`splits-v5-carry.md`).
        carried_outer = json.loads(CARRIED_OUTER.read_text(encoding="utf-8"))["folds"]
        folds = {int(k): v for k, v in carried_outer.items()}
    by_source = defaultdict(list)
    for r in seo:
        by_source[r["split_group_source"]].append(r)

    metrics_rows, o1_rows = [], []
    for estimand in ("full", "net"):
        pooled_y, pooled_scores, pooled_rows = [], [], []
        pooled_by_doc = {}
        for i, fold in sorted(folds.items()):
            test_rows = [r for r in seo if r["generation_channel"] == fold["ai"]]
            test_rows += [r for s in fold["human"] for r in by_source[s]]
            test_ids = {r["document_id"] for r in test_rows}
            train_rows = [r for r in seo if r["document_id"] not in test_ids]
            train_ids = [r["document_id"] for r in train_rows]
            test_ids_list = [r["document_id"] for r in test_rows]

            x_train, feats, levels = design_for("main", train_ids, values, lengths,
                                                docs_by_id, estimand)
            x_test, _, _ = design_for("main", test_ids_list, values, lengths,
                                      docs_by_id, estimand, levels=levels)
            x_train, x_test = impute_with_train(x_train, x_test)
            y_train = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0
                                for d in train_ids])
            y_test = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0
                               for d in test_ids_list])
            # внутренний цикл: три fold-а по оставшимся AI-каналам
            inner_groups = [docs_by_id[d]["generation_channel"]
                            or docs_by_id[d]["split_group_source"] for d in train_ids]
            assignments = (None if SERIES == "clf-v1"
                           else load_carried_inner(i, fold))  # P2b схемой не затронут
            c, inner_auc, inner_folds = pick_c(x_train, y_train, inner_groups,
                                               assignments)
            model = make_model(c).fit(x_train, y_train)
            scores = model.predict_proba(x_test)[:, 1]

            block = metrics_block(y_test, scores, test_rows)
            block.update({"split": f"outer_fold_{i}", "held_out_channel": fold["ai"],
                          "held_out_human_sources": ";".join(fold["human"]),
                          "estimand": estimand, "C": c, "inner_auc": inner_auc,
                          "inner_folds_total": inner_folds["total"],
                          "inner_folds_used": inner_folds["used"],
                          "n_train": len(train_ids), "n_test": len(test_ids_list),
                          "n_features": len(feats)})
            metrics_rows.append(block)
            pooled_y += list(y_test)
            pooled_scores += list(scores)
            pooled_rows += test_rows
            pooled_by_doc.update(dict(zip(test_ids_list, scores)))
            mcc_text = "—" if block["mcc"] is None else f"{block['mcc']:.3f}"
            print(f"  fold{i} ({estimand}), канал {fold['ai']}: "
                  f"AUROC {block['auroc']:.3f}, MCC {mcc_text}")

        pooled = metrics_block(pooled_y, pooled_scores, pooled_rows)
        pooled.update({"split": "pooled_out_of_fold", "held_out_channel": "все четыре",
                       "held_out_human_sources": "", "estimand": estimand,
                       "C": "", "inner_auc": "", "n_train": "", "n_test": len(pooled_y),
                       "n_features": ""})
        metrics_rows.append(pooled)
        by_channel = [r["auroc"] for r in metrics_rows
                      if r["estimand"] == estimand and r["split"].startswith("outer_fold")
                      and r["auroc"] is not None]
        pooled["auroc_spread_between_channels"] = (max(by_channel) - min(by_channel)
                                                   if len(by_channel) > 1 else None)

        # контрасты O1 внутри SEO: 120 пар, 15 кластеров-заданий
        pairs = [p for p in read_rows(PAIRS)
                 if docs_by_id.get(p["doc_left"], {}).get("genre") == "seo"]
        for contrast in ("P3-P1", "P2-P1"):
            diffs, clusters, dropped = [], [], 0
            for pair in pairs:
                if pair["contrast"] != contrast:
                    continue
                left = pooled_by_doc.get(pair["doc_left"])
                right = pooled_by_doc.get(pair["doc_right"])
                if left is None or right is None:
                    dropped += 1
                    continue
                diffs.append(float(left) - float(right))
                clusters.append(pair["brief_id"])
            if not diffs:
                continue
            mean = statistics.fmean(diffs)
            lo, hi = cluster_percentile_ci(diffs, clusters, SEED)
            p_wild, p_sign = wild_cluster_p(diffs, clusters, SEED)
            o1_rows.append({
                "estimand": estimand, "contrast": contrast, "status": "exploratory",
                "n_pairs": len(diffs), "dropped_pairs": dropped,
                "n_clusters": len(set(clusters)),
                "mean_diff_prob": f"{mean:.4f}",
                "ci_low": f"{lo:.4f}", "ci_high": f"{hi:.4f}",
                "p_wild_cluster": f"{p_wild:.4f}", "p_cluster_sign_flip": f"{p_sign:.4f}",
                "limitation": "120 пар — 15 независимых кластеров; четыре машинных "
                              "source-кластера дают слабую оценку межканальной обобщаемости",
            })
    return metrics_rows, o1_rows


def write_csv(path, rows):
    fields = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k, "")) for k in fields})


def switch_to_v2(variant="clf-v2-valid"):
    """Входы, разбиения и имена выходов серии v2. Файлы v1 не перезаписываются.

    Схема B (`clf-v2-valid`) — основной результат P2a и источник для fairness,
    разбора ошибок и статьи, поэтому её файлы носят имя `clf-v2-*`. Схема A
    (`clf-v2-legacy`) — sensitivity и мост к замороженному прогону.
    """
    global MATRIX, PREP_MANIFEST, SPLITS
    global OUT_P2A, OUT_P2A_SLICES, OUT_P2B, OUT_P2B_O1, OUT_MANIFEST, OUT_REPORT
    # Серия v2r2 читает исправленную матрицу: D04 и D05 согласованы с prep-v5
    # (амендмент feature-matrix-v5-r2). Разбиения те же, они не перестраиваются.
    # Файлы серии v2 не перезаписываются: у v2r2 свои имена.
    scheme = base_series(variant)
    revision = variant[len(scheme):].lstrip("-")   # "" | "r2" | "r2b" | "r3"
    reject_rolled_back(revision, variant)
    MATRIX = (ROOT / "06-features"
              / ("feature-matrix-v5-r2.csv" if revision
                 else "feature-matrix-v5.csv"))
    PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v5" / "manifest.csv"
    SPLITS = ROOT / "07-analysis" / "splits-v5"
    analysis = ROOT / "07-analysis"
    stem = f"clf-v2{revision}"
    if scheme == "clf-v2-legacy":
        stem += "-legacy"
    OUT_P2A = analysis / f"{stem}-p2a-metrics.csv"
    OUT_P2A_SLICES = analysis / f"{stem}-p2a-slices.csv"
    OUT_P2B = analysis / f"{stem}-p2b-metrics.csv"
    OUT_P2B_O1 = analysis / f"{stem}-p2b-o1-contrasts.csv"
    OUT_MANIFEST = analysis / f"{stem}-manifest.json"
    OUT_REPORT = analysis / f"{stem}-report.md"


def main():
    global SERIES
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default=SERIES,
                        choices=["clf-v1", "clf-v2-valid", "clf-v2-legacy",
                                 "clf-v2-valid-r2", "clf-v2-legacy-r2",
                                 "clf-v2-valid-r2b", "clf-v2-legacy-r2b",
                                 "clf-v2-valid-r3", "clf-v2-legacy-r3"],
                        help="clf-v1 — замороженный прогон на prep-v4 (по умолчанию); "
                             "clf-v2-valid — основной вариант на prep-v5 с "
                             "двухклассовым inner CV; clf-v2-legacy — sensitivity, "
                             "воспроизводящий поведение clf-v1")
    args = parser.parse_args()
    SERIES = args.series

    if SERIES != "clf-v1":
        switch_to_v2(SERIES)
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        # Ревизия имени серии передаётся шлюзу: прогон на матрице v5-r2 обязан
        # сверяться с ней, а не с прежней.
        gate.require(gate.STAGE_ANALYSIS, "proc2",
                     revision=SERIES[len(base_series()):].lstrip("-"))
        check_schemes()
    else:
        manifest = json.loads(PREFLIGHT_MANIFEST.read_text(encoding="utf-8"))
        if not manifest.get("preflight_passed"):
            raise SystemExit("preflight не пройден — расчёт запрещён")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{SERIES}, запуск {stamp}, seed {SEED}")

    registry_rows = read_rows(DOCUMENTS, "utf-8-sig")
    docs_by_id = {r["document_id"]: r for r in registry_rows}
    values = load_matrix(set(FEATURES_CORE + FEATURES_STRUCTURAL + [FEATURE_M02]))
    lengths = load_length_features()
    print(f"  документов {len(docs_by_id)}, признаков core {len(FEATURES_CORE)}, "
          f"структурных {len(FEATURES_STRUCTURAL)}")

    p2a_metrics, p2a_slices = run_p2a(docs_by_id, values, lengths)
    write_csv(OUT_P2A, p2a_metrics)
    write_csv(OUT_P2A_SLICES, p2a_slices)
    print(f"  P2a: {OUT_P2A.name} — {len(p2a_metrics)} строк, "
          f"{OUT_P2A_SLICES.name} — {len(p2a_slices)} строк")

    p2b_metrics, p2b_o1 = run_p2b(docs_by_id, values, lengths, registry_rows)
    write_csv(OUT_P2B, p2b_metrics)
    write_csv(OUT_P2B_O1, p2b_o1)
    print(f"  P2b: {OUT_P2B.name} — {len(p2b_metrics)} строк, "
          f"{OUT_P2B_O1.name} — {len(p2b_o1)} строк")

    out_manifest = {
        "created_at": stamp, "procedure": SERIES, "status": "exploratory",
        "seed": SEED, "c_grid": C_GRID, "inner_folds": INNER_FOLDS,
        "features_core": FEATURES_CORE, "features_structural": FEATURES_STRUCTURAL,
        "m02_policy": "вне основного варианта; sensitivity — модель main+M02 с "
                      "импутацией медианой train",
        "inner_selection_metric": "ROC AUC",
        "post_hoc_decisions": [
            {"what": "негативный контроль считается как серия из "
                     f"{NEG_CONTROL_PERMUTATIONS} кластерных перестановок",
             "when": "2026-07-28, после первого прогона",
             "why": "одна перестановка при четырёх машинных кластерах инвертировала "
                    "сигнал вместо его снятия: AUROC уходил к 0.01 вместо 0.5"},
            {"what": "на одноклассовых тестах считается FPR, остальные метрики "
                     "остаются пустыми",
             "when": "2026-07-28, после первого прогона",
             "why": "пять holdout из восемнадцати имеют целиком человеческий тест; "
                    "молчаливый пропуск скрывал бы цену ложного обвинения на "
                    "невиданном человеческом жанре"},
        ],
        "first_run_preserved": "07-analysis/_clf-v1-run1-before-posthoc/",
        "inputs": {"documents-registry.csv": sha256_file(DOCUMENTS),
                   MATRIX.name: sha256_file(MATRIX),
                   "score-v1-pairs.csv": sha256_file(PAIRS),
                   f"{PREP_MANIFEST.parent.name}/manifest.csv": sha256_file(PREP_MANIFEST)},
        "splits": {p.name: sha256_file(p) for p in sorted(SPLITS.glob("holdout_*.json"))},
        "code_sha256": sha256_file(Path(__file__)),
        "bootstrap_reps": BOOTSTRAP, "wild_cluster_reps": WILD_REPS,
    }
    if SERIES != "clf-v1":
        # Разбиения P2b перенесены из серии v1; хеши обоих файлов переноса
        # фиксируются здесь, потому что своё разбиение в v2 не строится.
        out_manifest["carried_splits"] = {
            CARRIED_OUTER.name: sha256_file(CARRIED_OUTER),
            CARRIED_INNER.name: sha256_file(CARRIED_INNER),
            CARRIED_P2A.name: sha256_file(CARRIED_P2A),
        }
        out_manifest["inner_selection_source"] = (
            "перенесённое соответствие группа → inner fold в P2a и P2b; "
            "построение своего разбиения и fallback на GroupKFold запрещены")
        out_manifest["c_rebuild_diagnostic"] = {
            "what": "рядом с рабочим C записан c_rebuilt — значение, которое дало "
                    "бы разбиение, построенное заново на составе prep-v5",
            "rows_compared": sum(1 for r in p2a_metrics if "c_rebuilt" in r),
            "rows_changed": sum(int(r.get("c_changed_by_rebuild") or 0)
                                for r in p2a_metrics),
            "decision": "PI, 2026-07-29: перенести и сверить",
        }
    OUT_MANIFEST.write_text(json.dumps(out_manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"  манифест: {OUT_MANIFEST.name}")

    main_rows = [r for r in p2a_metrics if r["model"] == "main" and r["estimand"] == "full"]
    report = [
        f"# Процедура 2, {SERIES}: результаты замороженного прогона", "",
        f"Запуск {stamp}, seed {SEED}. Хеши входов, разбиений и кода — "
        f"`{OUT_MANIFEST.name}`.", "",
        "**Статус — exploratory.** P2a — корпусный baseline, подверженный жанровому "
        "и источниковому конфаундингу. Формулировка «точность обнаружения машинного "
        "текста» к нему неприменима.", "",
        "## Два решения, принятых после первого прогона", "",
        "Первый прогон сохранён целиком в `_clf-v1-run1-before-posthoc/`. Оба "
        "изменения помечены post hoc и касаются протоколирования, а не выбора "
        "модели или признаков.", "",
        f"1. **Негативный контроль** считается как серия из {NEG_CONTROL_PERMUTATIONS} "
        "кластерных перестановок. Одна перестановка при четырёх машинных кластерах "
        "против 124 человеческих сигнал не снимала, а инвертировала: AUROC уходил "
        "к 0.01 вместо 0.5, и число читалось бы как сильный эффект наоборот.",
        "2. **На одноклассовых тестах считается FPR.** Пять holdout из восемнадцати "
        "имеют целиком человеческую тестовую часть — жанры `news`, `prose`, "
        "`science`, `translation` машинных документов не содержат, а временной срез "
        "выносит период без генераций. AUROC, MCC и TPR там неопределимы; FPR "
        "определим и показывает цену ложного обвинения на невиданном жанре.", "",
        "## P2a: основная модель против диагностических baseline", "",
        "| Holdout | Модель | Estimand | AUROC | MCC | Balanced acc. | TPR@1%FPR | FPR | Статус |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    def fmt(value):
        return "—" if value is None or value == "" else f"{float(value):.3f}"

    for r in sorted(p2a_metrics, key=lambda r: (r["split"], r["model"], r["estimand"])):
        status = r.get("status", "")
        if r["model"] == "negative-control":
            status += (f"; {r.get('n_permutations')} перестановок, разброс "
                       f"{fmt(r.get('auroc_min'))}–{fmt(r.get('auroc_max'))}")
        report.append(f"| {r['split']} | {r['model']} | {r['estimand']} | "
                      f"{fmt(r['auroc'])} | {fmt(r.get('mcc'))} | "
                      f"{fmt(r.get('balanced_accuracy'))} | {fmt(r.get('tpr_at_1pct_fpr'))} | "
                      f"{fmt(r.get('fpr'))} | {status} |")
    if SERIES != "clf-v1":
        compared = [r for r in p2a_metrics if "c_rebuilt" in r]
        changed = [r for r in compared if r.get("c_changed_by_rebuild")]
        report += ["", "### Сверка подбора C: перенос против пересборки", "",
                   "Разбиение подбора регуляризации перенесено из clf-v1 "
                   "(`p2a-inner-carry.md`). Колонка `c_rebuilt` показывает "
                   "значение, которое дало бы разбиение, построенное заново на "
                   "составе prep-v5.", "",
                   f"Сверено строк: {len(compared)}; выбранный C отличался бы у "
                   f"**{len(changed)}**."]
        if changed:
            report += ["", "| Holdout | Модель | Estimand | C перенос | C пересборка |",
                       "|---|---|---|---|---|"]
            for r in sorted(changed, key=lambda r: (r["split"], r["model"],
                                                    r["estimand"])):
                report.append(f"| {r['split']} | {r['model']} | {r['estimand']} | "
                              f"{r['C']} | {r['c_rebuilt']} |")
    report += ["", "**Как читается разрыв с baseline.** Если genre-only или source-only "
                   "приближается к основной модели, вывод о происхождении не делается: "
                   "результат публикуется как разделение жанров и источников.", "",
               "## P2b: четыре внешних fold-а", "",
               "| Estimand | Fold | Удержанный канал | AUROC | MCC | TPR@1%FPR | N теста |",
               "|---|---|---|---|---|---|---|"]
    for r in p2b_metrics:
        report.append(f"| {r['estimand']} | {r['split']} | {r['held_out_channel']} | "
                      f"{fmt(r['auroc'])} | {fmt(r['mcc'])} | "
                      f"{fmt(r['tpr_at_1pct_fpr'])} | {r['n_test']} |")
    report += ["", "**Ограничение публикуется всегда:** четыре машинных source-кластера "
                   "дают слабую оценку межканальной обобщаемости.", "",
               "## P2b: контрасты O1 внутри SEO", "",
               "| Estimand | Контраст | Пар | Кластеров | Средняя разница вероятности | "
               "95% CI | p wild cluster | p sign-flip |",
               "|---|---|---|---|---|---|---|---|"]
    for r in p2b_o1:
        report.append(f"| {r['estimand']} | {r['contrast']} | {r['n_pairs']} | "
                      f"{r['n_clusters']} | {r['mean_diff_prob']} | "
                      f"[{r['ci_low']}; {r['ci_high']}] | {r['p_wild_cluster']} | "
                      f"{r['p_cluster_sign_flip']} |")
    report += ["", "**120 пар не равны 120 независимым наблюдениям:** это 15 кластеров-"
                   "заданий. Сильные выводы только из обычного bootstrap не делаются.", "",
               f"Основная модель P2a посчитана на {len(main_rows)} holdout-разбиениях.", ""]
    OUT_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}")

    if SERIES != "clf-v1":
        import preflight_v2_run as gate  # noqa: PLC0415 — только для серии v2
        gate.emit_child_manifest(
            SERIES,
            inputs=[DOCUMENTS, MATRIX, PAIRS, PREP_MANIFEST,
                    CARRIED_OUTER, CARRIED_INNER],
            outputs=[OUT_P2A, OUT_P2A_SLICES, OUT_P2B, OUT_P2B_O1,
                     OUT_MANIFEST, OUT_REPORT])
        print(f"  дочерний манифест: manifests-v2/{SERIES}-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
