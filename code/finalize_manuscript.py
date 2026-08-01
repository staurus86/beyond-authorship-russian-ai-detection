#!/usr/bin/env python3

"""Финальная редактура рукописи: цитаты и единообразие чисел.



    python 09-tools/finalize_manuscript.py



Две задачи, обе из редакторского чеклиста §5 манифеста сборки.



**Служебные `ref_id` заменяются на авторско-годовые ссылки.** В рабочих

документах ссылки на литературу стоят как `r004` — это ключ строки в

`evidence-matrix.csv`, внутренний идентификатор проекта. В публикуемом тексте его

быть не должно. Соответствие `ref_id` → библиографическая запись берётся из той же

матрицы, а форма ссылки — фамилия первого автора, `et al.` при числе авторов

больше двух, год.



**Разрядность чисел скрипт не трогает, и это решение по итогам первой попытки.**

Автоматическое приведение к четырём знакам испортило три вида записей: пороги

(«потолок FPR 0.5%» превращался в «0.5000%»), уже согласованные доли и величины,

которые в тексте намеренно даны с шестью знаками — Brier 0.036515 округлялся до

0.0365, то есть терял точность. Регулярное выражение не различает измеренную

величину, порог и долю, а различие это смысловое.



Единообразие разрядности остаётся редакторской задачей и делается точечно, с

разбором каждого случая. Правило записано в отчёте.

"""



import csv

import hashlib

import re

import sys

from datetime import datetime, timezone

from pathlib import Path



ROOT = Path(__file__).resolve().parent.parent

SRC = ROOT / "08-paper" / "manuscript.md"

OUT = ROOT / "08-paper" / "manuscript-final.md"

OUT_REPORT = ROOT / "08-paper" / "manuscript-finalize-report.md"

MATRIX = ROOT / "01-literature" / "evidence-matrix.csv"

BIB = ROOT / "01-literature" / "references.bib"



for stream in (sys.stdout, sys.stderr):

    if hasattr(stream, "reconfigure"):

        stream.reconfigure(encoding="utf-8", errors="replace")





def load_refs(text=None):

    """ref_id -> ссылка вида «Dugan et al., 2024».



    При совпадении «автор, год» у нескольких работ добавляются суффиксы a, b, c.

    Порядок суффиксов задаётся первым появлением ref_id в тексте, а не номером

    записи в матрице: читатель встречает ссылки в порядке чтения.

    """

    base, order = {}, {}

    with MATRIX.open(encoding="utf-8") as fh:

        for r in csv.DictReader(fh):

            authors = [a.strip() for a in (r.get("authors") or "").split(",") if a.strip()]

            surnames = [a for a in authors

                        if not re.fullmatch(r"[А-ЯA-Z]\.(\s*[А-ЯA-Z]\.)*", a)]

            first = surnames[0] if surnames else "Anon"

            year = (r.get("year") or "n.d.").strip()

            if len(surnames) == 1:

                cite = f"{first}, {year}"

            elif len(surnames) == 2:

                cite = f"{first} и {surnames[1]}, {year}"

            else:

                cite = f"{first} et al., {year}"

            base[r["ref_id"]] = cite



    if text:

        for rid in base:

            m = re.search(r"\b" + rid + r"\b", text)

            order[rid] = m.start() if m else 10 ** 9



    groups = {}

    for rid, cite in base.items():

        groups.setdefault(cite, []).append(rid)



    refs, collisions = {}, {}

    for cite, ids in groups.items():

        if len(ids) == 1:

            refs[ids[0]] = cite

            continue

        ids_sorted = sorted(ids, key=lambda r: (order.get(r, 10 ** 9), r))

        for i, rid in enumerate(ids_sorted):

            suffix = chr(ord("a") + i)

            refs[rid] = cite.replace(f", {cite.rsplit(', ', 1)[1]}",

                                     f", {cite.rsplit(', ', 1)[1]}{suffix}")

        collisions[cite] = [(r, refs[r]) for r in ids_sorted]

    return refs, collisions





def replace_refs(text, refs):

    """Замена r0NN на авторско-годовую ссылку. Возвращает (текст, отчёт)."""

    missing, used = set(), {}



    def one(m):

        rid = m.group(0)

        if rid not in refs:

            missing.add(rid)

            return rid

        used[rid] = used.get(rid, 0) + 1

        return refs[rid]



    out = re.sub(r"\br\d{3}\b", one, text)

    return out, used, missing





def digit_audit(text):

    """Только отчёт: где какая разрядность встречается у ключевых величин.



    Ничего не меняет. Приведение делает редактор вручную: автоматика не отличает

    измеренную величину от порога и от доли, и первая попытка это показала.

    """

    pattern = re.compile(r"(AUROC|MCC|Brier|ECE|MCE|FPR|TPR)[^0-9\n]{0,40}?(\d\.\d{1,8})")

    seen = {}

    for m in pattern.finditer(text):

        digits = len(m.group(2).split(".")[1])

        seen.setdefault(digits, []).append(f"{m.group(1)} {m.group(2)}")

    return seen





def main():

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print(f"финальная редактура, {stamp}")



    text = SRC.read_text(encoding="utf-8")

    before_numbers = re.findall(r"\d+\.\d+", text)



    refs, collisions = load_refs(text)

    print(f"  записей в матрице литературы: {len(refs)}")

    for cite, items in collisions.items():

        print(f"  коллизия «{cite}» -> " + ", ".join(f"{r}={c}" for r, c in items))



    text, used, missing = replace_refs(text, refs)

    print(f"  заменено ссылок: {sum(used.values())} вхождений, {len(used)} различных")

    if missing:

        print(f"  БЕЗ СООТВЕТСТВИЯ: {sorted(missing)}")



    digits = digit_audit(text)

    print("  разрядность у ключевых величин: "

          + ", ".join(f"{k} зн. — {len(v)}" for k, v in sorted(digits.items())))



    left = re.findall(r"\br\d{3}\b", text)

    if left:

        raise SystemExit(f"ОСТАНОВ: в тексте остались служебные идентификаторы: {sorted(set(left))}")



    OUT.write_text(text, encoding="utf-8", newline="\n")



    lines = [

        "# Отчёт о финальной редактуре", "",

        f"Собрано {stamp} скриптом `09-tools/finalize_manuscript.py`.", "",

        "## Ссылки на литературу", "",

        f"- заменено вхождений: {sum(used.values())};",

        f"- различных источников: {len(used)};",

        f"- без соответствия в матрице: {len(missing)};",

        f"- служебных идентификаторов в итоговом тексте: {len(left)}.",

        "",

    ]

    if used:

        lines += ["| ref_id | Ссылка в тексте | Вхождений |", "|---|---|---|"]

        for rid in sorted(used):

            lines.append(f"| `{rid}` | {refs[rid]} | {used[rid]} |")

    lines += ["", "## Разрядность чисел: аудит без правки", "",

              "Автоматическое приведение отключено после первой попытки: оно "

              "испортило пороги («потолок FPR 0.5%» → «0.5000%») и округлило "

              "величины, намеренно данные с шестью знаками (Brier 0.036515 → "

              "0.0365). Регулярное выражение не отличает измеренную величину от "

              "порога и доли.", "",

              "| Знаков после запятой | Вхождений | Примеры |", "|---|---|---|"]

    for k, v in sorted(digits.items()):

        lines.append(f"| {k} | {len(v)} | {'; '.join(v[:3])} |")

    lines += ["", "Правило для редактора: измеренные метрики качества и калибровки "

              "— четыре знака, кроме случаев, где в Results намеренно дано больше; "

              "пороги записываются как в постановке (1%, 5%, 0.5%); p-value — как в "

              "источнике."]



    lines += ["", "## Контроль", "",

              f"- хеш итогового файла: `{hashlib.sha256(text.encode('utf-8')).hexdigest()}`;",

              f"- строк: {len(text.splitlines())}.", ""]

    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")



    print(f"  записано: {OUT.name}, {OUT_REPORT.name}")





if __name__ == "__main__":

    main()
