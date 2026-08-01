#!/usr/bin/env python3
"""Проверка test-retest: повторный расчёт на подвыборке (retest-v1).

Процедура зафиксирована в `06-features/retest-spec.md` до прогона.

    python 09-tools/retest_features.py --round 1   # тот же разбор, признаки заново
    python 09-tools/retest_features.py --round 2   # разбор заново, сверка кэшей
    python 09-tools/retest_features.py --report    # собрать отчёт из результатов кругов

Круг 1 проверяет детерминизм кода признаков, круг 2 — стабильность Stanza,
bge-m3 и Natasha. Матрица не изменяется ни в одном из кругов: экстракторы
получают ключ `--out`, кэш круга 2 восстанавливается из копии.
"""

import argparse
import csv
import gzip
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "09-tools"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
PILOT1 = ROOT / "06-features" / "pilot-1-ids.csv"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
IDS_FILE = ROOT / "06-features" / "retest-ids.txt"
WORK = ROOT / "06-features" / "retest"
CACHE = ROOT / "06-features" / "cache"
BACKUP = CACHE / "_retest-backup"
REPORT_MD = ROOT / "06-features" / "retest-report.md"
REPORT_CSV = ROOT / "06-features" / "retest-by-feature.csv"

RETEST_VERSION = "retest-v1"

# §5 спецификации. Межтекстовый слой и X05 требуют полной группы сравнения,
# поэтому считаются на всём корпусе, а сверяются на подвыборке.
EXTRACTORS = [
    {"version": "feat-v1", "script": "extract_features.py",
     "args": ["--stage", "features"], "subset": True},
    {"version": "lex-v1", "script": "extract_lexicon.py",
     "args": [], "subset": True},
    {"version": "sem-v1", "script": "extract_semantic.py",
     "args": ["--stage", "features"], "subset": False},
    {"version": "ner-v3", "script": "extract_ner.py",
     "args": ["--stage", "features"], "subset": True},
    {"version": "art-v2", "script": "extract_artifacts.py",
     "args": [], "subset": True},
    {"version": "disc-v1", "script": "extract_discourse.py",
     "args": [], "subset": True},
    {"version": "x-v1", "script": "extract_intertext.py",
     "args": [], "subset": False},
]

# §3 спецификации: пересчёт разбора и что с чем сверяется.
PARSERS = [
    {"cache": "stanza-v1", "script": "extract_features.py", "args": ["--stage", "parse"],
     "suffix": ".json.gz", "kind": "json"},
    {"cache": "embed-v1", "script": "extract_semantic.py", "args": ["--stage", "embed"],
     "suffix": ".npz", "kind": "npz"},
    {"cache": "ner-v1", "script": "extract_ner.py", "args": ["--stage", "tag"],
     "suffix": ".json.gz", "kind": "json"},
]


def build_ids():
    """Подвыборка: development set Pilot-1 за вычетом исключённых документов."""
    registry = {row["document_id"] for row in csv.DictReader(
        DOCUMENTS.open(encoding="utf-8-sig", newline=""))}
    pilot = [row["document_id"] for row in csv.DictReader(
        PILOT1.open(encoding="utf-8", newline=""))]
    ids = [doc for doc in pilot if doc in registry]
    dropped = [doc for doc in pilot if doc not in registry]
    IDS_FILE.write_text("\n".join(ids) + "\n", encoding="utf-8")
    return ids, dropped


def owned_features():
    """(признак → версия) по матрице: чем экстрактор владеет на самом деле.

    Экстрактор пишет строки и для чужих признаков, проставляя им заглушку
    с собственной версией. Сверять их с матрицей нельзя — там значение
    другого слоя, и расхождение означало бы только порядок запуска.
    """
    owner = {}
    with MATRIX.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            owner.setdefault(row["feature_id"], row["extractor_version"])
    return owner


def read_matrix_values(ids):
    values = {}
    with MATRIX.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["document_id"] in ids:
                values[(row["document_id"], row["feature_id"])] = (
                    row["raw_value"], row["normalized_value"])
    return values


def run(script, args):
    cmd = [sys.executable, str(TOOLS / script), *args]
    print("  $ " + " ".join(cmd[1:]))
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(f"прогон {script} завершился с кодом {result.returncode}")
    return result.stdout


def round1(ids):
    WORK.mkdir(parents=True, exist_ok=True)
    for extractor in EXTRACTORS:
        out = WORK / f"{extractor['version']}.csv"
        args = list(extractor["args"])
        if extractor["subset"]:
            args += ["--ids-file", str(IDS_FILE)]
        args += ["--out", str(out)]
        print(f"{extractor['version']}:")
        run(extractor["script"], args)
    print(f"круг 1: результаты в {WORK.relative_to(ROOT)}")


def compare_values(ids, folder, round_no):
    owner = owned_features()
    old = read_matrix_values(ids)
    rows = []
    for extractor in EXTRACTORS:
        path = folder / f"{extractor['version']}.csv"
        if not path.exists():
            continue
        stats = {}
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                fid = row["feature_id"]
                if row["document_id"] not in ids:
                    continue
                if owner.get(fid) != extractor["version"]:
                    continue
                entry = stats.setdefault(fid, {"n": 0, "diff": 0, "examples": [],
                                                "abs": 0.0, "rel": 0.0})
                entry["n"] += 1
                current = (row["raw_value"], row["normalized_value"])
                was = old.get((row["document_id"], fid))
                if was != current:
                    entry["diff"] += 1
                    # Величина расхождения: без неё «нестабилен» не отличить
                    # от расхождения в последней значащей цифре.
                    for a, b in zip(was or ("", ""), current):
                        try:
                            x, y = float(a), float(b)
                        except ValueError:
                            continue
                        entry["abs"] = max(entry["abs"], abs(x - y))
                        if x:
                            entry["rel"] = max(entry["rel"], abs(x - y) / abs(x))
                    if len(entry["examples"]) < 3:
                        entry["examples"].append((row["document_id"], was, current))
        for fid, entry in sorted(stats.items()):
            rows.append({"round": round_no, "extractor_version": extractor["version"],
                         "feature_id": fid, "n_compared": entry["n"],
                         "n_diff": entry["diff"], "examples": entry["examples"],
                         "max_abs": entry["abs"], "max_rel": entry["rel"]})
    return rows


def backup_cache(ids):
    saved = 0
    for parser in PARSERS:
        target = BACKUP / parser["cache"]
        target.mkdir(parents=True, exist_ok=True)
        for doc in ids:
            src = CACHE / parser["cache"] / f"{doc}{parser['suffix']}"
            if src.exists():
                shutil.copy2(src, target / src.name)
                saved += 1
    return saved


def restore_cache(ids):
    restored = 0
    for parser in PARSERS:
        for doc in ids:
            src = BACKUP / parser["cache"] / f"{doc}{parser['suffix']}"
            if src.exists():
                shutil.copy2(src, CACHE / parser["cache"] / src.name)
                restored += 1
    return restored


# Поля, которые описывают прогон, а не разбор. Пересчёт ставит в них текущие
# значения, и сравнение объектов целиком объявляло бы разбор нестабильным из-за
# смены метки версии препроцессинга. §3 спецификации требует сверять сам разбор.
STAMP_FIELDS = ("prep_version", "profile", "stanza", "natasha", "slovnet", "document_id")


def compare_json(path_a, path_b):
    """Сверка объектов разбора: предложения, токены, леммы, теги, спаны."""
    def load(path):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    a, b = load(path_a), load(path_b)
    content_a = {k: v for k, v in a.items() if k not in STAMP_FIELDS}
    content_b = {k: v for k, v in b.items() if k not in STAMP_FIELDS}
    if content_a != content_b:
        keys = sorted(k for k in set(content_a) | set(content_b)
                      if content_a.get(k) != content_b.get(k))
        return "разбор различается: " + ", ".join(keys)
    stamps = sorted(k for k in STAMP_FIELDS if a.get(k) != b.get(k))
    if stamps:
        return "равны, сменились метки: " + ", ".join(
            f"{k} {a.get(k)} → {b.get(k)}" for k in stamps)
    return "равны"


def compare_npz(path_a, path_b):
    import numpy as np
    a, b = np.load(path_a), np.load(path_b)
    keys_a, keys_b = sorted(a.files), sorted(b.files)
    if keys_a != keys_b:
        return f"разный состав массивов: {keys_a} против {keys_b}"
    worst = 0.0
    for key in keys_a:
        x, y = a[key], b[key]
        if x.shape != y.shape:
            return f"{key}: разная форма {x.shape} против {y.shape}"
        if x.size:
            worst = max(worst, float(np.max(np.abs(x.astype("float64") - y.astype("float64")))))
    return worst


def round2(ids):
    saved = backup_cache(ids)
    print(f"круг 2: сохранено файлов кэша {saved}")
    results = []
    try:
        for parser in PARSERS:
            print(f"{parser['cache']}:")
            run(parser["script"], [*parser["args"], "--ids-file", str(IDS_FILE), "--force"])
            same, differ, stamps, worst, examples = 0, 0, 0, 0.0, []
            for doc in ids:
                new = CACHE / parser["cache"] / f"{doc}{parser['suffix']}"
                old = BACKUP / parser["cache"] / f"{doc}{parser['suffix']}"
                if not (new.exists() and old.exists()):
                    continue
                if parser["kind"] == "json":
                    verdict = compare_json(old, new)
                    if verdict.startswith("равны"):
                        # Смена метки версии — не расхождение разбора: пересчёт
                        # ставит текущую версию препроцессинга, текст тот же.
                        same += 1
                        if "метки" in verdict:
                            stamps += 1
                            if len(examples) < 5:
                                examples.append((doc, verdict))
                    else:
                        differ += 1
                        if len(examples) < 5:
                            examples.append((doc, verdict))
                else:
                    verdict = compare_npz(old, new)
                    if isinstance(verdict, float):
                        worst = max(worst, verdict)
                        if verdict == 0.0:
                            same += 1
                        else:
                            differ += 1
                            if len(examples) < 5:
                                examples.append((doc, f"max|Δ| = {verdict:.3e}"))
                    else:
                        differ += 1
                        if len(examples) < 5:
                            examples.append((doc, verdict))
            results.append({"cache": parser["cache"], "same": same, "differ": differ,
                            "stamps": stamps, "worst": worst, "examples": examples})
            print(f"  совпало {same} (из них со сменой метки версии {stamps}), различается {differ}")
        # Признаки поверх нового разбора: без этого шага осталось бы неизвестным,
        # доходит ли расхождение эмбеддингов до значений в матрице.
        print("признаки поверх нового разбора:")
        recomputed = WORK / "after-reparse"
        recomputed.mkdir(parents=True, exist_ok=True)
        for extractor in EXTRACTORS:
            args = list(extractor["args"])
            if extractor["subset"]:
                args += ["--ids-file", str(IDS_FILE)]
            args += ["--out", str(recomputed / f"{extractor['version']}.csv")]
            run(extractor["script"], args)
    finally:
        restored = restore_cache(ids)
        print(f"кэш восстановлен из копии: файлов {restored}")
    (WORK / "round2.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def write_report(ids, dropped, round1_rows, round2_rows, round2_results):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    out = []
    w = out.append
    w(f"# Отчёт test-retest ({RETEST_VERSION})")
    w("")
    w(f"Процедура — `06-features/retest-spec.md`, зафиксирована до прогона. Прогон {stamp}.")
    w("")
    w(f"Подвыборка — development set Pilot-1, **{len(ids)} документов**. "
      f"Исключено после составления набора: {len(dropped)} "
      + (", ".join(f"`{d}`" for d in dropped) if dropped else "—") + ".")
    w("")
    w("Отчёт отвечает на один вопрос: даёт ли повторный расчёт то же значение. "
      "Значения по классам не сравнивались.")
    w("")

    w("## Круг 1 — повторный расчёт признаков поверх того же разбора")
    w("")
    total = sum(r["n_compared"] for r in round1_rows)
    diff = sum(r["n_diff"] for r in round1_rows)
    w(f"Сверено {total} значений у {len({r['feature_id'] for r in round1_rows})} признаков. "
      f"Расхождений — **{diff}**.")
    w("")
    w("| Экстрактор | Признаков | Сверено значений | Расхождений |")
    w("|---|---|---|---|")
    for extractor in EXTRACTORS:
        rows = [r for r in round1_rows if r["extractor_version"] == extractor["version"]]
        if not rows:
            continue
        w(f"| `{extractor['version']}` | {len(rows)} | {sum(r['n_compared'] for r in rows)} "
          f"| {sum(r['n_diff'] for r in rows)} |")
    w("")
    unstable = [r for r in round1_rows if r["n_diff"]]
    if unstable:
        w("Нестабильные признаки:")
        w("")
        for r in unstable:
            w(f"- **{r['feature_id']}** (`{r['extractor_version']}`): "
              f"{r['n_diff']} из {r['n_compared']}")
            for doc, was, now in r["examples"]:
                w(f"    - `{doc}`: было {was}, стало {now}")
        w("")
    else:
        w("Нестабильных признаков нет.")
        w("")

    w("## Круг 2 — разбор заново")
    w("")
    w("Кэш подвыборки пересчитан с ключом `--force`, затем восстановлен из копии: "
      "матрица посчитана на исходном разборе.")
    w("")
    w("| Кэш | Разбор совпал | Из них сменилась метка версии | Разбор различается | Максимум расхождения |")
    w("|---|---|---|---|---|")
    for result in round2_results:
        worst = f"{result['worst']:.3e}" if result["worst"] else "0"
        w(f"| `{result['cache']}` | {result['same']} | {result.get('stamps', 0)} "
          f"| {result['differ']} | {worst} |")
    w("")
    for result in round2_results:
        if result["examples"]:
            head = ("Смена метки версии" if result.get("stamps") and not result["differ"]
                    else "Расхождения")
            w(f"{head}, `{result['cache']}`:")
            w("")
            for doc, note in result["examples"]:
                w(f"- `{doc}`: {note}")
            w("")

    w("### Признаки поверх нового разбора")
    w("")
    if round2_rows:
        total2 = sum(r["n_compared"] for r in round2_rows)
        diff2 = sum(r["n_diff"] for r in round2_rows)
        w(f"Сверено {total2} значений. Расхождений — **{diff2}**. "
          "Это и есть ответ на вопрос, доходит ли расхождение разбора до матрицы.")
        w("")
        w("| Экстрактор | Признаков | Сверено значений | Расхождений |")
        w("|---|---|---|---|")
        for extractor in EXTRACTORS:
            rows = [r for r in round2_rows if r["extractor_version"] == extractor["version"]]
            if not rows:
                continue
            w(f"| `{extractor['version']}` | {len(rows)} | {sum(r['n_compared'] for r in rows)} "
              f"| {sum(r['n_diff'] for r in rows)} |")
        w("")
        unstable2 = [r for r in round2_rows if r["n_diff"]]
        if unstable2:
            w("Признаки, значение которых изменилось:")
            w("")
            for r in unstable2:
                w(f"- **{r['feature_id']}** (`{r['extractor_version']}`): "
                  f"{r['n_diff']} из {r['n_compared']}, "
                  f"максимум расхождения {r['max_abs']:.2e} по величине "
                  f"и {r['max_rel']:.2e} по доле от значения")
                for doc, was, now in r["examples"]:
                    w(f"    - `{doc}`: было {was}, стало {now}")
            w("")
        else:
            w("Ни одно значение не изменилось.")
            w("")
    else:
        w("Не считалось.")
        w("")

    REPORT_MD.write_text("\n".join(out) + "\n", encoding="utf-8")

    with REPORT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["round", "extractor_version", "feature_id", "n_compared", "n_diff",
                         "max_abs_diff", "max_rel_diff", "verdict"])
        for r in round1_rows + round2_rows:
            writer.writerow([r["round"], r["extractor_version"], r["feature_id"], r["n_compared"],
                             r["n_diff"], f"{r['max_abs']:.3e}", f"{r['max_rel']:.3e}",
                             "стабилен" if not r["n_diff"] else "нестабилен"])
        for result in round2_results:
            writer.writerow([2, result["cache"], "", result["same"] + result["differ"],
                             result["differ"], f"{result['worst']:.3e}", "",
                             "стабилен" if not result["differ"] else "нестабилен"])
    print(f"отчёт: {REPORT_MD.relative_to(ROOT)}")
    print(f"таблица: {REPORT_CSV.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, choices=(1, 2))
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    ids, dropped = build_ids()
    print(f"подвыборка: {len(ids)} документов, исключено из набора Pilot-1: {len(dropped)}")

    if args.round == 1:
        round1(set(ids))
    if args.round == 2:
        round2(ids)
    if args.report:
        round1_rows = compare_values(set(ids), WORK, 1)
        round2_rows = compare_values(set(ids), WORK / "after-reparse", 2)
        path = WORK / "round2.json"
        round2_results = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        write_report(ids, dropped, round1_rows, round2_rows, round2_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
