#!/usr/bin/env python3
"""Ревизия 11 манифеста допуска серии v2: допуск upstream по цепочке ревизий.

    python 09-tools/make_run_manifest_rev11.py

Ревизия 10 не изменяется и не удаляется. Повод — решение PI о допуске upstream
по цепочке ревизий: процедуры 1, 3 и 4 не пересчитываются и остаются собранными
под шестой ревизией, тогда как правки кода процедуры 2 подняли номер до десятой.
Совпадение имён родительского манифеста стало недостижимым.

Проверка выходов upstream по хешам, статуса `completed` и пометки `invalidated`
сохраняется без изменений.

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
SRC = ANALYSIS / "run-manifest-v2-rev10.json"
DST = ANALYSIS / "run-manifest-v2-rev11.json"
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

    manifest["revision"] = 11
    manifest["created_at"] = now.isoformat(timespec="seconds")
    manifest["supersedes"] = {
        "file": SRC.name, "sha256": sha256_file(SRC),
        "reason": "upstream допускается, если его родительский манифест — "
                  "действующая ревизия либо её предок по цепочке supersedes, "
                  "при совпадении хешей всех выходов upstream",
    }
    manifest["upstream_rule"] = {
        "amendment": "02-preregistration/amendment-run-manifest-ancestry.md",
        "amendment_sha256": sha256_file(
            ROOT / "02-preregistration" / "amendment-run-manifest-ancestry.md"),
        "test": "09-tools/test_manifest_ancestry_synth.py",
        "test_sha256": sha256_file(TOOLS / "test_manifest_ancestry_synth.py"),
        "checks": 13, "failures": 0,
        "limitation": "ревизии до седьмой хранят supersedes без хеша: для этих "
                      "звеньев цепочка опирается на файл на диске",
    }
    manifest["synthetic_test"] = {
        "file": f"09-tools/{TEST.name}", "sha256": sha256_file(TEST),
        "checks": 26, "failures": 0,
        "note": "прогнан до нового расчёта, корпуса не касается",
    }
    manifest["scope_rev11"] = {
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
