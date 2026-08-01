#!/usr/bin/env python3
"""Симметричный скан дословных повторов: обе части корпуса, две версии препроцессинга.

    python 09-tools/repeat_scan_v2.py

Спецификация — `07-analysis/repeat-scan-v2-spec.md`, зафиксирована до запуска.

**Статус — post hoc diagnostic.** Замороженные величины не меняются, документы не
исключаются, признаки не пересчитываются.

Причина скана: `text_defects_scan.py` строка 80 отбирает документы условием
`origin_class == "H"`, поэтому утверждение «односторонний дефект против нуля
машинных» из отчёта v1 не следует — машинная часть не сканировалась вовсе.

Параметры сегментации, нормализации, фильтра длины и порога взяты из v1 без
единого изменения: иначе аудит A перестанет быть сравнимым с историческим числом.
Скрипт останавливается, если контрольное число 97 не воспроизводится.
"""

import csv
import hashlib
import json
import re
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PREP_V4 = ROOT / "04-corpus" / "derived" / "prep-v4" / "prose"
PREP_V5 = ROOT / "04-corpus" / "derived" / "prep-v5" / "prose"
REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
REGISTRY_HISTORIC = ROOT / "04-corpus" / "documents-registry.csv.bak-before-correction-exclusion"

OUT_V4 = ROOT / "07-analysis" / "repeat-scan-v2-prep-v4.csv"
OUT_V5 = ROOT / "07-analysis" / "repeat-scan-v2-prep-v5.csv"
OUT_REPORT = ROOT / "07-analysis" / "repeat-scan-v2-report.md"
OUT_MANIFEST = ROOT / "07-analysis" / "repeat-scan-v2-manifest.json"

# Всё ниже — из text_defects_scan.py, строки 46-48 и 131. Не менять без новой
# ревизии спецификации: аудит A сравнивается с числом 97 из v1.
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
GLUED = re.compile(r"[а-яёa-z0-9][.!?][А-ЯЁA-Z]")
MIN_SENT_CHARS = 40
THRESHOLD = 0.10

# Контрольное число: человеческих документов с repeat_share > 0.10 на историческом
# составе prep-v4 (`text-defects-v1.md`, раздел «Связь с ложными срабатываниями»).
CONTROL_V1_HUMAN = 97

FIELDS = ["document_id", "origin_class", "source", "genre", "in_current_registry",
          "n_sentences", "repeat_share", "repeated_unique", "max_multiplicity",
          "longest_repeated_block", "repeat_char_share", "adjacent_share",
          "glued_per_1000w", "verdict"]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_registry(path):
    with path.open(encoding="utf-8-sig") as fh:
        return {r["document_id"]: r for r in csv.DictReader(fh)}


def segment(text):
    """Сегментация и нормализация ровно как в v1: strip плюс фильтр длины."""
    return [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]


def scan_text(text):
    """Метрики повтора по одному документу. None — если считать не на чем."""
    sentences = segment(text)
    if not sentences:
        return None

    counts = defaultdict(int)
    for s in sentences:
        counts[s] += 1

    # Знаменатель v1: все вхождения повторяющихся предложений, включая первое.
    repeated_occurrences = sum(n for n in counts.values() if n > 1)
    repeated_unique = sum(1 for n in counts.values() if n > 1)

    # Наибольшая цепочка подряд идущих повторяющихся предложений в исходном порядке.
    longest = current = 0
    for s in sentences:
        current = current + 1 if counts[s] > 1 else 0
        longest = max(longest, current)

    # Доля символов внутри повторяющихся предложений.
    total_chars = sum(len(s) for s in sentences)
    repeat_chars = sum(len(s) for s in sentences if counts[s] > 1)

    # Смежность: сколько избыточных вхождений стоит вплотную к такому же
    # предложению. Каскадный дубль извлечения даёт высокую долю, рефрен — низкую.
    excess = repeated_occurrences - repeated_unique
    adjacent = sum(1 for i in range(len(sentences) - 1)
                   if sentences[i] == sentences[i + 1])

    return {
        "n_sentences": len(sentences),
        "repeat_share": repeated_occurrences / len(sentences),
        "repeated_unique": repeated_unique,
        "max_multiplicity": max(counts.values()),
        "longest_repeated_block": longest,
        "repeat_char_share": repeat_chars / total_chars if total_chars else 0.0,
        "adjacent_share": adjacent / excess if excess else 0.0,
        "glued_per_1000w": len(GLUED.findall(text)) / max(len(text.split()), 1) * 1000,
    }


def scan_profile(profile_dir, registry, current_ids):
    """Скан всех файлов профиля. Метаданные берутся из переданного реестра."""
    rows, skipped, unknown = [], [], []
    for path in sorted(profile_dir.glob("*.txt")):
        doc_id = path.stem
        meta = registry.get(doc_id)
        if meta is None:
            unknown.append(doc_id)
            continue
        stats = scan_text(path.read_text(encoding="utf-8"))
        if stats is None:
            skipped.append(doc_id)
            continue
        rows.append({
            "document_id": doc_id,
            "origin_class": meta["origin_class"],
            "source": meta.get("source_platform", ""),
            "genre": meta.get("genre", ""),
            "in_current_registry": int(doc_id in current_ids),
            **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in stats.items()},
            "verdict": "pending" if stats["repeat_share"] > THRESHOLD else "",
        })
    return rows, skipped, unknown


def candidates(rows, origin=None, current_only=False):
    out = [r for r in rows if r["repeat_share"] > THRESHOLD]
    if origin:
        out = [r for r in out if r["origin_class"] == origin]
    if current_only:
        out = [r for r in out if r["in_current_registry"]]
    return out


def class_table(rows, current_only=False):
    """Строки таблицы по классам: сколько документов, кандидатов, медианы."""
    table = []
    for cls in ("H", "A"):
        items = [r for r in rows if r["origin_class"] == cls
                 and (r["in_current_registry"] if current_only else True)]
        if not items:
            continue
        cand = [r for r in items if r["repeat_share"] > THRESHOLD]
        table.append({
            "class": cls,
            "documents": len(items),
            "candidates": len(cand),
            "share": len(cand) / len(items),
            "median_repeat": st.median([r["repeat_share"] for r in items]),
            "median_glued": st.median([r["glued_per_1000w"] for r in items]),
        })
    return table


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def fmt_class_table(table):
    lines = ["| Класс | Документов | Кандидатов (>0.10) | Доля | Медиана повторов | Медиана склеек |",
             "|---|---|---|---|---|---|"]
    for r in table:
        lines.append(f"| {r['class']} | {r['documents']} | {r['candidates']} | "
                     f"{r['share']:.1%} | {r['median_repeat']:.3f} | {r['median_glued']:.1f} |")
    return lines


def write_report(rows_v4, rows_v5, control_ok, stamp):
    v4_hist = class_table(rows_v4)
    v4_curr = class_table(rows_v4, current_only=True)
    v5 = class_table(rows_v5)

    lines = [
        "# Дословные повторы в обеих частях корпуса",
        "",
        f"Собрано {stamp} скриптом `09-tools/repeat_scan_v2.py`. Спецификация — "
        "`07-analysis/repeat-scan-v2-spec.md`, зафиксирована до запуска.",
        "",
        "**Статус — post hoc diagnostic.** Замороженные величины не менялись.",
        "",
        "Скан v1 покрывал только человеческую часть, поэтому вывод об "
        "односторонности дефекта из него не следовал. Здесь сканируются оба класса "
        "на обеих версиях препроцессинга. Параметры сегментации, фильтра длины и "
        "порога взяты из v1 без изменений.",
        "",
        "## Аудит A: prep-v4, исторический состав",
        "",
        "1916 документов реестра до исключения 29 июля 2026 — тот же состав, на "
        "котором работал скан v1.",
        "",
        *fmt_class_table(v4_hist),
        "",
        f"Контрольная проверка: человеческих кандидатов "
        f"{sum(r['candidates'] for r in v4_hist if r['class'] == 'H')}, "
        f"в `text-defects-v1.md` — {CONTROL_V1_HUMAN}. "
        + ("Совпадает, параметры воспроизведены." if control_ok
           else "**Не совпадает** — см. остановку расчёта."),
        "",
        "## Аудит A: prep-v4, действующий состав",
        "",
        "Те же тексты, но без 34 документов, исключённых кодом `LEN` 29 июля.",
        "",
        *fmt_class_table(v4_curr),
        "",
        "## Аудит B: prep-v5, действующий состав",
        "",
        "Тексты, на которых обучался исправленный классификатор.",
        "",
        *fmt_class_table(v5),
        "",
        "## Что эти числа значат и чего не значат",
        "",
        "Кандидат — документ с `repeat_share` выше 0.10, не более того. Категория "
        "«подтверждённый дефект» и категория «допустимый повтор как свойство "
        "текста» заполняются человеком по текстам; до разбора у всех кандидатов "
        "`verdict = pending`, и ни один документ дефектным не называется.",
        "",
        "Формулировка «односторонний дефект» становится допустимой только после "
        "разбора обеих частей корпуса и только если числа подтверждённых дефектов "
        "окажутся несопоставимыми.",
        "",
        "**Что уже допустимо утверждать, решение PI от 2026-08-01.** На уровне "
        "автоматического критерия — «повторы выше 0.10 обнаружены только в "
        "человеческой части». Это прямое чтение таблиц выше, суждения оно не "
        "требует. Слово «дефект» применяется лишь к вручную подтверждённым "
        "случаям; семнадцать кандидатов `prep-v5` остаются кандидатами с "
        "отдельными вердиктами по каждому, разбор — `repeat-scan-v2-manual.md`.",
        "",
        "Поведение процедуры 2 этот скан не объясняет: для механизма нужны "
        "распределение M01 по классам внутри train-fold и направление вклада M01 в "
        "моделях. Это отдельный расчёт.",
        "",
    ]

    # Кандидаты по источникам: где именно сидит повтор.
    for title, rows, current_only in (("prep-v4, исторический состав", rows_v4, False),
                                      ("prep-v5", rows_v5, False)):
        cand = candidates(rows, current_only=current_only)
        if not cand:
            continue
        by_source = defaultdict(list)
        for r in cand:
            by_source[(r["origin_class"], r["source"] or "—")].append(r)
        lines += [f"## Кандидаты по источникам: {title}", "",
                  "| Класс | Источник | Кандидатов | Медиана повторов | "
                  "Медиана max кратности | Медиана смежности |",
                  "|---|---|---|---|---|---|"]
        for (cls, source), items in sorted(by_source.items(),
                                           key=lambda kv: (kv[0][0], -len(kv[1]))):
            lines.append(
                f"| {cls} | {source} | {len(items)} | "
                f"{st.median([r['repeat_share'] for r in items]):.3f} | "
                f"{st.median([r['max_multiplicity'] for r in items]):.0f} | "
                f"{st.median([r['adjacent_share'] for r in items]):.2f} |")
        lines.append("")

    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"симметричный скан повторов, {stamp}")

    current = read_registry(REGISTRY)
    historic = read_registry(REGISTRY_HISTORIC)
    current_ids = set(current)
    print(f"  реестр действующий: {len(current)}, исторический: {len(historic)}")

    rows_v4, skipped4, unknown4 = scan_profile(PREP_V4, historic, current_ids)
    rows_v5, skipped5, unknown5 = scan_profile(PREP_V5, current, current_ids)
    print(f"  prep-v4: просканировано {len(rows_v4)}, без длинных предложений "
          f"{len(skipped4)}, вне реестра {len(unknown4)}")
    print(f"  prep-v5: просканировано {len(rows_v5)}, без длинных предложений "
          f"{len(skipped5)}, вне реестра {len(unknown5)}")

    if unknown4:
        raise SystemExit(f"ОСТАНОВ: {len(unknown4)} документов prep-v4 нет в историческом "
                         f"реестре, метаданные брать неоткуда: {unknown4[:5]}")

    human_hist = len(candidates(rows_v4, origin="H"))
    control_ok = human_hist == CONTROL_V1_HUMAN
    print(f"  контроль: человеческих кандидатов на историческом составе {human_hist}, "
          f"ожидалось {CONTROL_V1_HUMAN} — {'совпало' if control_ok else 'РАСХОЖДЕНИЕ'}")

    write_csv(OUT_V4, rows_v4)
    write_csv(OUT_V5, rows_v5)
    write_report(rows_v4, rows_v5, control_ok, stamp)

    manifest = {
        "series": "repeat-scan-v2",
        "status": "post hoc diagnostic",
        "spec": "07-analysis/repeat-scan-v2-spec.md",
        "reason": ("скан v1 покрывал только origin_class == H; утверждение об "
                   "односторонности дефекта требовало симметричного расчёта"),
        "parameters": {
            "profile": "prose",
            "sentence_split": SENT_SPLIT.pattern,
            "min_sentence_chars": MIN_SENT_CHARS,
            "threshold": THRESHOLD,
            "denominator_rule": ("sum(n) по предложениям с n>1, делённое на общее число "
                                 "предложений после фильтра длины; повтор трижды даёт 3"),
            "source_of_parameters": "09-tools/text_defects_scan.py, строки 46-48 и 131",
        },
        "audit_a_prep_v4": {
            "documents": len(rows_v4),
            "historic_registry": REGISTRY_HISTORIC.name,
            "by_class_historic": class_table(rows_v4),
            "by_class_current": class_table(rows_v4, current_only=True),
            "skipped_no_long_sentences": skipped4,
        },
        "audit_b_prep_v5": {
            "documents": len(rows_v5),
            "by_class": class_table(rows_v5),
            "skipped_no_long_sentences": skipped5,
        },
        "control_check": {
            "expected_human_candidates_v1": CONTROL_V1_HUMAN,
            "got": human_hist,
            "passed": control_ok,
        },
        "verdicts": {"pending": sum(1 for r in rows_v4 + rows_v5 if r["verdict"] == "pending"),
                     "defect": 0, "legitimate": 0},
        "inputs_sha256": {REGISTRY.name: sha256(REGISTRY),
                          REGISTRY_HISTORIC.name: sha256(REGISTRY_HISTORIC)},
        "code_sha256": {Path(__file__).name: sha256(Path(__file__))},
        "outputs": [OUT_V4.name, OUT_V5.name, OUT_REPORT.name],
        "created_at": stamp,
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    for title, rows in (("prep-v4 исторический", rows_v4), ("prep-v5", rows_v5)):
        line = ", ".join(f"{t['class']}: {t['candidates']} из {t['documents']} "
                         f"({t['share']:.1%})" for t in class_table(rows))
        print(f"  кандидаты {title} — {line}")
    print(f"  записано: {OUT_V4.name}, {OUT_V5.name}, {OUT_REPORT.name}, {OUT_MANIFEST.name}")

    if not control_ok:
        raise SystemExit("ОСТАНОВ: контрольное число не воспроизвелось, параметры "
                         "скана расходятся с v1 — выходы записаны для разбора, "
                         "выводы по ним не делать")


if __name__ == "__main__":
    main()
