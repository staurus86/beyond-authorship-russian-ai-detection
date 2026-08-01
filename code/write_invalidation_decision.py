#!/usr/bin/env python3
"""Неизменяемое решение об инвалидировании процедуры 2 и её downstream.

    python 09-tools/write_invalidation_decision.py

Записывается **до** нового прогона и содержит только то, что известно на момент
решения: хеши прежних манифестов, причину, область допустимого использования и
идентификатор запланированного преемника. Хеш преемника здесь не появляется —
результата ещё нет, и записывать неизвестное значение нельзя.

Связь «старое — новое» оформляется отдельной записью после успешного завершения
каждой процедуры.

Статус `invalidated`, а не `superseded`: прежний результат содержит дефект входов,
а не просто заменён более новым. Поэтому преемник у него логически не обязателен —
если новый расчёт не состоится, прежний всё равно остаётся непригодным для
содержательных выводов.

Основание — `02-preregistration/amendment-feature-matrix-v5-r2-discourse.md`.
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "07-analysis"
MANIFESTS = ANALYSIS / "manifests-v2"
OUT = ANALYSIS / "invalidation-decision-2026-07-31-p2-discourse.json"
MSK = timezone(timedelta(hours=3))

PLANNED_SUCCESSOR = "p2-recompute-matrix-v5-r2"

TARGETS = [
    ("clf-v2-valid-manifest.json", "P2a: восемнадцать моделей, основной результат"),
    ("clf-v2-legacy-manifest.json", "P2a: sensitivity-схема inner CV"),
    ("fairness-v2-manifest.json", "fairness как downstream P2"),
    ("error-v2-manifest.json", "разбор ошибок как downstream P2"),
    ("synthesis-v2-manifest.json", "синтез O1 как потребитель P2"),
]

UNAFFECTED = [
    ("proc1-v2-manifest.json", "процедура 1: индекс не читает D04 и D05"),
    ("proc3-v2-manifest.json", "процедура 3: NLL считается по тексту"),
    ("proc4-v2-manifest.json", "процедура 4: судья получает текст"),
]

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
    if OUT.exists():
        print(f"ОТКАЗ: {OUT.name} уже существует и неизменяем")
        return 2

    invalidated, missing = [], []
    for name, role in TARGETS:
        path = MANIFESTS / name
        if not path.exists():
            missing.append(name)
            continue
        invalidated.append({"manifest": f"manifests-v2/{name}", "role": role,
                            "sha256": sha256_file(path)})
    if missing:
        print(f"ОТКАЗ: не найдены манифесты {missing}")
        return 2

    unaffected = [{"manifest": f"manifests-v2/{name}", "reason": reason,
                   "sha256": sha256_file(MANIFESTS / name),
                   "lifecycle_status": "current"}
                  for name, reason in UNAFFECTED
                  if (MANIFESTS / name).exists()]

    now = datetime.now(timezone.utc)
    decision = {
        "decision": "инвалидирование процедуры 2 и её downstream",
        "lifecycle_status": "invalidated",
        "status": "invalidated_for_substantive_use",
        "recorded_before_new_metrics": True,
        "note": "решение принято до запуска нового прогона и до просмотра любых "
                "его метрик",
        "reason": "D04 и D05 в feature-matrix-v5.csv посчитаны по текстам до "
                  "коррекции извлечения correction-v5.0. Расхождение с профилями "
                  "prep-v5 у 68 документов, все входят и в train, и в test хотя "
                  "бы одного из восемнадцати holdout. Оба признака входят в "
                  "FEATURES_CORE, поэтому дефект затрагивает обучение и оценку "
                  "процедуры 2 и всё, что от неё зависит",
        "evidence": {
            "audit": "07-analysis/corpus-audit-d04-d05.json",
            "audit_sha256": sha256_file(ANALYSIS / "corpus-audit-d04-d05.json"),
            "amendment": "02-preregistration/"
                         "amendment-feature-matrix-v5-r2-discourse.md",
            "amendment_sha256": sha256_file(
                ROOT / "02-preregistration"
                / "amendment-feature-matrix-v5-r2-discourse.md"),
            "corrected_artifact": "06-features/feature-matrix-v5-r2.csv",
            "corrected_artifact_sha256": sha256_file(
                ROOT / "06-features" / "feature-matrix-v5-r2.csv"),
        },
        "scope": {
            "historical_diagnostics": "разрешено: результаты сохраняются как "
                                      "диагностическая история",
            "substantive_conclusions": "запрещено: выводы и числа статьи на этих "
                                       "результатах не строятся",
            "files": "не удаляются и не перезаписываются; прежние манифесты не "
                     "изменяются",
        },
        "invalidated": invalidated,
        "unaffected": unaffected,
        "planned_successor_run_id": PLANNED_SUCCESSOR,
        "successor_hash": None,
        "successor_note": "хеш преемника здесь не записывается: результата ещё "
                          "нет. Связь оформляется отдельной записью после "
                          "успешного завершения каждой процедуры. Если новый "
                          "расчёт не состоится, прежний результат остаётся "
                          "инвалидированным без преемника",
        "decided_at_utc": now.isoformat(timespec="seconds"),
        "decided_at_moscow": now.astimezone(MSK).isoformat(timespec="seconds"),
    }
    OUT.write_text(json.dumps(decision, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"записано: {OUT.name}")
    print(f"  статус: {decision['status']}")
    print(f"  инвалидировано манифестов: {len(invalidated)}")
    for item in invalidated:
        print(f"    {item['manifest']} — {item['sha256'][:12]}…")
    print(f"  не затронуто: {len(unaffected)}")
    print(f"  запланированный преемник: {PLANNED_SUCCESSOR}, хеш не записан")
    print(f"\nsha256 решения: {sha256_file(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
