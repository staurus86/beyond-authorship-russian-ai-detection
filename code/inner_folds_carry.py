#!/usr/bin/env python3
"""Сопоставимость inner-разбиений P2b между clf-v1 и clf-v2: сравнение и перенос.

    python 09-tools/inner_folds_carry.py

`GroupKFold` учитывает размеры групп. TexTerra и Dr.Max уменьшились после
коррекции, поэтому человеческий источник мог сменить inner fold — тем же
механизмом, который обнаружился во внешнем разбиении.

Скрипт строит соответствие `source → inner_fold` дважды: на составе до
исключения 34 документов и на текущем, — сравнивает их и **переносит прежнее**,
записывая `splits-v5/p2b-inner-folds-carried.json`. Тогда различие clf-v1 → clf-v2
отражает коррекцию корпуса, а не смену tuning split.

Внешние fold-ы берутся уже перенесённые: `p2b-outer-folds-carried.json`.
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BACKUP = ROOT / "04-corpus" / "documents-registry.csv.bak-before-correction-exclusion"
CARRIED_OUTER = ROOT / "07-analysis" / "splits-v5" / "p2b-outer-folds-carried.json"
OUT_JSON = ROOT / "07-analysis" / "splits-v5" / "p2b-inner-folds-carried.json"
OUT_REPORT = ROOT / "07-analysis" / "inner-folds-carry.md"


def group_of(row):
    return row["split_group_source"] or row["generation_channel"]


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def assignments(registry, fold):
    """Соответствие группа → номер inner fold, в котором она попадает в validation."""
    held_sources, held_channel = set(fold["human"]), fold["ai"]
    train = [r for r in registry
             if not ((r["origin_class"] == "A"
                      and r["generation_channel"] == held_channel)
                     or (r["origin_class"] == "H"
                         and r["split_group_source"] in held_sources))]
    groups = [group_of(r) for r in train]
    n_groups = len(set(groups))
    splitter = GroupKFold(n_splits=min(clf.INNER_FOLDS, n_groups))
    x = np.zeros((len(train), 1))
    y = np.array([1 if r["origin_class"] == "A" else 0 for r in train])
    mapping = {}
    for index, (_, va) in enumerate(splitter.split(x, y, groups)):
        for i in va:
            mapping[groups[i]] = index
    return mapping


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"перенос inner-разбиений P2b, {stamp}")

    before, now = read(BACKUP), read(clf.DOCUMENTS)
    outer = json.loads(CARRIED_OUTER.read_text(encoding="utf-8"))["folds"]

    carried, report = {}, []
    total_moved = 0
    for key in sorted(outer, key=int):
        fold = outer[key]
        old = assignments(before, fold)
        new = assignments(now, fold)
        shared = sorted(set(old) & set(new))
        moved = [g for g in shared if old[g] != new[g]]
        gone = sorted(set(old) - set(new))
        total_moved += len(moved)
        carried[key] = {"held_out_channel": fold["ai"], "assignments": old}
        report.append({"fold": key, "channel": fold["ai"], "groups": len(old),
                       "moved": moved, "gone": gone})
        print(f"  outer {key} ({fold['ai']}): групп {len(old)}, сменили inner "
              f"{len(moved)}, исчезли из train {len(gone)}")

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(
        {"carried_from": BACKUP.name, "seed_note": "GroupKFold детерминирован, "
         "сид не используется; порядок задаётся размерами групп",
         "inner_folds": clf.INNER_FOLDS, "created_at": stamp,
         "note": "соответствие группа → inner fold зафиксировано на составе до "
                 "исключения 34 документов; исключённые документы вычитаются, "
                 "группы не перераспределяются",
         "folds": carried}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Сопоставимость inner-разбиений P2b между clf-v1 и clf-v2", "",
             f"Собрано {stamp} скриптом `09-tools/inner_folds_carry.py`.", "",
             "`GroupKFold` распределяет группы с учётом их размера, поэтому "
             "сокращение TexTerra и Dr.Max могло сдвинуть человеческие источники "
             "между inner fold-ами так же, как это произошло во внешнем разбиении.",
             "",
             "| Outer fold | Канал | Групп в train | Сменили inner fold | Исчезли из train |",
             "|---|---|---|---|---|"]
    for r in report:
        lines.append(f"| {r['fold']} | {r['channel']} | {r['groups']} | "
                     f"{len(r['moved'])} | {len(r['gone'])} |")
    lines.append("")
    if total_moved:
        lines += ["## Группы, сменившие inner fold при пересборке", ""]
        for r in report:
            if r["moved"]:
                lines.append(f"**outer {r['fold']} ({r['channel']}):** "
                             + ", ".join(f"`{g}`" for g in r["moved"][:40])
                             + (" …" if len(r["moved"]) > 40 else "") + ";")
        lines.append("")
    lines += [
        ("**Соответствия различаются, поэтому взят перенос.** Прежнее "
         "распределение `группа → inner fold` зафиксировано на составе до "
         "исключения и записано в `p2b-inner-folds-carried.json`; исключённые "
         "документы вычитаются, группы не перераспределяются. Тогда различие "
         "clf-v1 → clf-v2 отражает коррекцию корпуса, а не смену tuning split."
         if total_moved else
         "**Соответствия совпали полностью.** Пересборка inner fold-ов даёт то же "
         "распределение, что и прежде; вопрос сопоставимости закрыт без переноса. "
         "Файл `p2b-inner-folds-carried.json` записан для явной фиксации."),
        "",
        "Файл читает `clf_run` при пересчёте на prep-v5: подбор регуляризации "
        "обязан идти по этому распределению, а не строить своё.",
        ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  всего групп сменили inner fold: {total_moved}")
    print(f"  записано: {OUT_JSON.name}, отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
