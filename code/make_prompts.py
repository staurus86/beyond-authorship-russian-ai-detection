#!/usr/bin/env python3
"""Генерация текстов промптов из 03-briefs/briefs.json.

Собирает 45 заданий × 3 режима = 135 готовых к вставке промптов, заполняет
реестр промптов и пишет инструкцию по ручному прогону.

Содержательные требования во всех трёх режимах одинаковы. Различаются только
ограничения формы — в этом смысл эксперимента (RQ1).

    python 09-tools/make_prompts.py
    python 09-tools/make_prompts.py --only b001
"""

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BRIEFS = ROOT / "03-briefs" / "briefs.json"
OUT_DIR = ROOT / "03-briefs" / "prompts"
PROMPT_REGISTRY = ROOT / "03-briefs" / "prompt-registry.csv"
BRIEFS_REGISTRY = ROOT / "03-briefs" / "briefs-registry.csv"

CONDITIONS = ["P1", "P2", "P3"]

# Общая часть: одинакова во всех режимах.
CORE = """Напиши текст на русском языке.

Тема: {topic}
Аудитория: {audience}
Задача текста: {task}
Объём: {length_min}–{length_max} слов.

Обязательно раскрой:
{must_cover}

{sources_policy}"""

RIGID = """
Требования к оформлению:
- раздели текст на разделы с заголовками H2 по списку ниже;
- в каждом разделе используй списки или таблицу, где это уместно;
- добавь раздел «Частые вопросы» с тремя вопросами и ответами;
- заверши текст разделом «Вывод»;
- ключевую фразу «{keyword}» употреби в первом абзаце и в одном из заголовков;
- абзац не длиннее пяти строк.

Заголовки H2:
{headings}"""

ANTI_SLOP = """
Требования к стилю:
- не повторяй одну мысль разными словами;
- не пиши вводных абзацев о важности темы;
- не используй шаблонные обороты вроде «в современном мире», «важно отметить», «таким образом»;
- не выравнивай абзацы под одну длину;
- не завершай текст обобщающим выводом, если он не добавляет нового;
- не добавляй оговорки, которых не требует содержание."""


def build_headings(brief):
    """Заголовки для жёсткого режима строятся из обязательных пунктов."""
    return "\n".join(f"{index}. {point[0].upper()}{point[1:]}" for index, point in enumerate(brief["must_cover"], start=1))


def keyword(brief):
    return brief["topic"].split(":")[0].strip().lower()


def render(brief, defaults, condition):
    must_cover = "\n".join(f"- {point};" for point in brief["must_cover"])
    text = CORE.format(
        topic=brief["topic"],
        audience=brief["audience"],
        task=brief["task"],
        length_min=brief.get("length_min", defaults["length_min"]),
        length_max=brief.get("length_max", defaults["length_max"]),
        must_cover=must_cover,
        sources_policy=defaults["sources_policy"],
    )

    if condition == "P2":
        text += "\n" + RIGID.format(keyword=keyword(brief), headings=build_headings(brief))
    elif condition == "P3":
        text += "\n" + ANTI_SLOP

    return text.strip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="сгенерировать один бриф по id")
    args = parser.parse_args()

    data = json.loads(BRIEFS.read_text(encoding="utf-8"))
    defaults = data["defaults"]
    briefs = data["briefs"]
    if args.only:
        briefs = [b for b in briefs if b["id"] == args.only]
        if not briefs:
            raise SystemExit(f"нет брифа {args.only}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prompt_rows, brief_rows = [], []
    written = 0

    for brief in briefs:
        for condition in CONDITIONS:
            text = render(brief, defaults, condition)
            name = f"{brief['id']}_{condition}.txt"
            (OUT_DIR / name).write_text(text, encoding="utf-8")
            written += 1

            prompt_rows.append(
                {
                    "prompt_id": f"{brief['id']}_{condition}",
                    "brief_id": brief["id"],
                    "prompt_condition": condition,
                    "prompt_family": condition,
                    "system_prompt_hash": "",
                    "user_prompt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:32],
                    "user_prompt_text_file": f"03-briefs/prompts/{name}",
                    "required_structure": "H2+FAQ+вывод+ключевая фраза" if condition == "P2" else "",
                    "prohibited_content": "шаблонные обороты, повторы, ритуальный вывод" if condition == "P3" else "",
                    "target_length_min": brief.get("length_min", defaults["length_min"]),
                    "target_length_max": brief.get("length_max", defaults["length_max"]),
                    "created_date": "",
                    "frozen": "no",
                    "notes": "",
                }
            )

        brief_rows.append(
            {
                "brief_id": brief["id"],
                "genre": brief["genre"],
                "topic_id": brief["id"],
                "topic_title": brief["topic"],
                "audience": brief["audience"],
                "task": brief["task"],
                "length_min": brief.get("length_min", defaults["length_min"]),
                "length_max": brief.get("length_max", defaults["length_max"]),
                "required_facts_count": len(brief["must_cover"]),
                "sources_policy": "не выдумывать числа и цитаты",
                "created_date": "",
                "status": "ready",
                "human_authors_assigned": "",
                "ai_cells_planned": len(CONDITIONS),
                "notes": "",
            }
        )

    with PROMPT_REGISTRY.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(prompt_rows[0].keys()))
        writer.writeheader()
        writer.writerows(prompt_rows)

    with BRIEFS_REGISTRY.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(brief_rows[0].keys()))
        writer.writeheader()
        writer.writerows(brief_rows)

    print(f"Заданий: {len(briefs)}")
    print(f"Промптов записано: {written} в {OUT_DIR.relative_to(ROOT)}")
    print(f"Реестры обновлены: prompt-registry.csv, briefs-registry.csv")


if __name__ == "__main__":
    main()
