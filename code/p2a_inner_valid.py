#!/usr/bin/env python3
"""Схема B: inner-разбиение P2a, где каждый validation двухклассовый.

    python 09-tools/p2a_inner_valid.py

Правило зафиксировано амендментом `02-preregistration/amendment-clf-v2-inner-cv.md`
до построения. Группы класса A сортируются по убыванию числа документов, при
равенстве — по имени, и раскладываются по fold-ам по кругу; группы класса H —
так же и независимо. Правило детерминировано и смотрит только на состав групп.

Три fold-а строятся, когда в обучающей части не меньше трёх групп каждого класса.
Иначе число fold-ов снижается до минимума из двух чисел, и причина пишется в
отчёт **до** расчёта метрик.

У одиннадцати holdout, где перенесённое разбиение уже двухклассово, назначения
копируются из `p2a-inner-folds-carried.json` без изменений: чинится только то,
что сломано.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

SPLITS_V5 = ROOT / "07-analysis" / "splits-v5"
OUT_JSON = SPLITS_V5 / "p2a-inner-folds-valid.json"
OUT_REPORT = ROOT / "07-analysis" / "p2a-inner-valid.md"
AMENDMENT = ROOT / "02-preregistration" / "amendment-clf-v2-inner-cv.md"


def train_groups(train_ids, registry):
    """Группы обучающей части с классом и числом документов."""
    sizes, origin = defaultdict(int), {}
    for doc_id in train_ids:
        row = registry.get(doc_id)
        if row is None:
            continue
        group = row["split_group_source"] or row["generation_channel"]
        sizes[group] += 1
        origin[group] = row["origin_class"]
    return sizes, origin


def assign(sizes, origin, n_folds):
    """Раскладка по кругу внутри каждого класса. Порядок задан правилом."""
    out = {}
    for cls in ("A", "H"):
        groups = sorted((g for g in sizes if origin[g] == cls),
                        key=lambda g: (-sizes[g], g))
        for i, group in enumerate(groups):
            out[group] = i % n_folds
    return out


def composition(assignments, sizes, origin, n_folds):
    """Сколько документов каждого класса попадает в validation каждого fold-а."""
    rows = []
    for fold in range(n_folds):
        members = [g for g, f in assignments.items() if f == fold]
        rows.append({
            "fold": fold,
            "documents_A": sum(sizes[g] for g in members if origin[g] == "A"),
            "documents_H": sum(sizes[g] for g in members if origin[g] == "H"),
            "groups_A": sum(1 for g in members if origin[g] == "A"),
            "groups_H": sum(1 for g in members if origin[g] == "H"),
        })
    return rows


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"схема B: двухклассовые inner fold-ы P2a, {stamp}")
    if not AMENDMENT.exists():
        raise SystemExit("нет амендмента с правилом построения — расчёт запрещён")

    registry = {r["document_id"]: r for r in clf.read_rows(clf.DOCUMENTS, "utf-8-sig")}
    carried = json.loads(clf.CARRIED_P2A.read_text(encoding="utf-8"))["splits"]

    out, report = {}, []
    for path in sorted(SPLITS_V5.glob("holdout_*_prep-v5.json")):
        split = json.loads(path.read_text(encoding="utf-8"))
        name = split["split_name"]
        sizes, origin = train_groups(split["train"], registry)
        old = carried[name]["assignments"]

        # Перенесённое разбиение проверяется первым: чинить нужно только сломанное.
        old_folds = sorted({old[g] for g in sizes})
        old_comp = composition({g: old[g] for g in sizes}, sizes, origin, clf.INNER_FOLDS)
        degenerate = [c for c in old_comp if c["documents_A"] == 0 or c["documents_H"] == 0]
        if not degenerate:
            out[name] = {"source": "перенос из clf-v1, все validation двухклассовы",
                         "n_folds": len(old_folds),
                         "assignments": {g: old[g] for g in sizes},
                         "composition": old_comp}
            report.append({"split": name, "rebuilt": False, "n_folds": len(old_folds),
                           "reason": "перенесённое разбиение двухклассово",
                           "composition": old_comp})
            print(f"  {name}: перенос сохранён, fold-ов {len(old_folds)}")
            continue

        groups_a = sum(1 for g in sizes if origin[g] == "A")
        groups_h = sum(1 for g in sizes if origin[g] == "H")
        n_folds = min(clf.INNER_FOLDS, groups_a, groups_h)
        reason = ("три fold-а возможны" if n_folds == clf.INNER_FOLDS else
                  f"групп A {groups_a}, групп H {groups_h}: больше {n_folds} "
                  "двухклассовых fold-ов не построить")
        assignments = assign(sizes, origin, n_folds)
        comp = composition(assignments, sizes, origin, n_folds)
        broken = [c for c in comp if c["documents_A"] == 0 or c["documents_H"] == 0]
        if broken:
            raise SystemExit(f"{name}: правило не дало двухклассовых fold-ов — {broken}")
        out[name] = {"source": "перестроено по правилу амендмента",
                     "n_folds": n_folds, "groups_A": groups_a, "groups_H": groups_h,
                     "reason": reason, "assignments": assignments,
                     "composition": comp}
        report.append({"split": name, "rebuilt": True, "n_folds": n_folds,
                       "reason": reason, "composition": comp,
                       "degenerate_before": [c["fold"] for c in degenerate]})
        print(f"  {name}: перестроено, fold-ов {n_folds} — {reason}")

    OUT_JSON.write_text(json.dumps(
        {"scheme": "B — двухклассовый validation",
         "rule": "группы класса по убыванию размера, при равенстве по имени, "
                 "по кругу; классы раскладываются независимо",
         "amendment": AMENDMENT.name, "inner_folds_requested": clf.INNER_FOLDS,
         "created_at": stamp, "splits": out}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    rebuilt = [r for r in report if r["rebuilt"]]
    lines = ["# Схема B: двухклассовые inner fold-ы подбора C в P2a", "",
             f"Собрано {stamp} скриптом `09-tools/p2a_inner_valid.py`. Правило "
             f"зафиксировано амендментом `{AMENDMENT.name}` до построения.", "",
             f"Перестроено разбиений: {len(rebuilt)} из {len(report)}. У остальных "
             "перенесённое из clf-v1 разбиение уже двухклассово и не менялось.", "",
             "| Holdout | Перестроено | Fold-ов | Причина |", "|---|---|---|---|"]
    for r in report:
        lines.append(f"| `{r['split']}` | {'да' if r['rebuilt'] else 'нет'} | "
                     f"{r['n_folds']} | {r['reason']} |")
    lines += ["", "## Состав validation у перестроенных разбиений", "",
              "| Holdout | Fold | Документов A | Документов H | Групп A | Групп H |",
              "|---|---|---|---|---|---|"]
    for r in rebuilt:
        for c in r["composition"]:
            lines.append(f"| `{r['split']}` | {c['fold']} | {c['documents_A']} | "
                         f"{c['documents_H']} | {c['groups_A']} | {c['groups_H']} |")
    lines += ["", "Ни один validation не остался одноклассовым. Состав посчитан до "
                  "расчёта метрик: правило смотрит на класс и размер группы, а не "
                  "на результат модели.", ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  записано: {OUT_JSON.name}, отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
