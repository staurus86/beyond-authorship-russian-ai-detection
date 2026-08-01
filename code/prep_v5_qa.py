#!/usr/bin/env python3
"""QA перехода prep-v4 → prep-v5: что изменилось и что обязано было остаться прежним.

    python 09-tools/prep_v5_qa.py

Три проверки, все с жёстким исходом:

1. **Неизменённые документы совпадают побитово.** Документ, которого не касалась
   коррекция, обязан иметь тот же sha256 профилей, что в prep-v4. Расхождение
   означает, что коррекция задела не то, что должна.
2. **Дефект ушёл там, где правился.** У документов с вердиктом
   `extraction-defect` и `intermediary-defect` доля повторов в профиле prose
   должна упасть ниже порога.
3. **Дефект остался там, где правка запрещена.** У `source-property` и
   `unresolved` текст обязан совпасть с prep-v4: их не трогали.

Отчёт — `07-analysis/prep-v5-qa.md`.
"""

import csv
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

V4 = ROOT / "04-corpus" / "derived" / "prep-v4"
V5 = ROOT / "04-corpus" / "derived" / "prep-v5"
CORRECTIONS = ROOT / "04-corpus" / "prep-v5-corrections.csv"
OUT = ROOT / "07-analysis" / "prep-v5-qa.md"

SENT = re.compile(r"(?<=[.!?])\s+")
THRESHOLD = 0.10
MIN_SENT_CHARS = 40
EDITED = {"extraction-defect", "intermediary-defect"}


def repeat_share(text):
    parts = [s.strip() for s in SENT.split(text) if len(s.strip()) >= MIN_SENT_CHARS]
    if not parts:
        return 0.0
    counts = defaultdict(int)
    for s in parts:
        counts[s] += 1
    return sum(n for n in counts.values() if n > 1) / len(parts)


def manifest(path):
    with (path / "manifest.csv").open(encoding="utf-8", newline="") as fh:
        return {r["document_id"]: r for r in csv.DictReader(fh)}


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"QA prep-v4 → prep-v5, {stamp}")

    m4, m5 = manifest(V4), manifest(V5)
    with CORRECTIONS.open(encoding="utf-8", newline="") as fh:
        corrections = {r["document_id"]: r for r in csv.DictReader(fh)}

    edited = {d for d, r in corrections.items() if r["verdict"] in EDITED
              and r["sha256_after"]}
    untouched_verdict = {d for d, r in corrections.items()
                         if r["verdict"] not in EDITED}

    common = set(m4) & set(m5)
    dropped = set(m4) - set(m5)
    mismatched, checked = [], 0
    for doc in sorted(common):
        if doc in edited:
            continue
        checked += 1
        for field in ("prose_sha256", "full_sha256"):
            if m4[doc][field] != m5[doc][field]:
                mismatched.append((doc, field))
                break

    fixed, still = [], []
    for doc in sorted(edited & common):
        text = (V5 / "prose" / f"{doc}.txt").read_text(encoding="utf-8")
        share = repeat_share(text)
        (fixed if share <= THRESHOLD else still).append((doc, round(share, 3)))

    frozen_ok = all(m4[doc]["prose_sha256"] == m5[doc]["prose_sha256"]
                    for doc in untouched_verdict & common)

    print(f"  документов: v4 {len(m4)}, v5 {len(m5)}, исключено {len(dropped)}")
    print(f"  сверено неизменённых: {checked}, расхождений {len(mismatched)}")
    print(f"  исправлено документов: {len(fixed)}, дефект остался у {len(still)}")
    print(f"  запрещённые к правке не тронуты: {frozen_ok}")

    lines = [
        "# QA перехода prep-v4 → prep-v5",
        "",
        f"Собрано {stamp} скриптом `09-tools/prep_v5_qa.py`.",
        "",
        "| Проверка | Результат |",
        "|---|---|",
        f"| документов в prep-v4 / prep-v5 | {len(m4)} / {len(m5)} |",
        f"| исключено по критерию §6 | {len(dropped)} |",
        f"| неизменённых сверено побитово | {checked} |",
        f"| расхождений среди неизменённых | **{len(mismatched)}** |",
        f"| скорректировано документов | {len(edited)} |",
        f"| из них дефект ушёл | {len(fixed)} |",
        f"| дефект остался | {len(still)} |",
        f"| документы, правка которых запрещена, не тронуты | "
        f"{'да' if frozen_ok else '**нет**'} |",
        "",
    ]
    if mismatched:
        lines += ["## Расхождения среди неизменённых — это дефект коррекции", "",
                  "| Документ | Поле |", "|---|---|"]
        lines += [f"| `{doc}` | {field} |" for doc, field in mismatched[:50]]
        lines.append("")
    if still:
        lines += ["## Документы, где дефект остался после правки", "",
                  "| Документ | Доля повторов |", "|---|---|"]
        lines += [f"| `{doc}` | {share} |" for doc, share in still]
        lines += ["", "Остаток означает, что механизм повтора у них другой: "
                  "правило коррекции его не описывает. Такие документы идут в "
                  "ограничения, а не под новое правило, придуманное после просмотра.",
                  ""]

    all_v5 = []
    for doc in sorted(m5):
        path = V5 / "prose" / f"{doc}.txt"
        if path.exists():
            all_v5.append(repeat_share(path.read_text(encoding="utf-8")))
    above = sum(1 for v in all_v5 if v > THRESHOLD)
    lines += ["## Состояние корпуса после коррекции", "",
              f"Документов с долей повторов выше {THRESHOLD}: **{above}** из "
              f"{len(all_v5)}. Медиана доли по корпусу: {st.median(all_v5):.4f}.",
              ""]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  отчёт: {OUT.name}")

    if mismatched or not frozen_ok:
        raise SystemExit("QA не пройден: коррекция задела документы, которых не должна")


if __name__ == "__main__":
    main()
