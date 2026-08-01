#!/usr/bin/env python3
"""Извлечение матрицы весов индекса стиля из первоисточника и её проверка.

    python 09-tools/extract_weight_matrix.py --out <каталог vendor>

Матрица задаёт веса категорий признаков для процедуры 1. Первоисточник —
`references/domain-adaptation.md` пакета `ai-text-detector`, раздел «Матрица весов
по доменам»; в репозиторий публикуется только эта таблица, не пакет целиком.

Скрипт делает четыре вещи: извлекает таблицу из исходника, сверяет её с
опубликованной копией, проверяет нормировку каждого столбца и записывает хеш
первоисточника. Расхождение останавливает работу — молча переписать матрицу нельзя.

**Размер матрицы — 13 категорий × 8 столбцов.** Спецификация `scoring-spec.md` §1
называет её «13 × 7»: семь доменов плюс производный столбец «Смешанный», который
там же используется для двух жанров. Публикуется всё восемь столбцов, включая
неиспользованные, — иначе читатель не увидит исходного пространства доменов.
"""

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path.home() / ".claude" / "skills" / "ai-text-detector" / \
    "references" / "domain-adaptation.md"

# Отображение жанров корпуса на столбцы, `scoring-spec.md` §2.
GENRE_TO_DOMAIN = {
    "news": "News",
    "prose": "Creative",
    "analytics": "Opinion",
    "science": "Scientific",
    "seo": "Смешанный",
    "commercial": "Смешанный",
    "translation": "Смешанный",
}

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def parse_matrix(text):
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("| Категория |"))
    rows = []
    i = start
    while i < len(lines) and lines[i].startswith("|"):
        rows.append([c.strip().replace("**", "") for c in lines[i].strip("|").split("|")])
        i += 1
    header = rows[0]
    body = [r for r in rows[2:] if r[0] != "Сумма"]
    declared_sum = next((r for r in rows[2:] if r[0] == "Сумма"), None)
    return header, body, declared_sum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        raise SystemExit(f"ОСТАНОВ: первоисточник не найден: {src}")
    raw = src.read_text(encoding="utf-8")
    src_sha = hashlib.sha256(src.read_bytes()).hexdigest()

    header, body, declared = parse_matrix(raw)
    domains = header[1:]
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    check("категорий 13", len(body) == 13, str(len(body)))
    check("столбцов 8", len(domains) == 8, str(len(domains)))
    for j, dom in enumerate(domains, start=1):
        total = sum(float(r[j]) for r in body)
        check(f"сумма столбца {dom} равна 1.00", abs(total - 1.0) < 1e-9, f"{total:.4f}")
        if declared:
            check(f"объявленная сумма {dom} совпадает с посчитанной",
                  abs(float(declared[j]) - total) < 1e-9)
    used = {GENRE_TO_DOMAIN[g] for g in GENRE_TO_DOMAIN}
    check("все использованные столбцы есть в матрице", used <= set(domains),
          str(sorted(used - set(domains))))

    failed = [c for c in checks if not c[1]]
    print(f"проверок: {len(checks)}, непройденных: {len(failed)}")
    for name, ok, detail in failed:
        print(f"  ПРОВАЛ: {name}" + (f" — {detail}" if detail else ""))
    if failed:
        raise SystemExit("ОСТАНОВ: матрица весов не прошла проверку")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with (out / "weight-matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["category"] + domains)
        for r in body:
            w.writerow(r)
        if declared:
            w.writerow(declared)

    md_start = raw.index("| Категория |")
    md_end = raw.index("\n\n", md_start)
    (out / "weight-matrix.md").write_text(
        "# Матрица весов по доменам\n\n"
        "Дословная копия таблицы из первоисточника, без правок.\n\n"
        + raw[md_start:md_end] + "\n", encoding="utf-8", newline="\n")

    used_rows = "\n".join(
        f"| `{g}` | {d} |" for g, d in sorted(GENRE_TO_DOMAIN.items()))
    unused = ", ".join(d for d in domains if d not in used)
    (out / "README.md").write_text(f"""# Матрица весов индекса стиля

Замороженный компонент, использованный в статье. Это не полный пакет
`ai-text-detector`, а одна таблица из него вместе с тем, что нужно для её чтения.

## Что здесь лежит

| Файл | Что это |
|---|---|
| `weight-matrix.md` | дословная копия таблицы из первоисточника, без правок |
| `weight-matrix.csv` | та же таблица машинно-читаемо |
| `weight-matrix-manifest.json` | хеш первоисточника, статус каждого столбца, результаты проверок |

## Размер и статус столбцов

Таблица содержит 13 категорий признаков и {len(domains)} столбцов. Пять столбцов
использованы в исследовании, три помечены `not used in this study` и публикуются,
чтобы читатель видел исходное пространство доменов, а не только удобные веса.

Не используются: {unused}.

## Как жанры корпуса отображаются на столбцы

| Жанр корпуса | Столбец матрицы |
|---|---|
{used_rows}

Отображение задано `07-analysis/scoring-spec.md` §2 до просмотра значений признаков
по классам.

## Правила расчёта, без которых веса читаются неверно

Индекс считается как взвешенная сумма категорий, делённая на сумму весов
использованных категорий. Знаменатель одинаков у всех документов: ни одна категория
ни у одного документа не осталась без признаков. Веса исключённых категорий между
оставшимися не перераспределяются.

Направление шкалы задаёт конвенция «больше значит более AI-подобно»; она относится к
итоговому индексу, а не к отдельным признакам.

Полное описание процедуры — `07-analysis/scoring-spec.md`. Размер таблицы там
записан как «13 × 7»: поправка и её основание — в
`07-analysis/scoring-spec-erratum-2026-08-01.md`.

## О дате

Первоисточник документирован как существовавший 12 июля 2026 — отметкой времени
файла и записью в спецификации. Это документальное свидетельство, а не
криптографическое доказательство: отметку времени файловой системы можно изменить.
""", encoding="utf-8", newline="\n")

    manifest = {
        "component": "матрица весов индекса стиля, процедура 1",
        "source_file": "ai-text-detector/references/domain-adaptation.md",
        "source_sha256": src_sha,
        "source_documented_date": "2026-07-12",
        "date_evidence": "дата документирована отметкой времени файла и записью в "
                         "scoring-spec.md §1. Это документальное свидетельство, а не "
                         "криптографическое доказательство: отметку времени можно "
                         "изменить, и на неё нельзя ссылаться как на неопровержимую",
        "matrix_shape": {"categories": len(body), "columns": len(domains)},
        "shape_note": "13 категорий × 8 столбцов: семь доменов и производный "
                      "«Смешанный». Формулировка «13 × 7» в scoring-spec.md §1 "
                      "считает только домены",
        "columns": [
            {"domain": d,
             "used": d in used,
             "genres": sorted(g for g, v in GENRE_TO_DOMAIN.items() if v == d) or None,
             "status": "used in this study" if d in used else "not used in this study"}
            for d in domains],
        "column_sums_verified": True,
        "spec": "07-analysis/scoring-spec.md",
        "published_scope": "только эта таблица; остальной пакет ai-text-detector в "
                           "репозиторий не входит",
        "checks_passed": len(checks),
    }
    (out / "weight-matrix-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        newline="\n")

    print(f"столбцов использовано: {sum(1 for d in domains if d in used)} из {len(domains)}")
    print("не используются:", ", ".join(d for d in domains if d not in used))
    print(f"записано: {out}")


if __name__ == "__main__":
    main()
