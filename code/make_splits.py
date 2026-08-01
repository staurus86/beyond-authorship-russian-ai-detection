#!/usr/bin/env python3
"""Групповые разбиения корпуса по восьми holdout из 07-analysis/splits-spec.md.

Запуск из корня папки исследования:
    python 09-tools/make_splits.py                # отчёт, файлы не пишутся
    python 09-tools/make_splits.py --write        # плюс манифесты в 07-analysis/splits/
    python 09-tools/make_splits.py --seed 20260725

Случайное перемешивание с разделением 80/20 запрещено спецификацией: при нём
модель видит в обучении ту же тему, того же автора, другую версию того же
текста и тот же машинный шаблон. Здесь разбиения строятся по группам.

Единица разбиения — не документ, а атом provenance. Атом склеивается
union-find по двум полям: `revision_family_id` (версии одного текста) и
`dedup_cluster_id` (почти-дубли, dedup-v1). Цепочка «человеческий текст →
машинная редактура → повторная правка» не рвётся между train и test ни при
каких условиях. `leakage_group` в атом не входит и работает как ось изоляции —
поправка от 2026-07-25, подтверждена PI в тот же день.

Hard-human не участвует в обучении ни в одном разбиении: подгруппы из
§5.2.1 preregistration нужны для честного FPR, а не для подгонки.

Манифест каждого разбиения содержит списки document_id, сид, дату, версию
реестра и его хеш. Без манифеста результат невоспроизводим и в статью не идёт.
"""

import argparse
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

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
SPLITS = ROOT / "07-analysis" / "splits"
SEEDS = ROOT / "09-tools" / "seed-registry.csv"

SPLIT_VERSION = "splits-v1"
TEST_SHARE = 0.20

# Поля, склеивающие документы в один атом provenance. Только те, разрыв
# которых означает утечку самого текста: версии одного документа и почти-дубли.
#
# `leakage_group` в атом не входит. У машинных документов это идентификатор
# задания, и все 24 ячейки одного задания — четыре семейства × три режима ×
# два повтора — оказались бы одним неделимым куском, после чего holdout по
# семейству модели и по режиму задания стал бы невозможен: любое семейство
# тянуло бы за собой три остальных. Группа утечки применяется как ось
# изоляции там, где она к месту, — в разбиениях по теме и по источнику.
ATOM_KEYS = ("revision_family_id", "dedup_cluster_id")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class Union:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def build_atoms(rows):
    """Документы, связанные общей версией, дублем или группой утечки, попадают
    в один атом. Разбиения оперируют атомами, а не документами."""
    union = Union([row["document_id"] for row in rows])
    for key in ATOM_KEYS:
        buckets = defaultdict(list)
        for row in rows:
            value = (row.get(key) or "").strip()
            if value:
                buckets[f"{key}:{value}"].append(row["document_id"])
        for members in buckets.values():
            for other in members[1:]:
                union.union(members[0], other)

    atoms = defaultdict(list)
    for row in rows:
        atoms[union.find(row["document_id"])].append(row["document_id"])
    return atoms, union


def is_hard_human(row):
    return bool((row.get("hh_subgroups") or "").strip())


def atom_values(members, registry, field):
    return {(registry[doc_id].get(field) or "").strip() for doc_id in members}


def split_by_field(rows, registry, atoms, union, field, test_values, name, note, isolate_leakage=False):
    """Тест — атомы, у которых хотя бы один документ несёт значение из
    test_values. Атом целиком уходит на одну сторону: разрыв атома означал бы
    утечку версии или дубля.

    isolate_leakage расширяет тест на всю группу утечки. Включается там, где
    ось разбиения — тема или источник: у машинных документов группа утечки
    равна заданию, у человеческих — коллекции, и оставлять половину группы в
    обучении значит отдать классификатору ту же тему под другим номером.
    """
    test_atoms = set()
    for key, members in atoms.items():
        if atom_values(members, registry, field) & test_values:
            test_atoms.add(key)

    if isolate_leakage:
        groups = set()
        for key in test_atoms:
            groups |= atom_values(atoms[key], registry, "leakage_group") - {""}
        for key, members in atoms.items():
            if atom_values(members, registry, "leakage_group") & groups:
                test_atoms.add(key)

    train, test = [], []
    for key, members in atoms.items():
        target = test if key in test_atoms else train
        target.extend(members)

    # Hard-human выводится из обучения всегда, независимо от оси разбиения.
    train, moved = drop_hard_human(train, registry, test)
    return {
        "split_name": name,
        "holdout_field": field,
        "holdout_values": sorted(test_values),
        "note": note,
        "train": sorted(train),
        "test": sorted(test),
        "hard_human_moved_to_test": moved,
    }


def drop_hard_human(train, registry, test):
    kept, moved = [], 0
    for doc_id in train:
        if is_hard_human(registry[doc_id]):
            test.append(doc_id)
            moved += 1
        else:
            kept.append(doc_id)
    return kept, moved


def choose_values(rows, registry, field, rng, share=TEST_SHARE):
    """Отбор значений оси в тест до нужной доли документов.

    Считать долю от числа значений нельзя: у машинных документов автор — это
    канал, четыре значения по 270 документов, и случайные 20% значений из 285
    уносят в тест почти весь корпус. Значения перебираются в случайном порядке
    и добавляются, пока накопленный объём не достигнет доли; значение, которое
    перепрыгивает цель больше чем вдвое, пропускается.
    """
    sizes = Counter(
        (row.get(field) or "").strip() for row in rows if (row.get(field) or "").strip()
    )
    if not sizes:
        return set()
    target = len(rows) * share
    order = sorted(sizes)
    rng.shuffle(order)

    chosen, volume = set(), 0
    for value in order:
        if volume >= target:
            break
        if sizes[value] > target * 2:
            continue
        chosen.add(value)
        volume += sizes[value]
    if not chosen:
        chosen.add(min(sizes, key=sizes.get))
    return chosen


def check(split, registry, atoms, union):
    """Проверки перед обучением, чеклист splits-spec.md §«Проверка»."""
    train, test = set(split["train"]), set(split["test"])
    problems = []

    if train & test:
        problems.append(f"пересечение document_id: {len(train & test)}")

    for field in ("split_group_author", "split_group_topic", "revision_family_id", "dedup_cluster_id"):
        left = {(registry[doc_id].get(field) or "").strip() for doc_id in train} - {""}
        right = {(registry[doc_id].get(field) or "").strip() for doc_id in test} - {""}
        shared = left & right
        if shared and field in ("revision_family_id", "dedup_cluster_id"):
            problems.append(f"{field} разорван между train и test: {len(shared)}")
        elif shared:
            split.setdefault("crossing", {})[field] = len(shared)

    leaked = [doc_id for doc_id in train if is_hard_human(registry[doc_id])]
    if leaked:
        problems.append(f"hard-human в обучении: {len(leaked)}")

    sources_train = {(registry[doc_id].get("split_group_source") or "").strip() for doc_id in train}
    sources_test = {(registry[doc_id].get("split_group_source") or "").strip() for doc_id in test}
    split["sources_crossing"] = len((sources_train & sources_test) - {""})

    split["stats"] = {
        "train": len(train),
        "test": len(test),
        "train_origin": dict(Counter(registry[doc_id]["origin_class"] for doc_id in train)),
        "test_origin": dict(Counter(registry[doc_id]["origin_class"] for doc_id in test)),
        "test_genres": dict(Counter(registry[doc_id]["genre"] for doc_id in test)),
    }
    # Тест из одного класса не позволяет считать TPR и AUROC: на таком срезе
    # публикуется только FPR. Это свойство состава корпуса — человеческие
    # жанры не имеют машинной пары, — а не ошибка разбиения.
    warnings = []
    if len(split["stats"]["test_origin"]) < 2:
        warnings.append("в тесте один класс: считается только FPR")
    if split["stats"]["train"] < 300:
        warnings.append(f"обучающая часть мала: {split['stats']['train']}")
    split["warnings"] = warnings
    split["problems"] = problems
    return problems


def plan(rows, registry, atoms, union, rng):
    """Восемь holdout спецификации. Где ось даёт несколько вариантов —
    семейство модели, prompt condition, жанр, — строится по манифесту на
    каждое значение: перенос проверяется на каждом неизвестном генераторе,
    а не на одном выбранном."""
    splits = []

    splits.append(
        split_by_field(
            rows, registry, atoms, union, "split_group_topic",
            choose_values(rows, registry, "split_group_topic", rng),
            "holdout_topic", "проверяет, не выучил ли классификатор предметную лексику",
            isolate_leakage=True,
        )
    )

    splits.append(
        split_by_field(
            rows, registry, atoms, union, "split_group_author",
            choose_values(rows, registry, "split_group_author", rng),
            "holdout_author", "проверяет, не выучил ли классификатор индивидуальный стиль",
        )
    )

    for family in sorted({row["model_family"] for row in rows if (row.get("model_family") or "").strip()}):
        splits.append(
            split_by_field(
                rows, registry, atoms, union, "model_family", {family},
                f"holdout_model_{family}", "перенос на неизвестный генератор",
            )
        )

    for condition in sorted({row["prompt_condition"] for row in rows if (row.get("prompt_condition") or "").strip()}):
        splits.append(
            split_by_field(
                rows, registry, atoms, union, "prompt_condition", {condition},
                f"holdout_prompt_{condition}", "перенос на другую формулировку задания",
            )
        )

    for genre in sorted({row["genre"] for row in rows if (row.get("genre") or "").strip()}):
        splits.append(
            split_by_field(
                rows, registry, atoms, union, "genre", {genre},
                f"holdout_genre_{genre}", "перенос между типами текста",
            )
        )

    # Изоляция по группе утечки здесь не нужна и вредна: у человеческих
    # документов `split_group_source` и `leakage_group` совпадают, а у машинных
    # источник — это канал, и расширение по группе утечки (заданию) утащило бы
    # в тест все четыре канала разом, оставив в обучении 361 документ.
    splits.append(
        split_by_field(
            rows, registry, atoms, union, "split_group_source",
            choose_values(rows, registry, "split_group_source", rng),
            "holdout_source", "source-disjoint: ограничен, страты 0, 1 и 3 стоят на двух источниках каждая",
        )
    )

    # Holdout по времени. Машинная часть сгенерирована в один месяц, поэтому
    # ось работает только на человеческих датах публикации; ограничение
    # записывается в манифест, а не скрывается.
    dates = sorted(
        (row.get("human_publication_date") or "").strip()
        for row in rows
        if (row.get("human_publication_date") or "").strip()
    )
    if dates:
        cutoff = dates[int(len(dates) * (1 - TEST_SHARE))]
        late = {
            (row.get("human_publication_date") or "").strip()
            for row in rows
            if (row.get("human_publication_date") or "").strip() >= cutoff
        }
        split = split_by_field(
            rows, registry, atoms, union, "human_publication_date", late,
            "holdout_time", f"тест — человеческие тексты с даты {cutoff}; машинная часть сгенерирована в один месяц и по этой оси не делится",
        )
        split["holdout_values"] = [f">= {cutoff}"]
        splits.append(split)

    # Revision family и hard-human отдельными разбиениями не выделяются:
    # первый инвариант обеспечен атомом, второй — правилом drop_hard_human,
    # и оба проверяются на каждом манифесте.
    return splits


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="записать манифесты и строку в seed-registry")
    parser.add_argument("--seed", type=int, default=20260725)
    args = parser.parse_args()

    rows = read_rows(DOCUMENTS)
    registry = {row["document_id"]: row for row in rows}
    atoms, union = build_atoms(rows)
    rng = random.Random(args.seed)

    registry_hash = hashlib.sha256(DOCUMENTS.read_bytes()).hexdigest()
    # Локальная дата, а не UTC: журналы проекта ведутся в MSK, и манифест,
    # датированный вчерашним днём из-за смещения на три часа, ломает сверку
    # с corpus-changelog.md.
    today = datetime.now().date().isoformat()

    print(f"Разбиения: {SPLIT_VERSION}, сид {args.seed}, документов {len(rows)}")
    sizes = Counter(len(members) for members in atoms.values())
    print(f"Атомов provenance: {len(atoms)} (из них больше одного документа: {sum(1 for m in atoms.values() if len(m) > 1)})")
    print(f"  размеры атомов: " + ", ".join(f"{size} док.×{count}" for size, count in sorted(sizes.items())[:6]))
    print(f"Hard-human документов: {sum(1 for row in rows if is_hard_human(row))} — в обучение не идут ни в одном разбиении")
    print()

    splits = plan(rows, registry, atoms, union, rng)
    failed = 0
    print(f"{'разбиение':34} {'train':>6} {'test':>6} {'H/A test':>10} {'источн.':>8}  замечания")
    for split in splits:
        problems = check(split, registry, atoms, union)
        stats = split["stats"]
        origin = stats["test_origin"]
        print(
            f"{split['split_name']:34} {stats['train']:6d} {stats['test']:6d} "
            f"{origin.get('H', 0):5d}/{origin.get('A', 0):<4d} {split['sources_crossing']:8d}  "
            + "; ".join(problems + split["warnings"])
        )
        failed += len(problems)

    if args.write:
        SPLITS.mkdir(parents=True, exist_ok=True)
        for split in splits:
            payload = dict(split)
            payload.update(
                {
                    "split_version": SPLIT_VERSION,
                    "seed": args.seed,
                    "created_at": today,
                    "registry_file": str(DOCUMENTS.relative_to(ROOT)).replace("\\", "/"),
                    "registry_sha256": registry_hash,
                    "documents_total": len(rows),
                    "test_share_target": TEST_SHARE,
                    "atom_keys": list(ATOM_KEYS),
                }
            )
            path = SPLITS / f"{split['split_name']}_{today}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nЗаписано манифестов: {len(splits)} в {SPLITS.relative_to(ROOT)}")

        with SEEDS.open("a", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow(
                [
                    f"splits-{today}",
                    "групповые разбиения по восьми holdout",
                    args.seed,
                    "09-tools/make_splits.py",
                    today,
                    SPLIT_VERSION,
                    f"random.Random({args.seed}): отбор значений оси для holdout_topic, holdout_author, holdout_source; "
                    f"остальные оси перебираются целиком; доля теста по значениям {TEST_SHARE}",
                ]
            )
        print(f"Сид записан в {SEEDS.relative_to(ROOT)}")
    else:
        print("\nМанифесты не записаны: запуск без --write")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
