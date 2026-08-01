#!/usr/bin/env python3
"""Preflight перед замороженным прогоном индекса стиля (score-v1).

Жёсткое завершение при любой ошибке: расчёт не запускается, пока все проверки
не пройдены. Требования — решение PI от 2026-07-27, спецификация
`07-analysis/scoring-spec.md`, таблицы `design-support-table.md`.

    python 09-tools/preflight_score.py

Проверяет: единицу пары O1, число полных пар, статус главного контраста,
фиксацию всех параметров расчёта. Пишет манифест запуска с хешами входов.
"""

import csv
import hashlib
import json
import re
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
SPEC = ROOT / "07-analysis" / "scoring-spec.md"
COVERAGE = ROOT / "07-analysis" / "construct-coverage.md"
SUPPORT = ROOT / "07-analysis" / "design-support-table.md"
PREP_SPEC = ROOT / "06-features" / "preprocessing-spec.md"
MANIFEST = ROOT / "07-analysis" / "score-v1-manifest.json"
PAIRS_LOG = ROOT / "07-analysis" / "score-v1-pairs.csv"
INCOMPLETE_LOG = ROOT / "07-analysis" / "score-v1-incomplete-pairs.csv"

SEED = 20260727
MAIN_CONTRAST = "P3-P1"          # §3 требования PI: главный контраст зафиксирован здесь
SECONDARY_CONTRAST = "P2-P1"
PAIR_KEYS = ("brief_id", "generation_channel", "repeat_index")

# Фиксируется здесь, до расчёта. Любое расхождение с реализацией — ошибка кода.
CONFIG = {
    "version": "score-v1",
    "seed": SEED,
    "status": "exploratory operationalized style index",
    "normalization": "genre_percentile, терцили → 0 / 0.5 / 1",
    "reference_sample": "жанр документа внутри корпуса prep-v4",
    "missing_rule": "признак без значения в среднее категории не входит; "
                    "категория без признаков исключается; веса не перераспределяются",
    "excluded_features": ["X01", "X02", "X05", "F08", "M03"],
    "excluded_categories": {
        "Morphological": "механизм — сужение разброса, признаки меряют уровень",
        "POS": "распределение POS-тегов не считалось",
        "Emotion": "механизм — сужение разброса, признак меряет уровень",
        "Psycholinguistic": "механизм — сужение разброса, признак меряет уровень",
        "Knockoff": "требует прогона нейтрального рерайта, Y01 не рассчитан",
    },
    "variants": ["common", "format", "common+format", "O1-full", "O1-net"],
    "confirmatory": ["O1-full: P3−P1"],
    "registered_secondary": ["O1-full: P2−P1"],
    "descriptive_only": ["O2", "O3"],
    "multiplicity": "Бонферрони внутри confirmatory-семейства O1; "
                    "описательные O2 и O3 в семейство не входят",
    "directions": {
        "L01": "up", "L02": "up", "L03": "up", "L04": "down", "L05": "down",
        "S09": "up", "M05": "down", "S03": "down", "S04": "down", "S05": "down",
        "M01": "up", "M02": "up", "R01": "down", "S02": "down", "R04": "down",
        "C02": "down", "C01": "down", "S01": "up",
        "F01": "up", "R06": "up", "R07": "up", "P01": "up",
    },
    "weights_common": {
        "Lexical Richness": 0.30, "Information-theoretic": 0.10, "Dependencies": 0.07,
        "Semantic": 0.04, "Surface": 0.03, "Named Entities": 0.02, "Readability": 0.01,
    },
    "weights_format": {"Structural Markers": 0.12},
}

FAILURES = []


def check(condition, message, detail=""):
    mark = "  OK   " if condition else "  СБОЙ "
    print(f"{mark} {message}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(message + (f": {detail}" if detail else ""))
    return condition


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_registry():
    return list(csv.DictReader(DOCUMENTS.open(encoding="utf-8-sig", newline="")))


def main():
    print(f"PREFLIGHT score-v1, {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    rows = read_registry()
    machine = [r for r in rows if r["origin_class"] == "A"]

    print("1. Единица пары O1")
    missing_keys = [k for k in PAIR_KEYS if any(not r.get(k) for r in machine)]
    check(not missing_keys, "у всех машинных документов заполнены ключи пары",
          f"пустые: {missing_keys}" if missing_keys else "brief_id, generation_channel, repeat_index")
    cells = Counter((r["brief_id"], r["generation_channel"], r["repeat_index"],
                     r["prompt_condition"]) for r in machine)
    dupes = {k: n for k, n in cells.items() if n > 1}
    check(not dupes, "ячейка «задание × канал × повтор × режим» не повторяется",
          f"дублей: {len(dupes)}" if dupes else f"ячеек: {len(cells)}")

    print("\n2. Полные пары по контрастам")
    index = defaultdict(dict)
    for r in machine:
        index[(r["brief_id"], r["generation_channel"], r["repeat_index"])][r["prompt_condition"]] = r["document_id"]
    pairs, incomplete = [], []
    for contrast, (left, right) in (("P3-P1", ("P3", "P1")), ("P2-P1", ("P2", "P1"))):
        full = 0
        for key, cell in sorted(index.items()):
            if left in cell and right in cell:
                full += 1
                pairs.append({"contrast": contrast, "brief_id": key[0],
                              "generation_channel": key[1], "repeat_index": key[2],
                              "doc_left": cell[left], "doc_right": cell[right]})
            else:
                absent = [c for c in (left, right) if c not in cell]
                incomplete.append({"contrast": contrast, "brief_id": key[0],
                                   "generation_channel": key[1], "repeat_index": key[2],
                                   "missing_condition": ",".join(absent),
                                   "present": ",".join(sorted(cell)),
                                   "reason": "ячейка отсутствует в реестре"})
        print(f"   {contrast}: полных пар {full}, неполных {sum(1 for i in incomplete if i['contrast'] == contrast)}")
    check(pairs, "полные пары найдены", f"всего строк пар: {len(pairs)}")

    with PAIRS_LOG.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["contrast", "brief_id", "generation_channel",
                                           "repeat_index", "doc_left", "doc_right"])
        w.writeheader(); w.writerows(pairs)
    with INCOMPLETE_LOG.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["contrast", "brief_id", "generation_channel",
                                           "repeat_index", "missing_condition", "present", "reason"])
        w.writeheader(); w.writerows(incomplete)
    print(f"   журнал пар: {PAIRS_LOG.name}, неполных: {INCOMPLETE_LOG.name}")
    check(True, "неполные пары не восстанавливаются и не импутируются",
          f"записано в журнал: {len(incomplete)}")

    print("\n3. Статус главного контраста")
    check(CONFIG["confirmatory"] == ["O1-full: P3−P1"],
          "главный confirmatory-контраст — P3 − P1", str(CONFIG["confirmatory"]))
    check(CONFIG["registered_secondary"] == ["O1-full: P2−P1"],
          "P2 − P1 зарегистрирован как дополнительный", str(CONFIG["registered_secondary"]))

    print("\n4. Фиксация параметров")
    check(len(CONFIG["directions"]) == 22, "направления заданы для всех признаков индекса",
          f"{len(CONFIG['directions'])} признаков")
    check(abs(sum(CONFIG["weights_common"].values()) - 0.57) < 1e-9,
          "веса сокращённого индекса совпадают со спецификацией",
          f"сумма {sum(CONFIG['weights_common'].values()):.2f}")
    check(CONFIG["seed"] == SEED, "seed зафиксирован", str(SEED))
    check(len(CONFIG["excluded_categories"]) == 5, "исключённые категории перечислены",
          ", ".join(CONFIG["excluded_categories"]))
    check(len(CONFIG["variants"]) == 5, "пять вариантов зарегистрированы", ", ".join(CONFIG["variants"]))
    check(CONFIG["descriptive_only"] == ["O2", "O3"], "O2 и O3 помечены описательными", "")

    print("\n5. Версии входов")
    frozen = "заморожена 2026-07-27" in PREP_SPEC.read_text(encoding="utf-8")
    check(frozen, "препроцессинг заморожен", "prep-v4")
    matrix_rows = sum(1 for _ in MATRIX.open(encoding="utf-8")) - 1
    check(matrix_rows == 120708, "матрица признаков ожидаемого размера", f"{matrix_rows} строк")
    extractors = Counter()
    for r in csv.DictReader(MATRIX.open(encoding="utf-8")):
        extractors[r["extractor_version"]] += 1
    check("sem-v1" in extractors and "feat-v1" in extractors,
          "версии экстракторов присутствуют", ", ".join(sorted(extractors)))
    for path in (SPEC, COVERAGE, SUPPORT):
        check(path.exists(), f"документ на месте: {path.name}")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": CONFIG,
        "inputs": {
            "documents-registry.csv": sha256(DOCUMENTS),
            "feature-matrix.csv": sha256(MATRIX),
            "scoring-spec.md": sha256(SPEC),
            "construct-coverage.md": sha256(COVERAGE),
            "design-support-table.md": sha256(SUPPORT),
            "preprocessing-spec.md": sha256(PREP_SPEC),
        },
        "code": {p.name: sha256(p) for p in
                 [ROOT / "09-tools" / "preflight_score.py", ROOT / "09-tools" / "score_style_index.py"]
                 if p.exists()},
        "pairs": {c: sum(1 for p in pairs if p["contrast"] == c) for c in ("P3-P1", "P2-P1")},
        "incomplete_pairs": len(incomplete),
        "preflight_passed": not FAILURES,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nманифест: {MANIFEST.relative_to(ROOT)}")

    if FAILURES:
        print(f"\nPREFLIGHT НЕ ПРОЙДЕН — {len(FAILURES)} сбоев:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nPREFLIGHT ПРОЙДЕН. Расчёт разрешён.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
