#!/usr/bin/env python3
"""Стресс-тест, процедура 2a: классификатор на 660 преобразованных текстах.

    python 09-tools/stress_run_p2.py --stage features   # извлечение признаков
    python 09-tools/stress_run_p2.py --stage score      # восстановление моделей и дельта

Этап features (требует кэшей stanza/embed/ner от stress_run_p1.py):
  Извлекает 22 признака для 660 стресс-текстов → stress-p2a-features.csv.

Этап score:
  Для каждого из 18 holdout-разбиений clf-v2-valid:
    — восстанавливает модель с теми же train-ID, carried inner folds,
      выбранным C, scaler, признаками и solver;
    — генерирует held-out P(AI) → stress-p2a-baseline-scores.csv;
    — верифицирует детерминизм полным вторым прогоном (max|Δ| < 1e-8).

  **Шлюз воспроизводимости жёсткий.** При max|Δ| >= 1e-8 либо расхождении
  бинарных решений прогон останавливается: манифест получает
  `status: blocked`, `reason: frozen_model_not_reproducible`, файл
  stress-p2a-scores.csv **не создаётся**, код возврата ненулевой.
  Диагностические файлы реконструкции сохраняются.

  **Число строк не равно 660.** Holdout-разбиения диагностические и
  пересекаются: панельный документ входит в test от одной до 18 моделей
  (18 документов `human_hard_rusltc_*` никогда не в train). Каждый
  преобразованный вариант оценивается ВСЕМИ соответствующими моделями;
  произвольный выбор одного split_name запрещён. Ожидаемое число строк —
  11 × Σ eligible_split_count, аудит в stress-p2a-eligible-holdouts.csv.
  При своде повторные оценки одного документа не считаются независимыми.

  Выходные файлы:
    stress-p2a-eligible-holdouts.csv — document → eligible модели (аудит)
    stress-p2a-baseline-scores.csv   — held-out P(AI), по holdout
    stress-p2a-scores.csv            — delta_prob, по holdout
    stress-p2a-manifest.json
"""

import argparse, csv, gzip, hashlib, json, math, re, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import feature_cache as fc
import extract_features as ef
import extract_semantic as es
import extract_ner as en
import extract_discourse as disc
import extract_artifacts as art
import stress_transforms as st
import stress_paths as sp

PANEL       = sp.PANEL
# Матрица серии v2. Переход на v5-r2 отменён: та матрица собрана пересчётом по
# профилям prep-v4 и содержит неверные D04 и D05, тогда как здесь они посчитаны
# по prep-v5 и воспроизводятся пересчётом побитово
# (`07-analysis/rollback-decision-2026-08-01-d04-d05.json`).
MATRIX_V5   = ROOT / "06-features" / "feature-matrix-v5.csv"
REGISTRY    = ROOT / "04-corpus" / "documents-registry.csv"
SPLITS_V5   = ROOT / "07-analysis" / "splits-v5"
VALID_P2A   = ROOT / "07-analysis" / "splits-v5" / "p2a-inner-folds-valid.json"
# Каталог входов и метка ревизии — только из stress_paths (амендмент r5,
# изменение 2).
TEXTS       = sp.TEXTS
PANEL_MANIFEST = sp.MANIFEST
ORIG_PROSE  = ROOT / "04-corpus" / "derived" / "prep-v5" / "prose"
ORIG_FULL   = ROOT / "04-corpus" / "derived" / "prep-v5" / "full"
PREP_MANIFEST = ROOT / "04-corpus" / "derived" / "prep-v5" / "manifest.csv"
CACHE       = ROOT / "06-features" / "cache" / "stress-stanza-v2"
EMBED_CACHE = ROOT / "06-features" / "cache" / "stress-embed-v2"
NER_CACHE   = ROOT / "06-features" / "cache" / "stress-ner-v2"

OUT_FEATURES = sp.analysis("p2a", "features.csv")
OUT_ELIGIBLE = sp.analysis("p2a", "eligible-holdouts.csv")
OUT_BASELINE = sp.analysis("p2a", "baseline-scores.csv")
OUT_CSV      = sp.analysis("p2a", "scores.csv")
OUT_CELLS    = sp.analysis("p2a", "cells.csv")
OUT_DOCS     = sp.analysis("p2a", "by-document.csv")
OUT_HOLDOUT  = sp.analysis("p2a", "by-holdout.csv")
OUT_JSON     = sp.analysis("p2a", "manifest.json")

# Таблица хешей ревизии: вход сверяется до расчёта, а не только записывается
# в манифест после него.
HASHFILE    = ROOT / "07-analysis" / f"stress-{sp.PROCEDURE_REVISION.get('p2a', sp.REVISION)}-code.sha256.md"

# Всё зафиксировано amendment-p2-stress-units.md (2026-07-30) до этапа score.
AMENDMENT = ROOT / "02-preregistration" / "amendment-p2-stress-units.md"
VERIFY_TOL   = 1e-8    # допуск воспроизведения held-out вектора
SENTINEL_TOL = 1e-8    # допуск |delta_prob| у applied_no_change
DELTA_THRESHOLD = 0.05  # §3: порог нестабильности на шкале вероятности

# Те же наборы признаков, что в clf_run.py (proc2-classifier-spec.md §6).
# Estimand «full»: CORE + STRUCTURAL (22 признака), без M02.
FEATURES_CORE       = ["L01", "L02", "L04", "L05", "S01", "S02", "S03", "S06", "S08",
                       "R01", "M01", "D04", "D05", "C01", "C02", "F04", "F05", "F06"]
FEATURES_STRUCTURAL = ["F01", "R06", "R07", "P01"]
FEATURES_FULL       = FEATURES_CORE + FEATURES_STRUCTURAL   # 22 признака

C_GRID                 = [0.01, 0.1, 1.0, 10.0]
INNER_FOLDS            = 3
SEED                   = 20260727
INNER_SELECTION_METRIC = "ROC AUC"   # зафиксирована до расчёта

# Адресация кэша эмбеддингов берётся из stress_run_p1, а не дублируется: ключ,
# ревизия и способ считать хеш модельного входа обязаны совпадать у P1 и P2,
# иначе один и тот же вход адресуется двумя разными записями.
import stress_run_p1 as srp1  # noqa: E402

_EMBED_REVISION = srp1._EMBED_REVISION
_EMBED_KEY      = srp1._EMBED_KEY
PANEL_COUNTER_KEYS = srp1.PANEL_COUNTER_KEYS
_NER_REVISION   = "natasha 1.6.0/slovnet 0.6.0/navec 0.10.0"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file_safe(path):
    """Хеш или метка недоступности. Нужен в blocked_exit: манифест провала
    обязан записаться даже если часть входов пропала."""
    try:
        return sha256_file(path)
    except OSError as exc:
        return f"unavailable: {exc.__class__.__name__}"


def quantize_features(values):
    """Приводит признаки к точности, с которой обучалась замороженная модель.

    `feature-matrix-v5.csv` хранит значения как `%.6g` — модель, медианы
    импутации и параметры StandardScaler получены именно на них. Пересчитанные
    из панели признаки имеют полную точность, и подача их напрямую меняет вход
    модели относительно серии v2. Разница хвостов доходила до 0.538 по
    вероятности при неизменённом тексте.

    Пропуск остаётся пропуском: импутация идёт после квантизации, как в
    оригинальном прогоне. Амендмент r5 о входном контракте P2.
    """
    out = {}
    for key, value in values.items():
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number or number in (float("inf"), float("-inf")):
            continue
        out[key] = float(format(number, ".6g"))
    return out


# Признаки sem-v1 считаются на эмбеддингах bge-m3, которые воспроизводятся до
# шестой значащей цифры (retest-report.md: M01 до 1.00e-07, M02 до 1.00e-07,
# M05 до 1.00e-06 по величине). Матрица хранит `%.6g`, поэтому допуск инварианта
# для них — единица последнего разряда (амендмент stress-r9).
EMBEDDING_FEATURES = ("M01", "M02", "M05")


def last_digit_equal(a, b):
    """Совпадение с точностью до единицы последнего разряда представления %.6g."""
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    if scale == 0:
        return False
    ulp = 10 ** (math.floor(math.log10(scale)) - 5)
    return abs(a - b) <= ulp * (1 + 1e-9)


def read_csv_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


# ── Функции clf-v2-valid — скопированы из clf_run.py verbatim ─────────────────

def make_model(c):
    return Pipeline([("scale", StandardScaler()),
                     ("lr", LogisticRegression(C=c, penalty="l2", max_iter=5000,
                                               class_weight="balanced",
                                               random_state=SEED))])


def carried_folds(y, groups, assignments, strict=True):
    """Индексы train/validation по перенесённому назначению групп.
    Копия clf_run.py: нельзя отклоняться."""
    missing = sorted(set(groups) - set(assignments))
    if missing:
        raise SystemExit("нет перенесённого inner fold у групп train: "
                         + ", ".join(missing[:10])
                         + (" …" if len(missing) > 10 else ""))
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


def pick_c(x, y, groups, assignments, strict=True):
    """Вложенный подбор силы регуляризации (копия clf_run.py, ветка с assignments).

    В стресс-тесте assignments всегда задан: fallback на GroupKFold запрещён.
    Возвращает (c, inner_auc, accounting).
    """
    inner = carried_folds(y, groups, assignments, strict)
    usable = [i for i, (tr, va) in enumerate(inner)
              if len(set(y[tr])) > 1 and len(set(y[va])) > 1]
    accounting = {"total": len(inner), "used": len(usable)}
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


def impute_train_test(x_train, x_test):
    """§3.3: медиана считается на train fold и переносится на test и стресс-тексты.

    Расширено относительно clf_run.py: третьим значением возвращаются медианы
    для последующего скоринга стресс-текстов той же моделью.
    """
    medians = np.nanmedian(x_train, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)
    return (np.where(np.isnan(x_train), medians, x_train),
            np.where(np.isnan(x_test),  medians, x_test),
            medians)


# ── Воспроизведение одного holdout-разбиения ─────────────────────────────────

def reproduce_holdout(split, all_values, docs_by_id, p2a_splits):
    """Обучает модель для одного holdout, возвращает held-out P(AI).

    Воспроизводит clf-v2-valid строка в строку:
      - те же train-ID и test-ID из файла holdout;
      - assignments из p2a-inner-folds-valid.json;
      - strict=True (схема B не допускает одноклассовых inner fold-ов).

    Возвращает (model, medians, [(doc_id, prob), ...], c_selected)
    или (None, None, [], None), если данных недостаточно.
    """
    split_name = split["split_name"]
    train_ids = [d for d in split["train"]
                 if d in docs_by_id and d in all_values]
    test_ids  = [d for d in split["test"]
                 if d in docs_by_id and d in all_values]
    if not train_ids or not test_ids:
        return None, None, [], None
    if len(set(1 if docs_by_id[d]["origin_class"] == "A" else 0
               for d in train_ids)) < 2:
        return None, None, [], None

    X_train = np.array([[all_values[d].get(f, np.nan) for f in FEATURES_FULL]
                        for d in train_ids])
    X_test  = np.array([[all_values[d].get(f, np.nan) for f in FEATURES_FULL]
                        for d in test_ids])
    y_train = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0
                        for d in train_ids])

    X_train_imp, X_test_imp, medians = impute_train_test(X_train, X_test)

    groups = [docs_by_id[d]["split_group_source"] or docs_by_id[d]["generation_channel"]
              for d in train_ids]
    assignments = p2a_splits[split_name]["assignments"]
    c, inner_auc, _ = pick_c(X_train_imp, y_train, groups, assignments, strict=True)

    model = make_model(c)
    model.fit(X_train_imp, y_train)
    scores = model.predict_proba(X_test_imp)[:, 1]

    return model, medians, list(zip(test_ids, scores.tolist())), c


# ── Этап features ─────────────────────────────────────────────────────────────

def extract_feature_values(parsed, full_text, manifest_row, embed_path, ner_path,
                           registry_words=None, f06_original=None):
    """Возвращает {fid: normalized_or_raw} для признаков FEATURES_FULL.

    Пять признаков из двадцати двух раньше не считались вовсе и приходили
    пропусками: D04 и D05 требуют дискурсивного слоя, F04–F06 — артефактного
    (амендмент stress-r7). Теперь считаются все.
    """
    values, _ = ef.document_features(parsed, manifest_row)
    values.update(ef.surface_features(full_text, float(manifest_row["full_words"])))

    # D04 и D05: тот же disc-v1 и та же нормировка, что у основной матрицы
    # (build_matrix_v5_r2.recompute) — на 1000 слов по Stanza-разбору.
    disc_counts = disc.document_features(parsed)
    disc_words = disc_counts.get("words", 0)
    for fid in ("D04", "D05"):
        raw = disc_counts.get(fid)
        if raw is None:
            continue
        norm = raw * 1000 / disc_words if disc_words > 0 else None
        values[fid] = (fid, raw, norm, "на 1000 слов")

    # F04 и F05: скан art-v2 по профилю full преобразованного текста. Знаменатель
    # — word_count реестра, как в матрице: на неизменённом входе значения
    # совпадают у всех 60 панельных документов.
    if registry_words:
        for fid in ("F04", "F05"):
            found = art.scan(full_text, fid)
            values[fid] = (fid, len(found), len(found) / registry_words * 1000,
                           "на 1000 слов")

    # F06 меряет обёртку канала, которую препроцессинг снимает: в профиле её нет
    # и преобразование её не создаёт. Значение переносится от исходного документа.
    if f06_original is not None:
        values["F06"] = ("F06", f06_original, f06_original, "бинарно")

    if embed_path is not None:
        with np.load(embed_path) as payload:
            vectors    = payload["embeddings"]
            kept_index = list(payload["sentence_index"])
        sem_vals, _, _ = es.document_features(
            vectors, parsed, manifest_row, kept_index)
        values.update(sem_vals)

    if ner_path is not None:
        with gzip.open(ner_path, "rt", encoding="utf-8") as fh_ner:
            spans = json.load(fh_ner)["spans"]
        words_c01 = float(manifest_row["full_words"])
        cut = en.bibliography_start(full_text)
        if cut is not None:
            spans     = [s for s in spans if s["start"] < cut]
            words_c01 -= len(full_text[cut:].split())
        if words_c01 > 0:
            groups_ner = en.glue_spans(spans)
            counts, _, _ = en.document_counts(groups_ner)
            total = sum(counts.values())
            values["C01"] = ("Именованные сущности",
                             total, total / words_c01 * 1000, "на 1000 слов")

    result = {}
    for fid in FEATURES_FULL:
        if fid in values:
            v = values[fid]
            result[fid] = v[2] if v[2] is not None else v[1]
    return result


def features_stage(rows):
    """Извлекает признаки для всех стресс-текстов → OUT_FEATURES."""
    stanza_index = fc.load_index(CACHE)
    embed_index  = fc.load_index(EMBED_CACHE)
    ner_index    = fc.load_index(NER_CACHE)

    fieldnames = ["key", "document_id", "transform_number"] + FEATURES_FULL
    total      = len(rows) * len(st.TRANSFORMS)
    position   = done = skipped = 0

    # Счётчики препроцессинга ячейки: F01, R06 и R07 экстрактор читает из них.
    # Без манифеста три признака из четырёх в FORMAT молча становились нулями
    # (stress-format-features-defect.md).
    panel_stats = {(r["document_id"], int(r["transformation_id"])): r
                   for r in read_csv_rows(PANEL_MANIFEST)}
    print(f"  манифест панели: {len(panel_stats)} ячеек")

    # Знаменатель F04 и F05 — word_count реестра, как в матрице. F06 переносится
    # от исходного документа: обёртки канала в профиле нет (амендмент r7).
    registry_words = {r["document_id"]: float(r["word_count"] or 0)
                      for r in read_csv_rows(REGISTRY, "utf-8-sig")}
    f06_original = {}
    for r in csv.DictReader(MATRIX_V5.open(encoding="utf-8")):
        if r["feature_id"] == "F06":
            value = r["normalized_value"] or r["raw_value"]
            if value:
                f06_original[r["document_id"]] = float(value)
    print(f"  F06 исходных документов: {len(f06_original)}")

    with OUT_FEATURES.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for number in sorted(st.TRANSFORMS):
            for row in rows:
                position += 1
                doc_id  = row["document_id"]
                key     = f"t{number:02d}:{doc_id}"
                prose   = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
                full    = TEXTS / f"t{number:02d}" / "full"  / f"{doc_id}.txt"
                if not prose.exists() or not full.exists():
                    skipped += 1
                    continue
                prose_sha   = fc.sha256_file(prose)
                stanza_path = fc.lookup(CACHE, stanza_index, key,
                                        prose_sha, ef.STANZA_REVISION)
                if stanza_path is None:
                    skipped += 1
                    continue
                with gzip.open(stanza_path, "rt", encoding="utf-8") as fh_gz:
                    parsed = json.load(fh_gz)
                full_text = full.read_text(encoding="utf-8")
                stats_row = panel_stats.get((doc_id, number))
                if stats_row is None:
                    raise SystemExit(
                        f"нет строки манифеста панели для {key}: пересоберите "
                        f"панель (stress_run_p1.py --stage texts) до расчёта")
                manifest_row = {
                    "prose_words": len(prose.read_text(encoding="utf-8").split()),
                    "full_words":  len(full_text.split()),
                    "full_path":   str(full.relative_to(ROOT)).replace("\\", "/"),
                    "prose_path":  str(prose.relative_to(ROOT)).replace("\\", "/"),
                    "heading_md":      stats_row["heading_md"],
                    "list_items":      stats_row["list_items"],
                    "full_bold_spans": stats_row["full_bold_spans"],
                }
                # Адрес записи — хеш модельного входа, номер преобразования в
                # него не входит: ячейка с неизменённым prose берёт ту же
                # запись, что оригинал.
                embed_path = fc.lookup(
                    EMBED_CACHE, embed_index, _EMBED_KEY,
                    srp1.model_input_digest(*srp1.model_input(parsed)),
                    _EMBED_REVISION)
                full_sha   = fc.sha256_file(full)
                ner_path   = fc.lookup(NER_CACHE, ner_index, key,
                                       full_sha, _NER_REVISION)
                feat = extract_feature_values(
                    parsed, full_text, manifest_row, embed_path, ner_path,
                    registry_words=registry_words.get(doc_id),
                    f06_original=f06_original.get(doc_id))
                rec = {"key": key, "document_id": doc_id, "transform_number": number}
                rec.update({f: feat.get(f, "") for f in FEATURES_FULL})
                writer.writerow(rec)
                done += 1
                if position % 66 == 0 or position == total:
                    print(f"  {position}/{total}, строк {done}", flush=True)

    print(f"  признаки: {OUT_FEATURES.name}, строк {done}, пропущено {skipped}")
    return done


# ── Этап score ────────────────────────────────────────────────────────────────

def write_eligible_audit(rows, splits):
    """Аудит document → eligible holdout. Пишет OUT_ELIGIBLE, возвращает карту.

    Holdout-разбиения диагностические и пересекаются по осям (автор, жанр,
    модель, промпт, источник, время, тема). Документ может быть held-out
    сразу по нескольким осям — тогда его преобразованные варианты обязаны
    оцениваться всеми соответствующими моделями.
    """
    eligible = defaultdict(list)
    for split in splits:
        for doc_id in set(split["test"]):
            eligible[doc_id].append(split["split_name"])
    for doc_id in eligible:
        eligible[doc_id].sort()

    fnames = ["document_id", "origin_class", "genre",
              "eligible_split_count", "eligible_split_names"]
    audit_rows = []
    for row in rows:
        doc_id = row["document_id"]
        names  = eligible.get(doc_id, [])
        audit_rows.append({
            "document_id":          doc_id,
            "origin_class":         row["origin_class"],
            "genre":                row.get("genre", ""),
            "eligible_split_count": len(names),
            "eligible_split_names": ";".join(names),
        })
    with OUT_ELIGIBLE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fnames)
        w.writeheader()
        w.writerows(audit_rows)

    total = sum(r["eligible_split_count"] for r in audit_rows)
    dist  = defaultdict(int)
    for r in audit_rows:
        dist[r["eligible_split_count"]] += 1
    print(f"  аудит eligible: {OUT_ELIGIBLE.name}")
    for k in sorted(dist):
        print(f"    ровно {k:2d} модель(ей): {dist[k]} документов")
    print(f"    Σ eligible_split_count = {total}, "
          f"ожидаемых строк = {len(st.TRANSFORMS)} × {total} = "
          f"{len(st.TRANSFORMS) * total}")
    if total == len(rows):
        print("    каждый документ имеет ровно одну модель — 660 подтверждено")
    return eligible, total, audit_rows


def embedding_bound(model, medians, quantized):
    """Насколько сдвигает вероятность возмущение входа в пределах допуска.

    Признаки sem-v1 воспроизводятся до шестой значащей цифры, и матрица хранит
    их как `%.6g`. Пара с неизменённым входом обязана давать нулевую разность
    вероятности — но ровно до этой границы, а не абсолютно. Граница считается
    той же моделью на том же векторе, поэтому она не назначается числом и не
    подгоняется под наблюдение (амендмент stress-r10).
    """
    base = np.array([[quantized.get(f, np.nan) for f in FEATURES_FULL]])
    base = np.where(np.isnan(base), medians, base)
    p0 = float(model.predict_proba(base)[0, 1])
    worst = 0.0
    for fid in EMBEDDING_FEATURES:
        if fid not in FEATURES_FULL:
            continue
        i = FEATURES_FULL.index(fid)
        value = base[0, i]
        if value == 0:
            continue
        ulp = 10 ** (math.floor(math.log10(abs(value))) - 5)
        # Берётся полный размах на интервале [x−ulp, x+ulp], а не односторонний
        # сдвиг: модель по признаку монотонна, поэтому размах покрывает любое
        # отличие входа в пределах допуска, включая одностороннее.
        probes = []
        for sign in (1, -1):
            probe = base.copy()
            probe[0, i] = value + sign * ulp
            probes.append(float(model.predict_proba(probe)[0, 1]))
        worst = max(worst, abs(probes[0] - probes[1]),
                    abs(probes[0] - p0), abs(probes[1] - p0))
    return worst


def score_stage(rows):
    """Восстанавливает модели clf-v2-valid, верифицирует, оценивает стресс-тексты.

    Возвращает (rows_out, n_baseline, expected_rows, max_diff, eligible_total).
    При провале воспроизводимости вызывает blocked_exit и не возвращается.
    """

    # ── 1. Загрузка признаков основного корпуса ──────────────────────────────
    print(f"  загрузка {MATRIX_V5.name} …")
    all_values = defaultdict(dict)
    for r in csv.DictReader(MATRIX_V5.open(encoding="utf-8")):
        fid = r["feature_id"]
        if fid not in FEATURES_FULL:
            continue
        raw = r["normalized_value"] or r["raw_value"]
        if raw:
            all_values[r["document_id"]][fid] = float(raw)
    print(f"  документов в матрице: {len(all_values)}")

    docs_by_id = {r["document_id"]: r
                  for r in read_csv_rows(REGISTRY, "utf-8-sig")}

    # ── 2. Carried inner fold assignments (p2a-inner-folds-valid.json) ───────
    p2a_data   = json.loads(VALID_P2A.read_text(encoding="utf-8"))
    p2a_splits = p2a_data["splits"]   # {split_name: {assignments, n_folds, ...}}
    print(f"  p2a assignments: {len(p2a_splits)} записей")

    # ── 3. Holdout splits ────────────────────────────────────────────────────
    split_files = sorted(SPLITS_V5.glob("holdout_*.json"))
    splits      = [json.loads(p.read_text(encoding="utf-8")) for p in split_files]
    print(f"  holdout-разбиений: {len(splits)}")

    # ── 4. Аудит document → eligible holdout ─────────────────────────────────
    eligible, eligible_total, _ = write_eligible_audit(rows, splits)
    expected_rows = len(st.TRANSFORMS) * eligible_total

    panel_ids = {row["document_id"] for row in rows}
    missing_p = [d for d in panel_ids if not eligible.get(d)]
    if missing_p:
        print(f"  ПРЕДУПРЕЖДЕНИЕ: панельных без holdout: {len(missing_p)}: "
              + ", ".join(sorted(missing_p)[:5]))

    # ── 5. Признаки стресс-текстов ───────────────────────────────────────────
    stress_feat = {}
    for r in read_csv_rows(OUT_FEATURES):
        stress_feat[r["key"]] = {
            fid: float(r[fid]) for fid in FEATURES_FULL
            if r.get(fid) not in ("", None)
        }
    print(f"  стресс-признаков: {len(stress_feat)}")

    # ── 6. Прогон 1: восстановление 18 моделей ───────────────────────────────
    print("  прогон 1: восстановление 18 holdout-моделей …")
    # Ключи включают split_name: документ оценивается каждой eligible моделью
    baseline_scores = {}   # (split_name, doc_id) → P(AI)
    stress_results  = {}   # (split_name, key)    → P(AI)_transformed
    sentinel_bound  = {}   # (split_name, key)    → граница инварианта
    c_registry      = {}   # split_name → C

    for split in splits:
        split_name = split["split_name"]
        model, medians, test_pairs, c = reproduce_holdout(
            split, all_values, docs_by_id, p2a_splits)
        if model is None:
            print(f"  ПРОПУСК {split_name}: нет данных")
            continue
        c_registry[split_name] = c

        for doc_id, prob in test_pairs:
            baseline_scores[(split_name, doc_id)] = prob

        # Скоринг стресс-текстов документов из test-набора этого holdout
        test_set = set(split["test"])
        for row in rows:
            doc_id = row["document_id"]
            if doc_id not in test_set:
                continue
            for number in sorted(st.TRANSFORMS):
                key = f"t{number:02d}:{doc_id}"
                if key not in stress_feat:
                    continue
                # Вход модели квантуется до её контракта; полноточные
                # значения остаются в файле признаков для аудита.
                quantized = quantize_features(stress_feat[key])
                x     = np.array([[quantized.get(f, np.nan)
                                    for f in FEATURES_FULL]])
                x_imp = np.where(np.isnan(x), medians, x)
                stress_results[(split_name, key)] = float(
                    model.predict_proba(x_imp)[0, 1])
                # Граница инварианта для этой ячейки: насколько сдвинулась бы
                # вероятность, если бы признаки на эмбеддингах отличались ровно
                # на единицу последнего разряда. Порог считается для каждой пары
                # своей моделью, а не назначается числом (амендмент stress-r10).
                sentinel_bound[(split_name, key)] = embedding_bound(
                    model, medians, quantized)

        print(f"  {split_name}: test={len(test_pairs)}, C={c}", flush=True)

    n_panel_cells = sum(1 for (_, d) in baseline_scores if d in panel_ids)
    print(f"  baseline: {len(baseline_scores)} ячеек (split × документ), "
          f"панельных {n_panel_cells}")

    # ── 7. Верификация детерминизма: полный второй прогон ────────────────────
    print(f"  прогон 2: верификация held-out вектора (max|Δ| < {VERIFY_TOL:.0e}) …")
    max_diff          = 0.0
    binary_mismatches = 0
    verified_cells    = 0
    for split in splits:
        split_name = split["split_name"]
        _, _, test_pairs2, _ = reproduce_holdout(
            split, all_values, docs_by_id, p2a_splits)
        if not test_pairs2:
            continue
        for doc_id, prob2 in test_pairs2:
            prob1 = baseline_scores.get((split_name, doc_id))
            if prob1 is None:
                continue
            verified_cells += 1
            diff = abs(prob1 - prob2)
            if diff > max_diff:
                max_diff = diff
            if (prob1 > 0.5) != (prob2 > 0.5):
                binary_mismatches += 1

    print(f"  детерминизм: сверено ячеек {verified_cells}, "
          f"max|Δ| = {max_diff:.2e}, binary_mismatches = {binary_mismatches}")
    verify_passed = max_diff < VERIFY_TOL and binary_mismatches == 0

    # ── 8. Запись диагностических baseline scores (сохраняются всегда) ───────
    baseline_fnames = ["split_name", "document_id", "origin_class",
                       "generation_channel", "prob_ai", "decision_ai", "C"]
    baseline_rows = []
    for split in splits:
        split_name = split["split_name"]
        for doc_id in sorted(set(split["test"])):
            prob = baseline_scores.get((split_name, doc_id))
            if prob is None or doc_id not in docs_by_id:
                continue
            baseline_rows.append({
                "split_name":         split_name,
                "document_id":        doc_id,
                "origin_class":       docs_by_id[doc_id]["origin_class"],
                "generation_channel": docs_by_id[doc_id]["generation_channel"],
                "prob_ai":            f"{prob:.10f}",
                "decision_ai":        "1" if prob > 0.5 else "0",
                "C":                  c_registry.get(split_name, ""),
            })
    with OUT_BASELINE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=baseline_fnames)
        w.writeheader()
        w.writerows(baseline_rows)
    print(f"  baseline записан: {OUT_BASELINE.name}, строк {len(baseline_rows)}")

    # ── 9. ШЛЮЗ ВОСПРОИЗВОДИМОСТИ ────────────────────────────────────────────
    # Провал останавливает прогон: stress-p2a-scores.csv не создаётся.
    if not verify_passed:
        blocked_exit(max_diff, binary_mismatches, verified_cells,
                     eligible_total, expected_rows, len(baseline_rows))
        # blocked_exit не возвращается

    # ── 10. Запись стресс-оценок: каждая eligible модель отдельной строкой ──
    # SHA256 оригиналов: преобразование могло не изменить текст, тогда
    # delta_prob обязана быть нулевой (шлюз 5 амендмента).
    orig_sha = {}
    for row in rows:
        orig = ORIG_PROSE / f"{row['document_id']}.txt"
        if orig.exists():
            orig_sha[row["document_id"]] = sha256_file(orig)
    no_change = set()
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            doc_id = row["document_id"]
            prose  = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
            if prose.exists() and sha256_file(prose) == orig_sha.get(doc_id):
                no_change.add((doc_id, number))
    print(f"  applied_no_change: {len(no_change)} пар документ × преобразование")

    # Полный вход признаков: prose, full и счётчики препроцессинга. Совпадения
    # одного prose мало — surface-признаки читают full, форматные читают
    # счётчики (stress-p1-r3-gate.md). Первый признак остаётся диагностикой,
    # блокирует второй.
    orig_full_sha = {}
    for row in rows:
        orig = ORIG_FULL / f"{row['document_id']}.txt"
        if orig.exists():
            orig_full_sha[row["document_id"]] = sha256_file(orig)
    prep_manifest = {r["document_id"]: r for r in read_csv_rows(PREP_MANIFEST)}
    panel_stats = {(r["document_id"], int(r["transformation_id"])): r
                   for r in read_csv_rows(PANEL_MANIFEST)}
    input_unchanged = set()
    for doc_id, number in no_change:
        full = TEXTS / f"t{number:02d}" / "full" / f"{doc_id}.txt"
        stats_row = panel_stats.get((doc_id, number))
        prep_row = prep_manifest.get(doc_id)
        if not full.exists() or stats_row is None or prep_row is None:
            continue
        if (sha256_file(full) == orig_full_sha.get(doc_id)
                and all(int(float(stats_row[k] or 0)) == int(float(prep_row[k] or 0))
                        for k in PANEL_COUNTER_KEYS)):
            input_unchanged.add((doc_id, number))
    print(f"  input_unchanged: {len(input_unchanged)} пар с неизменённым входом")

    # Первая ступень инварианта: у ячейки с неизменившимся входом квантизованный
    # вектор обязан точно совпасть со строкой матрицы по всем признакам модели.
    # Вторая ступень — нулевая разность вероятностей — проверяется в шлюзе.
    # Прежняя редакция видела только вторую и не различала, что разошлось:
    # признаки или модель (амендмент r5 о входном контракте P2).
    features_match, features_mismatch = set(), {}
    for doc_id, number in sorted(input_unchanged):
        key = f"t{number:02d}:{doc_id}"
        if key not in stress_feat or doc_id not in all_values:
            continue
        quantized = quantize_features(stress_feat[key])
        matrix = quantize_features(all_values[doc_id])
        bad = []
        for f in FEATURES_FULL:
            if (f in quantized) != (f in matrix):
                bad.append(f)
            elif f in quantized and quantized[f] != matrix[f]:
                # Точное равенство — для детерминированных признаков; для трёх
                # признаков на эмбеддингах допускается последний разряд.
                if not (f in EMBEDDING_FEATURES
                        and last_digit_equal(quantized[f], matrix[f])):
                    bad.append(f)
        if bad:
            features_mismatch[key] = bad
        else:
            features_match.add((doc_id, number))
    print(f"  из них квантизованный вектор совпал с матрицей: "
          f"{len(features_match)}, разошёлся: {len(features_mismatch)}")
    if features_mismatch:
        example = next(iter(features_mismatch.items()))
        print(f"    пример расхождения: {example[0]} по признакам "
              f"{example[1][:5]}")

    score_fnames = ["split_name", "document_id", "transform_number",
                    "origin_class", "generation_channel",
                    "prob_baseline", "prob_transformed", "delta_prob",
                    "applied_no_change", "input_unchanged",
                    "features_match", "sentinel_bound", "status"]
    rows_out = []
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            doc_id = row["document_id"]
            key    = f"t{number:02d}:{doc_id}"
            for split_name in eligible.get(doc_id, []):
                prob_b = baseline_scores.get((split_name, doc_id))
                prob_t = stress_results.get((split_name, key))
                if prob_b is None:
                    status = "no_baseline"
                elif prob_t is None:
                    status = "no_features"
                else:
                    status = "ok"
                rows_out.append({
                    "split_name":         split_name,
                    "document_id":        doc_id,
                    "transform_number":   number,
                    "origin_class":       row["origin_class"],
                    "generation_channel": row["generation_channel"],
                    "prob_baseline":      f"{prob_b:.10f}" if prob_b is not None else "",
                    "prob_transformed":   f"{prob_t:.10f}" if prob_t is not None else "",
                    "delta_prob":         (f"{prob_t - prob_b:.10f}"
                                           if prob_b is not None and prob_t is not None
                                           else ""),
                    "applied_no_change":  int((doc_id, number) in no_change),
                    "input_unchanged":    int((doc_id, number) in input_unchanged),
                    "features_match":     int((doc_id, number) in features_match),
                    "sentinel_bound":     (f"{sentinel_bound[(split_name, key)]:.6e}"
                                           if (split_name, key) in sentinel_bound
                                           else ""),
                    "status": status,
                })

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=score_fnames)
        w.writeheader()
        w.writerows(rows_out)
    ok_rows = sum(1 for r in rows_out if r["status"] == "ok")
    print(f"  оценки: {OUT_CSV.name}, строк {len(rows_out)} "
          f"(ожидалось {expected_rows}), ok={ok_rows}")

    return (rows_out, len(baseline_rows), expected_rows,
            max_diff, eligible_total, eligible)


def blocked_exit(max_diff, binary_mismatches, verified_cells,
                 eligible_total, expected_rows, n_baseline):
    """Пишет манифест status=blocked и завершает прогон с кодом 1.

    Диагностические файлы (аудит eligible, baseline scores) остаются на диске;
    stress-p2a-scores.csv не создаётся, чтобы численно готовые результаты
    нельзя было случайно включить в отчёт.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "created_at": stamp,
        "procedure":  "P2a-stress",
        "series":     "clf-v2-valid",
        "status":     "blocked",
        "reason":     "frozen_model_not_reproducible",
        "reason_detail": (
            f"held-out вектор не воспроизведён: max|Δ| = {max_diff:.3e} "
            f"при допуске {VERIFY_TOL:.0e}, расхождений бинарных решений "
            f"{binary_mismatches} из {verified_cells} сверенных ячеек"),
        "verification": {
            "method":            "полный второй прогон по всем 18 holdout",
            "verified_cells":    verified_cells,
            "max_abs_diff":      max_diff,
            "tolerance":         VERIFY_TOL,
            "binary_mismatches": binary_mismatches,
            "passed":            False,
        },
        "eligible_holdouts": {
            "sum_eligible_split_count": eligible_total,
            "expected_rows":            expected_rows,
        },
        "diagnostics_preserved": [
            OUT_ELIGIBLE.name,
            OUT_BASELINE.name if n_baseline else None,
        ],
        "scores_written": False,
        "scores_note": (
            f"{OUT_CSV.name} не создан намеренно: при провале шлюза "
            "воспроизводимости преобразованные варианты не считаются"),
        "inputs": {
            "stress-panel-v1.csv":        sha256_file_safe(PANEL),
            MATRIX_V5.name:               sha256_file_safe(MATRIX_V5),
            "stress-p2a-features.csv":    sha256_file_safe(OUT_FEATURES),
            "p2a-inner-folds-valid.json": sha256_file_safe(VALID_P2A),
        },
        "code_sha256": sha256_file_safe(Path(__file__)),
    }
    manifest["diagnostics_preserved"] = [
        x for x in manifest["diagnostics_preserved"] if x]
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print()
    print("  BLOCKED: frozen_model_not_reproducible")
    print(f"    max|Δ| = {max_diff:.3e}, допуск {VERIFY_TOL:.0e}")
    print(f"    расхождений бинарных решений: {binary_mismatches}")
    print(f"    {OUT_CSV.name} НЕ создан")
    print(f"    диагностика сохранена: {OUT_ELIGIBLE.name}, {OUT_BASELINE.name}")
    print(f"    манифест: {OUT_JSON.name}")
    raise SystemExit(1)


def aggregate(rows_out, rows_panel, eligible):
    """Свёртка по правилу amendment-p2-stress-units.md §3.

    Шаг 1-3: по каждой ячейке `документ × преобразование` усреднить по
    допустимым моделям — signed-дельту, долю |Δ| > порога и долю смен решения.
    Шаг 4: агрегировать по 60 документам с равным весом каждого.
    Шаг 5: отдельная таблица по каждому holdout.

    RusLTC с 18 моделями не получает восемнадцатикратный вес: усреднение
    внутри ячейки снимает разницу в числе моделей до свёртки по документам.

    Возвращает (cell_rows, doc_rows, holdout_rows).
    """
    # ── Шаги 1-3: ячейка `документ × преобразование` ──────────────────────────
    by_cell = defaultdict(list)
    for r in rows_out:
        if r["status"] != "ok":
            continue
        by_cell[(r["document_id"], int(r["transform_number"]))].append(r)

    meta = {row["document_id"]: row for row in rows_panel}
    cell_rows = []
    for (doc_id, number), group in sorted(by_cell.items()):
        deltas = [float(r["delta_prob"]) for r in group]
        flips  = [1 if (float(r["prob_baseline"]) > 0.5)
                       != (float(r["prob_transformed"]) > 0.5) else 0
                  for r in group]
        unstable = [1 if abs(d) > DELTA_THRESHOLD else 0 for d in deltas]
        row = meta.get(doc_id, {})
        cell_rows.append({
            "document_id":       doc_id,
            "transform_number":  number,
            "origin_class":      row.get("origin_class", ""),
            "genre":             row.get("genre", ""),
            "n_models":          len(group),
            "mean_delta_prob":   f"{statistics.fmean(deltas):.10f}",
            "instability_rate":  f"{statistics.fmean(unstable):.6f}",
            "flip_rate":         f"{statistics.fmean(flips):.6f}",
            "max_abs_delta":     f"{max(abs(d) for d in deltas):.10f}",
        })

    # ── Шаг 4: по документам, равный вес каждого ──────────────────────────────
    by_doc = defaultdict(list)
    for c in cell_rows:
        by_doc[c["document_id"]].append(c)

    doc_rows = []
    for doc_id, cells in sorted(by_doc.items()):
        row = meta.get(doc_id, {})
        doc_rows.append({
            "document_id":      doc_id,
            "origin_class":     row.get("origin_class", ""),
            "genre":            row.get("genre", ""),
            "n_eligible_models": len(eligible.get(doc_id, [])),
            "n_cells":          len(cells),
            "mean_delta_prob":  f"{statistics.fmean(float(c['mean_delta_prob']) for c in cells):.10f}",
            "instability_rate": f"{statistics.fmean(float(c['instability_rate']) for c in cells):.6f}",
            "flip_rate":        f"{statistics.fmean(float(c['flip_rate']) for c in cells):.6f}",
        })

    # ── Шаг 5: отдельная таблица по каждому holdout ───────────────────────────
    by_holdout = defaultdict(list)
    for r in rows_out:
        if r["status"] == "ok":
            by_holdout[r["split_name"]].append(r)

    holdout_rows = []
    for split_name, group in sorted(by_holdout.items()):
        deltas = [float(r["delta_prob"]) for r in group]
        flips  = [1 if (float(r["prob_baseline"]) > 0.5)
                       != (float(r["prob_transformed"]) > 0.5) else 0
                  for r in group]
        unstable = [1 if abs(d) > DELTA_THRESHOLD else 0 for d in deltas]
        holdout_rows.append({
            "split_name":       split_name,
            "n_rows":           len(group),
            "n_documents":      len({r["document_id"] for r in group}),
            "mean_delta_prob":  f"{statistics.fmean(deltas):.10f}",
            "instability_rate": f"{statistics.fmean(unstable):.6f}",
            "flip_rate":        f"{statistics.fmean(flips):.6f}",
        })

    return cell_rows, doc_rows, holdout_rows


def frozen_split_names():
    """Имена holdout-разбиений из замороженной схемы inner CV.

    Единственный источник истины — файл схемы, а не число 18 и не список,
    собранный по ходу расчёта: подмена одного holdout другим сохранила бы
    количество и прошла бы проверку на длину.
    """
    data = json.loads(VALID_P2A.read_text(encoding="utf-8"))
    return sorted(data["splits"])


def check_completion_gate(rows_out, cell_rows, doc_rows, eligible,
                          expected_rows, rows_panel, max_diff,
                          holdout_rows=None, expected_split_names=None):
    """Шлюзы amendment-p2-stress-units.md §4 плюс два условия амендмента r5.

    Возвращает (passed: bool, detail: str, checks: dict).
    Условие 4 (verify_passed) проверяется раньше и жёстче — blocked_exit;
    туда же входит равенство нулю расхождений бинарных решений.

    Добавлено амендментом r5: ни одной строки выведенного преобразования и
    полный набор holdout-строк после свёртки.
    """
    checks, fails = {}, []
    n_transforms = len(st.TRANSFORMS)

    # Выведенные преобразования не должны попасть ни в один из выходов.
    dropped_hits = {
        "rows": sum(1 for r in rows_out
                    if int(r.get("transform_number", 0)) in st.NOT_EXECUTABLE),
        "cells": sum(1 for r in cell_rows
                     if int(r.get("transform_number", 0)) in st.NOT_EXECUTABLE),
    }
    checks["not_executable_left"] = dict(dropped_hits,
                                        dropped=sorted(st.NOT_EXECUTABLE))
    if any(dropped_hits.values()):
        fails.append(f"строки выведенных преобразований: {dropped_hits}")

    # Holdout-строки после свёртки: ровно те разбиения, что заморожены в
    # схеме inner CV. Сверяется множество имён, а не их число: совпадение
    # количества не исключает подмены одного holdout другим.
    if holdout_rows is not None and expected_split_names is not None:
        got = {r.get("split_name") for r in holdout_rows}
        expected = set(expected_split_names)
        checks["holdouts"] = {"got": len(got), "expected": len(expected),
                              "missing": sorted(expected - got),
                              "unexpected": sorted(got - expected)}
        if got != expected:
            missing, extra = sorted(expected - got), sorted(got - expected)
            parts = []
            if missing:
                parts.append(f"нет holdout: {missing}")
            if extra:
                parts.append(f"лишние holdout: {extra}")
            fails.append("; ".join(parts))
        if len(holdout_rows) != len(expected):
            fails.append(f"holdout-строк {len(holdout_rows)}, "
                         f"ожидалось {len(expected)}")

    # 1. Ровно expected_rows строк
    checks["rows"] = {"got": len(rows_out), "expected": expected_rows}
    if len(rows_out) != expected_rows:
        fails.append(f"строк {len(rows_out)}, ожидалось {expected_rows}")

    # 2. Каждая пара присутствует ровно для 11 преобразований
    pair_counts = defaultdict(set)
    for r in rows_out:
        pair_counts[(r["document_id"], r["split_name"])].add(
            int(r["transform_number"]))
    expected_pairs = {(d, s) for d in eligible for s in eligible[d]
                      if d in {row["document_id"] for row in rows_panel}}
    bad_pairs = [p for p in expected_pairs
                 if len(pair_counts.get(p, set())) != n_transforms]
    missing_pairs = [p for p in expected_pairs if p not in pair_counts]
    checks["pairs"] = {"expected": len(expected_pairs),
                       "with_all_transforms": len(expected_pairs) - len(bad_pairs),
                       "incomplete": len(bad_pairs), "missing": len(missing_pairs)}
    if bad_pairs:
        fails.append(f"{len(bad_pairs)} из {len(expected_pairs)} пар без полных "
                     f"{n_transforms} преобразований")

    # 3. Нет лишних сочетаний документ × split_name вне аудита
    extra = [p for p in pair_counts if p not in expected_pairs]
    checks["extra_pairs"] = {"count": len(extra), "examples": extra[:3]}
    if extra:
        fails.append(f"{len(extra)} сочетаний документ × split_name вне аудита: "
                     f"{extra[:3]}")

    # 4. verify_passed — сюда попадаем только если шлюз уже пройден
    checks["verification"] = {"max_abs_diff": max_diff, "tolerance": VERIFY_TOL,
                              "passed": max_diff < VERIFY_TOL}
    if max_diff >= VERIFY_TOL:
        fails.append(f"воспроизводимость: max|Δ| = {max_diff:.3e} >= {VERIFY_TOL:.0e}")

    # 5. applied_no_change — диагностика, не блокирует: совпал только профиль
    # prose, а признаки читают ещё full и счётчики препроцессинга.
    # str() обязателен: в памяти поле целое, из CSV приходит строкой —
    # сравнение только со строкой молча пропускало бы шлюз на живом прогоне.
    anc = [r for r in rows_out if str(r.get("applied_no_change")) == "1"
           and r["delta_prob"] != ""]
    anc_bad = [r for r in anc if abs(float(r["delta_prob"])) > SENTINEL_TOL]
    checks["applied_no_change"] = {"rows": len(anc), "out_of_tolerance": len(anc_bad),
                                  "tolerance": SENTINEL_TOL, "blocking": False,
                                  "note": "prose совпал; full и счётчики могли измениться"}

    # 5b. input_unchanged — блокирующий: совпал весь вход признаков, значит
    # вероятность обязана воспроизвестись.
    iu_features_bad = sum(1 for r in rows_out
                          if str(r.get("input_unchanged")) == "1"
                          and str(r.get("features_match")) == "0")
    checks["input_unchanged_features"] = {"mismatched_rows": iu_features_bad}
    if iu_features_bad:
        fails.append(f"{iu_features_bad} строк input_unchanged с расхождением "
                     f"квантизованного вектора против матрицы")

    iu = [r for r in rows_out if str(r.get("input_unchanged")) == "1"
          and r["delta_prob"] != ""]
    def iu_tolerance(row):
        """Граница для строки: своя у пары, если посчитана; иначе базовый допуск."""
        bound = (row.get("sentinel_bound") or "").strip()
        return max(SENTINEL_TOL, float(bound)) if bound else SENTINEL_TOL

    iu_bad = [r for r in iu
              if abs(float(r["delta_prob"])) > iu_tolerance(r)]
    checks["input_unchanged"] = {"rows": len(iu), "out_of_tolerance": len(iu_bad),
                                 "tolerance": SENTINEL_TOL, "blocking": True}
    if not iu:
        fails.append("ни одной строки с неизменённым входом: инвариант "
                     "input_unchanged не проверен")
    if iu_bad:
        worst = max(abs(float(r["delta_prob"])) for r in iu_bad)
        fails.append(f"{len(iu_bad)} из {len(iu)} input_unchanged вне допуска "
                     f"своей ячейки: max|Δ| = {worst:.3e}")

    # 6. Первичных ячеек после агрегации ровно 660
    expected_cells = len(rows_panel) * n_transforms
    checks["cells"] = {"got": len(cell_rows), "expected": expected_cells}
    if len(cell_rows) != expected_cells:
        fails.append(f"первичных ячеек {len(cell_rows)}, "
                     f"ожидалось {expected_cells}")

    # Документов после свёртки — 60
    checks["documents"] = {"got": len(doc_rows), "expected": len(rows_panel)}
    if len(doc_rows) != len(rows_panel):
        fails.append(f"документов после свёртки {len(doc_rows)}, "
                     f"ожидалось {len(rows_panel)}")

    if fails:
        return False, "; ".join(fails), checks
    return True, (f"completed: {len(rows_out)} строк, {len(expected_pairs)} пар "
                  f"× {n_transforms} преобразований, 0 лишних сочетаний, "
                  f"max|Δ| = {max_diff:.2e}, {len(anc)} applied_no_change "
                  f"в допуске, {len(cell_rows)} ячеек → {len(doc_rows)} документов"), checks


def verify_execution_inputs():
    """Проверка обязательных входов исполнения перед стартом.

    P2 не начинает работу, пока рядом с попыткой не лежат runtime-снимок и
    запись execution-inputs, а их хеши не совпадают с записанными. Снимок
    описывает окружение, в котором расчёт пойдёт: версии библиотек, параметры
    классификатора, хеши индексов кешей и манифеста активных входов.

    Причина проверки: 31 июля P2 отработал полный расчёт без снимка, потому что
    цепочка запустила его автоматически. Результат пришлось признать
    orphaned_after_scoring — расчёт был, а доказательства условий не было.
    """
    attempt = sp.attempt_dir(create=False)
    snapshot = attempt / "p2-runtime-snapshot.json"
    inputs = attempt / "p2-execution-inputs.json"
    for path in (snapshot, inputs):
        if not path.exists():
            raise SystemExit(
                f"P2 не запускается: нет {path.name} в каталоге попытки "
                f"{attempt.name}. Снимок обязателен и снимается до старта.")
    record = json.loads(inputs.read_text(encoding="utf-8"))
    recorded = (record.get("runtime_snapshot") or {}).get("sha256")
    actual = sha256_file(snapshot)
    if recorded != actual:
        raise SystemExit(
            f"P2 не запускается: хеш снимка не совпадает с записанным — "
            f"{actual[:12]} против {str(recorded)[:12]}")
    matrix_sha = verify_matrix()
    print(f"  входы исполнения проверены: снимок {actual[:12]}…, "
          f"запись {inputs.name}")
    return {"runtime_snapshot_sha256": actual,
            "execution_inputs_sha256": sha256_file(inputs),
            "matrix_sha256": matrix_sha}


def verify_matrix():
    """Хеш матрицы обязан совпасть с зафиксированным в таблице ревизии.

    Ревизия r6 отличается от r5 ровно входом: D04 и D05 согласованы с prep-v5.
    Если под тем же именем окажется другой файл, прогон сравнит пересчитанный
    вектор с чужими значениями и снова упрётся в инвариант входа — но уже без
    видимой причины (`amendment-stress-r6-p2-matrix.md`).
    """
    actual = sha256_file(MATRIX_V5)
    if not HASHFILE.exists():
        raise SystemExit(f"P2 не запускается: нет таблицы хешей {HASHFILE.name}")
    recorded = re.findall(
        r"\|\s*.06-features/" + re.escape(MATRIX_V5.name) + r".\s*\|[^|]+\|\s*."
        r"([0-9a-f]{64}).\s*\|", HASHFILE.read_text(encoding="utf-8"))
    if not recorded:
        raise SystemExit(f"P2 не запускается: {MATRIX_V5.name} не значится "
                         f"в {HASHFILE.name}")
    if recorded[0] != actual:
        raise SystemExit(
            f"P2 не запускается: хеш {MATRIX_V5.name} не совпадает с "
            f"зафиксированным — {actual[:12]} против {recorded[0][:12]}")
    print(f"  матрица сверена: {MATRIX_V5.name}, {actual[:12]}…")
    return actual


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("features", "score"), required=True)
    args = parser.parse_args()

    execution_inputs = verify_execution_inputs()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows  = read_csv_rows(PANEL)
    print(f"  P2a стресс-тест, {stamp}, этап {args.stage}, "
          f"документов {len(rows)}, преобразований {len(st.TRANSFORMS)}")

    if args.stage == "features":
        n = features_stage(rows)
        print(f"  этап features завершён: {n} строк")
        return 0

    # ── Этап score ────────────────────────────────────────────────────────────
    if not OUT_FEATURES.exists():
        raise SystemExit(
            f"нет {OUT_FEATURES.name}: сначала запустите --stage features")

    # score_stage сам вызывает blocked_exit при провале шлюза воспроизводимости
    (rows_out, n_baseline, expected_rows,
     max_diff, eligible_total, eligible) = score_stage(rows)

    # ── Агрегация по правилу амендмента §3 ────────────────────────────────────
    print("  агрегация: ячейки → документы, отдельно по holdout …")
    cell_rows, doc_rows, holdout_rows = aggregate(rows_out, rows, eligible)
    for path, data, label in ((OUT_CELLS, cell_rows, "ячейки"),
                              (OUT_DOCS, doc_rows, "документы"),
                              (OUT_HOLDOUT, holdout_rows, "holdout")):
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
        print(f"    {label}: {path.name}, строк {len(data)}")

    # ── Шлюзы амендмента §4 ───────────────────────────────────────────────────
    gate_ok, gate_detail, gate_checks = check_completion_gate(
        rows_out, cell_rows, doc_rows, eligible, expected_rows, rows, max_diff,
        holdout_rows=holdout_rows,
        expected_split_names=frozen_split_names())
    status_str = "completed" if gate_ok else "incomplete"
    print(f"  шлюз завершения: {status_str} — {gate_detail}")

    rows_ok = sum(1 for r in rows_out if r["status"] == "ok")

    manifest = {
        "created_at":    stamp,
        "procedure":     "P2a-stress",
        "series":        "clf-v2-valid",
        "status":        status_str,
        "gate_detail":   gate_detail,
        "gate_checks":   gate_checks,
        "registration": {
            "amendment":        "02-preregistration/amendment-p2-stress-units.md",
            "amendment_sha256": sha256_file_safe(AMENDMENT),
            "hash_file":        "07-analysis/p2-stress-units.sha256.md",
            "decision_status":  ("prospective protocol clarification based on "
                                 "frozen split audit"),
            "note": (
                "расхождение с прежним допущением о 660 строках обнаружено "
                "аудитом замороженных разбиений до получения результатов P2; "
                "correction note — analysis-closure.md §6.2, прежняя запись "
                "сохранена"),
        },
        "aggregation": {
            "rule":            "amendment-p2-stress-units.md §3",
            "delta_threshold": DELTA_THRESHOLD,
            "threshold_scale": "вероятность, не шкала 0–100",
            "steps": [
                "1. mean_delta_prob = mean(delta_prob по eligible holdout) "
                "для каждой ячейки документ × преобразование",
                "2. instability_rate = mean(|delta_prob| > "
                f"{DELTA_THRESHOLD} по eligible holdout)",
                "3. flip_rate = mean(decision_changed по eligible holdout)",
                "4. свёртка по 60 документам с равным весом каждого",
                "5. таблица по каждому holdout отдельно; интервалы — "
                "бутстрап с кластеризацией по document_id",
            ],
            "files": {
                "cells":      OUT_CELLS.name,
                "documents":  OUT_DOCS.name,
                "by_holdout": OUT_HOLDOUT.name,
            },
            "n_cells":     len(cell_rows),
            "n_documents": len(doc_rows),
            "n_holdouts":  len(holdout_rows),
            "why": (
                "усреднение внутри ячейки снимает разницу в числе моделей: "
                "подгруппа human_hard_rusltc_* с 18 моделями не получает "
                "восемнадцатикратный вес против документа с одной моделью"),
        },
        "units_of_analysis": {
            "documents":              len(rows),
            "primary_cells":          len(rows) * len(st.TRANSFORMS),
            "document_holdout_pairs": eligible_total,
            "technical_rows":         len(rows_out),
            "transformation_denominator": len(st.TRANSFORMS),
            "independence_note": (
                "технические строки независимыми наблюдениями не считаются; "
                "единица наблюдения — документ"),
        },
        "panel":         "stress-panel-v1.csv",
        "documents":     len(rows),
        "transformations": sorted(st.TRANSFORMS),
        "rows":          len(rows_out),
        "rows_ok":       rows_ok,
        "rows_expected": expected_rows,
        "classifier": {
            "model":                "LogisticRegression",
            "features":             FEATURES_FULL,
            "n_features":           len(FEATURES_FULL),
            "estimand":             "full",
            "penalty":              "l2",
            "class_weight":         "balanced",
            "random_state":         SEED,
            "c_grid":               C_GRID,
            "inner_selection_metric": INNER_SELECTION_METRIC,
            "inner_fold_source":    "p2a-inner-folds-valid.json",
            "n_holdouts":           18,
            "n_baseline_cells":     n_baseline,
            "note": (
                "baseline = P(AI) из holdout-модели clf-v2-valid, для которой "
                "оригинал документа был в test-наборе; модель обучена на тех же "
                "train-ID, carried inner folds, выбранном C, scaler, признаках "
                "и solver, что в clf-v2-valid"),
        },
        "verification": {
            "method":            "полный второй прогон по всем 18 holdout",
            "max_abs_diff":      max_diff,
            "tolerance":         VERIFY_TOL,
            "binary_mismatches": 0,
            "passed":            True,
            "covers":            "held-out P(AI) по всем holdout, бинарные решения",
            "gate": (
                "жёсткий: при провале манифест получает status=blocked, "
                "reason=frozen_model_not_reproducible, stress-p2a-scores.csv "
                "не создаётся, код возврата 1"),
        },
        "eligible_holdouts": {
            "audit_file":               OUT_ELIGIBLE.name,
            "sum_eligible_split_count": eligible_total,
            "expected_rows":            expected_rows,
            "note": (
                "holdout-разбиения диагностические и пересекаются по осям; "
                "панельный документ входит в test от одной до 18 моделей "
                "(18 документов human_hard_rusltc_* никогда не в train). "
                "Каждый преобразованный вариант оценён ВСЕМИ соответствующими "
                "моделями; произвольный выбор одного split_name запрещён"),
        },
        "threshold":      "not applicable",
        "threshold_note": (
            "analysis-closure.md §6.1: порог 5.0 пункта к P2 не применяется — "
            f"шкала вероятности. Порог {DELTA_THRESHOLD} для instability_rate "
            "и flip_rate задан амендментом §3 до расчёта"),
        "outputs": {
            "eligible_audit": OUT_ELIGIBLE.name,
            "baseline":       OUT_BASELINE.name,
            "technical_rows": OUT_CSV.name,
            "cells":          OUT_CELLS.name,
            "by_document":    OUT_DOCS.name,
            "by_holdout":     OUT_HOLDOUT.name,
        },
        "execution_inputs": execution_inputs,
        "inputs": {
            "stress-panel-v1.csv":      sha256_file(PANEL),
            MATRIX_V5.name:             sha256_file(MATRIX_V5),
            "stress-p2a-features.csv":  sha256_file(OUT_FEATURES),
            "p2a-inner-folds-valid.json": sha256_file(VALID_P2A),
        },
        "splits": {
            p.name: sha256_file(p)
            for p in sorted(SPLITS_V5.glob("holdout_*.json"))
        },
        "code_sha256": sha256_file(Path(__file__)),
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  манифест: {OUT_JSON.name}")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
