#!/usr/bin/env python3
"""Установление происхождения повторов: сверка корпуса с immutable raw и источником.

    python 09-tools/trace_defect_origin.py

**Статус — post hoc diagnostic, 2026-07-29.** Запущено по решению PI: до любой
очистки надо отличить технический дефект извлечения от свойства опубликованного
оригинала. Правило PI: повтор, присутствующий в оригинале, не редактируется.

Что делает скрипт:

1. читает документы с повтором предложений выше порога (`text-defects-v1.csv`);
2. сверяет `raw-human/<источник>/<id>.txt` с профилями `prep-v4` — показывает,
   на каком шаге появился повтор;
3. для Ленты и buriy_2014 находит исходную запись в датасете corus по URL и
   считает долю повторов **в оригинале**;
4. для источников с wayback-снапшотом ищет HTML в кэше и отмечает, доступна ли
   повторная экстракция.

Скрипт ничего не исправляет и ничего не перезаписывает.
"""

import bz2
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DEFECTS = ROOT / "07-analysis" / "text-defects-v1.csv"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
RAW = ROOT / "04-corpus" / "raw-human"
PREP = ROOT / "04-corpus" / "derived" / "prep-v4"
LENTA_CSV = ROOT / "04-corpus" / "_archives" / "lenta-ru-news.csv.bz2"
WAYBACK = ROOT / "04-corpus" / "_archives" / "wayback_cache"
BLOG_CACHE = ROOT / "04-corpus" / "_archives" / "blog_cache"
OUT_CSV = ROOT / "07-analysis" / "defect-origin-v1.csv"
OUT_REPORT = ROOT / "07-analysis" / "defect-origin-v1.md"

SENT = re.compile(r"(?<=[.!?])\s+")
THRESHOLD = 0.10
MIN_SENT_CHARS = 40
CONTROL_PER_SOURCE = 3      # чистые документы того же источника для контроля


def repeat_share(text):
    parts = [s.strip() for s in SENT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]
    if not parts:
        return None
    counts = defaultdict(int)
    for s in parts:
        counts[s] += 1
    return sum(n for n in counts.values() if n > 1) / len(parts)


def read_if(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"происхождение дефекта, запуск {stamp}")

    registry = {}
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            registry[r["document_id"]] = r

    defects = list(csv.DictReader(DEFECTS.open(encoding="utf-8")))
    affected = [r for r in defects if float(r["repeat_share"]) > THRESHOLD]
    clean_by_source = defaultdict(list)
    for r in defects:
        if float(r["repeat_share"]) <= THRESHOLD:
            clean_by_source[r["source"]].append(r["document_id"])

    sources = sorted({r["source"] for r in affected})
    controls = []
    for source in sources:
        controls += [{"document_id": d, "source": source, "role": "контроль"}
                     for d in sorted(clean_by_source[source])[:CONTROL_PER_SOURCE]]
    targets = ([{"document_id": r["document_id"], "source": r["source"],
                 "role": "поражённый"} for r in affected] + controls)
    print(f"  поражённых {len(affected)}, контрольных {len(controls)}, "
          f"источников {len(sources)}")

    rows = []
    for t in targets:
        doc_id, source = t["document_id"], t["source"]
        raw_text = read_if(RAW / source / f"{doc_id}.txt")
        meta_path = RAW / source / f"{doc_id}.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        rows.append({
            "document_id": doc_id, "source": source, "role": t["role"],
            "url": meta.get("url") or meta.get("external_id") or "",
            "snapshot": meta.get("snapshot") or "",
            "repeat_raw": round(repeat_share(raw_text), 4) if raw_text else None,
            "repeat_full": (round(repeat_share(read_if(PREP / "full" / f"{doc_id}.txt")), 4)
                            if (PREP / "full" / f"{doc_id}.txt").exists() else None),
            "repeat_prose": (round(repeat_share(read_if(PREP / "prose" / f"{doc_id}.txt")), 4)
                             if (PREP / "prose" / f"{doc_id}.txt").exists() else None),
            "repeat_origin": None, "origin_checked": "", "origin_verdict": "",
        })

    by_id = {r["document_id"]: r for r in rows}
    lenta_urls = {r["url"]: r["document_id"] for r in rows
                  if r["source"] == "lenta" and r["url"]}
    if lenta_urls and LENTA_CSV.exists():
        print(f"  сверка с датасетом Ленты: ищем {len(lenta_urls)} записей")
        csv.field_size_limit(10 ** 8)
        found = 0
        with bz2.open(LENTA_CSV, "rt", encoding="utf-8", newline="") as fh:
            for record in csv.DictReader(fh):
                url = (record.get("url") or "").strip()
                if url in lenta_urls:
                    row = by_id[lenta_urls[url]]
                    row["repeat_origin"] = round(repeat_share(record.get("text") or ""), 4)
                    row["origin_checked"] = "датасет corus lenta-ru-news"
                    found += 1
                    if found == len(lenta_urls):
                        break
        print(f"    найдено в датасете: {found} из {len(lenta_urls)}")

    for row in rows:
        if row["repeat_origin"] is not None:
            if row["repeat_origin"] > THRESHOLD:
                row["origin_verdict"] = "повтор есть в оригинале — не редактировать"
            elif (row["repeat_raw"] or 0) > THRESHOLD:
                row["origin_verdict"] = "дефект появился при сборе — исправлять"
            else:
                row["origin_verdict"] = "повтора нет ни там, ни там"
        elif row["snapshot"]:
            cached = list(WAYBACK.glob(f"*{row['document_id']}*")) or \
                list(BLOG_CACHE.glob(f"*{row['document_id']}*"))
            row["origin_checked"] = ("снапшот в кэше" if cached
                                     else "снапшот только по ссылке")
            row["origin_verdict"] = ("повторная экстракция возможна" if cached
                                     else "нужен доступ к снапшоту")
        else:
            row["origin_verdict"] = "источник оригинала не определён"

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    write_report(rows, stamp)
    print(f"  отчёт: {OUT_REPORT.name}")


def write_report(rows, stamp):
    affected = [r for r in rows if r["role"] == "поражённый"]
    lines = [
        "# Происхождение повторов: корпус против immutable raw и оригинала",
        "",
        f"Собрано {stamp} скриптом `09-tools/trace_defect_origin.py`.",
        "",
        "**Статус — post hoc diagnostic.** Проверка выполнена по решению PI от "
        "2026-07-29 до любой очистки. Правило: повтор, присутствующий в "
        "опубликованном оригинале, считается свойством источника и не "
        "редактируется. Скрипт ничего не исправляет.",
        "",
        "## На каком шаге появляется повтор",
        "",
        "| Источник | Поражённых | Медиана повтора в raw | В профиле full | В профиле prose |",
        "|---|---|---|---|---|",
    ]
    import statistics as st
    by_source = defaultdict(list)
    for r in affected:
        by_source[r["source"]].append(r)
    for source in sorted(by_source):
        items = by_source[source]
        def med(field):
            vals = [x[field] for x in items if x[field] is not None]
            return f"{st.median(vals):.3f}" if vals else "—"
        lines.append(f"| {source} | {len(items)} | {med('repeat_raw')} | "
                     f"{med('repeat_full')} | {med('repeat_prose')} |")
    lines += ["", "## Сверка с оригиналом", "",
              "| Документ | Источник | Повтор в raw | Повтор в оригинале | Вердикт |",
              "|---|---|---|---|---|"]
    for r in sorted(affected, key=lambda x: (x["source"], x["document_id"])):
        if r["repeat_origin"] is None and not r["origin_verdict"]:
            continue
        lines.append(
            f"| `{r['document_id']}` | {r['source']} | "
            f"{r['repeat_raw'] if r['repeat_raw'] is not None else '—'} | "
            f"{r['repeat_origin'] if r['repeat_origin'] is not None else '—'} | "
            f"{r['origin_verdict']} |")
    verdicts = defaultdict(int)
    for r in affected:
        verdicts[r["origin_verdict"]] += 1
    lines += ["", "## Сводка вердиктов", "", "| Вердикт | Документов |", "|---|---|"]
    for verdict, n in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {verdict} | {n} |")
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
