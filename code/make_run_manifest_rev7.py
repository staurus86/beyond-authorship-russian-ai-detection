#!/usr/bin/env python3
"""Ревизия 7 манифеста допуска серии v2: перепрогон процедуры 2 на матрице v5-r2.

    python 09-tools/make_run_manifest_rev7.py

Ревизия 6 не изменяется и не удаляется: она помечается `invalidated` отдельной
записью жизненного цикла, а не правкой файла. Новая ревизия наследует её
содержимое, обновляет хеши изменившегося кода и добавляет ссылки на исправленную
матрицу, отчёт аудита и решение об инвалидировании.

Основание — `02-preregistration/amendment-feature-matrix-v5-r2-discourse.md`.
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "07-analysis"
TOOLS = ROOT / "09-tools"
SRC = ANALYSIS / "run-manifest-v2-rev6.json"
DST = ANALYSIS / "run-manifest-v2-rev7.json"
DECISION = ANALYSIS / "invalidation-decision-2026-07-31-p2-discourse.json"
AUDIT = ANALYSIS / "corpus-audit-d04-d05.json"
MATRIX_R2 = ROOT / "06-features" / "feature-matrix-v5-r2.csv"
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
    for path in (SRC, DECISION, AUDIT, MATRIX_R2):
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

    manifest["revision"] = 7
    manifest["created_at"] = now.isoformat(timespec="seconds")
    manifest["supersedes"] = {
        "file": SRC.name, "sha256": sha256_file(SRC),
        "reason": "процедура 2 пересчитывается на исправленной матрице: D04 и "
                  "D05 в feature-matrix-v5.csv посчитаны по текстам до "
                  "коррекции извлечения",
    }
    manifest["artifacts"]["feature-matrix-v5-r2"] = {
        "sha256": sha256_file(MATRIX_R2),
        "note": "исправлены D04 и D05 у 68 документов; прочие значения, "
                "пропуски, метаданные и порядок строк не менялись",
    }
    manifest["invalidation_decision"] = {
        "file": DECISION.name, "sha256": sha256_file(DECISION),
        "status": "invalidated_for_substantive_use",
    }
    manifest["audit"] = {"file": AUDIT.name, "sha256": sha256_file(AUDIT)}
    manifest["recomputes_invalidated_predecessor"] = {
        "series": "clf-v2-valid и clf-v2-legacy",
        "manifests": ["manifests-v2/clf-v2-valid-manifest.json",
                      "manifests-v2/clf-v2-legacy-manifest.json"],
        "decision": DECISION.name,
        "note": "прежние результаты сохраняются как диагностическая история; "
                "связь с преемником оформляется отдельной записью после "
                "успешного завершения нового прогона",
    }
    manifest["scope_rev7"] = {
        "recompute": ["P2a", "P2b", "full", "net", "fairness",
                      "error analysis", "synthesis"],
        "unchanged": ["процедура 1", "процедура 3", "процедура 4"],
        "splits": "переносятся без перераспределения",
    }

    DST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"записано: {DST.name}")
    print(f"  ревизия: {manifest['revision']}, preflight_passed: "
          f"{manifest.get('preflight_passed')}")
    print(f"  обновлено хешей кода: {len(changed)} — {changed}")
    print(f"  добавлены ссылки: матрица v5-r2, аудит, решение об инвалидировании")
    print(f"  sha256 ревизии: {sha256_file(DST)[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
