#!/usr/bin/env python3
"""Преобразование sensitivity-проверки: сокращение без дублирования и без потери абзацев.

Регистрация — `02-preregistration/amendment-sensitivity-truncation.md`, записана
до написания этого кода. Имя преобразования: **paragraph-preserving truncation
without verbatim duplication**.

    python 09-tools/sensitivity_truncation.py --build     # собрать входы панели
    python 09-tools/sensitivity_truncation.py --check     # четыре проверки допуска

**Почему отдельный модуль, а не запись в `stress_transforms.py`.** Тот файл
зафиксирован таблицами хешей ревизий r5 и r11, и любое его изменение потребовало
бы новой ревизии. Кроме того, попадание в словарь `TRANSFORMS` включило бы
преобразование в знаменатель стресс-теста — а он остаётся равным 10 (§5
амендмента).

**Чем это отличается от t10.** `t10_shorten` режет весь документ регуляркой
`\\s+` и собирает через пробел, поэтому разделители абзацев исчезают: медианно
теряется 83.5% абзацев. Здесь единицей работы служит строка: предложения
удаляются внутри строки, а все разделители строк — и одиночные, и двойные —
остаются на местах вместе с заголовками, списками и разметкой.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PANEL = ROOT / "07-analysis" / "stress-panel-v1.csv"
ORIG = ROOT / "04-corpus" / "derived" / "prep-v5"
OUT_DIR = ROOT / "04-corpus" / "derived" / "sensitivity-v1" / "s01"
OUT_CHECK = ROOT / "07-analysis" / "sensitivity-v1-gate.md"
OUT_MANIFEST = ROOT / "07-analysis" / "sensitivity-v1-inputs-manifest.json"

TRANSFORM_ID = "s01"
TRANSFORM_NAME = "paragraph-preserving truncation without verbatim duplication"

# Сегментация та же, что у стресс-преобразований и скана повторов.
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Правило отбора: удаляется каждое DROP_EVERY-е предложение документа, отсчёт
# сквозной. Четвёрка выбрана, чтобы целевое сокращение было около 25% — тот же
# порядок, что у t10, иначе сравнение с ним теряет смысл.
DROP_EVERY = 4
DROP_OFFSET = 3      # удаляются индексы 3, 7, 11 … — первое предложение остаётся

# Критерии допуска, §3 амендмента.
VOLUME_RANGE = (0.70, 0.80)
M01_MEDIAN_MAX = 0.0          # медиана Δz не положительна
M01_DOC_MAX = 0.5             # ни у одного документа рост не выше 0.5 σ


def truncate(text):
    """Удаляет каждое четвёртое предложение, сохраняя строки и разметку.

    Единица работы — строка, а не документ: так все разделители, включая `\\n\\n`,
    остаются на месте. В строке всегда остаётся минимум одно предложение, поэтому
    пустых строк преобразование не создаёт и абзац не схлопывает.
    """
    lines = text.split("\n")
    out_lines = []
    counter = 0
    for line in lines:
        if not line.strip():
            out_lines.append(line)
            continue
        sentences = SENT_SPLIT.split(line)
        kept = []
        for s in sentences:
            drop = counter % DROP_EVERY == DROP_OFFSET
            counter += 1
            if drop:
                continue
            kept.append(s)
        if not kept:
            # Правило §2.6: строка не остаётся пустой. Возвращаем первое
            # предложение, счётчик при этом уже сдвинут — сквозной отсчёт не
            # нарушается.
            kept = [sentences[0]]
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_panel():
    with PANEL.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def paragraphs(text):
    return text.count("\n\n")


def repeat_share(text):
    """Доля повторяющихся предложений по правилу `repeat-scan-v2-spec.md` §2."""
    sentences = [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) >= 40]
    if not sentences:
        return 0.0
    counts = Counter(sentences)
    return sum(n for n in counts.values() if n > 1) / len(sentences)


def repeat_count(text):
    """Абсолютное число вхождений повторяющихся предложений.

    Прямая мера внесённого дублирования: удаление предложений её увеличить не
    может. Доля же растёт от одного удаления неповторяющегося предложения —
    знаменатель уменьшается, числитель остаётся. Поэтому §3 проверяется по обеим
    величинам, а расхождение между ними разбирается отдельно.
    """
    sentences = [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) >= 40]
    counts = Counter(sentences)
    return sum(n for n in counts.values() if n > 1)


def build():
    """Собирает входы панели: два профиля на документ, как у стресс-панели."""
    panel = read_panel()
    written = 0
    for profile in ("prose", "full"):
        (OUT_DIR / profile).mkdir(parents=True, exist_ok=True)
    for row in panel:
        doc_id = row["document_id"]
        for profile in ("prose", "full"):
            src = ORIG / profile / f"{doc_id}.txt"
            if not src.exists():
                print(f"  нет исходника: {src}")
                continue
            text = src.read_text(encoding="utf-8")
            (OUT_DIR / profile / f"{doc_id}.txt").write_text(
                truncate(text), encoding="utf-8", newline="")
            written += 1
    print(f"  записано файлов: {written}")
    return written


def check():
    """Четыре проверки допуска §3. Возвращает (отчёт, всё ли пройдено)."""
    panel = read_panel()
    rows = []
    for row in panel:
        doc_id = row["document_id"]
        orig = (ORIG / "full" / f"{doc_id}.txt").read_text(encoding="utf-8")
        new_path = OUT_DIR / "full" / f"{doc_id}.txt"
        if not new_path.exists():
            print(f"  нет преобразованного файла: {new_path}")
            continue
        new = new_path.read_text(encoding="utf-8")
        rows.append({
            "document_id": doc_id,
            "paragraphs_before": paragraphs(orig),
            "paragraphs_after": paragraphs(new),
            "repeat_before": repeat_share(orig),
            "repeat_after": repeat_share(new),
            "repeat_count_before": repeat_count(orig),
            "repeat_count_after": repeat_count(new),
            "words_before": len(orig.split()),
            "words_after": len(new.split()),
            "volume_ratio": len(new.split()) / max(len(orig.split()), 1),
        })

    par_bad = [r for r in rows if r["paragraphs_after"] != r["paragraphs_before"]]
    rep_bad = [r for r in rows if r["repeat_after"] > r["repeat_before"] + 1e-12]
    cnt_bad = [r for r in rows if r["repeat_count_after"] > r["repeat_count_before"]]
    ratios = [r["volume_ratio"] for r in rows]
    med_ratio = median(ratios) if ratios else 0.0
    volume_ok = VOLUME_RANGE[0] <= med_ratio <= VOLUME_RANGE[1]

    checks = {
        "paragraphs_preserved": {"documents": len(rows), "violations": len(par_bad),
                                 "passed": not par_bad},
        "no_new_duplication_share": {"documents": len(rows), "violations": len(rep_bad),
                                     "passed": not rep_bad},
        "no_new_duplication_count": {"documents": len(rows), "violations": len(cnt_bad),
                                     "passed": not cnt_bad},
        "volume_in_range": {"median_ratio": round(med_ratio, 4),
                            "range": list(VOLUME_RANGE),
                            "min": round(min(ratios), 4) if ratios else None,
                            "max": round(max(ratios), 4) if ratios else None,
                            "passed": volume_ok},
        "m01_not_growing": {"passed": None,
                            "note": "считается отдельным шагом на эмбеддингах"},
    }
    return rows, checks, par_bad, rep_bad, cnt_bad


def write_gate(rows, checks, par_bad, rep_bad, cnt_bad):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ratios = [r["volume_ratio"] for r in rows]
    lines = [
        f"# Допуск sensitivity-преобразования {TRANSFORM_ID}",
        "",
        f"Собрано {stamp} скриптом `09-tools/sensitivity_truncation.py`. "
        "Регистрация — `02-preregistration/amendment-sensitivity-truncation.md`, "
        "критерии §3 записаны до написания кода.",
        "",
        f"Преобразование: **{TRANSFORM_NAME}**. Панель — {len(rows)} документов, "
        "профиль `full`.",
        "",
        "| Проверка | Критерий | Результат | Статус |",
        "|---|---|---|---|",
        f"| Абзацная разметка сохранена | число `\\n\\n` не изменилось ни у одного "
        f"документа | нарушений {len(par_bad)} | "
        f"{'пройдена' if checks['paragraphs_preserved']['passed'] else 'ПРОВАЛЕНА'} |",
        f"| Дублирование не внесено, по числу вхождений | абсолютное число "
        f"повторов не выросло ни у одного | нарушений {len(cnt_bad)} | "
        f"{'пройдена' if checks['no_new_duplication_count']['passed'] else 'ПРОВАЛЕНА'} |",
        f"| Дублирование не внесено, по доле | доля повторов не выросла ни у "
        f"одного | нарушений {len(rep_bad)} | "
        f"{'пройдена' if checks['no_new_duplication_share']['passed'] else 'ПРОВАЛЕНА'} |",
        f"| Целевой диапазон объёма | медиана в {VOLUME_RANGE[0]}–{VOLUME_RANGE[1]} | "
        f"медиана {checks['volume_in_range']['median_ratio']}, "
        f"размах {checks['volume_in_range']['min']}–{checks['volume_in_range']['max']} | "
        f"{'пройдена' if checks['volume_in_range']['passed'] else 'ПРОВАЛЕНА'} |",
        "| M01 не растёт | медиана Δz ≤ 0, рост нигде не выше 0.5 σ | "
        "считается отдельным шагом | не выполнена |",
        "",
        "## Сравнение с t10 на той же панели",
        "",
        "Описательное, по решению PI: чистый эффект длины отсюда не следует — "
        "способы удаления содержания различаются.",
        "",
        "| Величина | t10 сокращение | s01 |",
        "|---|---|---|",
        "| Медианная доля потерянных абзацев | 0.835 | "
        f"{0.0 if checks['paragraphs_preserved']['passed'] else 'см. нарушения'} |",
        f"| Медианное отношение объёма | 0.762 | "
        f"{checks['volume_in_range']['median_ratio']} |",
        "",
    ]
    if par_bad:
        lines += ["## Документы с изменённой разметкой", "",
                  "| Документ | Абзацев до | После |", "|---|---|---|"]
        lines += [f"| `{r['document_id']}` | {r['paragraphs_before']} | "
                  f"{r['paragraphs_after']} |" for r in par_bad[:20]]
        lines.append("")
    if rep_bad:
        lines += ["## Документы с выросшей долей повторов", "",
                  "| Документ | Было | Стало |", "|---|---|---|"]
        lines += [f"| `{r['document_id']}` | {r['repeat_before']:.4f} | "
                  f"{r['repeat_after']:.4f} |" for r in rep_bad[:20]]
        lines.append("")

    OUT_GATE_TEXT = "\n".join(lines)
    OUT_CHECK.write_text(OUT_GATE_TEXT, encoding="utf-8")

    manifest = {
        "series": "sensitivity-v1",
        "transform_id": TRANSFORM_ID,
        "transform_name": TRANSFORM_NAME,
        "status": "prospectively registered post hoc P2 sensitivity",
        "amendment": "02-preregistration/amendment-sensitivity-truncation.md",
        "scope": "процедура 2, решение PI 2026-08-01",
        "rule": (f"удаляется каждое {DROP_EVERY}-е предложение сквозным отсчётом, "
                 f"смещение {DROP_OFFSET}; единица работы — строка; в строке "
                 "остаётся минимум одно предложение"),
        "inputs_dir": str(OUT_DIR.relative_to(ROOT)).replace("\\", "/"),
        "panel_documents": len(rows),
        "checks": checks,
        "volume_ratio_median": round(median(ratios), 4) if ratios else None,
        "code_sha256": {Path(__file__).name:
                        hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "created_at": stamp,
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="собрать входы панели")
    parser.add_argument("--check", action="store_true", help="проверки допуска")
    args = parser.parse_args()

    if not (args.build or args.check):
        parser.error("нужен --build или --check")

    if args.build:
        print(f"сборка входов {TRANSFORM_ID}")
        build()

    if args.check:
        print(f"проверки допуска {TRANSFORM_ID}")
        rows, checks, par_bad, rep_bad, cnt_bad = check()
        write_gate(rows, checks, par_bad, rep_bad, cnt_bad)
        for name, res in checks.items():
            state = ("пройдена" if res["passed"] else
                     "не выполнена" if res["passed"] is None else "ПРОВАЛЕНА")
            print(f"  {name}: {state}")
        print(f"  медианное отношение объёма: "
              f"{checks['volume_in_range']['median_ratio']}")
        print(f"  отчёт: {OUT_CHECK.name}")
        hard = [n for n, r in checks.items() if r["passed"] is False]
        if hard:
            raise SystemExit(f"ОСТАНОВ: провалены проверки {hard}; по §3 амендмента "
                             "ослаблять критерий нельзя, развилка выносится PI")


if __name__ == "__main__":
    main()
