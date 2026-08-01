#!/usr/bin/env python3
"""Отдельный этап: жанровые перцентили как корпус-зависимый артефакт.

    python 09-tools/compute_percentiles.py --regression        # сверка с prep-v4
    python 09-tools/compute_percentiles.py --prep-version prep-v5 --matrix <файл>

Перцентиль зависит от состава корпуса, поэтому его нельзя считать внутри
экстрактора: при частичном прогоне пул оказался бы неполным, а шкала —
несопоставимой с прежней. Этап выделен по решению PI от 2026-07-29.

**Правило воспроизводится дословно** из `extract_features.py`, чтобы вынос логики
не изменил метод одновременно с исправлением корпуса:

- значение документа — `normalized_value`, при его отсутствии `raw_value`;
- пул — значения того же признака у документов того же жанра, у которых значение
  есть; документы без значения в пул не входят и перцентиля не получают;
- ранг — число элементов пула **строго меньше** значения, поэтому совпадающие
  значения получают одинаковый ранг по нижней границе;
- перцентиль — `rank / len(pool)`, формат `%.4f`.

Режим `--regression` считает перцентили по матрице prep-v4 и требует побитового
совпадения с колонкой `genre_percentile` этой же матрицы. Пока эквивалентность не
доказана, расчёт для v5 запускать нельзя.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
MATRIX_V4 = ROOT / "06-features" / "feature-matrix.csv"
OUT_DIR = ROOT / "06-features"

RULE_VERSION = "percentile-v1.0"
RULE = {
    "value": "normalized_value, при пустом — raw_value",
    "pool": "значения того же feature_id у документов того же genre, где значение есть",
    "rank": "число элементов пула строго меньше значения (ties по нижней границе)",
    "formula": "rank / len(pool)",
    "format": "%.4f",
    "missing": "документ без значения перцентиля не получает и в пул не входит",
}


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


REGISTRY_V4 = ROOT / "04-corpus" / "documents-registry.csv.bak-before-correction-exclusion"


def read_registry(path=DOCUMENTS):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["document_id"]: r for r in csv.DictReader(fh)}


def compute(matrix_path, genre_of):
    """Перцентили по правилу RULE. Возвращает {(document_id, feature_id): строка}."""
    pools = defaultdict(list)
    rows = []
    with matrix_path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            value = r["normalized_value"] or r["raw_value"]
            genre = genre_of.get(r["document_id"])
            if value == "" or genre is None:
                continue
            value = float(value)
            rows.append((r["document_id"], r["feature_id"], genre, value))
            pools[(r["feature_id"], genre)].append(value)
    for key in pools:
        pools[key].sort()
    out = {}
    for doc_id, feature_id, genre, value in rows:
        pool = pools[(feature_id, genre)]
        rank = sum(1 for item in pool if item < value)
        out[(doc_id, feature_id)] = f"{rank / len(pool):.4f}" if pool else ""
    return out


def regression(genre_of):
    """Сверка новой реализации с колонкой genre_percentile матрицы prep-v4."""
    computed = compute(MATRIX_V4, genre_of)
    checked = mismatched = 0
    examples = []
    with MATRIX_V4.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            key = (r["document_id"], r["feature_id"])
            if key not in computed:
                continue
            checked += 1
            if computed[key] != r["genre_percentile"]:
                mismatched += 1
                if len(examples) < 10:
                    examples.append((key, r["genre_percentile"], computed[key]))
    return checked, mismatched, examples


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regression", action="store_true",
                        help="сверить реализацию с prep-v4 и выйти")
    parser.add_argument("--prep-version", help="версия препроцессинга для расчёта")
    parser.add_argument("--matrix", help="матрица признаков для расчёта")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Регрессия проверяет метод, а не состав, поэтому идёт на том же реестре,
    # при котором считалась матрица prep-v4 — до исключения 34 документов.
    # На текущем реестре пул был бы меньше, и различие метода смешалось бы с
    # различием состава: первая попытка 2026-07-29 дала именно это.
    registry_path = REGISTRY_V4 if args.regression else DOCUMENTS
    registry = read_registry(registry_path)
    genre_of = {d: r["genre"] for d, r in registry.items()}

    if args.regression:
        print(f"регрессионная проверка {RULE_VERSION} на prep-v4, {stamp}")
        print(f"  реестр состава v4: {registry_path.name}, документов {len(registry)}")
        checked, mismatched, examples = regression(genre_of)
        print(f"  сверено значений: {checked}, расхождений: {mismatched}")
        for (doc, feature), was, now in examples:
            print(f"    {doc} {feature}: было {was}, стало {now}")
        report = OUT_DIR / "genre-percentiles-regression.md"
        report.write_text("\n".join([
            "# Регрессионная проверка выноса перцентилей",
            "",
            f"Собрано {stamp} скриптом `09-tools/compute_percentiles.py`, "
            f"правило `{RULE_VERSION}`.",
            "",
            "Новая реализация прогнана на матрице prep-v4 и сверена с её же "
            "колонкой `genre_percentile`.",
            "",
            "| Что | Значение |",
            "|---|---|",
            f"| сверено значений | {checked} |",
            f"| расхождений | **{mismatched}** |",
            "",
            "## Правило",
            "",
            *[f"- **{k}**: {v};" for k, v in RULE.items()],
            "",
            ("**Вердикт: реализации эквивалентны.** Вынос перцентилей отдельным "
             "этапом не меняет метод, поэтому расчёт для prep-v5 допустим: "
             "различия v4 против v5 отражают только изменения входных данных и "
             "референсной выборки — исключение 34 документов, изменение текстов 69, "
             "вызванный этим пересчёт их raw и normalized значений и пересборку "
             "жанровых пулов, — но не изменение метода вычисления перцентилей."
             if mismatched == 0 else
             "**Вердикт: расхождения есть.** Пока они не объяснены, считать "
             "перцентили для prep-v5 нельзя: смена метода смешается с очисткой."),
            "",
        ]) + "\n", encoding="utf-8")
        print(f"  отчёт: {report.name}")
        if mismatched:
            raise SystemExit("регрессия не пройдена — расчёт для v5 запрещён")
        return

    if not (args.prep_version and args.matrix):
        parser.error("для расчёта укажите --prep-version и --matrix")

    matrix_path = Path(args.matrix)
    if not matrix_path.is_absolute():
        matrix_path = ROOT / matrix_path
    print(f"перцентили {args.prep_version}, {stamp}")
    computed = compute(matrix_path, genre_of)

    out_csv = OUT_DIR / f"genre-percentiles-{args.prep_version}.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["document_id", "feature_id", "genre_percentile"])
        for (doc_id, feature_id), value in sorted(computed.items()):
            writer.writerow([doc_id, feature_id, value])

    # Ключ ссылается только на upstream-вход. Хеш итоговой матрицы сюда не
    # входит: она собирается с использованием этого артефакта, и ссылка на неё
    # замкнула бы хеш на самого себя. Обратную ссылку несёт манифест матрицы.
    if "feature-matrix" in matrix_path.name:
        raise SystemExit(
            f"на вход подана итоговая матрица {matrix_path.name}: ключ перцентилей "
            "ссылался бы на артефакт, который сам собирается из перцентилей. "
            "Подавайте features-normalized-<версия>.csv")

    key = {
        "rule_version": RULE_VERSION, "rule": RULE,
        "prep_version": args.prep_version,
        "registry_sha256": sha256_file(DOCUMENTS),
        "features_normalized_sha256": sha256_file(matrix_path),
        "features_normalized_file": matrix_path.name,
        "reference_population_definition": (
            "все документы реестра prep-v5, у которых значение признака есть; "
            "пул строится внутри пары feature_id × genre; документы без значения "
            "в пул не входят"),
        "reference_set": {"documents": len({d for d, _ in computed})},
        "created_at": stamp,
    }
    (OUT_DIR / f"genre-percentiles-{args.prep_version}.key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  значений {len(computed)}, документов "
          f"{key['reference_set']['documents']}")
    print(f"  записано: {out_csv.name} и ключ")


if __name__ == "__main__":
    main()
