#!/usr/bin/env python3
"""Слепые батчи для процедур оценки и карта расшифровки.

Запуск из корня папки исследования:
    python 09-tools/make_batches.py                     # отчёт о составе
    python 09-tools/make_batches.py --write             # батчи и blinding-map
    python 09-tools/make_batches.py --profile full      # вариант с разметкой

Реализует 05-annotation/annotation-protocol.md, §2 и §3. Людей в этом
исследовании не нанимают, поэтому батчи адресованы четвёртой процедуре —
модели-судье, — и к ней применяется то же правило слепоты, что к человеку,
плюс три прогона с медианой (§7 протокола).

Что обеспечивает слепота:
    - оценивающий не видит происхождение, канал, модель и имя файла;
    - порядок документов внутри батча случайный;
    - документы одного атома provenance не попадают в один батч;
    - часть документов повторяется скрыто — контроль внутренней стабильности;
    - соответствие «слепой id → document_id» лежит в blinding-map.csv.

Профиль текста. По умолчанию батчи собираются в профиле `prose`: markdown у
машинных текстов порождается оболочкой канала, а не моделью, и судья, увидев
разметку, оценит оболочку. Вариант `--profile full` собирается отдельно и
служит проверкой чувствительности — той самой, что зарегистрирована как
«исходы считаются дважды, с форматными признаками и без».
"""

import argparse
import csv
import hashlib
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
DERIVED = ROOT / "04-corpus" / "derived" / "prep-v4"
PILOT = ROOT / "06-features" / "pilot-1-ids.csv"
OUT = ROOT / "05-annotation" / "batches"
MAP = ROOT / "05-annotation" / "blinding-map.csv"
SEEDS = ROOT / "09-tools" / "seed-registry.csv"

SEED = 20260725
TARGET = 360          # середина диапазона 300–450 из §2 протокола
BATCH_SIZE = 30
REPEAT_SHARE = 0.10   # доля скрытых повторов
RUNS = 3              # прогонов судьи на документ, §7 протокола


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def stratum(row):
    """Страта отбора: класс, жанр, семейство модели, режим задания.
    Отдельная страта «ошибки baseline-детекторов» из §2 протокола не строится:
    baseline ещё не посчитаны. Это записано в отчёте, а не обойдено молчанием.
    """
    return (
        row["origin_class"],
        row["genre"],
        (row.get("model_family") or "человек"),
        (row.get("prompt_condition") or "—"),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--profile", choices=("prose", "full"), default="prose")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--target", type=int, default=TARGET)
    args = parser.parse_args()

    rows = read_rows(DOCUMENTS)
    registry = {row["document_id"]: row for row in rows}
    rng = random.Random(args.seed)

    pilot = set()
    if PILOT.exists():
        pilot = {row["document_id"] for row in read_rows(PILOT)}

    # Pilot-1 выведен из confirmatory-теста навсегда, поэтому в батчи оценки
    # он не идёт.
    pool = [row for row in rows if row["document_id"] not in pilot]
    print(f"Батчи оценки: профиль {args.profile}, сид {args.seed}")
    print(f"Документов доступно: {len(pool)} (из {len(rows)}, вычтен Pilot-1: {len(pilot)})")

    strata = defaultdict(list)
    for row in pool:
        strata[stratum(row)].append(row)

    # Пропорциональный отбор с гарантией минимум одного документа на страту.
    selected = []
    share = args.target / len(pool)
    for key in sorted(strata):
        members = sorted(strata[key], key=lambda row: row["document_id"])
        count = max(1, round(len(members) * share))
        selected.extend(rng.sample(members, min(count, len(members))))

    rng.shuffle(selected)
    selected = selected[: args.target]
    print(f"Отобрано: {len(selected)} документов из {len(strata)} страт")

    repeats = rng.sample(selected, max(1, round(len(selected) * REPEAT_SHARE)))
    print(f"Скрытых повторов: {len(repeats)} — контроль внутренней стабильности")

    # Слепой идентификатор не должен восстанавливаться из document_id, поэтому
    # он строится от сида, а не от имени документа.
    items = []
    for index, row in enumerate(selected + repeats):
        token = hashlib.blake2b(f"{args.seed}:{index}:{row['document_id']}".encode(), digest_size=5).hexdigest()
        items.append({"blind_id": f"d{token}", "row": row, "is_repeat": index >= len(selected)})
    rng.shuffle(items)

    # Документы одного атома provenance разводятся по разным батчам: иначе
    # оценивающий увидит две версии одного текста подряд.
    atom_of = {}
    for row in rows:
        atom_of[row["document_id"]] = (row.get("dedup_cluster_id") or "") + "|" + (row.get("revision_family_id") or "")

    batches, current, used_atoms = [], [], set()
    for item in items:
        atom = atom_of[item["row"]["document_id"]]
        if len(current) >= BATCH_SIZE or atom in used_atoms:
            if len(current) >= BATCH_SIZE:
                batches.append(current)
                current, used_atoms = [], set()
        current.append(item)
        used_atoms.add(atom)
    if current:
        batches.append(current)

    print(f"Батчей: {len(batches)} по {BATCH_SIZE} документов, прогонов судьи на документ: {RUNS}")
    print()
    print("Состав отобранного:")
    print("  классы:  " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(row["origin_class"] for row in selected).items())))
    print("  жанры:   " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(row["genre"] for row in selected).items())))
    print("  каналы:  " + ", ".join(f"{k or 'человек'}={v}" for k, v in sorted(Counter(row.get("generation_channel") or "человек" for row in selected).items())))
    print("  режимы:  " + ", ".join(f"{k or 'человек'}={v}" for k, v in sorted(Counter(row.get("prompt_condition") or "человек" for row in selected).items())))
    hh = sum(1 for row in selected if (row.get("hh_subgroups") or "").strip())
    print(f"  hard-human: {hh}")
    print("  ! страта «ошибки baseline-детекторов» (§2 протокола) не построена: baseline ещё не посчитаны")

    missing = [
        item["row"]["document_id"]
        for item in items
        if not (DERIVED / args.profile / f"{item['row']['document_id']}.txt").exists()
    ]
    if missing:
        print(f"  ! нет производной версии профиля {args.profile}: {len(missing)} документов")

    if args.write:
        today = datetime.now().date().isoformat()
        OUT.mkdir(parents=True, exist_ok=True)
        for number, batch in enumerate(batches, start=1):
            path = OUT / f"batch-{number:02d}_{args.profile}_{today}.csv"
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["blind_id", "text_path", "word_count", "runs_required"])
                for item in batch:
                    text_path = DERIVED / args.profile / f"{item['row']['document_id']}.txt"
                    writer.writerow([
                        item["blind_id"],
                        str(text_path.relative_to(ROOT)).replace("\\", "/"),
                        item["row"]["word_count"],
                        RUNS,
                    ])
        print(f"\nЗаписано батчей: {len(batches)} в {OUT.relative_to(ROOT)}")

        with MAP.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["blind_id", "document_id", "batch", "is_hidden_repeat", "profile", "seed", "created_at"])
            for number, batch in enumerate(batches, start=1):
                for item in batch:
                    writer.writerow([
                        item["blind_id"], item["row"]["document_id"], f"batch-{number:02d}",
                        "да" if item["is_repeat"] else "нет", args.profile, args.seed, today,
                    ])
        print(f"Карта расшифровки: {MAP.relative_to(ROOT)} — оценивающему недоступна")

        with SEEDS.open("a", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow([
                f"batches-{args.profile}-{today}", "слепые батчи для процедур оценки", args.seed,
                "09-tools/make_batches.py", today, f"профиль {args.profile}",
                f"random.Random({args.seed}): отбор внутри страт, порядок документов, выбор скрытых повторов "
                f"({len(repeats)} из {len(selected)})",
            ])
        print(f"Сид записан в {SEEDS.relative_to(ROOT)}")
    else:
        print("\nФайлы не записаны: запуск без --write")

    return 0


if __name__ == "__main__":
    sys.exit(main())
