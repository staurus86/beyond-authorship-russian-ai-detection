#!/usr/bin/env python3
"""Шлюз: пропускает ли подбор регуляризации inner fold-ы в P2b.

    python 09-tools/inner_cv_audit.py

`clf_run.pick_c` молча пропускает разбиение, где в train или validation остался
один класс. Тогда гиперпараметр выбирается не по всем запланированным fold-ам,
часть источников не попадает в validation, а заявленный nested CV не совпадает с
выполненным. Скрипт считает это по фактическим данным для каждого внешнего
fold-а P2b.

Разбиение внешних fold-ов берётся перенесённым — `splits-v5/p2b-outer-folds-carried.json`,
зафиксированным на составе до исключения 34 документов.

Шлюз закрыт, если пропущенных inner fold-ов ноль во всех четырёх outer.
"""

import csv
import json
import sys
from collections import defaultdict
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

CARRIED = ROOT / "07-analysis" / "splits-v5" / "p2b-outer-folds-carried.json"
OUT = ROOT / "07-analysis" / "inner-cv-audit-v5.md"


def group_of(row):
    return row["split_group_source"] or row["generation_channel"]


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"аудит inner CV, {stamp}")

    with clf.DOCUMENTS.open(encoding="utf-8-sig", newline="") as fh:
        registry = list(csv.DictReader(fh))
    carried = json.loads(CARRIED.read_text(encoding="utf-8"))["folds"]

    rows, total_skipped = [], 0
    for key in sorted(carried, key=int):
        fold = carried[key]
        held_sources, held_channel = set(fold["human"]), fold["ai"]
        train = [r for r in registry
                 if not ((r["origin_class"] == "A"
                          and r["generation_channel"] == held_channel)
                         or (r["origin_class"] == "H"
                             and r["split_group_source"] in held_sources))]
        y = np.array([1 if r["origin_class"] == "A" else 0 for r in train])
        groups = [group_of(r) for r in train]
        n_groups = len(set(groups))
        requested = min(clf.INNER_FOLDS, n_groups)
        splitter = GroupKFold(n_splits=requested)
        x = np.zeros((len(train), 1))

        valid, skipped, detail = 0, 0, []
        for tr, va in splitter.split(x, y, groups):
            ai_groups = len({groups[i] for i in va if y[i] == 1})
            human_groups = len({groups[i] for i in va if y[i] == 0})
            if len(set(y[tr])) < 2 or len(set(y[va])) < 2:
                skipped += 1
                detail.append((ai_groups, human_groups, "пропущен"))
            else:
                valid += 1
                detail.append((ai_groups, human_groups, "валиден"))
        total_skipped += skipped
        rows.append({"fold": key, "channel": held_channel, "requested": requested,
                     "valid": valid, "skipped": skipped, "detail": detail,
                     "n_groups": n_groups})
        print(f"  outer {key} ({held_channel}): запрошено {requested}, валидных "
              f"{valid}, пропущено {skipped}, групп в train {n_groups}")
        for i, (ai, hum, status) in enumerate(detail):
            print(f"    inner {i}: AI-групп {ai}, human-групп {hum} — {status}")

    lines = ["# Шлюз: inner CV подбора регуляризации в P2b", "",
             f"Собрано {stamp} скриптом `09-tools/inner_cv_audit.py`.", "",
             "Внешние fold-ы взяты перенесёнными из "
             "`splits-v5/p2b-outer-folds-carried.json`. Группировка — "
             "`split_group_source` у человеческих документов, канал генерации у "
             "машинных; та же, что в `clf_run.pick_c`.", "",
             "| Outer fold | Удержанный канал | Запрошено inner | Валидных | "
             "Пропущенных | Групп в train |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['fold']} | {r['channel']} | {r['requested']} | "
                     f"{r['valid']} | {r['skipped']} | {r['n_groups']} |")
    lines += ["", "## Состав validation по каждому inner fold", "",
              "| Outer | Inner | AI-групп в validation | Human-групп | Статус |",
              "|---|---|---|---|---|"]
    for r in rows:
        for i, (ai, hum, status) in enumerate(r["detail"]):
            lines.append(f"| {r['fold']} | {i} | {ai} | {hum} | {status} |")
    ok = total_skipped == 0
    lines += ["",
              (f"**Вердикт: пропущенных inner fold-ов ноль во всех "
               f"{len(rows)} outer — шлюз закрыт.** Заявленный nested CV совпадает "
               "с выполненным: гиперпараметр выбирается по всем запрошенным "
               "разбиениям."
               if ok else
               f"**Вердикт: пропущено {total_skipped} inner fold-ов — шлюз не "
               "закрыт.** Нужны замороженные source-disjoint inner fold-ы: по "
               "одному AI-каналу в каждый, человеческие источники распределены на "
               "составе до коррекции и после исключения не перераспределяются. "
               "`pick_c` при этом должен не пропускать невалидный fold, а "
               "завершать preflight ошибкой."),
              ""]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  всего пропущено: {total_skipped}")
    print(f"  отчёт: {OUT.name}")
    if not ok:
        raise SystemExit("inner CV: есть пропущенные fold-ы, запуск P2b запрещён")


if __name__ == "__main__":
    main()
