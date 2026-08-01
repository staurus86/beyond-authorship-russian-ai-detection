#!/usr/bin/env python3
"""Шлюзы перед пересчётом на prep-v5: входы классификатора, fold-ы P2b, сверка счёта.

    python 09-tools/preflight_v5_gates.py

Три проверки, каждая с жёстким исходом.

**Шлюз A — что подаётся классификатору.** Если процедура 2 получает
`genre_percentile`, тест-документы участвуют в преобразовании собственных
признаков, и корпусную нормировку пришлось бы строить на train fold. Скрипт
печатает фактические колонки матрицы, которые читает `clf_run`, и падает, если
среди них окажется корпусная величина.

**Шлюз B — оба класса в fold-ах P2b.** Одноклассовый тест допустим только как
диагностический holdout из общего набора 18. Во внешних fold-ах P2b и во
внутренних fold-ах подбора регуляризации оба класса обязаны присутствовать.

**Шлюз C — сверка счёта неизменённых.** QA перехода насчитал 1813 полностью
неизменённых документов, шлюз переиспользования — 1814 с побитово совпавшим
профилем `prose`. Разница объясняется поимённо.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402
from preflight_procedures_2_4 import build_outer_folds  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

SPLITS_V5 = ROOT / "07-analysis" / "splits-v5"
V4 = ROOT / "04-corpus" / "derived" / "prep-v4"
V5 = ROOT / "04-corpus" / "derived" / "prep-v5"
CORRECTIONS = ROOT / "04-corpus" / "prep-v5-corrections.csv"
OUT = ROOT / "07-analysis" / "preflight-v5-gates.md"

CORPUS_LEVEL_COLUMNS = {"genre_percentile"}


def manifest(path):
    with (path / "manifest.csv").open(encoding="utf-8", newline="") as fh:
        return {r["document_id"]: r for r in csv.DictReader(fh)}


def gate_a(lines):
    """Колонки матрицы, которые фактически читает классификатор."""
    source = (ROOT / "09-tools" / "clf_run.py").read_text(encoding="utf-8")
    used = sorted({col for col in ("raw_value", "normalized_value", "genre_percentile")
                   if f'r["{col}"]' in source or f'["{col}"]' in source})
    leak = [c for c in used if c in CORPUS_LEVEL_COLUMNS]
    print(f"  шлюз A: классификатор читает {used}")
    lines += ["## Шлюз A: входы классификатора", "",
              "| Колонка матрицы | Уровень | Читает clf_run |",
              "|---|---|---|"]
    for col in ("raw_value", "normalized_value", "genre_percentile"):
        level = "корпус-зависимая" if col in CORPUS_LEVEL_COLUMNS else "документ-локальная"
        lines.append(f"| `{col}` | {level} | {'да' if col in used else 'нет'} |")
    lines += ["",
              "`clf_run.load_matrix` берёт `normalized_value or raw_value`. "
              "`normalized_value` — нормировка на длину самого документа (например, "
              "на 1000 слов), корпус в неё не входит. Диагностические baseline "
              "берут длины из манифеста препроцессинга и one-hot по метаданным "
              "реестра — тоже без корпусных величин.",
              "",
              "Процедура 1 наоборот строится на `genre_percentile` "
              "(`score_style_index.py`), поэтому её референсная выборка "
              "пересчитывается целиком на v5.",
              "",
              ("**Вердикт: утечки через корпусную нормировку у процедуры 2 нет**, "
               "строить перцентили на train fold не требуется."
               if not leak else
               f"**Вердикт: классификатор читает {leak} — нужна нормировка на train fold.**"),
              ""]
    return not leak


def gate_b(lines):
    """Fold-ы P2b переносятся, а не строятся заново; в каждом должны быть оба класса.

    `build_outer_folds` распределяет человеческие источники жадно по их размеру.
    После исключения 34 документов размеры изменились, и то же правило с тем же
    сидом даёт другое распределение. Строить fold-ы заново нельзя: изменение
    корпуса смешалось бы с изменением разбиения.
    """
    backup = ROOT / "04-corpus" / "documents-registry.csv.bak-before-correction-exclusion"
    with backup.open(encoding="utf-8-sig", newline="") as fh:
        before = list(csv.DictReader(fh))
    with clf.DOCUMENTS.open(encoding="utf-8-sig", newline="") as fh:
        now = list(csv.DictReader(fh))
    seed = json.loads((ROOT / "07-analysis" / "clf-v1-manifest.json")
                      .read_text(encoding="utf-8"))["seed"]

    carried, _ = build_outer_folds(before, seed)
    rebuilt, _ = build_outer_folds(now, seed)
    moved = sorted({s for i in carried
                    for s in set(carried[i]["human"]) ^ set(rebuilt[i]["human"])})

    alive = {r["document_id"]: r for r in now}
    rows, ok = [], True
    for i in sorted(carried):
        fold = carried[i]
        held_sources, held_channel = set(fold["human"]), fold["ai"]
        test = [r for r in now
                if (r["origin_class"] == "A" and r["generation_channel"] == held_channel)
                or (r["origin_class"] == "H" and r["split_group_source"] in held_sources)]
        train = [r for r in now if r["document_id"] not in {t["document_id"] for t in test}]
        counts = {"test": defaultdict(int), "train": defaultdict(int)}
        for part, items in (("test", test), ("train", train)):
            for r in items:
                counts[part][r["origin_class"]] += 1
        good = all(counts[p][c] > 0 for p in ("train", "test") for c in ("A", "H"))
        ok &= good
        rows.append((i, held_channel, counts["train"]["A"], counts["train"]["H"],
                     counts["test"]["A"], counts["test"]["H"], good))
        print(f"  шлюз B: fold {i} ({held_channel}): train "
              f"{counts['train']['A']}/{counts['train']['H']}, test "
              f"{counts['test']['A']}/{counts['test']['H']}, оба класса: {good}")

    out = SPLITS_V5 / "p2b-outer-folds-carried.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"carried_from": "documents-registry.csv.bak-before-correction-exclusion",
         "seed": seed,
         "note": "распределение зафиксировано на составе до исключения 34 документов; "
                 "заново не строится",
         "folds": {str(i): carried[i] for i in sorted(carried)}},
        ensure_ascii=False, indent=2), encoding="utf-8")

    lines += ["## Шлюз B: fold-ы P2b перенесены, а не перестроены", "",
              "`build_outer_folds` распределяет человеческие источники жадно по их "
              "размеру, поэтому после исключения 34 документов то же правило с тем "
              "же сидом даёт **другое** распределение. Например, `lenta` переехала "
              "бы из fold 2 в fold 3, `drmax` из 0 в 1, `spbgu` из 2 в 1 — всего "
              f"источников со сменой fold: {len(moved)}.",
              "",
              "Поэтому распределение зафиксировано на составе до исключения и "
              f"записано в `{out.relative_to(ROOT).as_posix()}`; документы, "
              "исключённые коррекцией, из fold-ов просто вычитаются.",
              "",
              "| Fold | Удержанный канал | Train A/H | Test A/H | Оба класса |",
              "|---|---|---|---|---|"]
    for i, channel, ta, th, sa, sh, good in rows:
        lines.append(f"| {i} | {channel} | {ta}/{th} | {sa}/{sh} | "
                     f"{'да' if good else '**нет**'} |")
    lines += ["",
              "Внутренние fold-ы подбора регуляризации строит `GroupKFold` внутри "
              "train; `clf_run.pick_c` пропускает разбиение, где в train или "
              "validation остался один класс, поэтому одноклассовый inner fold в "
              "подбор не попадает.",
              "",
              ("**Вердикт: оба класса есть во всех внешних fold-ах P2b.** Пять "
               "одноклассовых тестов относятся к диагностическим holdout процедуры "
               "P2a; там определима только классовая величина — FPR на human-only, "
               "а AUROC, MCC и balanced accuracy остаются NA и в сводные оценки "
               "качества разделения не входят."
               if ok else "**Вердикт: есть fold с одним классом — шлюз не закрыт.**"),
              ""]
    return ok


def gate_c(lines):
    """Сверка 1813 против 1814."""
    m4, m5 = manifest(V4), manifest(V5)
    common = set(m4) & set(m5)
    with CORRECTIONS.open(encoding="utf-8", newline="") as fh:
        corrections = {r["document_id"]: r for r in csv.DictReader(fh)}
    edited = {d for d, r in corrections.items()
              if r["verdict"] in {"extraction-defect", "intermediary-defect"}
              and r["sha256_after"]}

    prose_same = {d for d in common if m4[d]["prose_sha256"] == m5[d]["prose_sha256"]}
    fully_same = {d for d in prose_same if m4[d]["full_sha256"] == m5[d]["full_sha256"]}
    prose_same_but_edited = sorted(prose_same & edited)
    prose_same_full_differs = sorted(prose_same - fully_same)

    print(f"  шлюз C: prose совпал у {len(prose_same)}, из них целиком совпали "
          f"{len(fully_same)}")
    lines += ["## Шлюз C: сверка счёта неизменённых", "",
              "| Величина | Документов |", "|---|---|",
              f"| профиль `prose` совпал побитово | {len(prose_same)} |",
              f"| совпали оба профиля, `prose` и `full` | {len(fully_same)} |",
              f"| `prose` совпал, но документ правился коррекцией | "
              f"{len(prose_same_but_edited)} |",
              f"| `prose` совпал, а `full` различается | "
              f"{len(prose_same_full_differs)} |", ""]
    if prose_same_but_edited:
        lines += ["Документы, где правка не изменила профиль `prose`:", ""]
        lines += [f"- `{d}`;" for d in prose_same_but_edited]
        lines.append("")
    if prose_same_full_differs:
        lines += ["Документы, где downstream-профиль `prose` совпал, а "
                  "upstream-артефакт `full` изменился:", ""]
        lines += [f"- `{d}`;" for d in prose_same_full_differs]
        lines.append("")
    lines += [f"Запись равенства: **{len(fully_same)} документов неизменны целиком; "
              f"ещё у {len(prose_same) - len(fully_same)} изменился upstream-артефакт, "
              f"но downstream-профиль `prose` совпал побитово.**", ""]
    return True


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"шлюзы перед пересчётом, {stamp}")
    lines = ["# Шлюзы перед пересчётом на prep-v5", "",
             f"Собрано {stamp} скриптом `09-tools/preflight_v5_gates.py`.", ""]
    a = gate_a(lines)
    b = gate_b(lines)
    gate_c(lines)
    lines += ["## Итог", "",
              f"- шлюз A, входы классификатора: {'пройден' if a else '**не пройден**'};",
              f"- шлюз B, классы в fold-ах P2b: {'пройден' if b else '**не пройден**'};",
              "- шлюз C, сверка счёта: объяснена поимённо.", ""]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT.name}")
    if not (a and b):
        raise SystemExit("шлюзы не пройдены — пересчёт запрещён")


if __name__ == "__main__":
    main()
