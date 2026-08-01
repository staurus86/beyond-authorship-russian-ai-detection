#!/usr/bin/env python3
"""Артефактный слой: F04, F05, F06, F07, F08.

Процедура зафиксирована в `06-features/artifacts-spec.md` до первого прогона.
Здесь только её реализация — расхождение между спецификацией и кодом считается
ошибкой кода.

Запуск из корня папки исследования:
    python 09-tools/extract_artifacts.py                 # весь корпус
    python 09-tools/extract_artifacts.py --limit 20      # проба, матрица не трогается
    python 09-tools/extract_artifacts.py --hits          # выборка срабатываний на проверку

Материал — сырой файл из реестра, а не производная версия `prep-v1`: препроцессинг
снимает обёртку канала, то есть ровно то, что измеряет F06.
"""

import argparse
import csv
import gzip
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import retest_io

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_cache as fc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
BRIEFS = ROOT / "03-briefs" / "briefs.json"
STANZA_CACHE = ROOT / "06-features" / "cache" / "stanza-v1"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
SCHEMA = ROOT / "06-features" / "feature-matrix-schema.csv"
EXTRACTOR_VERSION = "art-v2"
# prep-v5, 2026-07-29: версия препроцессинга задаётся параметром, значение по
# умолчанию не меняется.
PREP_VERSION = "prep-v4"
OWNED = ("F04", "F05", "F06", "F07", "F08")

HITS_REPORT = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.csv"
HITS_SAMPLE = ROOT / "06-features" / f"{EXTRACTOR_VERSION}-hits.md"

# §2 спецификации: перечни F05 и F06 взяты из `09-tools/scan_leaks.py`,
# зафиксированного 2026-07-24. Новые шаблоны после первого прогона не
# добавляются — перечень заморожен спецификацией.
SHELL = [
    r"<system-reminder>", r"<user_instructions>", r"CLAUDE\.md", r"AGENTS\.md",
    r"You are Claude Code", r"OpenAI Codex v", r"^workdir:", r"^sandbox:",
    r"^approval:", r"^reasoning effort:", r"disable-slash-commands",
    r"^session id:", r"<function_calls>", r"antml:",
    r"релевантн\w+ навык\w*[^.\n]{0,40}нет",
    r"подходящ\w+ навык\w*[^.\n]{0,40}(?:нет|не наш)",
    r"пишу текст напрямую", r"инструменты (?:запрещены|недоступны|не нужны)",
    r"не могу использовать инструмент",
]
SELFREF = [
    r"как языковая модель", r"\bAs an AI\b", r"я — (?:ИИ|нейросеть|языковая модель)",
    r"я являюсь (?:ИИ|нейросетью|языковой моделью)",
]
# Приветственные формулы якорятся на начало документа, а не строки: правка
# `art-v2` по итогам ручной проверки. С якорем `^` и флагом re.M шаблон ловил
# вводное «Конечно,» в середине абзаца и дал 32 ложных срабатывания на
# человеческих текстах при нуле настоящих.
META = [
    r"\A\s*(?:Конечно|Разумеется|Отлично)[!,]",
    r"\A\s*Вот (?:текст|статья|материал|готовый)",
    r"\A\s*(?:Sure|Certainly|Here'?s)\b",
    r"Надеюсь,? (это|материал|текст) (поможет|будет полезен)",
    r"Если (нужно|нужны|потребуется).{0,40}(правк|доработ|измен|сократ)",
    r"Дайте знать, если",
    r"\A(?:Вот|Ниже|Перед вами|Готово|Конечно|Разумеется|Отлично)\b[^\n]{0,300}\n+\s*(?:-{3,}|\*{3,}|_{3,})\s*\n",
]
# §2: перечни F04 и F07 составлены в спецификации из типовых форм и на корпусе
# до фиксации не проверялись.
PLACEHOLDER = [
    r"\[(?:вставьте|вставить|укажите|указать|добавьте|добавить|заполните|заполнить)[^\]]{0,60}\]",
    r"\[(?:ваш[аеиу]?|название|имя|город|компан\w+|дата|ссылка|телефон)[^\]]{0,60}\]",
    r"\{\{[^}]{1,60}\}\}",
    # `XXX` снят в `art-v2`: в текстах о настройке серверов он ловит маску
    # адреса `xxx.xxx.xxx.xxx` — 33 ложных срабатывания в шести документах,
    # настоящих ноль.
    r"\b(?:TODO|FIXME)\b",
    r"Lorem ipsum",
    r"\((?:указать|уточнить|вставить|дополнить)\)",
]
SELF_LENGTH = [
    r"Объ[её]м:\s*\d{3,4}(?:\s*[–—-]\s*\d{3,4})?\s*слов",
    r"Количество слов:\s*\d{3,4}",
    r"\(\s*(?:примерно\s*|около\s*|~\s*)?\d{3,4}\s*слов\w*\s*\)",
    r"(?:примерно|около)\s+\d{3,4}\s+слов",
]

GROUPS = {
    "F04": ("Плейсхолдеры и незаполненные поля", PLACEHOLDER, "на 1000 слов"),
    "F05": ("Остатки инструкций и служебной разметки", SHELL + SELFREF, "на 1000 слов"),
    "F06": ("Обращение к пользователю от лица ассистента", META, "бинарно"),
    "F07": ("Самоотчёт об объёме", SELF_LENGTH, "на 1000 слов"),
}
COMPILED = {
    key: [re.compile(pattern, re.I | re.M) for pattern in patterns]
    for key, (_, patterns, _) in GROUPS.items()
}

PUNCT_POS = "PUNCT"
FUNCTION_POS = {"ADP", "AUX", "CCONJ", "SCONJ", "DET", "PART", "PRON", "PUNCT", "NUM", "SYM", "X"}
MIN_WORD_LEN = 4


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def scan(text, feature_id):
    """Совпадения одного признака: список (шаблон, фрагмент с контекстом)."""
    found = []
    for regex in COMPILED[feature_id]:
        for match in regex.finditer(text):
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 60)
            fragment = re.sub(r"\s+", " ", text[start:end]).strip()
            found.append((regex.pattern, fragment))
    return found


def brief_texts():
    """Текст задания по brief_id: тема, аудитория, задача и обязательные пункты."""
    payload = json.loads(BRIEFS.read_text(encoding="utf-8"))
    texts = {}
    for brief in payload["briefs"]:
        parts = [brief.get("topic", ""), brief.get("audience", ""), brief.get("task", "")]
        parts.extend(brief.get("must_cover", []))
        texts[brief["id"]] = ". ".join(part for part in parts if part)
    return texts


def content_bigrams(lemmas):
    """Биграммы из подряд идущих содержательных лемм."""
    return {
        (lemmas[index], lemmas[index + 1])
        for index in range(len(lemmas) - 1)
    }


def document_lemmas(doc_id):
    """Содержательные леммы документа из кэша Stanza."""
    path = STANZA_CACHE / f"{doc_id}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        parsed = json.load(fh)
    lemmas = []
    for sentence in parsed["sentences"]:
        for token in sentence:
            if token["p"] in FUNCTION_POS:
                continue
            lemma = (token.get("l") or token["t"]).lower()
            if len(lemma) >= MIN_WORD_LEN:
                lemmas.append(lemma)
    return lemmas


def brief_lemmas(texts):
    """Разбор заданий тем же пайплайном, что и документы: один лемматизатор на работу."""
    import stanza

    pipeline = stanza.Pipeline(
        lang="ru", processors="tokenize,pos,lemma", download_method=None, verbose=False
    )
    result = {}
    for brief_id, text in texts.items():
        doc = pipeline(text)
        lemmas = [
            word.lemma.lower()
            for sentence in doc.sentences
            for word in sentence.words
            if word.upos not in FUNCTION_POS and word.lemma and len(word.lemma) >= MIN_WORD_LEN
        ]
        result[brief_id] = content_bigrams(lemmas)
    return result


def make_record(doc_id, feature_id, name, raw, normalized, unit, computed_at, reason=""):
    return {
        "document_id": doc_id,
        "feature_id": feature_id,
        "feature_name": "" if reason else name,
        "extractor_version": EXTRACTOR_VERSION,
        "preprocessing_profile": "raw",
        "raw_value": "" if raw is None else f"{raw:.6g}",
        "normalized_value": "" if normalized is None else f"{normalized:.6g}",
        "unit": "" if reason else unit,
        "genre_percentile": "",
        "missing_reason": reason,
        "computed_at": computed_at,
    }


def merge_into_matrix(records, registry):
    with SCHEMA.open(encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh))

    kept, dropped_stale = [], 0
    with MATRIX.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["document_id"] not in registry:
                dropped_stale += 1
                continue
            if row["feature_id"] in OWNED:
                continue
            kept.append(row)

    merged = kept + records

    if fc.percentiles_inline(PREP_VERSION):
        pools = {}
        for record in merged:
            if record["normalized_value"] == "" and record["raw_value"] == "":
                continue
            genre = registry[record["document_id"]]["genre"]
            value = float(record["normalized_value"] or record["raw_value"])
            pools.setdefault((record["feature_id"], genre), []).append(value)
        for key in pools:
            pools[key].sort()
        for record in merged:
            if record["normalized_value"] == "" and record["raw_value"] == "":
                continue
            genre = registry[record["document_id"]]["genre"]
            pool = pools[(record["feature_id"], genre)]
            value = float(record["normalized_value"] or record["raw_value"])
            rank = sum(1 for item in pool if item < value)
            record["genre_percentile"] = f"{rank / len(pool):.4f}" if pool else ""

    backup = MATRIX.with_suffix(".csv.bak-before-art-v1")  # первая версия слоя
    if not backup.exists():
        backup.write_bytes(MATRIX.read_bytes())
    with MATRIX.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(merged)
    print(f"матрица переписана: {MATRIX.relative_to(ROOT)}, строк {len(merged)}")
    if dropped_stale:
        print(f"  снято строк документов вне реестра: {dropped_stale}")


def write_hits(hits):
    with HITS_REPORT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["document_id", "origin_class", "genre", "feature_id", "pattern", "fragment"]
        )
        writer.writeheader()
        writer.writerows(hits)
    print(f"диагностика срабатываний: {HITS_REPORT.relative_to(ROOT)}, строк {len(hits)}")


def sample_hits(limit_per_feature=20):
    """Выборка на ручную проверку — §5 спецификации."""
    rows = read_rows(HITS_REPORT)
    lines = [
        "# Срабатывания артефактного слоя — выборка на ручную проверку",
        "",
        f"Экстрактор `art-v1`, выборка от {datetime.now(timezone.utc).date()}. "
        f"По {limit_per_feature} первых совпадений на признак.",
        "",
        "Вердикт ставится вручную: `ок` — артефакт действительно есть; "
        "`ложь` — шаблон сработал на обычном тексте.",
        "",
    ]
    for feature_id in ("F04", "F05", "F06", "F07"):
        picked = [row for row in rows if row["feature_id"] == feature_id][:limit_per_feature]
        lines.append(f"## {feature_id} — совпадений всего {sum(1 for r in rows if r['feature_id'] == feature_id)}")
        lines.append("")
        if not picked:
            lines.extend(["Совпадений нет.", ""])
            continue
        lines.append("| № | документ | класс | шаблон | фрагмент | вердикт |")
        lines.append("|---|---|---|---|---|---|")
        for number, row in enumerate(picked, 1):
            fragment = row["fragment"].replace("|", "¦")[:200]
            pattern = row["pattern"].replace("|", "¦")[:60]
            lines.append(
                f"| {number} | `{row['document_id']}` | {row['origin_class']} | `{pattern}` | {fragment} |  |"
            )
        lines.append("")
    HITS_SAMPLE.write_text("\n".join(lines), encoding="utf-8")
    print(f"выборка на проверку: {HITS_SAMPLE.relative_to(ROOT)}")
    return 0


def main():
    global PREP_VERSION, MATRIX
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only")
    parser.add_argument("--hits", action="store_true", help="собрать выборку срабатываний на проверку")
    parser.add_argument("--ids-file", help="считать только документы из файла, по идентификатору на строку")
    parser.add_argument("--out", help="записать результат в CSV вместо слияния в матрицу")
    parser.add_argument("--prep-version", default=PREP_VERSION,
                        help="версия препроцессинга на входе")
    args = parser.parse_args()

    PREP_VERSION = args.prep_version
    MATRIX = fc.matrix_path(PREP_VERSION, MATRIX)

    if args.hits:
        return sample_hits()

    rows = read_rows(DOCUMENTS)
    registry = {row["document_id"]: row for row in rows}
    if args.ids_file:
        wanted = set(retest_io.read_ids(args.ids_file))
        rows = [row for row in rows if row["document_id"] in wanted]
    if args.only:
        rows = [row for row in rows if row["document_id"] == args.only]
    if args.limit:
        rows = rows[: args.limit]

    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    briefs = brief_lemmas(brief_texts())
    print(f"заданий разобрано: {len(briefs)}")

    records, hits = [], []
    counters = {key: 0 for key in GROUPS}
    docs_with = {key: 0 for key in GROUPS}
    no_brief = no_cache = 0
    f08_values = []

    for row in rows:
        doc_id = row["document_id"]
        # Слой читает исходник, а не профиль: у prep-v5 это скорректированный
        # текст, если он есть, — иначе дефект извлечения попал бы в F04–F08.
        text = fc.source_file(row, PREP_VERSION).read_text(encoding="utf-8-sig",
                                                          errors="replace")
        words = float(row["word_count"] or 0) or max(1, len(text.split()))

        for feature_id, (name, _, unit) in GROUPS.items():
            found = scan(text, feature_id)
            counters[feature_id] += len(found)
            if found:
                docs_with[feature_id] += 1
            for pattern, fragment in found:
                hits.append({
                    "document_id": doc_id, "origin_class": row["origin_class"],
                    "genre": row["genre"], "feature_id": feature_id,
                    "pattern": pattern, "fragment": fragment,
                })
            if feature_id == "F06":
                normalized = 1 if found else 0
            else:
                normalized = len(found) / words * 1000
            records.append(
                make_record(doc_id, feature_id, name, len(found), normalized, unit, computed_at)
            )

        # F08 — только машинная часть: у человеческих документов задания нет.
        if row["origin_class"] != "A" or not row["brief_id"]:
            records.append(make_record(
                doc_id, "F08", "", None, None, "", computed_at,
                "задание не задано: человеческая часть собрана из архивов",
            ))
            if row["origin_class"] == "A":
                no_brief += 1
            continue
        brief = briefs.get(row["brief_id"])
        lemmas = document_lemmas(doc_id)
        if brief is None or lemmas is None:
            no_cache += 1
            records.append(make_record(
                doc_id, "F08", "", None, None, "", computed_at, "нет разбора документа или задания",
            ))
            continue
        overlap = len(brief & content_bigrams(lemmas))
        share = overlap / len(brief) if brief else 0.0
        f08_values.append(share)
        records.append(make_record(
            doc_id, "F08", "Эхо задания", overlap, share, "доля биграмм задания", computed_at
        ))

    if args.out:
        written = retest_io.write_records(args.out, records)
        print(f"повторный прогон, матрица не изменена: {args.out}, строк {written}")
    elif args.limit or args.only:
        print(f"проба на {len(rows)} документах, матрица не изменена")
    else:
        merge_into_matrix(records, registry)
        write_hits(hits)

    print(f"артефактный слой посчитан: строк {len(records)}")
    for feature_id in GROUPS:
        print(f"  {feature_id}: срабатываний {counters[feature_id]}, документов {docs_with[feature_id]}")
    if f08_values:
        ordered = sorted(f08_values)
        print(
            f"  F08: документов {len(ordered)}, доля биграмм задания "
            f"мин {ordered[0]:.4f}, медиана {ordered[len(ordered) // 2]:.4f}, макс {ordered[-1]:.4f}"
        )
    if no_brief:
        print(f"  ! машинных документов без brief_id: {no_brief}")
    if no_cache:
        print(f"  ! нет разбора: {no_cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
