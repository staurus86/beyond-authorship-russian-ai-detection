#!/usr/bin/env python3
"""Синтетическая проверка перенесённого inner-разбиения в `clf_run.pick_c`.

    python 09-tools/test_clf_carried_synth.py

Прогон серии v2 смотрит на зависимую переменную один раз, поэтому отладка идёт
на числах с заранее известным ответом, а не на корпусе. Проверяется два класса
утверждений:

1. **поведение по умолчанию не изменилось** — без назначений `pick_c` даёт тот
   же ответ, что прежняя редакция на `GroupKFold`, включая вырожденный случай
   одной группы;
2. **перенос работает как ворота, а не как подсказка** — группа без назначения,
   удержанная группа внешнего теста, одноклассовый validation и чужой канал
   останавливают расчёт, а не чинятся по месту.

Тест ничего не пишет в 06-features и 07-analysis.
"""

import json
import statistics
import sys
import tempfile
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append((name, bool(condition), detail))
    mark = "ok" if condition else "СБОЙ"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def fails(name, call, fragment):
    """Вызов обязан остановить расчёт, и сообщение обязано называть причину."""
    try:
        call()
    except SystemExit as exc:
        text = str(exc)
        check(name, fragment in text, f"сообщение: {text[:90]}")
        return
    check(name, False, "исключения не было")


def old_pick_c(x, y, groups):
    """Прежняя редакция: разбиение строится GroupKFold на месте."""
    n_groups = len(set(groups))
    if n_groups < 2:
        return clf.C_GRID[len(clf.C_GRID) // 2], None
    splitter = GroupKFold(n_splits=min(clf.INNER_FOLDS, n_groups))
    best, best_score = None, -np.inf
    for c in clf.C_GRID:
        scores = []
        for tr, va in splitter.split(x, y, groups):
            if len(set(y[tr])) < 2 or len(set(y[va])) < 2:
                continue
            model = clf.make_model(c).fit(x[tr], y[tr])
            from sklearn.metrics import roc_auc_score
            scores.append(roc_auc_score(y[va], model.predict_proba(x[va])[:, 1]))
        if scores and statistics.fmean(scores) > best_score:
            best, best_score = c, statistics.fmean(scores)
    return (best if best is not None else clf.C_GRID[len(clf.C_GRID) // 2],
            None if best_score == -np.inf else round(best_score, 4))


def make_data(n_groups=12, per_group=8, seed=20260729):
    """Разделимая задача: у машинных документов сдвинут первый признак."""
    rng = np.random.default_rng(seed)
    x, y, groups = [], [], []
    for g in range(n_groups):
        label = 1 if g % 3 == 0 else 0          # треть групп — машинные
        for _ in range(per_group):
            x.append(rng.normal(loc=[1.2 * label, 0.0, -0.5 * label], scale=1.0))
            y.append(label)
            groups.append(f"g{g:02d}")
    return np.array(x), np.array(y), groups


def carried_from_groups(groups, folds=clf.INNER_FOLDS):
    """Назначение групп по кругу: и машинные, и человеческие есть в каждом fold."""
    machine = sorted({g for g in groups if int(g[1:]) % 3 == 0})
    human = sorted({g for g in groups if int(g[1:]) % 3 != 0})
    out = {}
    for i, g in enumerate(machine):
        out[g] = i % folds
    for i, g in enumerate(human):
        out[g] = i % folds
    return out


def main():
    print("синтетическая проверка pick_c и переноса inner-разбиений")
    x, y, groups = make_data()

    print("\n1. поведение по умолчанию")
    check("без назначений ответ совпадает с прежней редакцией",
          clf.pick_c(x, y, groups)[:2] == old_pick_c(x, y, groups),
          f"C и inner AUC: {clf.pick_c(x, y, groups)[:2]}")
    single = ["g00"] * len(y)
    check("одна группа: середина сетки, inner AUC пуст",
          clf.pick_c(x, y, single)[:2] == old_pick_c(x, y, single),
          f"{clf.pick_c(x, y, single)[:2]}")

    print("\n2. перенос строит разбиение только из назначений")
    carried = carried_from_groups(groups)
    folds = clf.carried_folds(y, groups, carried)
    check("fold-ов ровно столько, сколько заявлено", len(folds) == clf.INNER_FOLDS,
          f"{len(folds)}")
    check("validation-части не пересекаются и покрывают выборку",
          sorted(np.concatenate([va for _, va in folds])) == list(range(len(y))),
          f"документов {len(y)}")
    check("train и validation не пересекаются ни в одном fold",
          all(not set(tr) & set(va) for tr, va in folds))
    check("группа целиком лежит в одном validation",
          all(len({carried[groups[i]] for i in va}) == 1 for _, va in folds))
    picked = clf.pick_c(x, y, groups, carried)[:2]
    check("подбор по перенесённому разбиению возвращает C из сетки",
          picked[0] in clf.C_GRID, f"C = {picked[0]}, inner AUC {picked[1]}")

    print("\n3. отказы вместо починки по месту")
    incomplete = {g: f for g, f in carried.items() if g != "g05"}
    fails("группа без назначения останавливает расчёт",
          lambda: clf.pick_c(x, y, groups, incomplete),
          "нет перенесённого inner fold")
    # Машинные группы разложены по fold-ам 0 и 1: train везде двухклассовый,
    # а validation fold-а 2 состоит из одних человеческих групп.
    one_class = {}
    machine = sorted({g for g in groups if int(g[1:]) % 3 == 0})
    human = sorted({g for g in groups if int(g[1:]) % 3 != 0})
    for i, g in enumerate(machine):
        one_class[g] = i % 2
    for i, g in enumerate(human):
        one_class[g] = i % clf.INNER_FOLDS
    fails("одноклассовый validation останавливает расчёт",
          lambda: clf.pick_c(x, y, groups, one_class),
          "в validation один класс")

    print("\n4. проверки файла переноса")
    payload = {
        "inner_folds": clf.INNER_FOLDS,
        "folds": {"0": {"held_out_channel": "gpt",
                        "assignments": {"lenta": 0, "drmax": 1, "gpt2": 2}}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "p2b-inner-folds-carried.json"
        original = clf.CARRIED_INNER
        clf.CARRIED_INNER = path

        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        check("корректный файл читается",
              clf.load_carried_inner(0, {"ai": "gpt", "human": ["buriy_2014"]})
              == payload["folds"]["0"]["assignments"])
        fails("чужой удержанный канал останавливает расчёт",
              lambda: clf.load_carried_inner(0, {"ai": "nemotron", "human": []}),
              "удержанный канал")
        fails("удержанная человеческая группа в назначениях останавливает расчёт",
              lambda: clf.load_carried_inner(0, {"ai": "gpt", "human": ["lenta"]}),
              "группы внешнего теста")
        fails("отсутствующий outer в переносе останавливает расчёт",
              lambda: clf.load_carried_inner(3, {"ai": "gpt", "human": []}),
              "нет назначений")

        broken = {"inner_folds": clf.INNER_FOLDS, "folds": {"0": {
            "held_out_channel": "gpt",
            "assignments": {"lenta": 0, "drmax": 1, "gpt2": 1}}}}
        path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        fails("пропущенный номер inner fold останавливает расчёт",
              lambda: clf.load_carried_inner(0, {"ai": "gpt", "human": []}),
              "номера inner fold-ов")

        path.write_text(json.dumps({"inner_folds": 5, "folds": {}},
                                   ensure_ascii=False), encoding="utf-8")
        fails("чужое число inner fold-ов останавливает расчёт",
              lambda: clf.load_carried_inner(0, {"ai": "gpt", "human": []}),
              "inner_folds")

        clf.CARRIED_INNER = Path(tmp) / "нет-такого-файла.json"
        fails("отсутствие файла переноса останавливает расчёт",
              lambda: clf.load_carried_inner(0, {"ai": "gpt", "human": []}),
              "строить своё запрещено")
        clf.CARRIED_INNER = original

    print("\n5. сверка переноса с реестром prep-v5")
    # Читается только состав корпуса: какие группы окажутся в train каждого
    # внешнего fold-а. Зависимая переменная здесь не участвует, поэтому проверка
    # допустима до замороженного прогона — и ловит расхождение имён групп
    # заранее, а не на середине расчёта.
    if clf.CARRIED_OUTER.exists() and clf.CARRIED_INNER.exists():
        registry = clf.read_rows(clf.DOCUMENTS, "utf-8-sig")
        seo = [r for r in registry if r["genre"] == "seo"]
        outer = json.loads(clf.CARRIED_OUTER.read_text(encoding="utf-8"))["folds"]
        gaps = []
        for key in sorted(outer, key=int):
            fold = outer[key]
            assignments = clf.load_carried_inner(int(key), fold)
            train = [r for r in seo
                     if not ((r["origin_class"] == "A"
                              and r["generation_channel"] == fold["ai"])
                             or (r["origin_class"] == "H"
                                 and r["split_group_source"] in fold["human"]))]
            train_groups = {r["generation_channel"] or r["split_group_source"]
                            for r in train}
            missing = sorted(train_groups - set(assignments))
            if missing:
                gaps.append(f"outer {key}: {', '.join(missing[:5])}")
        check("у каждой train-группы всех outer есть перенесённое назначение",
              not gaps, "; ".join(gaps) if gaps else "разрывов нет")
    else:
        check("файлы переноса на месте", False,
              "p2b-*-carried.json не найдены — сверка с реестром не выполнена")

    clf.SERIES = "clf-v2-legacy"
    clf._P2A_CARRIED = None
    if clf.CARRIED_P2A.exists():
        docs = {r["document_id"]: r for r in clf.read_rows(clf.DOCUMENTS, "utf-8-sig")}
        gaps, empty = [], []
        for path in sorted((clf.ROOT / "07-analysis" / "splits-v5")
                           .glob("holdout_*_prep-v5.json")):
            split = json.loads(path.read_text(encoding="utf-8"))
            assignments = clf.load_p2a_assignments(split["split_name"])
            train_groups = {docs[d]["split_group_source"]
                            or docs[d]["generation_channel"]
                            for d in split["train"] if d in docs}
            missing = sorted(train_groups - set(assignments))
            if missing:
                gaps.append(f"{split['split_name']}: {', '.join(missing[:5])}")
            covered = {assignments[g] for g in train_groups}
            if covered != set(range(clf.INNER_FOLDS)):
                empty.append(f"{split['split_name']}: fold-ы {sorted(covered)}")
        check("у каждой train-группы P2a есть перенесённое назначение",
              not gaps, "; ".join(gaps) if gaps else "разрывов нет")
        check("после исключения ни один inner fold P2a не опустел",
              not empty, "; ".join(empty) if empty else "все три fold-а заняты")
    else:
        check("перенос P2a на месте", False,
              f"{clf.CARRIED_P2A.name} не найден")

    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\nпроверок {len(CHECKS)}, провалено {len(failed)}")
    for name in failed:
        print(f"  ! {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
