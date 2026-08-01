#!/usr/bin/env python3
"""Панель из 60 документов для стресс-теста: отбор по зарегистрированным квотам.

    python 09-tools/stress_panel.py

Состав задан PI 2026-07-29 до отбора и до расчёта:

- 30 машинных: по 10 из `analytics`, `commercial`, `seo`;
- среди машинных ровно по 10 документов режимов P1, P2, P3;
- каналы генерации распределены максимально равномерно, 7–8 на канал;
- 30 человеческих: по 6 из `prose`, `news`, `seo`, `science`, `translation`;
- не более одного документа из одной `revision_family_id`;
- внутри человеческих жанров разнообразие источников максимизируется;
- отбор — новым целевым сидом из `09-tools/seed-registry.csv`.

Панель одна на все одиннадцать преобразований: 60 документов × 11 = 660
преобразованных текстов. Список идентификаторов фиксируется хешем до расчёта.

Отбор смотрит только на состав корпуса и не обращается ни к одной оценке.
"""

import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
SEEDS = ROOT / "09-tools" / "seed-registry.csv"
OUT_CSV = ROOT / "07-analysis" / "stress-panel-v1.csv"
OUT_REPORT = ROOT / "07-analysis" / "stress-panel-v1.md"

SEED_ID = "stress-panel-2026-07-29"
SEED_VALUE = 20260729
AI_GENRES = {"analytics": 10, "commercial": 10, "seo": 10}
HUMAN_GENRES = {"prose": 6, "news": 6, "seo": 6, "science": 6, "translation": 6}
PROMPTS = ("P1", "P2", "P3")
PROMPT_QUOTA = 10
CHANNEL_SPREAD = (7, 8)


def read_rows(path, encoding="utf-8-sig"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pick_ai(rows, rng):
    """Жанр × режим задаёт ячейку; канал выбирается наименее занятый.

    Порядок ячеек детерминирован, внутри ячейки документ берётся жребием с
    зарегистрированным сидом. Ограничение по `revision_family_id` соблюдается
    глобально, поэтому проверяется на каждом шаге.
    """
    pool = defaultdict(list)
    for r in rows:
        if r["origin_class"] != "A" or r["genre"] not in AI_GENRES:
            continue
        pool[(r["genre"], r["prompt_condition"])].append(r)
    for key in pool:
        pool[key].sort(key=lambda r: r["document_id"])

    picked, families, channels = [], set(), Counter()
    cells = [(genre, prompt) for genre in sorted(AI_GENRES) for prompt in PROMPTS]
    # По 10 на жанр и по 10 на режим при трёх жанрах и трёх режимах означает
    # 3–4 документа в ячейке; недобор одной ячейки компенсируется соседней
    # того же жанра, поэтому квоты считаются по обеим осям на каждом шаге.
    per_genre, per_prompt = Counter(), Counter()
    while len(picked) < sum(AI_GENRES.values()):
        progress = False
        for genre, prompt in cells:
            if per_genre[genre] >= AI_GENRES[genre] or per_prompt[prompt] >= PROMPT_QUOTA:
                continue
            candidates = [r for r in pool[(genre, prompt)]
                          if r["revision_family_id"] not in families]
            if not candidates:
                continue
            fewest = min(channels[c] for c in {r["generation_channel"]
                                               for r in candidates})
            candidates = [r for r in candidates
                          if channels[r["generation_channel"]] == fewest]
            choice = rng.choice(candidates)
            picked.append(choice)
            families.add(choice["revision_family_id"])
            channels[choice["generation_channel"]] += 1
            per_genre[genre] += 1
            per_prompt[prompt] += 1
            progress = True
        if not progress:
            break
    return picked, channels


def pick_human(rows, rng):
    """Внутри жанра источники перебираются по кругу: разнообразие важнее числа."""
    pool = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["origin_class"] != "H" or r["genre"] not in HUMAN_GENRES:
            continue
        pool[r["genre"]][r["split_group_source"] or "—"].append(r)

    picked, families = [], set()
    for genre in sorted(HUMAN_GENRES):
        quota = HUMAN_GENRES[genre]
        sources = sorted(pool[genre])
        for source in sources:
            pool[genre][source].sort(key=lambda r: r["document_id"])
        taken, index = 0, 0
        while taken < quota and sources:
            source = sources[index % len(sources)]
            candidates = [r for r in pool[genre][source]
                          if r["revision_family_id"] not in families]
            if candidates:
                choice = rng.choice(candidates)
                picked.append(choice)
                families.add(choice["revision_family_id"])
                pool[genre][source].remove(choice)
                taken += 1
            index += 1
            if index > len(sources) * 40:
                break
    return picked


def register_seed():
    """Сид записывается в реестр до отбора, а не после."""
    rows = read_rows(SEEDS, "utf-8-sig")
    if any(r["seed_id"] == SEED_ID for r in rows):
        return False
    with SEEDS.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow([
            SEED_ID, "панель стресс-теста, 60 документов", SEED_VALUE,
            "09-tools/stress_panel.py", "2026-07-29", "stress-panel-v1",
            "квоты жанра, режима, канала и revision_family заданы PI до отбора"])
    return True


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = register_seed()
    print(f"панель стресс-теста, {stamp}")
    print(f"  сид {SEED_ID} = {SEED_VALUE}, "
          f"{'записан в реестр' if added else 'уже в реестре'}")

    rows = read_rows(REGISTRY)
    rng = random.Random(SEED_VALUE)
    ai, channels = pick_ai(rows, rng)
    human = pick_human(rows, rng)
    panel = ai + human

    # Считать по всей панели нельзя: жанр `seo` есть в обеих половинах.
    by_genre = Counter(r["genre"] for r in ai)
    by_prompt = Counter(r["prompt_condition"] for r in ai)
    by_source = Counter(r["split_group_source"] for r in human)
    families = {r["revision_family_id"] for r in panel}

    print(f"  отобрано {len(panel)}: машинных {len(ai)}, человеческих {len(human)}")
    print(f"  каналы: {dict(channels)}")
    print(f"  режимы: {dict(by_prompt)}")
    print(f"  уникальных revision_family: {len(families)} из {len(panel)}")
    print(f"  источников в человеческой части: {len(by_source)}")

    fields = ["document_id", "origin_class", "genre", "prompt_condition",
              "generation_channel", "split_group_source", "revision_family_id",
              "word_count"]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in sorted(panel, key=lambda r: r["document_id"]):
            writer.writerow({k: r.get(k, "") for k in fields})

    checks = [
        ("машинных ровно 30", len(ai) == 30),
        ("человеческих ровно 30", len(human) == 30),
        ("квота по машинным жанрам", all(
            by_genre[g] == n for g, n in AI_GENRES.items())),
        ("человеческий seo не смешан с машинным", sum(
            1 for r in human if r["genre"] == "seo") == HUMAN_GENRES["seo"]),
        ("квота по человеческим жанрам", all(
            sum(1 for r in human if r["genre"] == g) == n
            for g, n in HUMAN_GENRES.items())),
        ("по 10 документов на каждый режим", all(
            by_prompt[p] == PROMPT_QUOTA for p in PROMPTS)),
        ("каналы 7–8 документов", all(
            CHANNEL_SPREAD[0] <= c <= CHANNEL_SPREAD[1] for c in channels.values())),
        ("не более одного из revision_family", len(families) == len(panel)),
    ]
    lines = ["# Панель стресс-теста: 60 документов", "",
             f"Собрано {stamp} скриптом `09-tools/stress_panel.py`. Квоты заданы "
             "PI 2026-07-29 до отбора; отбор смотрит только на состав корпуса.", "",
             f"Сид `{SEED_ID}` = {SEED_VALUE}, реестр — `09-tools/seed-registry.csv`.",
             "", "Панель одна на все одиннадцать преобразований: 60 × 11 = 660 "
             "преобразованных текстов.", "",
             "| Проверка квоты | Результат |", "|---|---|"]
    for name, ok in checks:
        lines.append(f"| {name} | {'выполнена' if ok else '**нарушена**'} |")
    lines += ["", "## Состав", "",
              "| Класс | Жанр | Документов |", "|---|---|---|"]
    for genre in sorted(AI_GENRES):
        lines.append(f"| A | `{genre}` | {sum(1 for r in ai if r['genre'] == genre)} |")
    for genre in sorted(HUMAN_GENRES):
        lines.append(f"| H | `{genre}` | "
                     f"{sum(1 for r in human if r['genre'] == genre)} |")
    lines += ["", "| Канал | Документов |", "|---|---|"]
    for channel, count in sorted(channels.items()):
        lines.append(f"| `{channel}` | {count} |")
    lines += ["", f"Источников в человеческой части: {len(by_source)}. "
                  f"Уникальных `revision_family_id`: {len(families)} из {len(panel)}.",
              "", "Результаты по отдельным жанрам, каналам и режимам остаются "
                  "описательными: для уверенных subgroup-выводов панели из 60 "
                  "документов мало.", ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    failed = [name for name, ok in checks if not ok]
    for name in failed:
        print(f"  ! квота нарушена: {name}")
    print(f"  записано: {OUT_CSV.name}, отчёт: {OUT_REPORT.name}")
    print(f"  sha256 панели: {sha256(OUT_CSV)[:16]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
