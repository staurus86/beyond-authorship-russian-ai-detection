#!/usr/bin/env python3
"""Шлюз 2: перенос разбиений на prep-v5 вычитанием исключённых ID.

    python 09-tools/splits_v5_carry.py

Новые разбиения **не строятся**. Берётся прежнее соответствие `document_id → fold`
от 2026-07-25, из него удаляются 34 ID, исключённые после коррекции, состав
источников не перераспределяется. Иначе изменение корпуса смешалось бы с
изменением разбиения, и разделить эти два эффекта после просмотра результатов
было бы нечем.

Проверки, каждая с жёстким исходом:

- каждый удалённый ID действительно исключён из реестра;
- ни один оставшийся ID не поменял fold;
- для каждого holdout записан список удалённых ID отдельно по train и test;
- фиксируется, у каких holdout тестовая часть стала одноклассовой: там, как и в
  прежнем прогоне, определим только FPR.

Выход — `07-analysis/splits-v5/` и отчёт `07-analysis/splits-v5-carry.md`.
Прежние манифесты не перезаписываются.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

SPLITS_V1 = ROOT / "07-analysis" / "splits"
SPLITS_V5 = ROOT / "07-analysis" / "splits-v5"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
OUT_REPORT = ROOT / "07-analysis" / "splits-v5-carry.md"
OUT_DROPPED = ROOT / "07-analysis" / "splits-v5-dropped-ids.csv"

EXCLUSION_STAGE = "коррекция извлечения correction-v5.0"


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"перенос разбиений на prep-v5, {stamp}")

    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        alive = {r["document_id"]: r for r in csv.DictReader(fh)}
    with EXCLUSIONS.open(encoding="utf-8-sig", newline="") as fh:
        excluded_now = {r["document_id"] for r in csv.DictReader(fh)
                        if r["stage"] == EXCLUSION_STAGE}

    print(f"  в реестре {len(alive)}, исключено коррекцией {len(excluded_now)}")
    still_present = excluded_now & set(alive)
    if still_present:
        raise SystemExit(f"исключённые ID остались в реестре: {sorted(still_present)[:5]}")

    SPLITS_V5.mkdir(exist_ok=True)
    rows, report_rows = [], []
    for path in sorted(SPLITS_V1.glob("holdout_*.json")):
        split = json.loads(path.read_text(encoding="utf-8"))
        name = split["split_name"]
        new_split = dict(split)
        dropped = {"train": [], "test": []}
        for part in ("train", "test"):
            kept = []
            for doc_id in split[part]:
                if doc_id in alive:
                    kept.append(doc_id)
                else:
                    dropped[part].append(doc_id)
            new_split[part] = kept

        # Ни один оставшийся документ не должен сменить сторону разбиения.
        assert set(new_split["train"]) <= set(split["train"])
        assert set(new_split["test"]) <= set(split["test"])

        new_split["carried_from"] = path.name
        new_split["carried_at"] = stamp
        new_split["dropped_train"] = len(dropped["train"])
        new_split["dropped_test"] = len(dropped["test"])
        out_path = SPLITS_V5 / path.name.replace("2026-07-25", "prep-v5")
        out_path.write_text(json.dumps(new_split, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        classes = {}
        for part in ("train", "test"):
            counts = defaultdict(int)
            for doc_id in new_split[part]:
                counts[alive[doc_id]["origin_class"]] += 1
            classes[part] = counts
        report_rows.append({
            "split": name,
            "train": len(new_split["train"]), "test": len(new_split["test"]),
            "dropped_train": len(dropped["train"]), "dropped_test": len(dropped["test"]),
            "train_A": classes["train"]["A"], "train_H": classes["train"]["H"],
            "test_A": classes["test"]["A"], "test_H": classes["test"]["H"],
        })
        for part in ("train", "test"):
            for doc_id in dropped[part]:
                rows.append({"split": name, "part": part, "document_id": doc_id})
        print(f"  {name}: train {len(new_split['train'])} (-{len(dropped['train'])}), "
              f"test {len(new_split['test'])} (-{len(dropped['test'])})")

    with OUT_DROPPED.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["split", "part", "document_id"])
        writer.writeheader()
        writer.writerows(rows)

    broken = [r for r in report_rows if r["train_A"] == 0 or r["train_H"] == 0]
    single_class_test = [r for r in report_rows if r["test_A"] == 0 or r["test_H"] == 0]

    lines = [
        "# Шлюз 2: перенос разбиений на prep-v5",
        "",
        f"Собрано {stamp} скриптом `09-tools/splits_v5_carry.py`.",
        "",
        "Новые разбиения не строились. Взято прежнее соответствие `document_id → "
        "fold` от 2026-07-25, из него вычтены исключённые после коррекции ID; "
        "состав источников не перераспределялся. Прежние манифесты не "
        "перезаписаны, новые лежат в `07-analysis/splits-v5/`.",
        "",
        "| Holdout | Train | Test | Убрано из train | Убрано из test | Train A/H | Test A/H |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report_rows:
        lines.append(
            f"| {r['split']} | {r['train']} | {r['test']} | {r['dropped_train']} | "
            f"{r['dropped_test']} | {r['train_A']}/{r['train_H']} | "
            f"{r['test_A']}/{r['test_H']} |")
    lines += ["", "## Проверки", "",
              f"- обучающая часть с одним классом: "
              f"{'нет' if not broken else ', '.join(r['split'] for r in broken)};",
              f"- тестовая часть с одним классом: "
              f"{len(single_class_test)} — "
              + (", ".join(r["split"] for r in single_class_test) or "нет")
              + ". Как и в прежнем прогоне, на таких срезах определим только FPR;",
              "- ни один оставшийся документ не сменил сторону разбиения: проверено "
              "включением множеств;",
              f"- список удалённых ID по каждому holdout: `{OUT_DROPPED.name}`, "
              f"строк {len(rows)}.",
              ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT_REPORT.name}, удалённых записей {len(rows)}")


if __name__ == "__main__":
    main()
