#!/usr/bin/env python3
"""Стресс-тест, процедура 3: NLL на 660 преобразованных текстах.

    python 09-tools/stress_run_p3.py

Вычисляет средний NLL для каждого стресс-текста и записывает
    delta_nll = nll_transformed - nll_baseline.
Порог нестабильности не задан (analysis-closure.md §6.1).
Прогон возобновляется с места обрыва.
"""

import csv, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stress_transforms as st
import stress_paths as sp
from lifecycle_gate import check_previous_lifecycle

PREFLIGHT    = ROOT / "07-analysis" / "procedures-2-4-manifest.json"
PANEL        = sp.PANEL
# Каталог входов и метка ревизии берутся из stress_paths и нигде не дублируются:
# 30 июля этот скрипт остался на каталоге прежней ревизии и посчитал NLL по
# устаревшим текстам (амендмент r5, изменение 2).
TEXTS        = sp.TEXTS
ORIG_PROSE   = ROOT / "04-corpus" / "derived" / "prep-v5" / "prose"
BASELINE_CSV = ROOT / "07-analysis" / "nll-v2-scores.csv"
# Прежняя ревизия: её статус жизненного цикла проверяет шлюз (амендмент r5).
PREV_JSON    = ROOT / "07-analysis" / "stress-p3-r4-manifest.json"
OUT_CSV      = sp.analysis("p3", "scores.csv")
OUT_JSON     = sp.analysis("p3", "manifest.json")

# Допуск воспроизведения NLL для applied_no_change записей.
# Текст идентичен оригиналу → Scorer детерминирован → расхождение должно быть 0;
# 1e-6 покрывает возможные float32-погрешности при идентичных входах.
NLL_SENTINEL_TOL = 1e-6

FIELDNAMES = ["document_id", "transform_number", "origin_class", "generation_channel",
              "nll_baseline", "nll_transformed", "delta_nll",
              "scored_tokens", "n_windows", "status", "text_sha256"]


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def done_set():
    """Уже оценённые ячейки: (document_id, transform_number) -> хеш входа.

    Возвращается не множество, а отображение на хеш текста, по которому ячейка
    была посчитана. Пропуск разрешён только при совпадении хеша: ключ без него
    подсунул бы значение, посчитанное по другому тексту, — тот же класс ошибки,
    что адресация кеша по document_id (амендмент r5, изменение 3).

    У строк, записанных до появления колонки, хеша нет. Такая ячейка считается
    заново: пустое значение доверия не даёт.
    """
    if not OUT_CSV.exists():
        return {}
    try:
        return {(r["document_id"], int(r["transform_number"])):
                (r.get("text_sha256") or "").strip()
                for r in read_csv_rows(OUT_CSV)}
    except Exception:
        return {}


def check_completion_gate(all_rows, expected_count, texts_dir=None,
                          previous_manifest=None):
    """Шлюз завершения: возвращает (passed: bool, detail: str).

    Условия:
    1. Присутствуют все ожидаемые ячейки (60 документов × выполнимые
       преобразования).
    2. У каждой ячейки scored_tokens == expected_tokens
       (нет строк со статусом coverage:*).
    3. Нет строк со статусом invalid или no_baseline.
    4. Все applied_no_change записи воспроизводят исходный NLL
       с допуском NLL_SENTINEL_TOL.
    5. Ни одной строки преобразования, выведенного из состава.
    6. Хеш входа каждой строки совпадает с фактическим файлом панели.
    7. Прежняя ревизия объявлена заменённой или негодной.

    Условия 5–7 добавлены амендментом r5: состав преобразований изменился, и
    результат обязан доказывать, что посчитан по действующей панели, а не
    подхвачен от прежнего прогона.
    """
    if len(all_rows) != expected_count:
        return False, (f"строк {len(all_rows)}, ожидалось {expected_count}")

    # 5. Выведенные преобразования
    dropped = sorted(set(st.NOT_EXECUTABLE))
    left = [r for r in all_rows
            if int(r.get("transform_number", 0)) in st.NOT_EXECUTABLE]
    if left:
        found = sorted({int(r["transform_number"]) for r in left})
        return False, (f"{len(left)} строк невыполнимых преобразований {found}; "
                       f"выведены из состава: {dropped}")

    # 6. Хеш входа
    if texts_dir is not None:
        mismatched, missing_hash = [], 0
        for r in all_rows:
            recorded = (r.get("text_sha256") or "").strip()
            if not recorded:
                missing_hash += 1
                continue
            path = (Path(texts_dir) / f"t{int(r['transform_number']):02d}"
                    / "prose" / f"{r['document_id']}.txt")
            if not path.exists() or sha256_file(path) != recorded:
                mismatched.append(f"{r['document_id']}/t{r['transform_number']}")
        if missing_hash:
            return False, (f"{missing_hash} строк без хеша входа: происхождение "
                           f"оценки не доказано")
        if mismatched:
            return False, (f"{len(mismatched)} строк с хешем, не совпавшим с "
                           f"файлом панели: {mismatched[:3]}")

    # 7. Статус прежней ревизии
    if previous_manifest is not None:
        prev_ok, prev_note = check_previous_lifecycle(previous_manifest)
        if not prev_ok:
            return False, (f"прежняя ревизия "
                           f"{Path(previous_manifest).name}: {prev_note}")

    coverage_err = [r for r in all_rows if r.get("status", "").startswith("coverage:")]
    if coverage_err:
        return False, (f"{len(coverage_err)} строк с ошибками покрытия токенов")

    invalid = [r for r in all_rows
               if r.get("status") in ("invalid", "no_baseline")]
    if invalid:
        return False, (f"{len(invalid)} строк со статусом invalid/no_baseline")

    anc = [r for r in all_rows if r.get("status") == "applied_no_change"]
    bad = []
    for r in anc:
        delta = r.get("delta_nll", "")
        if delta and abs(float(delta)) > NLL_SENTINEL_TOL:
            bad.append(r)
    if bad:
        worst = max(abs(float(r["delta_nll"])) for r in bad)
        return False, (f"{len(bad)} applied_no_change записей вне допуска "
                       f"{NLL_SENTINEL_TOL}: max|Δ| = {worst:.6f}")

    return True, (f"completed: {len(all_rows)} строк, 0 coverage_errors, "
                  f"0 invalid, 0 строк выведенных преобразований, хеши входов "
                  f"сверены, {len(anc)} applied_no_change в допуске")


def main():
    cfg = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not cfg.get("preflight_passed"):
        raise SystemExit("preflight не пройден")
    nll_cfg = cfg["nll"]

    import torch
    from nll_zero_shot import Scorer
    device = nll_cfg.get("device", "cpu")
    dtype  = getattr(torch, nll_cfg.get("dtype", "float32"))
    print(f"  загрузка {nll_cfg['model']} @ {nll_cfg['revision'][:12]} …")
    scorer = Scorer(nll_cfg["model"], nll_cfg["revision"], device, dtype)
    print(f"  bos_token_id={scorer.bos_id}, "
          f"ставит BOS сам: {'да' if scorer.adds_bos else 'нет'}")

    rows     = read_csv_rows(PANEL)
    baseline = {r["document_id"]: float(r["nll"])
                for r in read_csv_rows(BASELINE_CSV) if r.get("nll")}

    # SHA256 оригинальных prose-текстов для обнаружения applied_no_change
    orig_sha = {}
    for row in rows:
        orig = ORIG_PROSE / f"{row['document_id']}.txt"
        if orig.exists():
            orig_sha[row["document_id"]] = sha256_file(orig)
    print(f"  orig_sha загружен: {len(orig_sha)}/{len(rows)} документов")

    done      = done_set()
    stamp     = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_pairs = len(rows) * len(st.TRANSFORMS)
    print(f"  P3 стресс-тест, {stamp}")
    print(f"  документов {len(rows)}, преобразований {len(st.TRANSFORMS)}, "
          f"всего пар {total_pairs}")
    if done:
        print(f"  уже оценено пар: {len(done)}")

    mode   = "a" if done else "w"
    out_fh = OUT_CSV.open(mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(out_fh, fieldnames=FIELDNAMES)
    if mode == "w":
        writer.writeheader()

    pair_num = 0
    scored_new = skipped = 0

    for number in sorted(st.TRANSFORMS):
        for row in rows:
            pair_num += 1
            doc_id = row["document_id"]
            prose = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
            if not prose.exists():
                skipped += 1
                continue
            text_sha = sha256_file(prose)
            # Пропуск только при совпадении хеша входа: ключ без хеша вернул бы
            # оценку, посчитанную по другому тексту.
            recorded = done.get((doc_id, number))
            if recorded and recorded == text_sha:
                continue
            if recorded is not None and recorded != text_sha:
                print(f"  ПЕРЕСЧЁТ t{number:02d}/{doc_id}: хеш входа изменился")
            text = prose.read_text(encoding="utf-8")
            nll, scored_tok, windows, _ = scorer.score(text)
            if nll is None or scored_tok == 0:
                skipped += 1
                continue
            expected = len(scorer.encode(text)) - 1
            if scored_tok != expected:
                status = f"coverage:{scored_tok}/{expected}"
                print(f"  ПОКРЫТИЕ t{number:02d}/{doc_id}: "
                      f"scored={scored_tok} expected={expected}")
            elif text_sha == orig_sha.get(doc_id):
                # Текст не изменился: NLL должен совпасть с baseline
                status = "applied_no_change"
            else:
                status = "ok"
            baseline_nll = baseline.get(doc_id)
            rec = {
                "document_id":       doc_id,
                "transform_number":  number,
                "origin_class":      row["origin_class"],
                "generation_channel": row["generation_channel"],
                "nll_baseline":      f"{baseline_nll:.6f}" if baseline_nll is not None else "",
                "nll_transformed":   f"{nll:.6f}",
                "delta_nll":         (f"{nll - baseline_nll:.6f}"
                                      if baseline_nll is not None else ""),
                "scored_tokens":     scored_tok,
                "n_windows":         windows,
                "status":            status if baseline_nll is not None else "no_baseline",
                # Хеш входа записывается, чтобы повторный запуск мог отличить
                # уже посчитанную ячейку от ячейки с изменившимся текстом.
                "text_sha256":       text_sha,
            }
            writer.writerow(rec)
            out_fh.flush()
            scored_new += 1
            if pair_num % 66 == 0 or pair_num == total_pairs:
                print(f"  пар {pair_num}/{total_pairs}, "
                      f"новых {scored_new}, пропущено {skipped}", flush=True)

    out_fh.close()
    total_done = len(done) + scored_new
    print(f"  готово: {total_done} строк, пропущено {skipped}")

    # ── Шлюз завершения ───────────────────────────────────────────────────────
    all_rows = read_csv_rows(OUT_CSV)
    gate_ok, gate_detail = check_completion_gate(
        all_rows, expected_count=len(rows) * len(st.TRANSFORMS),
        texts_dir=TEXTS, previous_manifest=PREV_JSON)
    status_str = "completed" if gate_ok else "incomplete"
    print(f"  шлюз завершения: {status_str} — {gate_detail}")

    anc_count = sum(1 for r in all_rows if r.get("status") == "applied_no_change")

    manifest = {
        "created_at":  stamp,
        "procedure":   "P3-stress",
        "status":      status_str,
        "gate_detail": gate_detail,
        "panel":       "stress-panel-v1.csv",
        "documents":   len(rows),
        "transformations": sorted(st.TRANSFORMS),
        "rows":        total_done,
        "skipped":     skipped,
        "applied_no_change_count": anc_count,
        "applied_no_change_sentinel_tol": NLL_SENTINEL_TOL,
        "nll_model": {
            "model":    nll_cfg["model"],
            "revision": nll_cfg["revision"],
            "device":   device,
            "dtype":    nll_cfg.get("dtype", "float32"),
        },
        "threshold":      "not applicable",
        "threshold_note": (
            "analysis-closure.md §6.1: к процедурам вне шкалы 0–100 "
            "порог напрямую не применяется"),
        "inputs": {
            "stress-panel-v1.csv": sha256_file(PANEL),
            "nll-v2-scores.csv":   sha256_file(BASELINE_CSV),
        },
        "code_sha256": sha256_file(Path(__file__)),
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  манифест: {OUT_JSON.name}")
    # Данные сохраняются всегда: прогон можно продолжить после локальной ошибки.
    # Ненулевой код — сигнал, что общий статус incomplete и анализировать
    # результат с уменьшенным знаменателем нельзя.
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
