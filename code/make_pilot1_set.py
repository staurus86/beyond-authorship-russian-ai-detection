#!/usr/bin/env python3
"""Отбор development set Pilot-1 по 06-features/pilot-1-spec.md.

Запуск из корня папки исследования:
    python 09-tools/make_pilot1_set.py            # отчёт о покрытии
    python 09-tools/make_pilot1_set.py --write    # записать 06-features/pilot-1-ids.csv

Порядок нерушим: набор отбирается **до** расчёта любых первичных признаков и
выводится из confirmatory-теста навсегда. Отбор идёт по паспорту документа —
канал, режим, жанр, страта, подгруппа hard-human, источник, длина, — и ни
одна оценка машинности при этом не считается.

Покрытие, которого требует спецификация: все четыре канала генерации, все три
режима задания, все три машинных жанра, все четыре уровня регламентированности,
все подгруппы hard-human, разные источники внутри страты, короткий, средний и
длинный диапазоны длины.

Целевой объём — 72–108 документов.
"""

import argparse
import csv
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
OUTPUT = ROOT / "06-features" / "pilot-1-ids.csv"
SEEDS = ROOT / "09-tools" / "seed-registry.csv"

SEED = 20260725
REGULATION = re.compile(r"regulation_level=(\d)")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def regulation_level(row):
    match = REGULATION.search(row.get("notes") or "")
    return match.group(1) if match else ""


def length_band(row, thresholds):
    words = int(row["word_count"] or 0)
    low, high = thresholds
    if words <= low:
        return "короткий"
    if words >= high:
        return "длинный"
    return "средний"


def pick(rng, pool, taken, prefer_band=None, thresholds=None):
    """Берёт документ из пула, стараясь не повторять источник и попасть в
    нужный диапазон длины."""
    candidates = [row for row in pool if row["document_id"] not in taken]
    if not candidates:
        return None
    if prefer_band and thresholds:
        banded = [row for row in candidates if length_band(row, thresholds) == prefer_band]
        if banded:
            candidates = banded
    fresh = [row for row in candidates if row["split_group_source"] not in taken_sources]
    if fresh:
        candidates = fresh
    choice = rng.choice(sorted(candidates, key=lambda row: row["document_id"]))
    taken.add(choice["document_id"])
    taken_sources.add(choice["split_group_source"])
    return choice


taken_sources = set()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    rows = read_rows(DOCUMENTS)
    rng = random.Random(args.seed)

    words = sorted(int(row["word_count"] or 0) for row in rows)
    thresholds = (words[len(words) // 3], words[2 * len(words) // 3])
    print(f"Pilot-1: сид {args.seed}, границы длины {thresholds[0]} и {thresholds[1]} слов")

    selected, reasons = [], {}
    taken = set()

    # Машинная часть: канал × режим, по одному документу на ячейку, жанр и
    # диапазон длины чередуются, чтобы покрыть обе оси без полного факториала.
    machine = [row for row in rows if row["origin_class"] == "A"]
    channels = sorted({row["generation_channel"] for row in machine})
    conditions = sorted({row["prompt_condition"] for row in machine})
    genres = sorted({row["genre"] for row in machine})
    bands = ["короткий", "средний", "длинный"]

    index = 0
    for channel in channels:
        for condition in conditions:
            for genre_step in range(len(genres)):
                genre = genres[(index + genre_step) % len(genres)]
                pool = [
                    row for row in machine
                    if row["generation_channel"] == channel
                    and row["prompt_condition"] == condition
                    and row["genre"] == genre
                ]
                choice = pick(rng, pool, taken, bands[index % len(bands)], thresholds)
                if choice:
                    selected.append(choice)
                    reasons[choice["document_id"]] = f"канал {channel}, режим {condition}, жанр {genre}"
                index += 1

    # Человеческая часть: каждый уровень регламентированности, разные
    # источники внутри уровня, все три диапазона длины.
    human = [row for row in rows if row["origin_class"] == "H"]
    by_level = defaultdict(list)
    for row in human:
        by_level[regulation_level(row)].append(row)

    # По восемь документов на уровень. Проход идёт по источникам циклически:
    # у страт 0, 1 и 3 источников всего два-три, и отбор «по одному с
    # источника» дал бы 66 документов при нижней границе спецификации 72.
    for level in sorted(by_level):
        sources = sorted({row["split_group_source"] for row in by_level[level]})
        rng.shuffle(sources)
        for position in range(8):
            source = sources[position % len(sources)]
            pool = [row for row in by_level[level] if row["split_group_source"] == source]
            choice = pick(rng, pool, taken, bands[position % len(bands)], thresholds)
            if choice:
                selected.append(choice)
                label = f"уровень {level}" if level else "уровень не проставлен"
                reasons[choice["document_id"]] = f"{label}, источник {source}"

    # Подгруппы hard-human: каждая по два документа.
    subgroups = defaultdict(list)
    for row in rows:
        for subgroup in (row.get("hh_subgroups") or "").split(";"):
            subgroup = subgroup.strip()
            if subgroup:
                subgroups[subgroup].append(row)
    for subgroup in sorted(subgroups):
        for position in range(2):
            choice = pick(rng, subgroups[subgroup], taken, bands[position % len(bands)], thresholds)
            if choice:
                selected.append(choice)
                reasons[choice["document_id"]] = f"hard-human: {subgroup}"

    print(f"Отобрано документов: {len(selected)}")
    print()
    print("Покрытие:")
    print("  каналы:   " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(row["generation_channel"] or "human" for row in selected).items())))
    print("  режимы:   " + ", ".join(f"{k or 'человек'}={v}" for k, v in sorted(Counter(row["prompt_condition"] for row in selected).items())))
    print("  жанры:    " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(row["genre"] for row in selected).items())))
    print("  уровни:   " + ", ".join(f"{k or 'машина/нет'}={v}" for k, v in sorted(Counter(regulation_level(row) for row in selected).items())))
    print("  длина:    " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(length_band(row, thresholds) for row in selected).items())))
    print("  источники: " + str(len({row["split_group_source"] for row in selected})))
    hh = Counter(sub.strip() for row in selected for sub in (row.get("hh_subgroups") or "").split(";") if sub.strip())
    print("  hard-human: " + (", ".join(f"{k}={v}" for k, v in sorted(hh.items())) or "нет"))

    gaps = []
    if len(selected) < 72:
        gaps.append(f"отобрано {len(selected)}, спецификация требует не менее 72")
    if len(selected) > 108:
        gaps.append(f"отобрано {len(selected)}, спецификация ограничивает 108")
    for gap in gaps:
        print(f"  ! {gap}")

    if args.write:
        today = datetime.now().date().isoformat()
        with OUTPUT.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["document_id", "origin_class", "genre", "generation_channel", "prompt_condition",
                             "regulation_level", "hh_subgroups", "source", "word_count", "length_band",
                             "selection_reason", "seed", "selected_at"])
            for row in selected:
                writer.writerow([
                    row["document_id"], row["origin_class"], row["genre"], row.get("generation_channel") or "",
                    row.get("prompt_condition") or "", regulation_level(row), row.get("hh_subgroups") or "",
                    row["split_group_source"], row["word_count"], length_band(row, thresholds),
                    reasons[row["document_id"]], args.seed, today,
                ])
        print(f"\nЗаписано: {OUTPUT.relative_to(ROOT)}")

        with SEEDS.open("a", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow([
                f"pilot1-{today}", "отбор development set Pilot-1", args.seed,
                "09-tools/make_pilot1_set.py", today, "pilot-1",
                f"random.Random({args.seed}): выбор документа внутри ячейки покрытия; "
                f"границы длины {thresholds[0]}/{thresholds[1]} слов по терцилям корпуса",
            ])
        print(f"Сид записан в {SEEDS.relative_to(ROOT)}")
    else:
        print("\nФайл не записан: запуск без --write")

    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
