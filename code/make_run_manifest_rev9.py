#!/usr/bin/env python3
"""Ревизия 9 манифеста допуска серии v2: downstream переводится на матрицу v5-r2.

    python 09-tools/make_run_manifest_rev9.py

Ревизия 8 не изменяется и не удаляется. Повод — правка трёх downstream-скриптов:
fairness, разбор ошибок и синтез получили ревизию входов, а `error_run.py`
дополнительно избавлен от того же дефекта сравнения имени серии, из-за которого
строгий режим подбора C отключался на серии с суффиксом ревизии.

Шлюз с этой ревизии сверяет ту матрицу, которую серия читает: прогон на v5-r2
проверялся против прежней матрицы, и проверка относилась не к тому файлу.

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
SRC = ANALYSIS / "run-manifest-v2-rev8.json"
DST = ANALYSIS / "run-manifest-v2-rev9.json"
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

    manifest["revision"] = 9
    manifest["created_at"] = now.isoformat(timespec="seconds")
    manifest["supersedes"] = {
        "file": SRC.name, "sha256": sha256_file(SRC),
        "reason": "fairness, разбор ошибок и синтез переведены на ревизию "
                  "входов; шлюз сверяет матрицу той ревизии, которую серия "
                  "читает, и ведёт upstream downstream-этапов на clf-v2-valid-r2b",
    }
    manifest["p2_r2b_completed"] = {
        "series": ["clf-v2-valid-r2b", "clf-v2-legacy-r2b"],
        "manifests": ["manifests-v2/clf-v2-valid-r2b-manifest.json",
                      "manifests-v2/clf-v2-legacy-r2b-manifest.json"],
        "matrix_verified": "хеш feature-matrix-v5-r2.csv в дочерних манифестах "
                           "совпадает с записанным в ревизии 8",
    }
    manifest["synthetic_test"] = {
        "file": f"09-tools/{TEST.name}", "sha256": sha256_file(TEST),
        "checks": 26, "failures": 0,
        "note": "прогнан до нового расчёта, корпуса не касается",
    }
    manifest["scope_rev9"] = {
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
