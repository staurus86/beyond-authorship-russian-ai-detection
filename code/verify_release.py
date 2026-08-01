#!/usr/bin/env python3
"""Все доступные проверки релиза одной командой.

    python code/verify_release.py

Запускает проверку целостности и проверку псевдонимизации. Расчёты, требующие
недоступных исходных текстов или локальных моделей, сюда не входят: уровни
воспроизводимости описаны в REPRODUCIBILITY.md.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = [
    ("целостность и состав", "verify_export.py"),
    ("псевдонимизация", "verify_pseudonymization.py"),
]

failed = []
for title, script in STEPS:
    print(f"\n=== {title} ===")
    proc = subprocess.run([sys.executable, str(ROOT / "code" / script)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip())
    if proc.returncode != 0:
        failed.append(title)

print()
if failed:
    print("НЕ ПРОЙДЕНО:", ", ".join(failed))
    sys.exit(1)
print("все доступные проверки пройдены")
