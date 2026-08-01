#!/usr/bin/env python3
"""Отчёт Pilot-1: проверка NLP-стека на development set.

Запуск из корня папки исследования:
    python 09-tools/pilot1_report.py            # отчёт на экран
    python 09-tools/pilot1_report.py --write    # плюс 06-features/pilot-1-report.md

Проверяются десять пунктов `06-features/pilot-1-spec.md` на 82 документах из
`06-features/pilot-1-ids.csv`. Набор отобран до расчёта первичных признаков и
выведен из confirmatory-теста.

Главное правило пилота: инструмент не выбирается по тому, какой лучше
разделяет человека и модель. AUROC здесь не считается вообще. Сравнение идёт
по качеству разбора и операционной устойчивости.

Что этот скрипт проверить не может и почему — печатается в отчёте отдельным
разделом, а не умалчивается.
"""

import argparse
import csv
import gzip
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PILOT = ROOT / "06-features" / "pilot-1-ids.csv"
DERIVED = ROOT / "04-corpus" / "derived" / "prep-v4"
CACHE = ROOT / "06-features" / "cache" / "stanza-v1"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
REPORT = ROOT / "06-features" / "pilot-1-report.md"

# Сокращения, на которых точка не должна рвать предложение.
ABBREVIATIONS = ["т. е.", "т.е.", "т. д.", "т.д.", "т. п.", "т.п.", "г.", "гг.", "руб.", "тыс.",
                 "млн", "млрд", "им.", "ул.", "стр.", "рис.", "табл.", "см.", "др.", "проф."]
INITIALS = re.compile(r"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.")


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_parsed(doc_id):
    path = CACHE / f"{doc_id}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def sentence_boundaries(text, sentences):
    """Позиции концов предложений по разбору Stanza — для сверки с Razdel.

    Курсор двигается по каждому токену, а не по последнему в предложении:
    последний токен обычно точка, и поиск ближайшей точки от курсора находит
    не тот знак. При первой же осечке такая метрика занижает согласие на всём
    оставшемся документе.
    """
    cursor, bounds = 0, []
    for sentence in sentences:
        for word in sentence:
            position = text.find(word["t"], cursor)
            if position < 0:
                continue
            cursor = position + len(word["t"])
        bounds.append(cursor)
    return bounds


def check_segmentation(documents):
    """Пункт 1: точность разбиения на предложения, сверка с Razdel."""
    import razdel

    rows = []
    for doc_id, text, parsed in documents:
        stanza_bounds = set(sentence_boundaries(text, parsed["sentences"]))
        razdel_bounds = {segment.stop for segment in razdel.sentenize(text)}
        agreement = len(stanza_bounds & razdel_bounds) / max(len(stanza_bounds | razdel_bounds), 1)

        broken_abbr = 0
        for sentence in parsed["sentences"]:
            head = " ".join(word["t"] for word in sentence[:2])
            if any(head.startswith(abbr.split()[-1].strip(".")) for abbr in ("д.", "п.", "е.")):
                broken_abbr += 1
        rows.append(
            {
                "document_id": doc_id,
                "stanza_sentences": len(parsed["sentences"]),
                "razdel_sentences": len(razdel_bounds),
                "agreement": agreement,
                "broken_on_abbreviation": broken_abbr,
                "initials": len(INITIALS.findall(text)),
            }
        )
    return rows


def boundary_examples(documents, worst_ids, limit=3):
    """Контексты, где парсеры разошлись. Без них цифра согласия не говорит,
    кто прав и что чинить — текст или пайплайн."""
    import razdel

    examples = []
    for doc_id, text, parsed in documents:
        if doc_id not in worst_ids:
            continue
        stanza_bounds = set(sentence_boundaries(text, parsed["sentences"]))
        razdel_bounds = {segment.stop for segment in razdel.sentenize(text)}
        for label, positions in (("Stanza", stanza_bounds - razdel_bounds), ("Razdel", razdel_bounds - stanza_bounds)):
            for position in sorted(positions)[:limit]:
                context = text[max(0, position - 45) : position + 25].replace("\n", "⏎")
                examples.append((doc_id, label, context))
    return examples


def check_lemmatization(documents):
    """Пункт 2: характер ошибок лемматизации."""
    unchanged, latin, empty, total = 0, 0, 0, 0
    suspicious = Counter()
    for _, _, parsed in documents:
        for sentence in parsed["sentences"]:
            for word in sentence:
                if word["p"] == "PUNCT":
                    continue
                total += 1
                lemma, token = word["l"], word["t"]
                if not lemma:
                    empty += 1
                    continue
                # Форма изменилась только регистром — для флективного языка
                # это норма у неизменяемых слов и подозрительно у остальных.
                if lemma == token and word["p"] in {"NOUN", "VERB", "ADJ"} and len(token) > 3 and token != token.lower():
                    unchanged += 1
                if re.search(r"[A-Za-z]", lemma) and re.search(r"[а-яё]", token, re.IGNORECASE):
                    latin += 1
                    suspicious[f"{token} → {lemma}"] += 1
    return {
        "tokens": total,
        "empty_lemma": empty,
        "unchanged_capitalized": unchanged,
        "latin_lemma_for_cyrillic_token": latin,
        "examples": suspicious.most_common(10),
    }


def check_trees(documents):
    """Пункт 3: доля сломанных деревьев зависимостей."""
    broken, sentences = 0, 0
    no_root = 0
    for _, _, parsed in documents:
        for sentence in parsed["sentences"]:
            sentences += 1
            heads = {word["i"] for word in sentence}
            roots = [word for word in sentence if word["d"] == "root"]
            if len(roots) != 1:
                no_root += 1
            for word in sentence:
                if word["h"] and word["h"] not in heads:
                    broken += 1
                    break
    return {"sentences": sentences, "broken": broken, "wrong_root_count": no_root}


def check_repeatability(documents, sample=5):
    """Пункт 8: побайтовая повторяемость разбора при повторном запуске."""
    import stanza

    nlp = stanza.Pipeline("ru", package="syntagrus", processors="tokenize,pos,lemma,depparse",
                          use_gpu=False, verbose=False)
    identical, checked, elapsed = 0, 0, 0.0
    for doc_id, text, parsed in documents[:sample]:
        start = time.time()
        again = nlp(text)
        elapsed += time.time() - start
        rebuilt = [
            [{"t": w.text, "l": w.lemma or w.text, "p": w.upos, "d": w.deprel or "", "h": w.head, "i": w.id,
              "f": w.feats or ""} for w in sentence.words]
            for sentence in again.sentences
        ]
        checked += 1
        if json.dumps(rebuilt, ensure_ascii=False) == json.dumps(parsed["sentences"], ensure_ascii=False):
            identical += 1
    return {"checked": checked, "identical": identical, "seconds_per_document": elapsed / max(checked, 1)}


def check_length_normalization(pilot_ids):
    """Пункт 10: связь признаков с длиной. Диагностика 4 §11.1 preregistration:
    у признака, нормированного правильно, корреляция с log(word_count) должна
    быть слабой."""
    import math

    if not MATRIX.exists():
        return None
    words = {}
    for row in read_rows(ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"):
        value = float(row["prose_words"] or 0)
        if value > 0:
            words[row["document_id"]] = math.log(value)

    # Только документы Pilot-1: диагностика идёт на development set. Считать
    # её на всём корпусе означало бы смотреть на данные, зарезервированные под
    # confirmatory-проверку.
    values = defaultdict(list)
    for row in read_rows(MATRIX):
        if row["missing_reason"] or row["document_id"] not in words:
            continue
        if row["document_id"] not in pilot_ids:
            continue
        value = row["normalized_value"] or row["raw_value"]
        if value:
            values[row["feature_id"]].append((words[row["document_id"]], float(value)))

    result = []
    for feature_id, pairs in sorted(values.items()):
        # Порог 20 документов: на меньшем числе корреляция не интерпретируется,
        # а набор Pilot-1 всего 82 документа.
        if len(pairs) < 20:
            continue
        left = [pair[0] for pair in pairs]
        right = [pair[1] for pair in pairs]
        if statistics.pstdev(right) == 0:
            continue
        mean_left, mean_right = statistics.mean(left), statistics.mean(right)
        cov = sum((a - mean_left) * (b - mean_right) for a, b in pairs) / len(pairs)
        correlation = cov / (statistics.pstdev(left) * statistics.pstdev(right))
        result.append((feature_id, correlation, len(pairs)))
    return sorted(result, key=lambda item: -abs(item[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    pilot = read_rows(PILOT)
    documents = []
    missing = []
    for row in pilot:
        doc_id = row["document_id"]
        parsed = load_parsed(doc_id)
        path = DERIVED / "prose" / f"{doc_id}.txt"
        if parsed is None or not path.exists():
            missing.append(doc_id)
            continue
        documents.append((doc_id, path.read_text(encoding="utf-8"), parsed))

    print(f"Pilot-1: документов в наборе {len(pilot)}, разобрано {len(documents)}")
    if missing:
        print(f"  ! нет разбора: {len(missing)} — сначала --stage parse")
        if not documents:
            return 1

    lines = []

    segmentation = check_segmentation(documents)
    agreements = [row["agreement"] for row in segmentation]
    worst = sorted(segmentation, key=lambda row: row["agreement"])[:5]
    print()
    print("1. Границы предложений: Stanza против Razdel")
    print(f"   согласие по границам: медиана {statistics.median(agreements):.3f}, "
          f"минимум {min(agreements):.3f}, ниже 0.90 — {sum(1 for a in agreements if a < 0.90)} документов")
    for row in worst:
        print(f"   хуже всего: {row['document_id']} — согласие {row['agreement']:.3f}, "
              f"Stanza {row['stanza_sentences']} предложений, Razdel {row['razdel_sentences']}")

    examples = boundary_examples(documents, {row["document_id"] for row in worst[:2]})
    for doc_id, label, context in examples[:6]:
        print(f"   граница только у {label} ({doc_id}): …{context}…")

    lemmas = check_lemmatization(documents)
    print()
    print("2. Лемматизация")
    print(f"   токенов: {lemmas['tokens']}, пустых лемм: {lemmas['empty_lemma']}, "
          f"латинская лемма у кириллического токена: {lemmas['latin_lemma_for_cyrillic_token']}")
    for example, count in lemmas["examples"][:5]:
        print(f"   пример: {example} ×{count}")

    trees = check_trees(documents)
    print()
    print("3. Деревья зависимостей")
    print(f"   предложений: {trees['sentences']}, с потерянным узлом: {trees['broken']}, "
          f"с числом корней не равным одному: {trees['wrong_root_count']}")

    print()
    print("6, 8. Скорость и повторяемость")
    repeat = check_repeatability(documents)
    print(f"   повторный разбор {repeat['checked']} документов: побайтово совпало {repeat['identical']}, "
          f"{repeat['seconds_per_document']:.2f} с на документ")

    correlations = check_length_normalization({row["document_id"] for row in pilot})
    print()
    print("10. Нормировка по длине: корреляция признака с log(word_count)")
    if correlations:
        for feature_id, correlation, count in correlations[:8]:
            mark = " — сильная связь, проверить нормировку" if abs(correlation) > 0.5 else ""
            print(f"   {feature_id}: r = {correlation:+.3f} на {count} документах{mark}")
    else:
        print("   матрица признаков не посчитана")

    print()
    print("Пункты 4 и 5 закрыты вне этого скрипта:")
    print("   4. Качество NER по жанрам — 06-features/ner-v3-pilot.md, разбор шума ner-noise-analysis.md")
    print("   5. Обрезка на входе в bge-m3 — 06-features/semantic-spec.md, потолок 512 токенов")
    print("Чего пилот не проверил на этой машине:")
    print("   7. Совпадение результата CPU и GPU — в системе только сборка torch для CPU")

    if args.write:
        today = datetime.now().date().isoformat()
        report = [
            "# Отчёт Pilot-1",
            "",
            f"Дата: {today}. Набор: `06-features/pilot-1-ids.csv`, {len(pilot)} документов, разобрано {len(documents)}.",
            "",
            "Проверка идёт по десяти пунктам `pilot-1-spec.md`. AUROC человек/машина не считается: "
            "инструмент не выбирается по тому, какой лучше разделяет классы.",
            "",
            "## 1. Границы предложений",
            "",
            f"Согласие Stanza и Razdel по позициям границ: медиана {statistics.median(agreements):.3f}, "
            f"минимум {min(agreements):.3f}. Ниже 0.90 — {sum(1 for a in agreements if a < 0.90)} документов из {len(segmentation)}.",
            "",
            "| Документ | Согласие | Stanza | Razdel |",
            "|---|---|---|---|",
        ]
        for row in worst:
            report.append(f"| `{row['document_id']}` | {row['agreement']:.3f} | {row['stanza_sentences']} | {row['razdel_sentences']} |")
        report += [
            "",
            "Где именно расходятся — контексты границ, найденных одним парсером и пропущенных другим:",
            "",
        ]
        for doc_id, label, context in examples[:6]:
            report.append(f"- граница только у **{label}** (`{doc_id}`): …{context}…")
        report += [
            "",
            "## 2. Лемматизация",
            "",
            f"Токенов: {lemmas['tokens']}. Пустых лемм: {lemmas['empty_lemma']}. "
            f"Латинская лемма у кириллического токена: {lemmas['latin_lemma_for_cyrillic_token']}.",
            "",
            "## 3. Деревья зависимостей",
            "",
            f"Предложений: {trees['sentences']}. С потерянным узлом: {trees['broken']}. "
            f"С числом корней не равным одному: {trees['wrong_root_count']}.",
            "",
            "## 6, 8. Скорость и повторяемость",
            "",
            f"Повторный разбор {repeat['checked']} документов: побайтово совпало {repeat['identical']}. "
            f"Скорость {repeat['seconds_per_document']:.2f} секунды на документ на CPU.",
            "",
            "## 10. Нормировка по длине",
            "",
            "Корреляция значения признака с `log(word_count)` профиля `prose`. "
            "Сильная связь означает, что признак несёт длину под другим именем.",
            "",
            "| Признак | r | Документов |",
            "|---|---|---|",
        ]
        for feature_id, correlation, count in (correlations or [])[:15]:
            report.append(f"| {feature_id} | {correlation:+.3f} | {count} |")
        report += [
            "",
            "## Пункты, закрытые вне этого скрипта",
            "",
            "- **Качество NER по жанрам** (пункт 4). Экстрактор `ner-v3` написан, C01 посчитан у всех "
            "документов. Разбор по жанрам — `06-features/ner-v3-pilot.md`, причина остаточного шума "
            "измерена в `ner-noise-analysis.md`.",
            "- **Обрезка на входе в bge-m3** (пункт 5). Семантический слой посчитан экстрактором `sem-v1`. "
            "Потолок задан 512 токенами вместо модельных 8192, решение и число обрезанных предложений — "
            "`06-features/semantic-spec.md`.",
            "",
            "## Чего пилот не проверил",
            "",
            "- **Совпадение CPU и GPU** (пункт 7). В системе стоит сборка torch только для CPU, сравнить не с чем.",
            "",
            "Пункт закрывается до заморозки признаков либо переносится в ограничения статьи с этой формулировкой.",
        ]
        REPORT.write_text("\n".join(report), encoding="utf-8")
        print(f"\nОтчёт записан: {REPORT.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
