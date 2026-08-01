#!/usr/bin/env python3
"""Коррекция дефекта извлечения текста: построение корректированных входов prep-v5.

    python 09-tools/correct_extraction.py --dry-run    # только классификация
    python 09-tools/correct_extraction.py              # записать корректированные тексты

Правила зарегистрированы до расчёта:
`02-preregistration/amendment-prep-v5-data-quality.md`.

Что делает скрипт:

1. **Классифицирует происхождение** каждого документа-кандидата. Кандидат — любой
   документ с долей повторяющихся предложений выше порога; флаг служит отбором на
   проверку, а не командой на правку.
2. **Правит только то, что имеет право править**: `extraction-defect` — повторной
   экстракцией из кэшированного HTML исправленным парсером; `intermediary-defect`
   — детерминированным удалением точных каскадов; `source-property` и
   `unresolved` не трогает.
3. **Применяет QA ко всем документам корпуса**, независимо от класса и источника.
4. **Пишет журнал** `04-corpus/prep-v5-corrections.csv` со всеми полями §5
   амендмента.

Исходные файлы не перезаписываются: корректированные тексты идут в
`04-corpus/derived/raw-v5/`, `raw-human` и `prep-v4` остаются как есть.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_html as ex  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
RAW = ROOT / "04-corpus" / "raw-human"
BLOG_CACHE = ROOT / "04-corpus" / "_archives" / "blog_cache"
WAYBACK_CACHE = ROOT / "04-corpus" / "_archives" / "wayback_cache"
OUT_ROOT = ROOT / "04-corpus" / "derived" / "raw-v5"
OUT_LOG = ROOT / "04-corpus" / "prep-v5-corrections.csv"
OUT_REPORT = ROOT / "07-analysis" / "prep-v5-correction-report.md"

RULE_VERSION = "correction-v5.0"
THRESHOLD = 0.10          # диагностический флаг отбора кандидатов
MIN_SENT_CHARS = 40
MIN_WORDS = 700           # §6 амендмента: порог сбора, ниже — исключение
SENT = re.compile(r"(?<=[.!?])\s+")

# Источники, для которых сверка с публикацией выполнена 2026-07-29 и показала,
# что повтор внесён датасетом-посредником, а не изданием.
INTERMEDIARY_SOURCES = {
    "lenta": "сверка с lenta.ru 2026-07-29: три заметки из трёх повторов не содержат; "
             "повтор внесён датасетом corus lenta-ru-news",
}

LOG_FIELDS = ["document_id", "source", "verdict", "rule_version",
              "sha256_before", "sha256_after", "words_before", "words_after",
              "chars_removed", "sentences_removed", "blocks_removed",
              "origin_evidence", "origin_checked_against", "below_min_words",
              "changed_at"]


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sentences(text):
    return [s.strip() for s in SENT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]


def repeat_share(text):
    parts = sentences(text)
    if not parts:
        return 0.0
    counts = defaultdict(int)
    for s in parts:
        counts[s] += 1
    return sum(n for n in counts.values() if n > 1) / len(parts)


def cached_html(meta):
    """HTML из кэша: blog_cache по sha1(url), wayback_cache по sha1(timestamp+url)."""
    url = meta.get("url")
    if url:
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".html"
        hits = list(BLOG_CACHE.glob(f"*/{name}"))
        if hits:
            return hits[0]
    snapshot = meta.get("snapshot")
    if snapshot:
        m = re.search(r"/web/(\d{14})id_/(.+)$", snapshot)
        if m:
            timestamp, target = m.group(1), m.group(2)
            name = hashlib.sha1(f"{timestamp}{target}".encode("utf-8")).hexdigest()[:20] + ".html"
            hits = list(WAYBACK_CACHE.glob(f"*/{name}"))
            if hits:
                return hits[0]
    return None


def drop_exact_cascades(text):
    """Детерминированное удаление точных каскадов (§4.2 амендмента).

    Единица — связный блок между пустыми строками. Сохраняется первое вхождение,
    последующие точные копии удаляются. Неидентичные блоки не трогаются, порядок
    оставшихся сохраняется. Правило не смотрит на длину, класс и оценку документа.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text)]
    seen, kept, removed = set(), [], 0
    for block in blocks:
        if not block:
            continue
        key = re.sub(r"\s+", " ", block)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(block)
    joined = "\n\n".join(kept)
    if removed:
        return joined, removed
    # Абзацного членения нет: каскад лежит одной строкой — режем по предложениям.
    parts = SENT.split(text)
    seen, kept, removed = set(), [], 0
    for part in parts:
        key = re.sub(r"\s+", " ", part.strip())
        if len(key) >= MIN_SENT_CHARS and key in seen:
            removed += 1
            continue
        if len(key) >= MIN_SENT_CHARS:
            seen.add(key)
        kept.append(part.strip())
    return " ".join(p for p in kept if p), removed


def classify(doc_id, source, raw_text, meta):
    """Вердикт происхождения по §3 амендмента. Возвращает (вердикт, основание, чем проверено)."""
    if source in INTERMEDIARY_SOURCES:
        return "intermediary-defect", INTERMEDIARY_SOURCES[source], "публикация источника"

    html_path = cached_html(meta)
    if html_path is None:
        return ("unresolved", "кэшированный HTML не найден, оригинал недоступен",
                "нет")

    html = html_path.read_text(encoding="utf-8", errors="replace")
    current = ex.extract_text(BeautifulSoup(html, "lxml"))
    if repeat_share(current) > THRESHOLD:
        return ("extraction-defect",
                f"экстрактор воспроизводит повтор из HTML: доля "
                f"{repeat_share(current):.3f}", html_path.name)
    return ("source-property",
            "HTML повтора не даёт — повтор пришёл из самого текста источника",
            html_path.name)


def corrected_text(verdict, raw_text, meta):
    """Правка по §4. Возвращает (новый текст, число удалённых блоков) или (None, 0)."""
    if verdict == "extraction-defect":
        html_path = cached_html(meta)
        html = html_path.read_text(encoding="utf-8", errors="replace")
        fixed = ex.extract_text(BeautifulSoup(html, "lxml"), leaf_only=True)
        return fixed, 0
    if verdict == "intermediary-defect":
        return drop_exact_cascades(raw_text)
    return None, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="только классифицировать, ничего не записывать")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"коррекция извлечения, {RULE_VERSION}, {stamp}")

    with DOCUMENTS.open(encoding="utf-8-sig", newline="") as fh:
        registry = list(csv.DictReader(fh))

    scanned = corrected = 0
    log_rows, verdicts = [], defaultdict(int)

    for row in registry:
        doc_id = row["document_id"]
        path = ROOT / row["file_path"]
        if not path.exists():
            continue
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        share = repeat_share(raw_text)
        if share <= THRESHOLD:
            continue

        source = row["source_platform"] or row["generation_channel"] or "unknown"
        meta_path = path.with_suffix(".json")
        meta = (json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists() else {})

        verdict, evidence, checked = classify(doc_id, source, raw_text, meta)
        verdicts[verdict] += 1
        new_text, blocks_removed = corrected_text(verdict, raw_text, meta)

        entry = {
            "document_id": doc_id, "source": source, "verdict": verdict,
            "rule_version": RULE_VERSION,
            "sha256_before": sha256_text(raw_text), "sha256_after": "",
            "words_before": len(raw_text.split()), "words_after": "",
            "chars_removed": "", "sentences_removed": "",
            "blocks_removed": blocks_removed,
            "origin_evidence": evidence, "origin_checked_against": checked,
            "below_min_words": "", "changed_at": stamp,
        }

        if new_text is not None and new_text.strip():
            entry.update({
                "sha256_after": sha256_text(new_text),
                "words_after": len(new_text.split()),
                "chars_removed": len(raw_text) - len(new_text),
                "sentences_removed": len(sentences(raw_text)) - len(sentences(new_text)),
                "below_min_words": int(len(new_text.split()) < MIN_WORDS),
            })
            if not args.dry_run:
                out_path = OUT_ROOT / source / f"{doc_id}.txt"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(new_text, encoding="utf-8")
            corrected += 1

        log_rows.append(entry)

    print(f"  просмотрено документов: {scanned}")
    print(f"  кандидатов с повтором выше {THRESHOLD}: {len(log_rows)}")
    for verdict, n in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        print(f"    {verdict}: {n}")
    print(f"  скорректировано: {corrected}")

    below = [r for r in log_rows if r["below_min_words"] == 1]
    print(f"  после коррекции ниже {MIN_WORDS} слов: {len(below)}")

    if not args.dry_run:
        with OUT_LOG.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
            writer.writeheader()
            writer.writerows(log_rows)
        write_report(log_rows, verdicts, scanned, corrected, below, stamp)
        print(f"  журнал: {OUT_LOG.name}, отчёт: {OUT_REPORT.name}")


def write_report(log_rows, verdicts, scanned, corrected, below, stamp):
    import statistics as st
    by_source = defaultdict(list)
    for r in log_rows:
        by_source[r["source"]].append(r)

    lines = [
        "# Коррекция извлечения: что и почему изменено",
        "",
        f"Собрано {stamp} скриптом `09-tools/correct_extraction.py`, правило "
        f"`{RULE_VERSION}`.",
        "",
        "Правила зарегистрированы до расчёта — "
        "`02-preregistration/amendment-prep-v5-data-quality.md`. Исходные файлы не "
        "перезаписаны: корректированные тексты лежат в `04-corpus/derived/raw-v5/`.",
        "",
        f"Просмотрено документов корпуса: {scanned}. Кандидатов с долей повторов "
        f"выше {THRESHOLD}: {len(log_rows)}. Скорректировано: {corrected}.",
        "",
        "## Вердикты происхождения",
        "",
        "| Вердикт | Документов | Что сделано |",
        "|---|---|---|",
    ]
    actions = {
        "extraction-defect": "повторная экстракция исправленным парсером",
        "intermediary-defect": "детерминированное удаление точных каскадов",
        "source-property": "**не редактируется**: повтор есть в самом источнике",
        "unresolved": "**не редактируется**: происхождение не установлено",
    }
    for verdict, n in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{verdict}` | {n} | {actions.get(verdict, '—')} |")
    lines += ["", "## По источникам", "",
              "| Источник | Кандидатов | Вердикты | Медиана удалённых слов |",
              "|---|---|---|---|"]
    for source in sorted(by_source):
        items = by_source[source]
        vs = defaultdict(int)
        for r in items:
            vs[r["verdict"]] += 1
        removed = [r["words_before"] - r["words_after"] for r in items
                   if r["words_after"] != ""]
        lines.append(
            f"| {source} | {len(items)} | "
            + ", ".join(f"{k} {v}" for k, v in sorted(vs.items())) + " | "
            + (f"{st.median(removed):.0f}" if removed else "—") + " |")
    lines += ["", "## Документы ниже порога после коррекции", ""]
    if below:
        lines += [f"Порог сбора — {MIN_WORDS} слов. Ниже него оказались "
                  f"{len(below)} документов; по §6 амендмента они подлежат "
                  "исключению, решение фиксируется отдельно.", "",
                  "| Документ | Источник | Слов до | Слов после |", "|---|---|---|---|"]
        for r in sorted(below, key=lambda x: x["words_after"]):
            lines.append(f"| `{r['document_id']}` | {r['source']} | "
                         f"{r['words_before']} | {r['words_after']} |")
    else:
        lines.append(f"Ни один документ не опустился ниже {MIN_WORDS} слов.")
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
