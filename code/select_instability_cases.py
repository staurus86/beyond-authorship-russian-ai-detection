#!/usr/bin/env python3
"""Отбор нестабильных случаев instability-v1: 30 карточек по стресс-тесту P2.

Спецификация — `07-analysis/instability-v1-spec.md`, зафиксирована до отбора.
Форма карточки — `07-analysis/error-analysis-protocol.md`, класс `instability`.

    python 09-tools/select_instability_cases.py

**Статус — exploratory.** Отбор идёт по оценкам процедуры 2, имеющей тот же
статус.

Скрипт не выносит вердиктов. Он сортирует 600 ячеек `документ × преобразование`
по правилу §2 спецификации, берёт первые тридцать и заполняет поля, выводимые из
файлов прогона: какие holdout сменили решение и в какую сторону, какие признаки
сдвинулись сильнее всего, жанр, режим, длина, канал. Поля «альтернативное
объяснение», «реальная редакторская проблема» и «вывод» остаются человеку.

`shifted_features` описывает сдвиг входа, а не причину смены решения: атрибуция
решения требует коэффициентов модели и в этом блоке не делается (§6 спецификации).
"""

import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stress_transforms as st  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# Принятая попытка прогона P2. Путь задан явно и один раз: ровно из-за
# дублирования константы по скриптам 30 июля два прогона прочитали каталог
# прежней ревизии (`stress_paths.py`).
ATTEMPT = ROOT / "07-analysis" / "stress-r5-attempts" / "20260731T220132Z"
CELLS = ATTEMPT / "stress-p2a-r11-cells.csv"
SCORES = ATTEMPT / "stress-p2a-r11-scores.csv"
FEATURES = ATTEMPT / "stress-p2a-r11-features.csv"

MATRIX_V5 = ROOT / "06-features" / "feature-matrix-v5.csv"
PANEL = ROOT / "07-analysis" / "stress-panel-v1.csv"

OUT_CSV = ROOT / "07-analysis" / "instability-v1-cases.csv"
OUT_CASES = ROOT / "07-analysis" / "instability-v1-cases.md"
OUT_MANIFEST = ROOT / "07-analysis" / "instability-v1-manifest.json"

N_CASES = 30
TOP_FEATURES = 5

# Те же 22 признака, что читает модель estimand «full» (stress_run_p2.FEATURES_FULL).
FEATURES_FULL = ["L01", "L02", "L04", "L05", "S01", "S02", "S03", "S06", "S08",
                 "R01", "M01", "D04", "D05", "C01", "C02", "F04", "F05", "F06",
                 "F01", "R06", "R07", "P01"]

FIELDS = ["case_id", "document_id", "transform_number", "transform_name", "error_class",
          "flip_rate", "instability_rate", "max_abs_delta", "mean_delta_prob", "n_models",
          "flipped_splits", "flip_direction", "shifted_features",
          "origin_class", "genre", "prompt_condition", "generation_channel",
          "word_count", "length_tertile",
          "alternative_explanation", "real_editorial_problem", "conclusion",
          "reviewed_by", "reviewed_at"]


def read_rows(path, encoding="utf-8"):
    with path.open(encoding=encoding) as fh:
        return list(csv.DictReader(fh))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sort_key(row):
    """Правило §2: flip по убыванию, max|Δ| по убыванию, затем детерминация."""
    return (-float(row["flip_rate"]),
            -float(row["max_abs_delta"]),
            row["document_id"],
            int(row["transform_number"]))


def select(cells, n=N_CASES):
    """Первые n ячеек по правилу §2. Возвращает (набор, граничное flip_rate)."""
    ordered = sorted(cells, key=sort_key)
    chosen = ordered[:n]
    boundary = float(chosen[-1]["flip_rate"]) if chosen else None
    return chosen, boundary


def zstats(matrix_rows):
    """Среднее и стандартное отклонение каждого признака по матрице v5."""
    values = defaultdict(list)
    for r in matrix_rows:
        fid = r["feature_id"]
        if fid not in FEATURES_FULL:
            continue
        raw = r["normalized_value"] or r["raw_value"]
        if raw:
            values[fid].append(float(raw))
    return {fid: (fmean(v), pstdev(v)) for fid, v in values.items() if len(v) > 1}


def baseline_values(matrix_rows):
    """Значения 22 признаков исходных документов: {document_id: {fid: value}}."""
    out = defaultdict(dict)
    for r in matrix_rows:
        fid = r["feature_id"]
        if fid not in FEATURES_FULL:
            continue
        raw = r["normalized_value"] or r["raw_value"]
        if raw:
            out[r["document_id"]][fid] = float(raw)
    return out


def shifted_features(base, transformed, stats, top=TOP_FEATURES):
    """Топ признаков по |Δz| между исходной и преобразованной версией.

    Признак пропускается, если значения нет у одной из версий или разброс по
    корпусу нулевой: делить на такое стандартное отклонение нельзя.
    """
    shifts = []
    for fid in FEATURES_FULL:
        if fid not in base or fid not in transformed:
            continue
        mean, sd = stats.get(fid, (0.0, 0.0))
        if sd == 0:
            continue
        dz = (transformed[fid] - base[fid]) / sd
        if dz:
            shifts.append((abs(dz), fid, dz))
    shifts.sort(key=lambda t: (-t[0], t[1]))
    return "; ".join(f"{fid} Δz={dz:+.2f}" for _, fid, dz in shifts[:top])


def flips_of_cell(score_rows):
    """Holdout со сменой решения: описание перехода и его сторона.

    Сторона считается по преобразованному тексту: `H→A` — документ, который
    модель считала человеческим, стал машинным.
    """
    flipped, directions = [], set()
    for r in sorted(score_rows, key=lambda r: r["split_name"]):
        before, after = float(r["prob_baseline"]), float(r["prob_transformed"])
        if (before > 0.5) == (after > 0.5):
            continue
        flipped.append(f"{r['split_name']}: {before:.3f}→{after:.3f}")
        directions.add("H→A" if after > 0.5 else "A→H")
    return "; ".join(flipped), "; ".join(sorted(directions))


def tertiles(panel):
    """Границы терцилей длины по 60 панельным документам."""
    words = sorted(int(r["word_count"] or 0) for r in panel)
    n = len(words)
    return words[n // 3], words[2 * n // 3]


def length_note(words, low, high):
    band = "короткие" if words <= low else "средние" if words <= high else "длинные"
    return band


def build_cards(chosen, scores, features, base, stats, panel, low, high):
    by_cell_scores = defaultdict(list)
    for r in scores:
        if r["status"] != "ok":
            continue
        by_cell_scores[(r["document_id"], int(r["transform_number"]))].append(r)

    feat_by_cell = {(r["document_id"], int(r["transform_number"])): r for r in features}
    meta = {r["document_id"]: r for r in panel}

    cards = []
    for i, cell in enumerate(chosen, start=1):
        doc, number = cell["document_id"], int(cell["transform_number"])
        name = st.TRANSFORMS[number][0]
        flipped, direction = flips_of_cell(by_cell_scores[(doc, number)])

        row = feat_by_cell.get((doc, number), {})
        transformed = {fid: float(row[fid]) for fid in FEATURES_FULL
                       if row.get(fid) not in (None, "")}
        info = meta.get(doc, {})
        words = int(info.get("word_count") or 0)

        cards.append({
            "case_id": f"INST-{i:03d}",
            "document_id": doc,
            "transform_number": number,
            "transform_name": name,
            "error_class": "instability",
            "flip_rate": cell["flip_rate"],
            "instability_rate": cell["instability_rate"],
            "max_abs_delta": cell["max_abs_delta"],
            "mean_delta_prob": cell["mean_delta_prob"],
            "n_models": cell["n_models"],
            "flipped_splits": flipped,
            "flip_direction": direction,
            "shifted_features": shifted_features(base.get(doc, {}), transformed, stats),
            "origin_class": cell["origin_class"],
            "genre": cell["genre"],
            "prompt_condition": info.get("prompt_condition", ""),
            "generation_channel": info.get("generation_channel", ""),
            "word_count": words,
            "length_tertile": length_note(words, low, high),
            "alternative_explanation": "",
            "real_editorial_problem": "",
            "conclusion": "pending",
            "reviewed_by": "",
            "reviewed_at": "",
        })
    return cards


def write_cases_md(cards, boundary):
    lines = ["# Нестабильные случаи instability-v1", "",
             f"Отобрано {len(cards)} ячеек `документ × преобразование` правилом §2 "
             "`instability-v1-spec.md`, зафиксированным до отбора. Источник — "
             "прогон P2 ревизии r11, попытка `20260731T220132Z`.", "",
             f"Граничное значение `flip_rate` в наборе — {boundary:.3f}.", "",
             "Поля «альтернативное объяснение», «реальная редакторская проблема» и "
             "«вывод» заполняет человек по тексту документа. Карточка со значением "
             "`conclusion = pending` выводом не считается.", ""]

    by_transform = defaultdict(int)
    for c in cards:
        by_transform[c["transform_number"]] += 1
    lines += ["## Состав набора", "",
              "| Преобразование | Ячеек |", "|---|---|"]
    for number in sorted(by_transform):
        lines.append(f"| t{number:02d} {st.TRANSFORMS[number][0]} | {by_transform[number]} |")
    lines.append("")

    for c in cards:
        lines += [f"## {c['case_id']} · {c['document_id']} · "
                  f"t{c['transform_number']:02d} {c['transform_name']}", "",
                  f"- класс происхождения: {c['origin_class']}, жанр: {c['genre']}, "
                  f"канал: {c['generation_channel'] or 'н/п'}, "
                  f"режим: {c['prompt_condition'] or 'н/п'}",
                  f"- длина: {c['word_count']} слов, терциль {c['length_tertile']}",
                  f"- моделей в ячейке: {c['n_models']}, "
                  f"flip_rate {c['flip_rate']}, instability_rate {c['instability_rate']}, "
                  f"max|Δp| {float(c['max_abs_delta']):.4f}",
                  f"- сменили решение: {c['flipped_splits'] or 'нет'}",
                  f"- сторона перехода: {c['flip_direction'] or 'н/п'}",
                  f"- сдвиг входа: {c['shifted_features'] or 'нет данных'}",
                  f"- вывод: {c['conclusion']}", ""]
    OUT_CASES.write_text("\n".join(lines), encoding="utf-8")


def main():
    print("Отбор нестабильных случаев instability-v1")
    cells = read_rows(CELLS)
    scores = read_rows(SCORES)
    features = read_rows(FEATURES)
    panel = read_rows(PANEL)
    matrix = read_rows(MATRIX_V5)
    print(f"  ячеек: {len(cells)}, оценок: {len(scores)}, панель: {len(panel)}")

    if len(cells) != 600:
        raise SystemExit(f"ОСТАНОВ: ожидалось 600 ячеек, прочитано {len(cells)}")

    chosen, boundary = select(cells)
    zero_flip = [c for c in chosen if float(c["flip_rate"]) <= 0]
    if zero_flip:
        raise SystemExit(f"ОСТАНОВ: в наборе {len(zero_flip)} ячеек без смены решения, "
                         "класс instability не наполнен (§2 спецификации)")

    stats = zstats(matrix)
    base = baseline_values(matrix)
    low, high = tertiles(panel)
    cards = build_cards(chosen, scores, features, base, stats, panel, low, high)

    empty_flips = [c for c in cards if not c["flipped_splits"]]
    if empty_flips:
        raise SystemExit(f"ОСТАНОВ: у {len(empty_flips)} карточек не найдено ни одного "
                         "holdout со сменой решения — расходятся cells.csv и scores.csv")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(cards)
    write_cases_md(cards, boundary)

    by_transform = defaultdict(int)
    by_origin = defaultdict(int)
    for c in cards:
        by_transform[f"t{c['transform_number']:02d}"] += 1
        by_origin[c["origin_class"]] += 1

    manifest = {
        "series": "instability-v1",
        "status": "exploratory",
        "spec": "07-analysis/instability-v1-spec.md",
        "rule": ("сортировка по flip_rate убыв., max_abs_delta убыв., document_id возр., "
                 "transform_number возр.; первые 30"),
        "source_attempt": ATTEMPT.name,
        "procedure_revision": "r11",
        "n_cases": len(cards),
        "boundary_flip_rate": boundary,
        "documents": len({c["document_id"] for c in cards}),
        "by_transform": dict(sorted(by_transform.items())),
        "by_origin": dict(sorted(by_origin.items())),
        "length_tertile_bounds": {"low": low, "high": high},
        "checks": {
            "cells_read": len(cells),
            "all_cases_have_flip": True,
            "all_cases_have_flipped_splits": True,
        },
        "inputs_sha256": {p.name: sha256(p) for p in
                          (CELLS, SCORES, FEATURES, PANEL, MATRIX_V5)},
        "code_sha256": {Path(__file__).name: sha256(Path(__file__)),
                        "stress_transforms.py": sha256(ROOT / "09-tools" / "stress_transforms.py")},
        "outputs": {"cases_csv": OUT_CSV.name, "cases_md": OUT_CASES.name},
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    print(f"  отобрано: {len(cards)}, документов {manifest['documents']}, "
          f"граничное flip_rate {boundary:.3f}")
    print(f"  по преобразованиям: {manifest['by_transform']}")
    print(f"  записано: {OUT_CSV.name}, {OUT_CASES.name}, {OUT_MANIFEST.name}")


if __name__ == "__main__":
    main()
