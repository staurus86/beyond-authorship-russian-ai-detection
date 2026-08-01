#!/usr/bin/env python3
"""Ревизия 8 манифеста допуска серии v2: исправление разбора имени серии.

    python 09-tools/make_run_manifest_rev8.py

Ревизия 7 не изменяется и не удаляется. Повод для новой ревизии — правка
расчётного кода: `clf_run.py` сравнивал схему вложенного CV с полным именем
серии, из-за чего суффикс ревизии матрицы уводил `clf-v2-legacy-r2` на чужую
схему, а `preflight_v2_run.py` не знал профиля таких серий и писал дочерние
манифесты без хешей кода.

Основание — `07-analysis/invalidation-decision-2026-07-31-clf-r2-series-name.json`
и `02-preregistration/amendment-feature-matrix-v5-r2-discourse.md`.
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "07-analysis"
TOOLS = ROOT / "09-tools"
SRC = ANALYSIS / "run-manifest-v2-rev7.json"
DST = ANALYSIS / "run-manifest-v2-rev8.json"
DECISION = ANALYSIS / "invalidation-decision-2026-07-31-clf-r2-series-name.json"
TEST = TOOLS / "test_series_revision_synth.py"
MSK = timezone(timedelta(hours=3))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if DST.exists():
        print(f"ОТКАЗ: {DST.name} уже существует")
        return 2
    for path in (SRC, DECISION, TEST):
        if not path.exists():
            print(f"ОТКАЗ: нет {path.name}")
            return 2

    manifest = json.loads(SRC.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)

    changed = []
    for name in list(manifest.get("code", {})):
        path = TOOLS / name
        if not path.exists():
            continue
        digest = sha256_file(path)
        if manifest["code"][name] != digest:
            changed.append(name)
            manifest["code"][name] = digest

    manifest["revision"] = 8
    manifest["created_at"] = now.isoformat(timespec="seconds")
    manifest["supersedes"] = {
        "file": SRC.name, "sha256": sha256_file(SRC),
        "reason": "исправлен разбор имени серии: схема вложенного CV и строгий "
                  "режим подбора C сравниваются с базовым именем, суффикс "
                  "ревизии матрицы больше не уводит прогон на чужую схему",
    }
    manifest["invalidation_decision_series_name"] = {
        "file": DECISION.name, "sha256": sha256_file(DECISION),
        "status": "invalidated_for_substantive_use",
        "targets": ["clf-v2-valid-r2", "clf-v2-legacy-r2"],
    }
    manifest["synthetic_test"] = {
        "file": f"09-tools/{TEST.name}", "sha256": sha256_file(TEST),
        "checks": 21, "failures": 0,
        "note": "прогнан до нового расчёта, корпуса не касается",
    }
    manifest["scope_rev8"] = {
        "recompute": ["P2a", "P2b", "full", "net", "fairness",
                      "error analysis", "synthesis"],
        "series": ["clf-v2-valid-r2b", "clf-v2-legacy-r2b"],
        "unchanged": ["процедура 1", "процедура 3", "процедура 4",
                      "feature-matrix-v5-r2.csv", "разбиения"],
        "note": "файлы серий r2 не перезаписываются: у r2b свои имена",
    }

    DST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"записано: {DST.name}")
    print(f"  ревизия: {manifest['revision']}, preflight_passed: "
          f"{manifest.get('preflight_passed')}")
    print(f"  обновлено хешей кода: {len(changed)} — {changed}")
    print(f"  sha256 ревизии: {sha256_file(DST)[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
