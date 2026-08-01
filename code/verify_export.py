#!/usr/bin/env python3
"""Проверка целостности и состава публикации.

    python code/verify_export.py

Сверяет SHA256SUMS, структуру каталогов, отсутствие запрещённых путей и наличие
индекса артефактов со статусами.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


sums = (ROOT / "SHA256SUMS").read_text(encoding="utf-8").strip().split("\n")
mismatch, missing = [], []
for line in sums:
    digest, rel = line.split("  ", 1)
    f = ROOT / rel
    if not f.exists():
        missing.append(rel)
        continue
    if hashlib.sha256(f.read_bytes()).hexdigest() != digest:
        mismatch.append(rel)
check("файлы из SHA256SUMS на месте", not missing, f"нет {len(missing)}")

listed = {line.split("  ", 1)[1] for line in sums}
present = {f.relative_to(ROOT).as_posix() for f in ROOT.rglob("*")
           if f.is_file() and f.name != "SHA256SUMS" and ".git" not in f.parts}
uncovered = sorted(present - listed)
check("каждый файл покрыт SHA256SUMS", not uncovered,
      f"вне суммы {len(uncovered)}: {uncovered[:3]}")

listed = {line.split("  ", 1)[1] for line in sums}
present = {f.relative_to(ROOT).as_posix() for f in ROOT.rglob("*")
           if f.is_file() and f.name != "SHA256SUMS" and ".git" not in f.parts}
uncovered = sorted(present - listed)
check("каждый файл покрыт SHA256SUMS", not uncovered,
      f"вне суммы {len(uncovered)}: {uncovered[:3]}")
check("хеши совпадают", not mismatch, f"расходятся {len(mismatch)}")

for d in ("code", "data", "results", "paper", "preregistration", "specs", "vendor",
          "docs"):
    check(f"каталог {d}", (ROOT / d).is_dir())

for f in ("README.md", "REPRODUCIBILITY.md", "DATA_AVAILABILITY.md", "LICENSE.md",
          "LICENSE-CODE", "LICENSE-DATA", "CITATION.cff", "requirements-lock.txt",
          "ARTIFACT_INDEX.json", "ARTIFACT_INDEX.md"):
    check(f"документ {f}", (ROOT / f).is_file())

forbidden = [p.as_posix() for p in ROOT.rglob("*")
             if p.is_file() and re.search(r"private|author-pseudonyms|"
                                          r"pseudonymization-secret", p.name)]
check("приватных файлов нет", not forbidden, str(forbidden[:3]))

paths = []
for f in ROOT.rglob("*"):
    if f.is_file() and f.suffix in {".py", ".md", ".json", ".csv", ".txt"}:
        try:
            if re.search(r"[A-Za-z]:\\Users|[A-Za-z]:/Users", f.read_text(encoding="utf-8")):
                paths.append(f.name)
        except (UnicodeDecodeError, OSError):
            continue
check("абсолютных локальных путей нет", not paths, str(paths[:3]))

index = json.loads((ROOT / "ARTIFACT_INDEX.json").read_text(encoding="utf-8"))
check("индекс покрывает результаты", len(index["entries"]) > 200,
      f"{len(index['entries'])} записей")
check("действующие результаты помечены",
      index["counts"].get("current", 0) > 0, str(index["counts"]))

failed = [c for c in checks if not c[1]]
print(f"проверок: {len(checks)}, непройденных: {len(failed)}")
for name, ok, detail in failed:
    print(f"  ПРОВАЛ: {name}" + (f" — {detail}" if detail else ""))
sys.exit(1 if failed else 0)
