#!/usr/bin/env python3
"""Карточки кандидатов repeat-scan-v2 для ручной классификации.

    python 09-tools/repeat_candidates_cards.py

Готовит материал для независимой экспертной проверки: по каждому кандидату
`prep-v5` — дословные фрагменты повторяющихся предложений, позиции вхождений,
структурные маркеры документа и метрики повтора.

**Чего в карточках намеренно нет.** Оценок процедуры 2 и вклада M01. Решение PI
от 2026-08-01: классификация не должна зависеть от того, обвинил документ
классификатор или нет, иначе разбор дефектов сбора превратится в объяснение
поведения модели. Источник, жанр и структурный контекст оставлены — без них
двойную библиографию не отличить от дефекта извлечения.

**Три класса вердикта:**

- `confirmed_defect` — повтор возник из-за извлечения, посредника или
  технического каскада;
- `legitimate_source_property` — библиография на двух языках, цитирование,
  нормативный повтор, авторская структура;
- `unresolved` — происхождение однозначно не устанавливается.

Принудительно распределять спорные случаи не требуется: `unresolved` — полноценный
исход, а не отложенное решение.
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

SCAN = ROOT / "07-analysis" / "repeat-scan-v2-prep-v5.csv"
PROSE = ROOT / "04-corpus" / "derived" / "prep-v5" / "prose"
OUT_MD = ROOT / "07-analysis" / "repeat-scan-v2-manual.md"
OUT_CSV = ROOT / "07-analysis" / "repeat-scan-v2-verdicts.csv"

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
MIN_SENT_CHARS = 40
FRAGMENT_CHARS = 150
FRAGMENTS_PER_CARD = 3

VERDICTS = ("confirmed_defect", "legitimate_source_property", "unresolved")

# Маркеры структуры, по которым двойной список литературы отличается от дубля
# извлечения. Ищутся в тексте как есть, без нормализации регистра слова целиком.
STRUCTURE_MARKERS = ("References", "Литература", "Список литературы", "Библиография")

# Предварительная разметка автора работы. Не итог: PI выносит вердикты
# независимо, и совпадение с этой колонкой ничего не подтверждает.
PRELIMINARY = {
    "human_news_buriy_2014_0014": "confirmed_defect",
    "human_news_buriy_2014_0045": "confirmed_defect",
    "human_news_buriy_2014_0040": "confirmed_defect",
    "human_news_buriy_2014_0024": "confirmed_defect",
    "human_news_buriy_2014_0042": "confirmed_defect",
    "human_news_buriy_2014_0048": "unresolved",
    "human_seo_drmax_0013": "confirmed_defect",
    "human_seo_drmax_0024": "unresolved",
    "human_seo_devaka_ru_0027": "confirmed_defect",
    "human_seo_devaka_ru_0021": "confirmed_defect",
    "human_seo_devaka_ru_0019": "unresolved",
    "human_seo_madcats_0005": "legitimate_source_property",
    "human_seo_convertmonster_0001": "confirmed_defect",
    "human_science_spbgu_0042": "legitimate_source_property",
    "human_science_spbgu_0044": "legitimate_source_property",
    "human_science_spbgu_0062": "legitimate_source_property",
    "human_science_urfu_0039": "unresolved",
}


def segment(text):
    return [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]


def analyse(doc_id):
    text = (PROSE / f"{doc_id}.txt").read_text(encoding="utf-8")
    sentences = segment(text)
    counts = Counter(sentences)
    positions = defaultdict(list)
    for i, s in enumerate(sentences):
        if counts[s] > 1:
            positions[s].append(i)

    offsets = [p[1] - p[0] for p in positions.values() if len(p) == 2]
    modal = Counter(offsets).most_common(1)
    markers = {m: [i for i, s in enumerate(sentences) if m in s]
               for m in STRUCTURE_MARKERS}
    markers = {m: pos for m, pos in markers.items() if pos}

    return {
        "sentences": len(sentences),
        "repeated_unique": len(positions),
        "positions": dict(sorted(positions.items(), key=lambda kv: kv[1][0])),
        "modal_offset": modal[0] if modal else None,
        "markers": markers,
    }


def mechanism(info):
    """Механизм по структуре повторов, без суждения о происхождении."""
    modal = info["modal_offset"]
    if info["markers"] and modal and modal[1] >= 3:
        return "повторы по обе стороны структурного заголовка"
    if modal and modal[1] >= 3:
        return "блочный повтор с постоянным смещением"
    if any(n > 2 for n in
           (len(p) for p in info["positions"].values())):
        return "кратность выше двух"
    return "разрозненные повторы"


def fragment(text):
    one = " ".join(text.split())
    return one if len(one) <= FRAGMENT_CHARS else one[:FRAGMENT_CHARS] + "…"


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with SCAN.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["verdict"] == "pending"]
    rows.sort(key=lambda r: -float(r["repeat_share"]))
    print(f"кандидатов: {len(rows)}")

    lines = [
        "# Кандидаты repeat-scan-v2 для ручной классификации",
        "",
        f"Собрано {stamp} скриптом `09-tools/repeat_candidates_cards.py`. Источник "
        "кандидатов — `repeat-scan-v2-prep-v5.csv`, отбор по порогу 0.10 из "
        "`repeat-scan-v2-spec.md` §2.",
        "",
        "## Что здесь есть и чего нет",
        "",
        "Оценок процедуры 2 и вклада M01 в карточках нет намеренно, решение PI от "
        "2026-08-01: классификация не должна зависеть от того, обвинил документ "
        "классификатор или нет. Источник, жанр и структурный контекст оставлены — "
        "без них двойную библиографию не отличить от дефекта извлечения.",
        "",
        "Фрагменты приводятся дословно из `prep-v5/prose`, обрезаны по "
        f"{FRAGMENT_CHARS} символам. Позиции — порядковые номера предложений после "
        "фильтра длины 40 символов.",
        "",
        "## Классы вердикта",
        "",
        "| Класс | Когда |",
        "|---|---|",
        "| `confirmed_defect` | повтор возник из-за извлечения, посредника или технического каскада |",
        "| `legitimate_source_property` | библиография на двух языках, цитирование, нормативный повтор, авторская структура |",
        "| `unresolved` | происхождение однозначно не устанавливается |",
        "",
        "`unresolved` — полноценный исход. Принудительно распределять спорные "
        "случаи не требуется.",
        "",
        "## Статус предварительной разметки",
        "",
        "Колонка «предварительно» — разметка автора работы, **не итог**. До "
        "независимой проверки PI число подтверждённых дефектов не публикуется, а в "
        "статье используется только автоматический результат: 17 человеческих и 0 "
        "машинных документов превысили порог 0.10 на `prep-v5`.",
        "",
        "Вердикты вносятся в `repeat-scan-v2-verdicts.csv`, колонка `verdict`.",
        "",
        "## Карточки",
        "",
    ]

    verdict_rows = []
    for n, row in enumerate(rows, start=1):
        doc_id = row["document_id"]
        info = analyse(doc_id)
        modal = info["modal_offset"]
        marker_note = ", ".join(f"`{m}` на позиции {pos[0]}"
                                for m, pos in info["markers"].items()) or "нет"

        lines += [
            f"### {n}. `{doc_id}`",
            "",
            f"- источник: {row['source']}, жанр: {row['genre']}",
            f"- доля повторов {float(row['repeat_share']):.3f}, "
            f"предложений {info['sentences']}, "
            f"повторяющихся различных {info['repeated_unique']}",
            f"- наибольшая кратность {row['max_multiplicity']}, "
            f"цепочка подряд {row['longest_repeated_block']}, "
            f"смежность {float(row['adjacent_share']):.2f}",
            f"- модальное смещение между копиями: "
            + (f"{modal[0]} позиций у {modal[1]} повторов" if modal else "не определено"),
            f"- структурные маркеры: {marker_note}",
            f"- механизм по структуре: {mechanism(info)}",
            f"- предварительно: `{PRELIMINARY.get(doc_id, 'unresolved')}`",
            "- **вердикт PI:** _______",
            "",
            "Фрагменты:",
            "",
        ]
        for text, pos in list(info["positions"].items())[:FRAGMENTS_PER_CARD]:
            lines.append(f"- позиции {pos}: «{fragment(text)}»")
        lines.append("")

        verdict_rows.append({
            "document_id": doc_id,
            "source": row["source"],
            "genre": row["genre"],
            "repeat_share": row["repeat_share"],
            "max_multiplicity": row["max_multiplicity"],
            "longest_repeated_block": row["longest_repeated_block"],
            "modal_offset": modal[0] if modal else "",
            "modal_offset_count": modal[1] if modal else "",
            "structure_markers": ";".join(info["markers"]) or "",
            "mechanism": mechanism(info),
            "preliminary": PRELIMINARY.get(doc_id, "unresolved"),
            "verdict": "",
            "reviewed_by": "",
            "reviewed_at": "",
        })

    lines += [
        "## Сводка предварительной разметки",
        "",
        "| Класс | Документов |",
        "|---|---|",
    ]
    tally = Counter(r["preliminary"] for r in verdict_rows)
    for cls in VERDICTS:
        lines.append(f"| `{cls}` | {tally.get(cls, 0)} |")
    lines += [
        "",
        "Ещё раз: это не итог. Итог появляется после независимой проверки, и в "
        "статье он публикуется с оговоркой, что классификацию проводил один "
        "оценщик.",
        "",
    ]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(verdict_rows[0]))
        writer.writeheader()
        writer.writerows(verdict_rows)

    print(f"  предварительно: {dict(tally)}")
    print(f"  записано: {OUT_MD.name}, {OUT_CSV.name}")


if __name__ == "__main__":
    main()
