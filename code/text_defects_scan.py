#!/usr/bin/env python3
"""Скан дефектов извлечения текста в человеческой части корпуса.

    python 09-tools/text_defects_scan.py

**Статус — post hoc diagnostic, 2026-07-29.** Проверка запущена после того, как
ручной разбор ошибок нашёл в двух ложноположительных документах дефекты сбора:
в `human_news_lenta_0019` хвост статьи продублирован каскадом, в
`human_science_cyberleninka_0036` слиплись колонки PDF. Скан отвечает на вопрос,
единичные это случаи или свойство источника, и связаны ли они с ложными
срабатываниями процедуры 2.

Меряются два дефекта, оба видны без обращения к оригиналу публикации:

1. **повтор предложений** — доля предложений документа, встречающихся более
   одного раза; нормальный текст почти не содержит дословных повторов;
2. **склейка на границе предложений** — число мест вида `слово.Слово`, где
   точка не отделена пробелом от следующего слова.

Скан читает профили `prep-v4/prose`, то есть ровно тот текст, по которому
считались признаки. Ни одна замороженная величина не изменяется.
"""

import csv
import re
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clf_run as clf  # noqa: E402

ROOT = clf.ROOT

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PROSE = ROOT / "04-corpus" / "derived" / "prep-v4" / "prose"
PREDICTIONS = ROOT / "07-analysis" / "fairness-v1-predictions.csv"
OUT_CSV = ROOT / "07-analysis" / "text-defects-v1.csv"
OUT_REPORT = ROOT / "07-analysis" / "text-defects-v1.md"

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
GLUED = re.compile(r"[а-яёa-z0-9][.!?][А-ЯЁA-Z]")
MIN_SENT_CHARS = 40      # короткие строки повторяются законно: заголовки, подписи


def scan(path):
    text = path.read_text(encoding="utf-8")
    sentences = [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]
    if not sentences:
        return None
    counts = defaultdict(int)
    for s in sentences:
        counts[s] += 1
    repeated = sum(n for n in counts.values() if n > 1)
    return {
        "n_sentences": len(sentences),
        "repeat_share": repeated / len(sentences),
        "glued_per_1000w": (len(GLUED.findall(text))
                            / max(len(text.split()), 1) * 1000),
    }


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"скан дефектов текста, {stamp}")

    registry = {r["document_id"]: r for r in clf.read_rows(clf.DOCUMENTS, "utf-8-sig")}
    fp_docs = set()
    for r in csv.DictReader(PREDICTIONS.open(encoding="utf-8")):
        if r["model"] == "main" and r["estimand"] == "full" and r["false_positive"] == "1":
            fp_docs.add(r["document_id"])

    rows = []
    for doc_id, row in registry.items():
        if row["origin_class"] != "H":
            continue
        path = PROSE / f"{doc_id}.txt"
        if not path.exists():
            continue
        stats = scan(path)
        if stats is None:
            continue
        rows.append({
            "document_id": doc_id,
            "source": row["source_platform"],
            "genre": row["genre"],
            "false_positive": int(doc_id in fp_docs),
            **{k: round(v, 4) for k, v in stats.items()},
        })
    clf.write_csv(OUT_CSV, rows)

    by_source = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)

    lines = [
        "# Дефекты извлечения текста в человеческой части корпуса",
        "",
        f"Собрано {stamp} скриптом `09-tools/text_defects_scan.py`.",
        "",
        "**Статус — post hoc diagnostic.** Проверка запущена после того, как ручной "
        "разбор ошибок нашёл дефекты сбора в двух ложноположительных документах. "
        "Замороженные величины не изменяются.",
        "",
        "Повтор предложений — доля предложений длиннее 40 символов, встречающихся в "
        "документе более одного раза. Склейка — случаи `слово.Слово` на 1000 слов.",
        "",
        "## По источникам",
        "",
        "| Источник | Документов | Медиана повторов | Доля документов с повторами выше 10% | "
        "Медиана склеек на 1000 слов | FPR процедуры 2 |",
        "|---|---|---|---|---|---|",
    ]
    for source in sorted(by_source, key=lambda s: -st.median(
            [r["repeat_share"] for r in by_source[s]])):
        items = by_source[source]
        rep = [r["repeat_share"] for r in items]
        glue = [r["glued_per_1000w"] for r in items]
        fp = st.fmean([r["false_positive"] for r in items])
        lines.append(
            f"| {source} | {len(items)} | {st.median(rep):.3f} | "
            f"{st.fmean([1 if x > 0.10 else 0 for x in rep]):.1%} | "
            f"{st.median(glue):.1f} | {fp:.1%} |")
    lines.append("")

    with_defect = [r for r in rows if r["repeat_share"] > 0.10]
    without = [r for r in rows if r["repeat_share"] <= 0.10]
    lines += [
        "## Связь с ложными срабатываниями",
        "",
        "| Группа | Документов | Доля обвинённых процедурой 2 |",
        "|---|---|---|",
        f"| повтор предложений выше 10% | {len(with_defect)} | "
        f"{st.fmean([r['false_positive'] for r in with_defect]):.1%} |"
        if with_defect else "| повтор предложений выше 10% | 0 | — |",
        f"| остальные | {len(without)} | "
        f"{st.fmean([r['false_positive'] for r in without]):.1%} |",
        "",
        "Доля обвинённых считается по объединению holdout: документ учитывается "
        "обвинённым, если хотя бы одно разбиение приписало ему машинное "
        "происхождение. Это верхняя оценка, и для сравнения групп между собой она "
        "годится, а как оценка FPR процедуры — нет.",
        "",
    ]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  документов просканировано {len(rows)}")
    print(f"  с повтором предложений выше 10%: {len(with_defect)}")
    print(f"  отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
