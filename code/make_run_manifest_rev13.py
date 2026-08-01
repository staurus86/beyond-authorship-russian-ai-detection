#!/usr/bin/env python3
"""Ревизия 13 манифеста допуска серии v2: ранги D04 и D05 отдельным артефактом.

    python 09-tools/make_run_manifest_rev13.py

Ревизия 12 не изменяется и не удаляется. Повод — она вышла неполной: ревизия
собирается копированием предыдущей, а хеши новых артефактов появляются в блоке
`artifacts` только при запуске preflight. Пересчёт рангов D04 и D05 туда попал
описанием, но без хешей слоя и артефакта рангов.

Ревизия 13 вносит эти хеши явно и называет действующий источник рангов для
каждого признака.

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
SRC = ANALYSIS / "run-manifest-v2-rev12.json"
DST = ANALYSIS / "run-manifest-v2-rev13.json"
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

    manifest["revision"] = 13
    manifest["created_at"] = now.isoformat(timespec="seconds")
    manifest["supersedes"] = {
        "file": SRC.name, "sha256": sha256_file(SRC),
        "reason": "в ревизии 12 не оказалось хешей нового слоя и артефакта "
                  "рангов: она собрана копированием предыдущей",
    }
    manifest["percentile_recompute"] = {
        "contract": "07-analysis/genre-percentile-d04-d05-contract.md",
        "contract_sha256": sha256_file(
            ROOT / "07-analysis" / "genre-percentile-d04-d05-contract.md"),
        "record": "07-analysis/genre-percentile-d04-d05-recompute.json",
        "record_sha256": sha256_file(
            ROOT / "07-analysis" / "genre-percentile-d04-d05-recompute.json"),
        "test": "09-tools/test_features_v5_r2_synth.py",
        "test_sha256": sha256_file(TOOLS / "test_features_v5_r2_synth.py"),
        "checks": 17, "failures": 0,
        "ranks_changed": 817, "by_feature": {"D04": 487, "D05": 330},
        "index_features_affected": 0,
        "source_of_ranks": {"D04, D05": "genre-percentiles-prep-v5-r2.csv",
                            "прочие": "genre-percentiles-prep-v5.csv"},
        "matrix_untouched": "feature-matrix-v5-r2.csv не перезаписан; его колонка "
                            "genre_percentile у D04 и D05 непригодна для расчёта",
    }
    manifest["synthetic_test"] = {
        "file": f"09-tools/{TEST.name}", "sha256": sha256_file(TEST),
        "checks": 26, "failures": 0,
        "note": "прогнан до нового расчёта, корпуса не касается",
    }
    for name, path in (("genre-percentiles-v5-r2",
                        ROOT / "06-features" / "genre-percentiles-prep-v5-r2.csv"),
                       ("features-normalized-v5-r2",
                        ROOT / "06-features" / "features-normalized-prep-v5-r2.csv"),
                       ("genre-percentiles-v5-r2-key",
                        ROOT / "06-features" / "genre-percentiles-prep-v5-r2.key.json")):
        manifest["artifacts"][name] = sha256_file(path)
    manifest["percentile_sources"] = {
        "D04, D05": "genre-percentiles-prep-v5-r2.csv",
        "прочие признаки": "genre-percentiles-prep-v5.csv и колонка матрицы",
        "contract": "07-analysis/genre-percentile-d04-d05-contract.md",
    }
    manifest["scope_rev13"] = {
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
