#!/usr/bin/env python3
"""Финальный шлюз препринта: проверка собранной рукописи перед фиксацией.

    python 09-tools/preprint_gate.py

Прежние прогоны шлюза выполнялись разовыми скриптами и в проекте не сохранились —
повторить их было нечем. Здесь проверки собраны в файл, который живёт вместе с
проектом и запускается перед каждой фиксацией препринта.

Шлюз читает `08-paper/manuscript-final.md`, манифесты рисунков и таблиц, исходные
файлы разделов. Он ничего не правит: при непройденной проверке печатает её и
завершается с ненулевым кодом.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "08-paper"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


def main():
    manuscript = (PAPER / "manuscript-final.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", manuscript)
    sources = ["introduction.md", "research-gap.md", "related-work.md", "methods.md",
               "results.md", "discussion.md", "main-claim-and-limitations.md",
               "declarations.md", "appendix-reproducibility.md", "figures-list.md",
               "supplementary.md"]
    source_text = "\n".join((PAPER / f).read_text(encoding="utf-8") for f in sources)

    # ── 1. Целостность сборки ────────────────────────────────────────────────
    import importlib.util
    spec = importlib.util.spec_from_file_location("asm", ROOT / "09-tools" / "assemble_paper.py")
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    for name, expected in asm.SOURCES.items():
        got = hashlib.sha256((PAPER / name).read_bytes()).hexdigest()
        check(f"хеш источника {name}", got == expected,
              "" if got == expected else f"ожидался {expected[:12]}…, получен {got[:12]}…")
    check("состав сборки — 11 источников", len(asm.SOURCES) == 11, str(len(asm.SOURCES)))

    # ── 2. Запрещённые значения ──────────────────────────────────────────────
    forbidden = [
        (r"\b0\.538\b", "ICC серии v1"),
        (r"\b5104\b", "старое число строк P2"),
        (r"\b6940\b", "устаревшее число переносов"),
        (r"одиннадцать преобразован", "старый знаменатель стресс-теста"),
        (r"\b420 документ", "размер одноклассовых holdout серии v1"),
    ]
    for pattern, why in forbidden:
        hits = re.findall(pattern, manuscript)
        check(f"нет запрещённого: {why}", not hits, f"найдено {len(hits)}")

    # ── 3. Числа корпуса и выборки ───────────────────────────────────────────
    for value, why in [("1882", "объём корпуса"), ("1079", "машинная часть"),
                       ("803", "человеческая часть"), ("73 работ", "объём выборки литературы")]:
        check(f"присутствует {why} ({value})", value in flat)

    # ── 4. Каноническая формулировка о регистрации ───────────────────────────
    canon = ("План межпроцедурного сопоставления и спецификации процедур 2–4 были "
             "зафиксированы после получения результата процедуры 1, но до запуска "
             "процедур 2–4.")
    check("каноническая формулировка о порядке регистрации",
          re.sub(r"\s+", " ", canon) in flat)

    # ── 5. Три оговорки, внесённые по требованию PI ──────────────────────────
    check("оговорка: регистр не отделён от жанра",
          "раздельно не идентифицируются" in flat)
    check("оговорка: source-only и порог решения",
          "все человеческие документы невиданной площадки" in flat)
    check("оговорка: macro — процедуры, а не независимые выборки",
          "а не независимые наборы документов" in flat)

    # ── 6. Таблицы ───────────────────────────────────────────────────────────
    tman = json.loads((PAPER / "tables" / "tables-manifest.json").read_text(encoding="utf-8"))
    idents = {("S" if t["kind"] == "supp" else "") + str(t["number"]) for t in tman["tables"]}
    check("собрано 24 таблицы", len(tman["tables"]) == 24, str(len(tman["tables"])))
    anchors = dict(re.findall(r"<!-- ТАБЛИЦА (S?\d+): ([A-Za-z0-9-]+) -->", source_text))
    check("якорь у каждой таблицы", idents <= set(anchors), str(sorted(idents - set(anchors))))
    refs = set(re.findall(r"таблиц[ае]\s+(S?\d+)", source_text, flags=re.IGNORECASE))
    check("ссылка в прозе у каждой таблицы", idents <= refs, str(sorted(idents - refs)))
    check("нет ссылок на несуществующие таблицы", refs <= idents, str(sorted(refs - idents)))
    for ident, slug in anchors.items():
        check(f"файлы таблицы {ident}",
              (PAPER / "tables" / f"{slug}.tex").exists()
              and (PAPER / "tables" / f"{slug}.csv").exists())
    for fname, sha in tman["inputs_sha256"].items():
        got = hashlib.sha256((ROOT / fname).read_bytes()).hexdigest()
        check(f"вход таблиц {Path(fname).name}", got == sha)

    # ── 7. Рисунки ───────────────────────────────────────────────────────────
    fman = json.loads((PAPER / "figures" / "figures-manifest.json").read_text(encoding="utf-8"))
    check("собрано 6 рисунков", len(fman["outputs_sha256"]) == 12,
          str(len(fman["outputs_sha256"])))
    for fname, sha in fman["outputs_sha256"].items():
        got = hashlib.sha256((PAPER / "figures" / fname).read_bytes()).hexdigest()
        check(f"рисунок {fname}", got == sha)
    code_now = hashlib.sha256((ROOT / "09-tools" / "build_figures.py").read_bytes()).hexdigest()
    check("код рисунков не менялся после сборки",
          code_now == fman["code_sha256"]["build_figures.py"])

    # ── 8. Декларации ────────────────────────────────────────────────────────
    # Пробелы нормализуются: цитаты деклараций переносятся по строкам, и проверка
    # по подстроке иначе ловит перенос вместо пробела.
    decl_raw = (PAPER / "declarations.md").read_text(encoding="utf-8")
    decl = re.sub(r"\s+", " ", decl_raw.replace("\n>", " "))
    for head in ["Acknowledgements", "CRediT author statement", "Funding statement",
                 "Competing interests", "Data and code availability",
                 "Ethics statement", "AI-use disclosure"]:
        check(f"декларация: {head}", head in decl)
    check("титул: ORCID", "0009-0001-2914-9541" in decl)
    check("титул: corresponding author", "Corresponding author" in decl)
    check("раскрыто владение детектором", "developed and owns" in decl)
    check("раскрыто отсутствие коммерческого лицензирования",
          "not been commercially licensed" in decl)
    open_slots = re.findall(r"\[требует подтверждения PI[^\]]*\]", decl)
    check("нет незакрытых вопросов к PI", not open_slots, str(len(open_slots)))
    check("служебный раздел деклараций не попал в рукопись",
          "Что нужно от PI" not in manuscript)

    # ── 9. Библиография и ссылки ─────────────────────────────────────────────
    leftover = re.findall(r"\br0\d\d\b", manuscript)
    check("служебных ref_id в рукописи нет", not leftover, str(sorted(set(leftover))))
    check("ключевых слов пять",
          (PAPER / "title-and-keywords.md").read_text(encoding="utf-8").count(
              "preprocessing sensitivity") >= 2)

    # ── итог ─────────────────────────────────────────────────────────────────
    failed = [c for c in CHECKS if not c[1]]
    print(f"проверок: {len(CHECKS)}, непройденных: {len(failed)}")
    for name, ok, detail in failed:
        print(f"  ПРОВАЛ: {name}" + (f" — {detail}" if detail else ""))
    if failed:
        raise SystemExit("ШЛЮЗ НЕ ПРОЙДЕН: препринт не фиксируется")
    print(f"хеш рукописи: {hashlib.sha256(manuscript.encode('utf-8')).hexdigest()}")
    print("шлюз пройден")


if __name__ == "__main__":
    main()
