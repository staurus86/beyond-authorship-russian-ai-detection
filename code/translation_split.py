# -*- coding: utf-8 -*-
"""Нарезка замороженной рукописи на чанки для перевода.

Режет manuscript-final.md по заголовкам уровня 1; секции длиннее MAX_CHARS
дорезаются по ### границам. Пишет чанки в 08-paper/translation-en/src/ и
манифест с sha256 каждого чанка.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "08-paper" / "manuscript-final.md"
OUT = ROOT / "08-paper" / "translation-en" / "src"
MAX_CHARS = 20000

text = SRC.read_text(encoding="utf-8")
lines = text.split("\n")

# границы секций уровня 1
bounds = [i for i, l in enumerate(lines) if l.startswith("# ")] + [len(lines)]
sections = []
for a, b in zip(bounds, bounds[1:]):
    sections.append((lines[a][2:].strip(), lines[a:b]))

chunks = []
for title, body in sections:
    joined = "\n".join(body)
    if len(joined) <= MAX_CHARS:
        chunks.append((title, body))
        continue
    # дорезка по ### внутри секции
    subs = [i for i, l in enumerate(body) if l.startswith("### ")]
    cuts, acc_start = [0], 0
    for s in subs:
        if len("\n".join(body[acc_start:s])) > MAX_CHARS * 0.75:
            cuts.append(s)
            acc_start = s
    cuts.append(len(body))
    for j, (a, b) in enumerate(zip(cuts, cuts[1:])):
        part_title = f"{title} (part {j + 1})"
        chunks.append((part_title, body[a:b]))

OUT.mkdir(parents=True, exist_ok=True)
manifest = []
for n, (title, body) in enumerate(chunks, 1):
    slug = re.sub(r"[^\w]+", "-", title.lower()).strip("-")[:40]
    name = f"chunk-{n:02d}-{slug}.md"
    content = "\n".join(body).rstrip() + "\n"
    (OUT / name).write_text(content, encoding="utf-8", newline="\n")
    manifest.append({
        "file": name,
        "title": title,
        "chars": len(content),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    })

(OUT.parent / "chunks-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
for m in manifest:
    print(f"{m['file']}: {m['chars']} зн.")
print(f"итого чанков: {len(manifest)}, сумма знаков: {sum(m['chars'] for m in manifest)} (исходник {len(text)})")
