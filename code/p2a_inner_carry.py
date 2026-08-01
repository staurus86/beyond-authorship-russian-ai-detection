#!/usr/bin/env python3
"""Перенос inner-разбиений подбора C в P2a между clf-v1 и clf-v2.

    python 09-tools/p2a_inner_carry.py

В P2b соответствие `группа → inner fold` уже переносится (`inner-folds-carry.md`).
В P2a подбор регуляризации до сих пор строил своё разбиение внутри `pick_c`, а
`GroupKFold` раскладывает группы по размеру: сокращение TexTerra, Dr.Max и Ленты
могло сдвинуть источники между inner fold-ами. Тогда часть различия clf-v1 →
clf-v2 объяснялась бы сменой tuning split, а не коррекцией корпуса.

Скрипт строит соответствие дважды — на составе до исключения 34 документов и на
текущем, — сравнивает их и **переносит прежнее** в
`splits-v5/p2a-inner-folds-carried.json`. Решение PI от 2026-07-29.

Разбиение воспроизводит вызов из `run_split`: обучающая часть берётся из файла
разбиения серии v1 в исходном порядке, группа — `split_group_source`, у машинных
документов канал генерации.
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
SPLITS_V1 = ROOT / "07-analysis" / "splits"
SPLITS_V5 = ROOT / "07-analysis" / "splits-v5"
OUT_JSON = SPLITS_V5 / "p2a-inner-folds-carried.json"
OUT_REPORT = ROOT / "07-analysis" / "p2a-inner-carry.md"


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def assignments(train_ids, docs_by_id):
    """Соответствие группа → номер inner fold, тот же вызов, что в `pick_c`."""
    present = [d for d in train_ids if d in docs_by_id]
    groups = [docs_by_id[d]["split_group_source"] or docs_by_id[d]["generation_channel"]
              for d in present]
    n_groups = len(set(groups))
    if n_groups < 2:
        return None, present
    splitter = GroupKFold(n_splits=min(clf.INNER_FOLDS, n_groups))
    x = np.zeros((len(present), 1))
    y = np.array([1 if docs_by_id[d]["origin_class"] == "A" else 0 for d in present])
    mapping = {}
    for index, (_, va) in enumerate(splitter.split(x, y, groups)):
        for i in va:
            mapping[groups[i]] = index
    return mapping, present


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"перенос inner-разбиений P2a, {stamp}")

    before = {r["document_id"]: r for r in read(BACKUP)}
    now = {r["document_id"]: r for r in read(clf.DOCUMENTS)}

    carried, report = {}, []
    total_moved = 0
    for path in sorted(SPLITS_V1.glob("holdout_*.json")):
        split = json.loads(path.read_text(encoding="utf-8"))
        name = split["split_name"]
        old, old_ids = assignments(split["train"], before)
        new, new_ids = assignments(split["train"], now)
        if old is None:
            print(f"  {name}: групп меньше двух, подбор C идёт серединой сетки")
            continue
        shared = sorted(set(old) & set(new or {}))
        moved = [g for g in shared if old[g] != new[g]]
        gone = sorted(set(old) - set(new or {}))
        total_moved += len(moved)
        carried[name] = {"source_file": path.name, "groups": len(old),
                         "train_documents_before": len(old_ids),
                         "train_documents_now": len(new_ids),
                         "assignments": old}
        report.append({"split": name, "groups": len(old), "moved": moved,
                       "gone": gone, "dropped": len(old_ids) - len(new_ids)})
        print(f"  {name}: групп {len(old)}, сменили inner {len(moved)}, "
              f"исчезли из train {len(gone)}, документов ушло "
              f"{len(old_ids) - len(new_ids)}")

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(
        {"carried_from": BACKUP.name,
         "seed_note": "GroupKFold детерминирован, сид не используется; порядок "
                      "задаётся размерами групп",
         "inner_folds": clf.INNER_FOLDS, "created_at": stamp,
         "grouping": "split_group_source, у машинных документов generation_channel",
         "note": "соответствие группа → inner fold зафиксировано на составе до "
                 "исключения 34 документов; исключённые документы вычитаются, "
                 "группы не перераспределяются",
         "splits": carried}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Перенос inner-разбиений подбора C в P2a", "",
             f"Собрано {stamp} скриптом `09-tools/p2a_inner_carry.py`.", "",
             "`GroupKFold` раскладывает группы по размеру, поэтому исключение 34 "
             "документов могло сдвинуть источники между inner fold-ами подбора "
             "регуляризации. Прежнее соответствие зафиксировано и переносится в "
             "clf-v2; своё разбиение в серии v2 не строится.", "",
             "| Holdout | Групп в train | Сменили inner fold | Исчезли из train | "
             "Документов ушло |", "|---|---|---|---|---|"]
    for r in report:
        lines.append(f"| {r['split']} | {r['groups']} | {len(r['moved'])} | "
                     f"{len(r['gone'])} | {r['dropped']} |")
    lines.append("")
    if total_moved:
        lines += ["## Группы, сменившие inner fold при пересборке", ""]
        for r in report:
            if r["moved"]:
                lines.append(f"**{r['split']}:** "
                             + ", ".join(f"`{g}`" for g in r["moved"][:40])
                             + (" …" if len(r["moved"]) > 40 else "") + ";")
        lines.append("")
    lines += [
        (f"**Пересборка сдвинула бы {total_moved} назначений — перенос обязателен.**"
         if total_moved else
         "**Пересборка дала бы то же распределение.** Файл записан для явной "
         "фиксации: серия v2 читает его, а не строит разбиение заново."),
        "",
        "Файл читает `clf_run --series clf-v2`. Диагностика «сменился ли выбранный "
        "C» считается в самом прогоне: рядом с рабочим значением записывается "
        "`c_rebuilt` — значение, которое дало бы разбиение, построенное заново.",
        ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  всего назначений сменилось бы: {total_moved}")
    print(f"  записано: {OUT_JSON.name}, отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
