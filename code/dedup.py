#!/usr/bin/env python3
"""Поиск дублей и почти-дублей корпуса, заполнение dedup_cluster_id.

Запуск из корня папки исследования:
    python 09-tools/dedup.py                 # только отчёт, реестр не трогается
    python 09-tools/dedup.py --write         # плюс запись dedup_cluster_id в реестр
    python 09-tools/dedup.py --threshold 0.7 # другой порог почти-дубля

Зачем. До сегодняшнего дня поле dedup_cluster_id заполняли сборщики: каждый
писал туда хеш нормализованного текста своего документа. Такое поле ловит
только побайтовое совпадение и по построению не может поймать переиздание той
же статьи под другим заголовком или машинную ячейку, повторившую соседнюю на
90%. Разбиения из splits-spec.md требуют, чтобы почти-дубли лежали по одну
сторону train/test, — значит кластер должен строиться по сходству, а не по
равенству.

Процедура:
  1. текст нормализуется под сравнение (регистр, пробелы, пунктуация);
  2. документ представляется множеством словных 5-грамм (шинглов);
  3. по шинглам считается MinHash на 128 детерминированных хеш-функциях;
  4. оценка сходства считается для всех пар, кандидаты уточняются точным
     Jaccard по множествам шинглов;
  5. пары выше порога соединяются транзитивно (union-find) в кластеры.

Детерминированность: хеш-функции строятся на blake2b с фиксированными солями,
встроенный hash() не используется — он рандомизирован между запусками.
Случайного выбора в процедуре нет, поэтому записи в seed-registry.csv не
требуется.

Скрипт без --write ничего не меняет на диске. Выход: 0 — почти-дублей выше
порога нет, 1 — есть.
"""

import argparse
import csv
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
REPORT = ROOT / "04-corpus" / "dedup-report.csv"

DEDUP_VERSION = "dedup-v1"

# Длина шингла в словах. Пять — стандарт корпусной дедупликации: короче даёт
# ложные совпадения на устойчивых оборотах, длиннее пропускает пересказ.
SHINGLE = 5
# Число хеш-функций MinHash. Ошибка оценки Jaccard ~ 1/sqrt(128) ≈ 0.09,
# поэтому кандидаты берутся с запасом и уточняются точным Jaccard.
PERMUTATIONS = 128
# Простое число Мерсенна 2^31-1 — модуль универсального хеширования.
MERSENNE = (1 << 31) - 1
# Порог отбора кандидатов по оценке MinHash: ниже порога отчёта на запас ошибки.
CANDIDATE = 0.40
# Порог попадания в отчёт. Пары от 0.50 до порога дубля — материал для глаз PI.
REPORT_FROM = 0.50

WORD = re.compile(r"[^\w\s]+", re.UNICODE)
SPACE = re.compile(r"\s+")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader), reader.fieldnames


def normalize(text):
    """Нормализация под сравнение, а не под расчёт признаков.

    Регистр, пунктуация и разбиение на строки для дедупликации шумом не
    являются: переизданная статья отличается от исходной ровно ими. Здесь
    снимается всё, что не несёт словесного содержания. Препроцессинг признаков
    живёт отдельно и мягче — 06-features/preprocessing-spec.md.
    """
    text = text.replace(" ", " ").replace("­", "")
    text = WORD.sub(" ", text.lower())
    return SPACE.sub(" ", text).strip()


def shingles(text):
    words = text.split()
    if len(words) < SHINGLE:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def shingle_codes(items):
    """Шинглы → 32-битные коды. blake2b вместо hash(): нужен один и тот же
    результат между запусками и машинами. Разрядность 32 выбрана, чтобы
    произведение a * x в универсальном хешировании укладывалось в uint64 без
    переполнения; на точность результата это не влияет — Jaccard кандидатов
    всё равно уточняется по самим строкам шинглов."""
    return np.array(
        [int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=4).digest(), "big") for s in items],
        dtype=np.uint64,
    )


def minhash(codes, coeffs):
    """Универсальное хеширование: (a * x + b) mod 2^31-1, минимум по каждой функции."""
    if codes.size == 0:
        return np.full(PERMUTATIONS, np.iinfo(np.uint64).max, dtype=np.uint64)
    prime = np.uint64(MERSENNE)
    a, b = coeffs
    values = (codes[:, None] * a[None, :] + b[None, :]) % prime
    return values.min(axis=0)


def hash_coefficients():
    """Коэффициенты хеш-функций из фиксированной соли — воспроизводимо."""
    a, b = [], []
    for i in range(PERMUTATIONS):
        seed_a = hashlib.blake2b(f"minhash-a-{i}".encode(), digest_size=4).digest()
        seed_b = hashlib.blake2b(f"minhash-b-{i}".encode(), digest_size=4).digest()
        a.append(int.from_bytes(seed_a, "big") % (MERSENNE - 1) + 1)
        b.append(int.from_bytes(seed_b, "big") % MERSENNE)
    return np.array(a, dtype=np.uint64), np.array(b, dtype=np.uint64)


def jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


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


def load_documents(rows):
    """Читает тексты документов. Отсутствующий файл — ошибка реестра, её ловит
    validate_registry.py; здесь документ пропускается с пометкой."""
    docs, missing = [], []
    for row in rows:
        path = ROOT / (row.get("file_path") or "")
        if not path.exists():
            missing.append(row.get("document_id"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        norm = normalize(text)
        docs.append(
            {
                "id": row["document_id"],
                "row": row,
                "shingles": shingles(norm),
                "exact": hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16],
                "words": len(norm.split()),
            }
        )
    return docs, missing


def pair_candidates(signatures):
    """Оценка сходства всех пар по MinHash. 1862 документа × 128 подписей —
    матрица целиком помещается в память, LSH не нужен и не создаёт риска
    пропущенного кандидата."""
    count = len(signatures)
    pairs = []
    for i in range(count):
        if i + 1 >= count:
            break
        block = signatures[i + 1 :]
        share = (block == signatures[i]).sum(axis=1) / PERMUTATIONS
        for offset in np.nonzero(share >= CANDIDATE)[0]:
            pairs.append((i, i + 1 + int(offset), float(share[offset])))
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="записать dedup_cluster_id в реестр")
    parser.add_argument("--threshold", type=float, default=0.80, help="порог почти-дубля по Jaccard (по умолчанию 0.80)")
    args = parser.parse_args()

    rows, fieldnames = read_rows(DOCUMENTS)
    print(f"Дедупликация: {DEDUP_VERSION}, шингл {SHINGLE} слов, порог {args.threshold}")
    print(f"Документов в реестре: {len(rows)}")

    docs, missing = load_documents(rows)
    if missing:
        print(f"  ! файлов нет на диске: {len(missing)} — {missing[:5]}")
    print(f"Прочитано документов: {len(docs)}")

    short = [d["id"] for d in docs if d["words"] < SHINGLE]
    if short:
        print(f"  ! короче шингла: {len(short)}")

    coeffs = hash_coefficients()
    signatures = np.vstack([minhash(shingle_codes(d["shingles"]), coeffs) for d in docs])

    candidates = pair_candidates(signatures)
    print(f"Пар-кандидатов после MinHash: {len(candidates)}")

    exact_groups = defaultdict(list)
    for doc in docs:
        exact_groups[doc["exact"]].append(doc["id"])
    exact_dups = {k: v for k, v in exact_groups.items() if len(v) > 1}

    scored = []
    for i, j, estimate in candidates:
        value = jaccard(docs[i]["shingles"], docs[j]["shingles"])
        if value >= REPORT_FROM:
            scored.append((docs[i], docs[j], value, estimate))
    scored.sort(key=lambda item: -item[2])

    union = Union([d["id"] for d in docs])
    for left, right, value, _ in scored:
        if value >= args.threshold:
            union.union(left["id"], right["id"])
    # Точные дубли соединяются всегда: MinHash их тоже находит, но связь
    # не должна зависеть от порога.
    for ids in exact_dups.values():
        for other in ids[1:]:
            union.union(ids[0], other)

    clusters = defaultdict(list)
    for doc in docs:
        clusters[union.find(doc["id"])].append(doc["id"])
    multi = {k: sorted(v) for k, v in clusters.items() if len(v) > 1}

    print()
    print(f"Точных дублей по нормализованному тексту: {len(exact_dups)} групп")
    for key, ids in sorted(exact_dups.items()):
        print(f"  {key}: {', '.join(sorted(ids))}")
    print(f"Пар с Jaccard ≥ {REPORT_FROM}: {len(scored)}")
    above = [item for item in scored if item[2] >= args.threshold]
    print(f"  из них почти-дубли (≥ {args.threshold}): {len(above)}")
    print(f"Кластеров больше одного документа: {len(multi)}")

    cross = [item for item in above if item[0]["row"]["origin_class"] != item[1]["row"]["origin_class"]]
    if cross:
        print(f"  ! среди них пар H↔A: {len(cross)} — это утечка класса, разбирать поимённо")

    if above:
        by_origin = Counter(
            tuple(sorted((item[0]["row"]["origin_class"], item[1]["row"]["origin_class"]))) for item in above
        )
        print("  состав пар по классам: " + ", ".join(f"{a}-{b}={n}" for (a, b), n in sorted(by_origin.items())))
        by_channel = Counter(
            tuple(sorted((item[0]["row"].get("generation_channel") or "human", item[1]["row"].get("generation_channel") or "human")))
            for item in above
        )
        print("  состав пар по каналам: " + ", ".join(f"{a}|{b}={n}" for (a, b), n in sorted(by_channel.items())))

    with REPORT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "left_id", "right_id", "jaccard", "minhash_estimate", "verdict",
                "left_origin", "right_origin", "left_channel", "right_channel",
                "left_source", "right_source", "left_words", "right_words", "dedup_version",
            ]
        )
        for left, right, value, estimate in scored:
            writer.writerow(
                [
                    left["id"], right["id"], f"{value:.4f}", f"{estimate:.4f}",
                    "near-duplicate" if value >= args.threshold else "similar",
                    left["row"]["origin_class"], right["row"]["origin_class"],
                    left["row"].get("generation_channel") or "", right["row"].get("generation_channel") or "",
                    left["row"].get("split_group_source") or "", right["row"].get("split_group_source") or "",
                    left["words"], right["words"], DEDUP_VERSION,
                ]
            )
    print(f"Отчёт: {REPORT.relative_to(ROOT)} ({len(scored)} строк)")

    if args.write:
        # Идентификатор кластера — минимальный хеш содержимого среди членов:
        # не зависит от порядка чтения и переживает переименование документов.
        assign = {}
        for members in clusters.values():
            digests = sorted(next(d["exact"] for d in docs if d["id"] == member) for member in members)
            for member in members:
                assign[member] = digests[0]

        backup = DOCUMENTS.with_suffix(f".csv.bak-before-{DEDUP_VERSION}")
        if not backup.exists():
            backup.write_bytes(DOCUMENTS.read_bytes())
            print(f"Резервная копия реестра: {backup.name}")

        changed = 0
        for row in rows:
            new = assign.get(row["document_id"])
            if new and row.get("dedup_cluster_id") != new:
                row["dedup_cluster_id"] = new
                changed += 1
        with DOCUMENTS.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Реестр обновлён: изменено значений dedup_cluster_id — {changed}")
    else:
        print("Реестр не менялся: запуск без --write")

    return 1 if above else 0


if __name__ == "__main__":
    sys.exit(main())
