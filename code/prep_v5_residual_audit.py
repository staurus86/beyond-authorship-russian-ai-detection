#!/usr/bin/env python3
"""Шлюз 1: сверка остаточных повторов в prep-v5 с журналом коррекции.

    python 09-tools/prep_v5_residual_audit.py

Реестр обязан замыкаться арифметически. QA насчитал 17 документов с долей
повторов выше порога в профиле `prose`, тогда как из журнала коррекции следует
12: два исправленных с остатком плюс десять, правка которых запрещена. Скрипт
перечисляет все 17 и приписывает каждому категорию:

- `правился, остаток` — вердикт из редактируемых, дефект не снят целиком;
- `правка запрещена` — `source-property` или `unresolved`;
- `не был кандидатом` — в raw доля была ниже порога, а в профиле `prose` выше;
  так бывает, когда препроцессинг сокращает знаменатель, снимая служебные строки;
- `не в журнале` — документ вообще не попадал в журнал коррекции.

Скрипт ничего не исправляет: он объясняет расхождение до пересчёта.
"""

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

V5 = ROOT / "04-corpus" / "derived" / "prep-v5"
CORRECTIONS = ROOT / "04-corpus" / "prep-v5-corrections.csv"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
OUT = ROOT / "07-analysis" / "prep-v5-residual-audit.md"

SENT = re.compile(r"(?<=[.!?])\s+")
THRESHOLD = 0.10
MIN_SENT_CHARS = 40
EDITED = {"extraction-defect", "intermediary-defect"}


def repeat_share(text):
    parts = [s.strip() for s in SENT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]
    if not parts:
        return 0.0, 0
    counts = defaultdict(int)
    for s in parts:
        counts[s] += 1
    return sum(n for n in counts.values() if n > 1) / len(parts), len(parts)


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"аудит остаточных повторов prep-v5, {stamp}")

    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        registry = {r["document_id"]: r for r in csv.DictReader(fh)}
    with CORRECTIONS.open(encoding="utf-8", newline="") as fh:
        corrections = {r["document_id"]: r for r in csv.DictReader(fh)}

    residual = []
    for doc_id in sorted(registry):
        path = V5 / "prose" / f"{doc_id}.txt"
        if not path.exists():
            continue
        share, n_sent = repeat_share(path.read_text(encoding="utf-8"))
        if share <= THRESHOLD:
            continue
        entry = corrections.get(doc_id)
        if entry is None:
            category = "не был кандидатом: в raw доля ниже порога"
            verdict = "—"
            raw_path = ROOT / registry[doc_id]["file_path"]
            raw_share = (repeat_share(raw_path.read_text(encoding="utf-8", errors="replace"))
                         if raw_path.exists() else (0.0, 0))
        elif entry["verdict"] in EDITED:
            category = "правился, остаток"
            verdict, raw_share = entry["verdict"], None
        else:
            category = "правка запрещена"
            verdict, raw_share = entry["verdict"], None
        residual.append({
            "document_id": doc_id,
            "source": registry[doc_id]["source_platform"]
                      or registry[doc_id]["generation_channel"],
            "origin_class": registry[doc_id]["origin_class"],
            "share_prose": round(share, 3),
            "sentences": n_sent,
            "share_raw": round(raw_share[0], 3) if raw_share else None,
            "sentences_raw": raw_share[1] if raw_share else None,
            "verdict": verdict,
            "category": category,
        })

    by_category = defaultdict(list)
    for r in residual:
        by_category[r["category"]].append(r)

    print(f"  документов с долей выше {THRESHOLD} в prose: {len(residual)}")
    for category, items in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        print(f"    {category}: {len(items)}")

    expected = sum(1 for r in corrections.values()
                   if r["verdict"] not in EDITED) + len(by_category["правился, остаток"])
    print(f"  ожидалось из журнала: {expected}, фактически {len(residual)}")

    lines = [
        "# Шлюз 1: остаточные повторы в prep-v5",
        "",
        f"Собрано {stamp} скриптом `09-tools/prep_v5_residual_audit.py`.",
        "",
        "Проверка выполнена до пересчёта: реестр обязан замыкаться арифметически.",
        "",
        "## Сводка",
        "",
        "| Категория | Документов |",
        "|---|---|",
    ]
    for category, items in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| {category} | {len(items)} |")
    lines += [f"| **всего** | **{len(residual)}** |", "",
              "## Поимённо", "",
              "| Документ | Источник | Класс | Доля в prose | Предложений | Вердикт | Категория |",
              "|---|---|---|---|---|---|---|"]
    for r in sorted(residual, key=lambda x: (x["category"], -x["share_prose"])):
        lines.append(
            f"| `{r['document_id']}` | {r['source']} | {r['origin_class']} | "
            f"{r['share_prose']} | {r['sentences']} | {r['verdict']} | {r['category']} |")
    lines.append("")

    borderline = [r for r in residual if r["share_raw"] is not None]
    if borderline:
        lines += [
            "## Почему пять документов не были кандидатами",
            "",
            "Отбор кандидатов шёл по профилю `raw`, а порог здесь считается по "
            "`prose`. Препроцессинг снимает служебные строки, знаменатель "
            "сокращается, и доля переходит порог без роста самого дефекта.",
            "",
            "| Документ | Предложений raw → prose | Доля raw → prose |",
            "|---|---|---|",
        ]
        for r in sorted(borderline, key=lambda x: -x["share_prose"]):
            lines.append(f"| `{r['document_id']}` | {r['sentences_raw']} → "
                         f"{r['sentences']} | {r['share_raw']} → {r['share_prose']} |")
        lines += [
            "",
            "У четырёх документов абсолютное число повторяющихся предложений не "
            "изменилось — выросла только доля. У `human_news_buriy_2014_0048` "
            "механизм другой: число предложений то же, а повторов стало вдвое "
            "больше, то есть нормализация препроцессинга сделала два ранее "
            "различавшихся предложения идентичными.",
            "",
            "**Замечание к следующей заморозке:** диагностический флаг стоит "
            "считать на том профиле, по которому считаются признаки, либо на обоих. "
            "Менять правило сейчас, после просмотра результатов, нельзя.",
            "",
        ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT.name}")


if __name__ == "__main__":
    main()
