#!/usr/bin/env python3
"""Слепые карточки 30 нестабильных случаев.

    python 09-tools/make_blind_cards.py

Спецификация ослепления — `07-analysis/instability-v1-blinding-spec.md`,
зафиксирована до просмотра. Отбор случаев задан `instability-v1-spec.md` и здесь
не меняется.

Скрывается всё, что подсказывает ответ: идентификатор документа, класс
происхождения, holdout, номер и название преобразования, вероятности, направление
смены решения, место в рейтинге. Остаются два текста в случайном порядке.

Ключ пишется отдельным файлом и не открывается до завершения всех тридцати
карточек.
"""

import csv
import hashlib
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

CASES = ROOT / "07-analysis" / "instability-v1-cases.csv"
ORIG = ROOT / "04-corpus" / "derived" / "prep-v5" / "full"
TRANSFORMED = ROOT / "04-corpus" / "derived" / "stress-v3"

OUT_CARDS = ROOT / "07-analysis" / "instability-v1-blind-cards.md"
OUT_FORM = ROOT / "07-analysis" / "instability-v1-blind-verdicts.csv"
OUT_KEY = ROOT / "07-analysis" / "instability-v1-blinding-key.json"

SEED = 20260801
MAX_CHARS = 4000        # длинные тексты обрезаются одинаково у обеих версий

FORM_FIELDS = ["card_id", "expected_change_present", "unexpected_changes",
               "change_type", "verdict", "rationale", "reviewed_by", "reviewed_at"]

CHANGE_TYPES = ("дублирование", "потеря содержания", "структура абзацев",
                "разметка", "семантика", "другое")
VERDICTS = ("интерпретируемый стресс-случай", "составное воздействие",
            "технический дефект", "неопределённо")

# Всё, чего не должно быть в слепой части.
FORBIDDEN_PATTERNS = [
    re.compile(r"\bt\d{2}\b"),
    re.compile(r"оригинал", re.I),
    re.compile(r"преобразованн", re.I),
    re.compile(r"\bholdout", re.I),
    re.compile(r"flip_rate|instability_rate|max_abs_delta", re.I),
]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases():
    with CASES.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_texts(doc_id, number):
    original = (ORIG / f"{doc_id}.txt").read_text(encoding="utf-8")
    changed = (TRANSFORMED / f"t{number:02d}" / "full" / f"{doc_id}.txt").read_text(
        encoding="utf-8")
    return original[:MAX_CHARS], changed[:MAX_CHARS]


def build(cases, rng):
    """Карточки и ключ. Порядок карточек и текстов внутри задаёт сид."""
    order = list(range(len(cases)))
    rng.shuffle(order)

    cards, key = [], []
    for position, idx in enumerate(order, start=1):
        case = cases[idx]
        doc_id = case["document_id"]
        number = int(case["transform_number"])
        original, changed = read_texts(doc_id, number)

        first_is_original = rng.random() < 0.5
        text_a, text_b = ((original, changed) if first_is_original
                          else (changed, original))

        card_id = f"BLIND-{position:02d}"
        cards.append({"card_id": card_id, "text_a": text_a, "text_b": text_b})
        key.append({
            "card_id": card_id,
            "case_id": case["case_id"],
            "document_id": doc_id,
            "transform_number": number,
            "transform_name": case["transform_name"],
            "text_a_is": "original" if first_is_original else "transformed",
            "origin_class": case["origin_class"],
            "genre": case["genre"],
            "flip_rate": case["flip_rate"],
            "instability_rate": case["instability_rate"],
            "max_abs_delta": case["max_abs_delta"],
            "flipped_splits": case["flipped_splits"],
            "flip_direction": case["flip_direction"],
            "rank_in_selection": idx + 1,
        })
    return cards, key


def leak_check(text, doc_ids):
    """Ищет в слепом тексте всё, чего там быть не должно."""
    hits = []
    for pattern in FORBIDDEN_PATTERNS:
        found = pattern.search(text)
        if found:
            hits.append(f"шаблон {pattern.pattern!r} → {found.group(0)!r}")
    for doc_id in doc_ids:
        if doc_id in text:
            hits.append(f"идентификатор документа {doc_id!r}")
    return hits


def render_cards(cards):
    lines = [
        "# Слепые карточки: 30 нестабильных случаев",
        "",
        "Процедура — `07-analysis/instability-v1-blinding-spec.md`, зафиксирована "
        "до просмотра.",
        "",
        "В каждой карточке два текста, **A** и **B**, в случайном порядке. Который "
        "из них исходный, не сообщается. Скрыты также источник, класс "
        "происхождения, разбиение, тип воздействия, оценки модели и место случая в "
        "отборе.",
        "",
        "## Как заполнять",
        "",
        "На каждую карточку пять полей, вносятся в "
        "`instability-v1-blind-verdicts.csv`:",
        "",
        "| Поле | Значения |",
        "|---|---|",
        "| `expected_change_present` | да · нет · не определяется |",
        "| `unexpected_changes` | да · нет |",
        f"| `change_type` | {' · '.join(CHANGE_TYPES)} — можно несколько через `;` |",
        f"| `verdict` | {' · '.join(VERDICTS)} |",
        "| `rationale` | краткое основание с **дословным фрагментом** из карточки |",
        "",
        "«Ожидаемое изменение» на этом этапе означает: видно ли одно "
        "целенаправленное изменение, которое можно назвать одним словом, — в "
        "отличие от россыпи разнородных правок или отсутствия видимой разницы. "
        "Сверка с фактическим воздействием идёт после раскрытия ключа.",
        "",
        "Основание без дословного фрагмента считается незаполненным.",
        "",
        "**Ключ не открывается до завершения всех тридцати карточек.**",
        "",
    ]
    for card in cards:
        lines += [
            f"## {card['card_id']}",
            "",
            "### Текст A",
            "",
            "```",
            card["text_a"].rstrip(),
            "```",
            "",
            "### Текст B",
            "",
            "```",
            card["text_b"].rstrip(),
            "```",
            "",
        ]
    return "\n".join(lines)


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"слепые карточки, {stamp}")

    cases = load_cases()
    print(f"  случаев в отборе: {len(cases)}")
    if len(cases) != 30:
        raise SystemExit(f"ОСТАНОВ: ожидалось 30 случаев, прочитано {len(cases)}")

    rng = random.Random(SEED)
    cards, key = build(cases, rng)

    doc_ids = [c["document_id"] for c in cases]
    rendered = render_cards(cards)
    hits = leak_check(rendered, doc_ids)
    if hits:
        raise SystemExit("ОСТАНОВ: в слепой части найдено скрываемое — "
                         + "; ".join(hits[:5]))
    print(f"  проверка утечек: чисто, {len(FORBIDDEN_PATTERNS)} шаблонов и "
          f"{len(set(doc_ids))} идентификаторов")

    OUT_CARDS.write_text(rendered, encoding="utf-8")

    with OUT_FORM.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FORM_FIELDS)
        writer.writeheader()
        for card in cards:
            writer.writerow({"card_id": card["card_id"],
                             **{f: "" for f in FORM_FIELDS[1:]}})

    OUT_KEY.write_text(json.dumps({
        "series": "instability-v1-blinding",
        "spec": "07-analysis/instability-v1-blinding-spec.md",
        "seed": SEED,
        "do_not_open_until": "заполнены все 30 карточек в instability-v1-blind-verdicts.csv",
        "cards": key,
        "source_cases_sha256": sha256(CASES),
        "code_sha256": {Path(__file__).name: sha256(Path(__file__))},
        "created_at": stamp,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    a_original = sum(1 for k in key if k["text_a_is"] == "original")
    print(f"  порядок текстов: A исходный в {a_original} карточках из {len(key)}")
    print(f"  записано: {OUT_CARDS.name}, {OUT_FORM.name}, {OUT_KEY.name}")
    print("  ключ не открывать до завершения всех тридцати карточек")


if __name__ == "__main__":
    main()
