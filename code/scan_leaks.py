#!/usr/bin/env python3
"""Проверка машинных текстов на утечку оболочки и служебного слоя.

Поле `system_prompt_leak` в журналах трёх каналов из четырёх проставлялось
константой `no`: проверки не было, было утверждение. Реальную проверку делает
этот скрипт — по всему машинному корпусу разом, после прогона.

Ищутся четыре класса загрязнения:

1. **shell** — следы оболочки CLI: разметка Claude Code, баннер Codex,
   имена служебных файлов. Прямая утечка системного слоя.
2. **meta** — обращение к оператору вместо текста: «Конечно!», «Вот статья»,
   «Надеюсь, это поможет». Модель отвечает собеседнику, а не пишет документ.
3. **echo** — пересказ задания внутри ответа: «Объём: 1200–1800 слов»,
   «Обязательно раскрой». Промпт протёк в текст.
4. **selfref** — модель называет себя моделью: «как языковая модель», «As an AI».

Находки не удаляются автоматически: решение принимает PI, скрипт только
показывает документ, класс и совпавший фрагмент с контекстом.

    python 09-tools/scan_leaks.py
    python 09-tools/scan_leaks.py --src 04-corpus/raw-ai/gpt
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_AI = ROOT / "04-corpus" / "raw-ai"
REPORT = ROOT / "04-corpus" / "leak-scan-report.csv"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PATTERNS = {
    "shell": [
        r"<system-reminder>", r"<user_instructions>", r"CLAUDE\.md", r"AGENTS\.md",
        r"You are Claude Code", r"OpenAI Codex v", r"^workdir:", r"^sandbox:",
        r"^approval:", r"^reasoning effort:", r"disable-slash-commands",
        r"^session id:", r"<function_calls>", r"antml:",
        # найдено сканом 2026-07-24: модель рассуждает про свой инструментарий
        # прежде чем писать. Первый набор шаблонов этот класс не ловил.
        # Формулировки узкие намеренно: слова «навык» и «скилл» сами по себе —
        # обычная деловая лексика и дают ложные срабатывания внутри статей
        r"релевантн\w+ навык\w*[^.\n]{0,40}нет",
        r"подходящ\w+ навык\w*[^.\n]{0,40}(?:нет|не наш)",
        r"пишу текст напрямую", r"инструменты (?:запрещены|недоступны|не нужны)",
        r"не могу использовать инструмент",
    ],
    "meta": [
        r"^(Конечно|Разумеется|Отлично)[!,]", r"^Вот (текст|статья|материал|готовый)",
        r"Надеюсь,? (это|материал|текст) (поможет|будет полезен)",
        r"Если (нужно|нужны|потребуется).{0,40}(правк|доработ|измен|сократ)",
        r"^(Sure|Certainly|Here'?s)\b", r"Дайте знать, если",
        # обёртка канала: короткое обращение к заказчику, затем горизонтальная
        # линейка, затем сам текст. Самый массовый класс — около сотни документов
        r"\A(?:Вот|Ниже|Перед вами|Готово|Конечно|Разумеется|Отлично)\b[^\n]{0,300}\n+\s*(?:-{3,}|\*{3,}|_{3,})\s*\n",
    ],
    "echo": [
        r"Объ[её]м:\s*\d{3,4}\s*[–-]\s*\d{3,4}\s*слов", r"^Обязательно раскрой",
        r"^Аудитория:\s", r"^Задача текста:\s", r"^Тема:\s",
        r"Количество слов:\s*\d", r"\(\s*\d{3,4}\s*слов\s*\)",
    ],
    "selfref": [
        r"как языковая модель", r"\bAs an AI\b", r"я — (?:ИИ|нейросеть|языковая модель)",
        r"я не могу (?:выполнить|помочь|написать)", r"\bI cannot\b", r"I can'?t help",
    ],
}

COMPILED = {
    name: [re.compile(p, re.I | re.M) for p in pats] for name, pats in PATTERNS.items()
}


def scan_text(text):
    hits = []
    for name, regexes in COMPILED.items():
        for rx in regexes:
            for m in rx.finditer(text):
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                fragment = re.sub(r"\s+", " ", text[start:end]).strip()
                hits.append((name, rx.pattern, fragment))
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", help="одна папка канала, по умолчанию все")
    args = parser.parse_args()

    if args.src:
        folders = [ROOT / args.src]
    else:
        folders = [p for p in sorted(RAW_AI.iterdir()) if p.is_dir() and not p.name.startswith("_")]

    rows = []
    scanned = 0
    for folder in folders:
        for path in sorted(folder.glob("*.txt")):
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for kind, pattern, fragment in scan_text(text):
                rows.append({
                    "document_id": path.stem,
                    "channel": folder.name,
                    "kind": kind,
                    "pattern": pattern,
                    "fragment": fragment[:200],
                })

    with REPORT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["document_id", "channel", "kind", "pattern", "fragment"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Просмотрено документов: {scanned}")
    print(f"Находок: {len(rows)}, документов с находками: {len({r['document_id'] for r in rows})}")
    print(f"Отчёт: {REPORT.relative_to(ROOT)}\n")

    if not rows:
        print("Чисто: утечек оболочки, метаобращений, пересказа задания и самоописаний не найдено.")
        return

    print("По классам:")
    for kind, n in Counter(r["kind"] for r in rows).most_common():
        docs = len({r["document_id"] for r in rows if r["kind"] == kind})
        print(f"  {kind}: {n} совпадений в {docs} документах")

    print("\nПо каналам:")
    for ch, n in Counter(r["channel"] for r in rows).most_common():
        docs = len({r["document_id"] for r in rows if r["channel"] == ch})
        print(f"  {ch}: {n} совпадений в {docs} документах")

    print("\nПримеры:")
    for r in rows[:12]:
        print(f"  [{r['kind']}] {r['document_id']}: …{r['fragment'][:110]}…")


if __name__ == "__main__":
    main()
