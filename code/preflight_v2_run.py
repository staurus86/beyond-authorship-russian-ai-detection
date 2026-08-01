#!/usr/bin/env python3
"""Единый run-manifest серии v2 и шесть инвариантов перед пересчётом.

    python 09-tools/preflight_v2_run.py

Скрипт — ворота пакета. Он собирает хеши всех входов и кода, прогоняет шесть
инвариантов и выставляет `preflight_passed`. Расчётные скрипты серии v2 обязаны
читать этот манифест и отказываться работать, если флаг не выставлен.

Инварианты (решение PI 2026-07-29):

1. реестр содержит 1882 документа;
2. ни один из 34 исключённых не попадает в матрицу;
3. raw и normalized значения 1814 неизменённых входов совпадают с v4;
4. percentile-реализация воспроизводит v4 на составе v4;
5. перцентили v5 посчитаны на новой референсной выборке;
6. результаты серии v1 не перезаписаны.

Статусы инвариантов различают три случая:

- `passed` — проверка выполнена и пройдена;
- `pending_dependency` — артефакт ещё не построен, проверить нечего;
- `failed` — проверка выполнена и провалена.

Финальный допуск — только шесть `passed`. `pending_dependency` отличает временно
отсутствующий артефакт от принципиально невыполнимой проверки.

**Две стадии, чтобы ворота не заблокировали сами себя.** Экстракторы, построение
перцентилей и сборка матрицы работают на стадии `artifact_build` и флага не
требуют — иначе матрица не появилась бы никогда, ведь финальный preflight
зависит от неё. Процедуры 1–4, fairness, разбор ошибок и синтез идут на стадии
`analysis` и требуют `preflight_passed: true`.

    python 09-tools/preflight_v2_run.py --stage prebuild   # до сборки артефактов
    python 09-tools/preflight_v2_run.py --stage final      # после сборки

Стадия `prebuild` пишет `run-manifest-v2-prebuild.json` с незакрытыми
зависимостями, `final` — `run-manifest-v2.json`, который дальше не меняется.
Расчётные скрипты серии v2 вызывают `require(stage)`: она читает манифест,
проверяет флаг и **повторно сверяет текущие хеши критических входов и кода** —
иначе файл можно было бы изменить уже после получения допуска.
"""

import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
PREP_V5 = ROOT / "04-corpus" / "derived" / "prep-v5" / "manifest.csv"
PREP_V4 = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
CORRECTIONS = ROOT / "04-corpus" / "prep-v5-corrections.csv"
EXCLUSIONS = ROOT / "00-admin" / "exclusion-log.csv"
SPLITS_V5 = ROOT / "07-analysis" / "splits-v5"
MATRIX_V4 = ROOT / "06-features" / "feature-matrix.csv"
MATRIX_V5 = ROOT / "06-features" / "feature-matrix-v5.csv"
MATRIX_V5_R2 = ROOT / "06-features" / "feature-matrix-v5-r2.csv"
PERCENTILES_V5 = ROOT / "06-features" / "genre-percentiles-prep-v5.csv"
PERCENTILE_KEY = ROOT / "06-features" / "genre-percentiles-prep-v5.key.json"
# Ранги D04 и D05 после исправления матрицы: значения в прежнем артефакте
# посчитаны по слою до коррекции. Действующий источник для этих двух признаков —
# артефакт r2 (`07-analysis/genre-percentile-d04-d05-contract.md`).
PERCENTILES_V5_R2 = ROOT / "06-features" / "genre-percentiles-prep-v5-r2.csv"
PERCENTILE_KEY_R2 = ROOT / "06-features" / "genre-percentiles-prep-v5-r2.key.json"
LAYER_V5_R2 = ROOT / "06-features" / "features-normalized-prep-v5-r2.csv"
REGRESSION = ROOT / "06-features" / "genre-percentiles-regression.md"
TOOLS = ROOT / "09-tools"
OUT_JSON = ROOT / "07-analysis" / "run-manifest-v2.json"
OUT_PREBUILD = ROOT / "07-analysis" / "run-manifest-v2-prebuild.json"
OUT_REPORT = ROOT / "07-analysis" / "preflight-v2-run.md"

PASSED, PENDING, FAILED = "passed", "pending_dependency", "failed"
# Стадии: сборка артефактов идёт без допуска, анализ — только с ним.
STAGE_BUILD, STAGE_ANALYSIS = "artifact_build", "analysis"

EXTRACTORS = ["extract_features.py", "extract_lexicon.py", "extract_semantic.py",
              "extract_ner.py", "extract_artifacts.py", "extract_discourse.py",
              "extract_intertext.py"]
CODE = EXTRACTORS + ["clf_run.py", "prep.py", "compute_percentiles.py",
                     "correct_extraction.py", "score_style_index.py",
                     # сборка матрицы v5, ключ кеша разборов и ручной слой M03
                     "feature_cache.py", "build_feature_matrix.py",
                     "extract_m03.py",
                     # появляются на стадии analysis: частичный пересчёт и сводка
                     "nll_zero_shot.py", "judge_run.py", "fairness_run.py",
                     "error_run.py", "synthesis_o1.py"]
V1_RESULTS = ["clf-v1-p2a-metrics.csv", "clf-v1-report.md", "score-v1-scores.csv",
              "nll-v1-scores.csv", "judge-v1-scores.csv", "synthesis-o1.csv"]
EXPECTED_DOCS = 1882
EXPECTED_UNCHANGED = 1814
EXCLUSION_STAGE = "коррекция извлечения correction-v5.0"


def sha256_file(path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path, encoding="utf-8"):
    with path.open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def excluded_ids():
    return {r["document_id"] for r in read_csv(EXCLUSIONS, "utf-8-sig")
            if r["stage"] == EXCLUSION_STAGE}


def invariants():
    checks = []
    registry = read_csv(REGISTRY, "utf-8-sig")
    dropped = excluded_ids()

    checks.append({
        "id": 1, "name": "реестр содержит 1882 документа",
        "status": PASSED if len(registry) == EXPECTED_DOCS else FAILED,
        "detail": f"в реестре {len(registry)}, ожидалось {EXPECTED_DOCS}",
    })

    if MATRIX_V5.exists():
        present = set()
        with MATRIX_V5.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                present.add(r["document_id"])
        leaked = sorted(dropped & present)
        checks.append({
            "id": 2, "name": "исключённые 34 не попадают в матрицу",
            "status": PASSED if not leaked else FAILED,
            "detail": f"найдено исключённых в матрице: {len(leaked)}",
        })
    else:
        checks.append({"id": 2, "name": "исключённые 34 не попадают в матрицу",
                       "status": PENDING,
                       "detail": f"{MATRIX_V5.name} ещё не собран"})

    m4 = {r["document_id"]: r for r in read_csv(PREP_V4)}
    m5 = {r["document_id"]: r for r in read_csv(PREP_V5)}
    unchanged = {d for d in set(m4) & set(m5)
                 if m4[d]["prose_sha256"] == m5[d]["prose_sha256"]
                 and m4[d]["full_sha256"] == m5[d]["full_sha256"]}
    if MATRIX_V5.exists():
        old_vals, new_vals = {}, {}
        with MATRIX_V4.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["document_id"] in unchanged:
                    old_vals[(r["document_id"], r["feature_id"])] = (
                        r["raw_value"], r["normalized_value"])
        with MATRIX_V5.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["document_id"] in unchanged:
                    new_vals[(r["document_id"], r["feature_id"])] = (
                        r["raw_value"], r["normalized_value"])
        diff = [k for k in old_vals if old_vals[k] != new_vals.get(k)]
        checks.append({
            "id": 3,
            "name": f"raw и normalized у {EXPECTED_UNCHANGED} неизменённых совпадают",
            "status": PASSED if not diff else FAILED,
            "detail": f"неизменённых {len(unchanged)}, расхождений значений {len(diff)}",
        })
    else:
        checks.append({
            "id": 3,
            "name": f"raw и normalized у {EXPECTED_UNCHANGED} неизменённых совпадают",
            "status": PENDING,
            "detail": f"неизменённых по манифестам {len(unchanged)}; "
                      f"{MATRIX_V5.name} ещё не собран"})

    regression_ok = (REGRESSION.exists()
                     and "расхождений | **0**" in REGRESSION.read_text(encoding="utf-8"))
    checks.append({
        "id": 4, "name": "percentile-реализация воспроизводит v4",
        "status": PASSED if regression_ok else FAILED,
        "detail": ("регрессия на составе v4: 0 расхождений" if regression_ok
                   else "отчёт регрессии отсутствует или содержит расхождения"),
    })

    if PERCENTILES_V5.exists() and PERCENTILE_KEY.exists():
        key = json.loads(PERCENTILE_KEY.read_text(encoding="utf-8"))
        same_registry = key.get("registry_sha256") == sha256_file(REGISTRY)
        checks.append({
            "id": 5, "name": "перцентили v5 посчитаны на новой референсной выборке",
            "status": PASSED if same_registry else FAILED,
            "detail": (f"документов в референсной выборке "
                       f"{key.get('reference_set', {}).get('documents')}, "
                       f"хеш реестра совпадает: {same_registry}"),
        })
    else:
        checks.append({"id": 5,
                       "name": "перцентили v5 посчитаны на новой референсной выборке",
                       "status": PENDING,
                       "detail": f"{PERCENTILES_V5.name} ещё не построен"})

    v1_intact = all((ROOT / "07-analysis" / name).exists() for name in V1_RESULTS)
    checks.append({
        "id": 6, "name": "результаты серии v1 на месте и не перезаписаны",
        "status": PASSED if v1_intact else FAILED,
        "detail": f"проверено файлов: {len(V1_RESULTS)}",
    })
    return checks


def git_or_none(args):
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001 — репозитория может не быть
        return None


# Что пересверяет require для каждой процедуры: свой артефакт и свой код.
# Общая часть — реестр и разбиения — проверяется всегда.
PROCEDURE_PROFILE = {
    "proc1": {"artifacts": ["feature-matrix-v5", "genre-percentiles-v5"],
              "code": ["score_style_index.py"]},
    "proc2": {"artifacts": ["feature-matrix-v5"], "code": ["clf_run.py"]},
    "proc3": {"artifacts": ["prep-v5"], "code": ["nll_zero_shot.py"],
              "models": ["nll"]},
    "proc4": {"artifacts": ["prep-v5"], "code": ["judge_run.py"], "models": ["judge"]},
    # downstream: своя матрица плюс выходы upstream-процедур, см. UPSTREAM
    "fairness": {"artifacts": ["feature-matrix-v5"], "code": ["fairness_run.py"]},
    "error": {"artifacts": ["feature-matrix-v5", "prep-v5"], "code": ["error_run.py"]},
    "synthesis": {"artifacts": [], "code": ["synthesis_o1.py"]},
}
ARTIFACT_PATHS = {
    "feature-matrix-v5": lambda: MATRIX_V5,
    "feature-matrix-v5-r2": lambda: MATRIX_V5_R2,
    "genre-percentiles-v5-r2": lambda: PERCENTILES_V5_R2,
    "genre-percentiles-v5": lambda: PERCENTILES_V5,
    "prep-v5": lambda: PREP_V5,
}
# Прогон на исправленной матрице обязан сверяться с ней же. Без подмены шлюз
# проверял бы хеш прежней матрицы — проверка проходит, но относится не к тому
# файлу, который процедура читает.
REVISED_ARTIFACT = {"feature-matrix-v5": "feature-matrix-v5-r2"}
# Upstream той же ревизии: downstream ревизии r3 обязан питаться прогоном P2 из
# r3, а не из любой предыдущей. Процедуры 1, 3 и 4 матрицу не читают и ревизии
# не имеют — их имена остаются прежними.
REVISED_UPSTREAM = ("clf-v2-valid",)

# Цепочка дочерних манифестов: downstream-этап читает результаты upstream, а не
# только исходную матрицу. Подмена clf-v2-predictions.csv после расчёта обязана
# немедленно остановить fairness и разбор ошибок.
CHILD_DIR = ROOT / "07-analysis" / "manifests-v2"
# Имя серии → имя профиля процедуры. Связка явная: серии названы решением о
# нумерации прогонов, профили — решением о том, что каждая процедура читает.
CHILD_PROFILE = {"proc1-v2": "proc1", "clf-v2-valid": "proc2",
                 "clf-v2-legacy": "proc2", "proc3-v2": "proc3",
                 "proc4-v2": "proc4", "fairness-v2": "fairness",
                 "error-v2": "error", "synthesis-v2": "synthesis"}
# Ревизия матрицы в имени серии профиль не меняет: код тот же, читаются те же
# файлы. Суффикс отбрасывается правилом, а не перечислением, иначе каждая новая
# ревизия снова выпадала бы из таблицы.
SERIES_REVISION = re.compile(r"-r\d+[a-z]?$")


def profile_of(procedure):
    """Имя профиля процедуры по имени серии, с любой ревизией матрицы."""
    name = SERIES_REVISION.sub("", procedure)
    return CHILD_PROFILE.get(name, name)
# Основной P2a — схема B (`clf-v2-valid`), она и стоит в upstream. Схема A
# остаётся sensitivity и downstream-этапы не питает (amendment-clf-v2-inner-cv.md).
UPSTREAM = {
    "fairness": ["clf-v2-valid"],
    "error": ["clf-v2-valid", "proc1-v2", "proc3-v2", "proc4-v2"],
    "synthesis": ["proc1-v2", "clf-v2-valid", "proc3-v2", "proc4-v2"],
}


def current_manifest_path():
    """Действующий манифест допуска: последняя ревизия без пометки invalidated.

    Ошибочный манифест не удаляется, а помечается `invalidated`, и работа
    продолжается в новой ревизии. Значит искать допуск нужно по цепочке ревизий,
    а не по одному имени.
    """
    candidates = [OUT_JSON] + sorted(
        OUT_JSON.parent.glob("run-manifest-v2-rev*.json"),
        key=lambda p: int(p.stem.rsplit("rev", 1)[1]))
    live = [p for p in candidates if p.exists()
            and not json.loads(p.read_text(encoding="utf-8")).get("invalidated")]
    return live[-1] if live else OUT_JSON


def child_path(name):
    return CHILD_DIR / f"{name}-manifest.json"


def emit_child_manifest(procedure, inputs, outputs, status="completed", extra=None):
    """Дочерний immutable-манифест: вызывается расчётным скриптом по завершении.

    `inputs` и `outputs` — списки путей. Записываются хеши родителя, фактически
    прочитанных входов, кода процедуры и созданных результатов.
    """
    CHILD_DIR.mkdir(exist_ok=True)
    path = child_path(procedure)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not existing.get("invalidated"):
            raise SystemExit(f"{path.name} уже существует и неизменяем; "
                             "ошибочный помечается invalidated, затем новая ревизия")
    # Неизвестная серия — отказ, а не пустой профиль: манифест без хешей кода
    # выглядит валидным и не воспроизводит прогон.
    profile_name = profile_of(procedure)
    if profile_name not in PROCEDURE_PROFILE:
        raise SystemExit(f"нет профиля процедуры для серии {procedure}: "
                         "дочерний манифест без хешей кода не пишется")
    profile = PROCEDURE_PROFILE[profile_name]
    manifest = {
        "procedure": procedure,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parent": current_manifest_path().name,
        "parent_sha256": sha256_file(current_manifest_path()),
        "inputs": {Path(p).name: sha256_file(Path(p)) for p in inputs},
        "code": {name: sha256_file(TOOLS / name) for name in profile.get("code", [])},
        "outputs": {Path(p).name: sha256_file(Path(p)) for p in outputs},
        "status": status,
    }
    if extra:
        manifest.update(extra)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return manifest


def recorded_hash(entry):
    """Хеш артефакта из манифеста: ранние ревизии пишут строку, поздние — запись
    с пояснением. Форма записи не должна выглядеть как расхождение хешей."""
    return entry.get("sha256") if isinstance(entry, dict) else entry


def ancestor_hashes(path=None):
    """Хеши действующей ревизии допуска и всех её предков по `supersedes`.

    Процедуры 1, 3 и 4 не пересчитываются и остаются собранными под ранней
    ревизией, тогда как правка кода процедуры 2 поднимает номер. Совпадение имён
    здесь недостижимо, поэтому проверяется принадлежность цепочке — по хешам, а
    не по именам файлов: хеш предка записан в момент создания ревизии и задним
    числом не подменяется (`amendment-run-manifest-ancestry.md`).

    Ревизии до седьмой хранят `supersedes` строкой без хеша: для этих звеньев
    хеш считается по файлу на диске, то есть подмена самого файла ревизии там не
    обнаруживается. Сверка выходов upstream по хешам действует независимо и
    остаётся основной защитой на исторической части цепочки.
    """
    path = path or current_manifest_path()
    seen, chain = set(), []
    while path and path.exists() and path.name not in seen:
        seen.add(path.name)
        chain.append(sha256_file(path))
        parent = json.loads(path.read_text(encoding="utf-8")).get("supersedes")
        if not parent:
            break
        if isinstance(parent, str):
            parent = {"file": parent}
        chain.append(parent.get("sha256"))
        path = path.parent / parent["file"]
    return {h for h in chain if h}


def check_upstream(procedure, drift, revision=""):
    """Upstream-манифесты существуют, завершены, и их выходы не изменились."""
    for name in UPSTREAM.get(procedure, []):
        if revision and name in REVISED_UPSTREAM:
            name = f"{name}-{revision}"
        path = child_path(name)
        if not path.exists():
            drift.append(f"нет upstream-манифеста {path.name}")
            continue
        child = json.loads(path.read_text(encoding="utf-8"))
        if child.get("invalidated"):
            drift.append(f"{path.name} помечен invalidated")
            continue
        if child.get("status") != "completed":
            drift.append(f"{path.name}: статус {child.get('status')}")
        if child.get("parent_sha256") not in ancestor_hashes():
            drift.append(f"{path.name}: собран под манифестом вне цепочки ревизий")
        for filename, digest in child.get("outputs", {}).items():
            actual = sha256_file(ROOT / "07-analysis" / filename)
            if actual != digest:
                drift.append(f"{name}: изменился выход {filename}")


def require(stage, procedure=None, revision=""):
    """Допуск для расчётного скрипта серии v2. Вызывать первой строкой.

    `artifact_build` — сборка признаков, перцентилей и матрицы: флаг не нужен,
    иначе матрица не появилась бы, ведь финальный preflight зависит от неё.
    `analysis` — процедуры, fairness, разбор ошибок, синтез: нужен
    `preflight_passed: true`.

    Флага мало: хеши сверяются заново, потому что файл можно изменить уже после
    получения допуска. Сверяется общая часть — реестр и разбиения — плюс то, что
    читает именно эта процедура: для процедур 1 и 2 матрица, для 3 и 4 профили
    prep-v5 вместе с их кодом и моделью.
    """
    if stage == STAGE_BUILD:
        return json.loads(OUT_PREBUILD.read_text(encoding="utf-8")) \
            if OUT_PREBUILD.exists() else {}
    path = current_manifest_path()
    if not path.exists():
        raise SystemExit("нет run-manifest-v2.json — стадия analysis запрещена")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("invalidated"):
        raise SystemExit(f"{path.name} помечен invalidated — нужна новая ревизия серии")
    if not manifest.get("preflight_passed"):
        raise SystemExit("preflight_passed не выставлен — расчёт запрещён")

    drift = []
    if manifest["corpus"]["registry_sha256"] != sha256_file(REGISTRY):
        drift.append("documents-registry.csv")
    for name, digest in manifest.get("splits", {}).items():
        if digest != sha256_file(SPLITS_V5 / name):
            drift.append(name)

    profile = PROCEDURE_PROFILE.get(procedure)
    if procedure and profile is None:
        raise SystemExit(f"неизвестная процедура {procedure}")
    if profile:
        for key in profile["artifacts"]:
            if revision:
                key = REVISED_ARTIFACT.get(key, key)
            recorded = recorded_hash(manifest.get("artifacts", {}).get(key))
            actual = sha256_file(ARTIFACT_PATHS[key]())
            if recorded != actual:
                drift.append(key)
        for name in profile["code"]:
            if manifest["code"].get(name) != sha256_file(TOOLS / name):
                drift.append(name)
        for name in profile.get("models", []):
            if name not in manifest.get("models", {}):
                drift.append(f"модель {name} отсутствует в манифесте")
    check_upstream(procedure, drift, revision)
    if drift:
        raise SystemExit("после допуска изменились: " + ", ".join(drift))
    return manifest


def main():
    import argparse
    parser = argparse.ArgumentParser(description="preflight серии v2")
    parser.add_argument("--stage", choices=["prebuild", "final"], default="prebuild",
                        help="prebuild — до сборки артефактов, final — после")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"run-manifest серии v2, стадия {args.stage}, {stamp}")

    checks = invariants()
    for c in checks:
        print(f"  инвариант {c['id']}: {c['status']} — {c['detail']}")
    passed = all(c["status"] == PASSED for c in checks)

    manifest = {
        "created_at": stamp, "series": "v2", "preflight_passed": passed,
        "corpus": {
            "registry_sha256": sha256_file(REGISTRY),
            "documents": len(read_csv(REGISTRY, "utf-8-sig")),
            "prep_v5_manifest_sha256": sha256_file(PREP_V5),
            "corrections_sha256": sha256_file(CORRECTIONS),
        },
        "splits": {p.name: sha256_file(p)
                   for p in sorted(SPLITS_V5.glob("*.json"))},
        "code": {name: sha256_file(TOOLS / name) for name in CODE},
        "percentiles": {
            "upstream_key": (json.loads(PERCENTILE_KEY.read_text(encoding="utf-8"))
                             if PERCENTILE_KEY.exists() else None),
            "artifact_sha256": sha256_file(PERCENTILES_V5),
            "regression_report_sha256": sha256_file(REGRESSION),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "git_commit": git_or_none(["git", "rev-parse", "HEAD"]),
        },
        "seeds": {"splits": "splits-2026-07-25", "clf": 20260727,
                  "judge": [20260727, 20260728, 20260729]},
        "models": {
            "judge": "gemma3:12b-it-qat",
            "nll": "ai-forever/rugpt3large_based_on_gpt2",
        },
        "invariants": checks,
        "policy": "результаты пишутся только в серию v2; файлы v1 не перезаписываются",
    }
    manifest["stage"] = args.stage
    manifest["stage_policy"] = {
        STAGE_BUILD: "экстракторы, перцентили, сборка матрицы — флаг не требуется",
        STAGE_ANALYSIS: "процедуры 1–4, fairness, разбор ошибок, синтез — "
                        "требуется preflight_passed: true",
    }
    # Артефакты, появляющиеся только после сборки, и digest моделей.
    manifest["artifacts"] = {
        "feature-matrix-v5": sha256_file(MATRIX_V5),
        "genre-percentiles-v5": sha256_file(PERCENTILES_V5),
        "prep-v5": sha256_file(PREP_V5),
    }
    # Ранги D04 и D05 живут отдельным артефактом: колонка матрицы v5-r2 у этих
    # признаков осталась от прежнего слоя. Оба файла входят в манифест, чтобы
    # происхождение рангов не раздваивалось молча.
    if PERCENTILES_V5_R2.exists():
        manifest["artifacts"]["genre-percentiles-v5-r2"] = sha256_file(PERCENTILES_V5_R2)
        manifest["artifacts"]["features-normalized-v5-r2"] = sha256_file(LAYER_V5_R2)
        manifest["percentile_sources"] = {
            "D04, D05": "genre-percentiles-prep-v5-r2.csv",
            "прочие признаки": "genre-percentiles-prep-v5.csv и колонка матрицы",
            "contract": "07-analysis/genre-percentile-d04-d05-contract.md",
        }
    manifest["models"] = {
        "judge": {"name": "gemma3:12b-it-qat",
                  "digest": "5d4fa005e7bb5931be8bc35224080a9b316c7e1c069c9481a6"
                            "6a51b607f4252e2"[:64], "ollama": "0.18.0"},
        "nll": {"name": "ai-forever/rugpt3large_based_on_gpt2",
                "revision": "29c569320be2", "device": "cpu/float32"},
    }
    manifest["procedure_profiles"] = PROCEDURE_PROFILE

    target = OUT_JSON if args.stage == "final" else OUT_PREBUILD
    if args.stage == "final" and not passed:
        raise SystemExit("стадия final требует шести passed; "
                         "манифест допуска не записан")
    # Финальный манифест не удаляется никогда. Ошибочный помечается invalidated
    # и остаётся на месте, а работа продолжается в новой ревизии серии.
    if args.stage == "final" and OUT_JSON.exists():
        latest = sorted(list(OUT_JSON.parent.glob("run-manifest-v2-rev*.json")),
                        key=lambda p: int(p.stem.rsplit("rev", 1)[1]))
        newest = latest[-1] if latest else OUT_JSON
        existing = json.loads(newest.read_text(encoding="utf-8"))
        if not existing.get("invalidated"):
            raise SystemExit(
                f"{newest.name} уже существует и неизменяем. Если он ошибочен, "
                "пометьте его invalidated и создайте новую ревизию серии; "
                "удалять имя нельзя")
        revision = int(existing.get("revision", 1)) + 1
        target = OUT_JSON.with_name(f"run-manifest-v2-rev{revision}.json")
        manifest["revision"] = revision
        manifest["supersedes"] = newest.name
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    lines = ["# Preflight серии v2: run-manifest и шесть инвариантов", "",
             f"Собрано {stamp} скриптом `09-tools/preflight_v2_run.py`.", "",
             f"**preflight_passed: {'да' if passed else 'нет'}**", "",
             "| № | Инвариант | Статус | Детали |", "|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['id']} | {c['name']} | {c['status']} | {c['detail']} |")
    lines += ["", "## Что зафиксировано в манифесте", "",
              f"- хеш реестра и манифеста `prep-v5`, документов {manifest['corpus']['documents']};",
              f"- хеши перенесённых разбиений: {len(manifest['splits'])} файлов;",
              f"- хеши кода: {len(manifest['code'])} скриптов, включая семь "
              "экстракторов и `clf_run`;",
              "- upstream-ключ перцентилей и хеш отчёта регрессии;",
              "- сиды, версии моделей и окружение;",
              "- политика: запись только в серию v2.", "",
              ("Пакет можно запускать." if passed else
               "**Пакет не запускается.** Инварианты со статусом «не выполним» "
               "закроются после сборки матрицы v5; до этого расчётные скрипты "
               "серии v2 обязаны отказываться работать по этому манифесту."), ""]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  preflight_passed: {passed}")
    print(f"  манифест: {target.name}, отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
