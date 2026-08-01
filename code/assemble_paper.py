#!/usr/bin/env python3
"""Сборка рукописи из разделов по манифесту.

    python 09-tools/assemble_paper.py

Манифест — `08-paper/paper-assembly-manifest.md`, составлен до объединения файлов.
Назначение сборки — не дать архивным версиям вернуться в текст.

**Хеши сверяются до сборки.** Расхождение хотя бы одного источника останавливает
работу: подмена файла архивной редакцией — ровно тот риск, ради которого манифест
и заведён.

Исключения §2 манифеста применяются по границам разделов, а не по номерам строк:
номера сдвигаются при любой правке, заголовки — нет.
"""

import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "08-paper"
OUT = PAPER / "manuscript.md"
OUT_REPORT = PAPER / "manuscript-assembly-report.md"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# Источники и их хеши на момент составления манифеста.
SOURCES = {
    "introduction.md": "2955442be999b1c266aea8eabd3a7248e93c0670f7723bc81e9823f3c0ec2dca",
    "research-gap.md": "5688ab9b3b716391194ae6af601f17b307ae8b6d33c8592b3ab590c34bc9b2cc",
    "related-work.md": "101a8b005b67110521205e917983b355e5e87c84c70a3952d3060fb3eff7d499",
    "methods.md": "02f1a072f10975be5f803ae96e9efe5089d7c1bde8fa861edc8b172b03184725",
    "results.md": "4d1e8d4a84e9cb066d0ea868a98422d5d8295afaf8828044ec9f6fcc511c2a44",
    "discussion.md": "bf1b4766026292feaebb1e52eadf9746cd52357b56fde434be4a48d781cf2a11",
    "main-claim-and-limitations.md": "d3940702bfccb3bfe768de751d7d687a8091bc0eee7470c7d5b9ab1681624672",
    "declarations.md": "85321382b7e7bc8069b4a4bbcfd211b1a79a2660c7f8402118c37984e36aba90",
    "appendix-reproducibility.md": "d2fae90ac291ca6429c8d9ad3fbb6b1e27ff68d68e0d38ef59c1747ba35348b4",
    "figures-list.md": "3df6b1319480ea4c98f5fc9f4a07f45367416d750a242e846e067041e8525a69",
    "supplementary.md": "77e8564eac18b1a83c877467c931f7f7a86a4e648fbe7c9c08df35cf63c31c7f",
}

# Порядок разделов рукописи: (заголовок, файл, что взять)
LAYOUT = [
    ("Introduction", "introduction.md", "all"),
    ("Research gap", "research-gap.md", "all"),
    ("Related work", "related-work.md", "all"),
    ("Methods", "methods.md", "all"),
    ("Results", "results.md", "all"),
    ("Discussion", "discussion.md", "all"),
    ("Limitations", "main-claim-and-limitations.md", "limitations"),
    ("Declarations", "declarations.md", "declarations"),
    ("Appendix A. Reproducibility and data quality", "appendix-reproducibility.md", "appendix"),
    ("Appendix B. Figures", "figures-list.md", "all"),
    ("Supplementary Material", "supplementary.md", "all"),
]

# Заголовки, которые вырезаются вместе со своим содержимым, §2 манифеста.
DROP_SECTIONS = {
    "main-claim-and-limitations.md": [
        "### Третий результат: цену ошибки определяет площадка, а не жанр",
    ],
    "appendix-reproducibility.md": [
        "## 8. Нормирование форматных признаков на объём — invalidated diagnostic run",
    ],
    "declarations.md": [
        "## Что нужно от PI, чтобы закрыть блок",
    ],
}

# Врезки рабочих документов: абзац, начинающийся с этих маркеров, в рукопись не идёт.
CALLOUT_MARKERS = ("> **Замещено", "> **Поправка", "> **Уточнение", "> **Архивный статус",
                   "> **Указатель")

FORBIDDEN = [
    (r"\b0\.538\b", "ICC серии v1 в основном тексте"),
    (r"\b5104\b", "старое число строк P2"),
    (r"\b660\b(?!\s*\))", "старое число ячеек стресс-теста"),
    (r"одиннадцать преобразован", "старый знаменатель стресс-теста"),
    (r"\b0\.072\b", "медиана FPR формального регистра серии v1"),
    (r"\b6940\b", "устаревшее число переносов"),
]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources():
    bad = []
    for name, expected in SOURCES.items():
        got = sha256(PAPER / name)
        if got != expected:
            bad.append((name, expected, got))
    return bad


def split_sections(text):
    """Разбивает текст на (уровень, заголовок, тело) по markdown-заголовкам."""
    lines = text.split("\n")
    out, cur = [], None
    for line in lines:
        m = re.match(r"^(#{1,6}) (.+)$", line)
        if m:
            if cur:
                out.append(cur)
            cur = [len(m.group(1)), line, []]
        elif cur:
            cur[2].append(line)
        else:
            cur = [0, None, [line]]
    if cur:
        out.append(cur)
    return out


def drop_sections(sections, headers):
    """Убирает раздел вместе с подчинёнными ему по уровню."""
    out, skip_level = [], None
    for level, head, body in sections:
        if skip_level is not None:
            if head and level <= skip_level:
                skip_level = None
            else:
                continue
        if head and head.strip() in headers:
            skip_level = level
            continue
        out.append((level, head, body))
    return out


def strip_callouts(text):
    """Снимает врезки рабочих документов: блок цитаты целиком."""
    out, skipping = [], False
    for line in text.split("\n"):
        if any(line.startswith(m) for m in CALLOUT_MARKERS):
            skipping = True
            continue
        if skipping:
            if line.startswith(">") or not line.strip():
                if not line.strip():
                    skipping = False
                continue
            skipping = False
        out.append(line)
    return "\n".join(out)


def take_limitations(text):
    """Только §5 «Ограничения» со всеми подразделами."""
    sections = split_sections(text)
    out, inside = [], False
    for level, head, body in sections:
        if head and head.strip().startswith("## 5. Ограничения"):
            inside = True
            out.append((level, head, body))
            continue
        if inside:
            if head and level <= 2:
                break
            out.append((level, head, body))
    return out


def take_appendix(text):
    sections = split_sections(text)
    return drop_sections(sections, set(DROP_SECTIONS["appendix-reproducibility.md"]))


def render(sections, demote=1):
    """Собирает разделы обратно, понижая уровень заголовков на demote."""
    parts = []
    for level, head, body in sections:
        if head:
            m = re.match(r"^(#{1,6}) (.+)$", head)
            parts.append("#" * min(6, len(m.group(1)) + demote) + " " + m.group(2))
        parts.append("\n".join(body).rstrip())
    return "\n\n".join(p for p in parts if p.strip())


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"сборка рукописи, {stamp}")

    bad = verify_sources()
    if bad:
        for name, exp, got in bad:
            print(f"  РАСХОЖДЕНИЕ {name}: ожидался {exp[:16]}…, получен {got[:16]}…")
        raise SystemExit("ОСТАНОВ: хеши источников не совпали с манифестом, сборка не выполнялась")
    print(f"  хеши сверены: {len(SOURCES)} источников, расхождений нет")

    chapters = []
    dropped = []
    for title, fname, mode in LAYOUT:
        text = (PAPER / fname).read_text(encoding="utf-8")
        text = strip_callouts(text)
        if mode == "all":
            sections = split_sections(text)
            heads = set(DROP_SECTIONS.get(fname, []))
            if heads:
                before = len(sections)
                sections = drop_sections(sections, heads)
                dropped.append((fname, before - len(sections)))
            # убираем заголовок первого уровня исходного файла
            sections = [s for s in sections if not (s[1] and s[0] == 1)]
        elif mode == "limitations":
            sections = take_limitations(text)
            sections = [(l, h.replace("## 5. Ограничения", "## Ограничения") if h else h, b)
                        for l, h, b in sections]
        elif mode == "appendix":
            sections = take_appendix(text)
            sections = [s for s in sections if not (s[1] and s[0] == 1)]
            dropped.append((fname, 1))
        elif mode == "declarations":
            # В рукопись идут семь деклараций и титульный блок; служебный раздел
            # с открытыми вопросами к PI остаётся рабочим.
            sections = split_sections(text)
            before = len(sections)
            sections = drop_sections(sections, set(DROP_SECTIONS[fname]))
            sections = [s for s in sections if not (s[1] and s[0] == 1)]
            dropped.append((fname, before - len(sections)))
        chapters.append((title, render(sections)))
        print(f"  {title}: {len(render(sections).splitlines())} строк")

    body = "\n\n".join(f"# {title}\n\n{content}" for title, content in chapters)
    header = (
        "# Условия производства текста как конфаундер оценки машинности\n\n"
        f"Сборка {stamp} по `08-paper/paper-assembly-manifest.md`. "
        "Источники сверены по хешам; исключения §2 манифеста применены.\n\n"
        "---\n"
    )
    manuscript = header + "\n" + body + "\n"
    OUT.write_text(manuscript, encoding="utf-8", newline="\n")

    # контроль: запрещённое не просочилось
    flat = re.sub(r"\s+", " ", manuscript)
    hits = []
    for pattern, why in FORBIDDEN:
        for m in re.finditer(pattern, flat):
            ctx = flat[max(0, m.start()-70):m.start()+70]
            hits.append((why, ctx))

    lines = [
        "# Отчёт о сборке рукописи", "",
        f"Собрано {stamp} скриптом `09-tools/assemble_paper.py` по манифесту.", "",
        f"- источников: {len(SOURCES)}, все хеши сошлись;",
        f"- разделов в рукописи: {len(chapters)};",
        f"- строк в собранном файле: {len(manuscript.splitlines())};",
        f"- вырезано разделов по §2 манифеста: {sum(n for _, n in dropped)};",
        "",
        "## Контроль запрещённого", "",
    ]
    if hits:
        lines += ["| Что найдено | Контекст |", "|---|---|"]
        lines += [f"| {why} | …{ctx.strip()}… |" for why, ctx in hits[:20]]
    else:
        lines.append("Ни одно из запрещённых значений в рукопись не попало.")
    lines += ["", "## Что вырезано", "",
              "| Файл | Разделов убрано |", "|---|---|"]
    lines += [f"| `{f}` | {n} |" for f, n in dropped]
    lines += ["", f"Хеш рукописи: `{hashlib.sha256(manuscript.encode('utf-8')).hexdigest()}`", ""]
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    print(f"  запрещённых значений найдено: {len(hits)}")
    for why, ctx in hits[:5]:
        print(f"    {why}: …{ctx.strip()[:90]}…")
    print(f"  записано: {OUT.name}, {OUT_REPORT.name}")
    if hits:
        raise SystemExit("ОСТАНОВ: в рукопись попали значения из списка исключений")


if __name__ == "__main__":
    main()
