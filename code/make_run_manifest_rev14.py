#!/usr/bin/env python3
"""Ревизия 14 манифеста допуска серии v2: откат правки D04 и D05.

    python 09-tools/make_run_manifest_rev14.py

Ревизия 13 не изменяется и не удаляется. Повод — откат: аудит и пересчёт D04 и
D05 шли по профилям prep-v4 из-за модульного умолчания, а исходная матрица v5
была корректна. Пересчёт по prep-v5 воспроизводит её побитово: 3764 значения из
3764.

Действующим артефактом снова становится `feature-matrix-v5.csv`. Прогоны серии
v2 восстановлены в статусе `current` отдельными записями жизненного цикла;
прогоны на матрице v5-r2 инвалидированы.

Основание — `07-analysis/rollback-decision-2026-08-01-d04-d05.json`.
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "07-analysis"
TOOLS = ROOT / "09-tools"
SRC = ANALYSIS / "run-manifest-v2-rev13.json"
DST = ANALYSIS / "run-manifest-v2-rev14.json"
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
    for path in (SRC, TEST):
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

    manifest["revision"] = 14
    manifest["created_at"] = now.isoformat(timespec="seconds")
    manifest["supersedes"] = {
        "file": SRC.name, "sha256": sha256_file(SRC),
        "reason": "откат: матрица v5-r2 собрана пересчётом по профилям prep-v4 "
                  "и содержит неверные D04 и D05; действует матрица v5",
    }
    manifest["rollback"] = {
        "decision": "07-analysis/rollback-decision-2026-08-01-d04-d05.json",
        "decision_sha256": sha256_file(
            ROOT / "07-analysis" / "rollback-decision-2026-08-01-d04-d05.json"),
        "evidence": "07-analysis/defect-2026-08-01-d04-d05-wrong-profile.json",
        "active_matrix": "feature-matrix-v5.csv",
        "verification": "пересчёт D04 и D05 по prep-v5 воспроизводит матрицу v5 "
                        "побитово: 3764 значения из 3764, расхождений нет",
        "invalidated": ["feature-matrix-v5-r2.csv",
                        "features-normalized-prep-v5-r2.csv",
                        "genre-percentiles-prep-v5-r2.csv",
                        "corpus-audit-d04-d05.json",
                        "серии clf-v2-*-r2, r2b, r3 и downstream ревизии r3"],
        "reinstated": ["clf-v2-valid", "clf-v2-legacy", "fairness-v2",
                       "error-v2", "synthesis-o1-v2"],
        "root_fix": "build_matrix_v5_r2.recompute() получает версию "
                    "препроцессинга явным аргументом",
    }
    manifest["synthetic_test"] = {
        "file": f"09-tools/{TEST.name}", "sha256": sha256_file(TEST),
        "checks": 26, "failures": 0,
        "note": "прогнан до нового расчёта, корпуса не касается",
    }
    manifest["scope_rev14"] = {
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
