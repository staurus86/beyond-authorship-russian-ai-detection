#!/usr/bin/env python3
"""Стресс-тест, процедура 4: судья на 660 преобразованных текстах.

    python 09-tools/stress_run_p4.py

Три вызова судьи (Ollama) на каждый текст, медиана трёх оценок.
delta_judge = median_transformed - median_baseline.
Порог нестабильности не задан (analysis-closure.md §6.1).
Прогон возобновляется с места обрыва: сырые ответы пишутся
построчно в stress-p4-raw.jsonl.
"""

import csv, hashlib, json, statistics, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stress_transforms as st
import stress_paths as sp

PREFLIGHT  = ROOT / "07-analysis" / "procedures-2-4-manifest.json"
PANEL      = sp.PANEL
# Каталог входов — только из stress_paths (амендмент r5, изменение 2).
TEXTS      = sp.TEXTS
ORIG_PROSE = ROOT / "04-corpus" / "derived" / "prep-v5" / "prose"
BASELINE_CSV = ROOT / "07-analysis" / "judge-v2-scores.csv"
SPEC       = ROOT / "07-analysis" / "proc4-judge-spec.md"

# Ревизия, в которой судья действительно вызывался. Результаты последующих
# ревизий получаются деривацией из неё, а не новым прогоном: тексты остальных
# преобразований не менялись, промпты те же, сиды фиксированы, и повторный вызов
# дал бы те же ответы за три часа работы. Решение PI 2026-07-31.
JUDGE_RUN_REVISION = "r4"
OUT_RAW    = sp.analysis("p4", "raw.jsonl", JUDGE_RUN_REVISION)
OUT_CSV    = sp.analysis("p4", "scores.csv", JUDGE_RUN_REVISION)
OUT_JSON   = sp.analysis("p4", "manifest.json", JUDGE_RUN_REVISION)

OLLAMA = "http://localhost:11434/api/generate"

FIELDNAMES = ["document_id", "transform_number", "origin_class", "generation_channel",
              "median_baseline", "score_seed1", "score_seed2", "score_seed3",
              "n_valid", "median_transformed", "delta_judge", "range_transformed",
              "status"]

# Допуск для applied_no_change: судья — целочисленная шкала 0-100,
# при идентичном тексте и том же seed ответ должен совпасть точно.
JUDGE_SENTINEL_TOL = 0


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv_rows(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def judge_prompt():
    """Промпт берётся из спецификации дословно (между первыми ```-ограничителями)."""
    return SPEC.read_text(encoding="utf-8").split("```")[1].strip("\n")


def call_judge(cfg, prompt, seed):
    body = {
        "model": cfg["model"], "prompt": prompt, "stream": False,
        "format": cfg["response_schema"],
        "options": {
            "temperature": cfg["sampling"]["temperature"],
            "top_p": cfg["sampling"]["top_p"],
            "top_k": cfg["sampling"]["top_k"],
            "seed": seed,
            "num_ctx": cfg["num_ctx"],
            "num_predict": cfg["num_predict"],
        },
    }
    req = urllib.request.Request(
        OLLAMA, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as fh:
        return json.load(fh)


def parse_response(response):
    """Возвращает (score или None, статус) — та же логика, что в judge_run.py §4."""
    raw = (response.get("response") or "").strip()
    if not raw:
        return None, "refusal"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, "invalid_json"
    score = parsed.get("score")
    if not isinstance(score, int):
        return None, "invalid_json"
    if not 0 <= score <= 100:
        return None, "out_of_range"
    return score, "ok"


def done_calls():
    """Уже выполненные вызовы из сырого журнала.

    Ключ: (document_id, transform_number, seed).
    Запись содержит дополнительные поля text_hash и attempt
    для верификации идентичности текста и числа попыток.
    """
    if not OUT_RAW.exists():
        return {}
    done = {}
    with OUT_RAW.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            done[(rec["document_id"], int(rec["transform_number"]), rec["seed"])] = rec
    return done


def check_completion_gate_p4(rows_out, expected_cells, seeds, done):
    """Шлюз завершения P4.

    Условия:
    1. Ровно expected_cells (660) строк.
    2. У КАЖДОЙ ячейки n_valid == len(seeds) == 3. Проверяется поячейково,
       а не суммой: сумма 1980 при потолке 3 на ячейку арифметически
       вынуждает ровно три, но поячейковая проверка не зависит от этого
       рассуждения и не сломается при смене числа сидов.
    3. n_valid — число РАЗНЫХ сидов с валидной оценкой, а не число строк
       в JSONL: per_doc индексируется по seed, retry перезаписывает
       значение. Сверяется с журналом: у каждой ячейки ровно len(seeds)
       записей, attempt <= 1.
    4. Нет строк со статусом missing / no_baseline / invalid.
    5. applied_no_change: медиана совпадает с baseline с допуском
       JUDGE_SENTINEL_TOL (0 — шкала целая).
    """
    if len(rows_out) != expected_cells:
        return False, (f"строк {len(rows_out)}, ожидалось {expected_cells}")

    # 2. Поячейковая проверка n_valid
    bad_valid = [r for r in rows_out if int(r.get("n_valid", 0)) != len(seeds)]
    if bad_valid:
        dist = {}
        for r in bad_valid:
            dist[int(r["n_valid"])] = dist.get(int(r["n_valid"]), 0) + 1
        return False, (f"{len(bad_valid)} ячеек с n_valid != {len(seeds)} "
                       f"(распределение {dist})")

    total_valid = sum(int(r["n_valid"]) for r in rows_out)

    # 3. Сверка с сырым журналом: ровно len(seeds) записей на ячейку
    per_cell_records = {}
    bad_attempt = 0
    for (doc_id, number, _seed), rec in done.items():
        per_cell_records[(doc_id, number)] = per_cell_records.get(
            (doc_id, number), 0) + 1
        if int(rec.get("attempt", 0)) > 1:
            bad_attempt += 1
    bad_cells = [k for k, n in per_cell_records.items() if n != len(seeds)]
    if bad_cells:
        return False, (f"{len(bad_cells)} ячеек журнала с числом записей "
                       f"!= {len(seeds)}: {bad_cells[:3]}")
    if bad_attempt:
        return False, (f"{bad_attempt} записей журнала с attempt > 1 — "
                       "спецификация допускает один повтор")

    # 4. Статусы
    bad_status = [r for r in rows_out
                  if r["status"] in ("missing", "no_baseline", "invalid")]
    if bad_status:
        dist = {}
        for r in bad_status:
            dist[r["status"]] = dist.get(r["status"], 0) + 1
        return False, f"{len(bad_status)} строк с плохим статусом ({dist})"

    # 5. applied_no_change
    anc = [r for r in rows_out if r.get("status") == "applied_no_change"]
    anc_bad = []
    for r in anc:
        mb, mt = r.get("median_baseline", ""), r.get("median_transformed", "")
        if mb and mt and abs(float(mt) - float(mb)) > JUDGE_SENTINEL_TOL:
            anc_bad.append(r)
    if anc_bad:
        worst = max(abs(float(r["median_transformed"]) - float(r["median_baseline"]))
                    for r in anc_bad)
        return False, (f"{len(anc_bad)} из {len(anc)} applied_no_change вне "
                       f"допуска {JUDGE_SENTINEL_TOL}: max|Δ| = {worst:.1f}")

    return True, (f"completed: {len(rows_out)} ячеек, у каждой n_valid="
                  f"{len(seeds)} (сумма {total_valid}), журнал сверен, "
                  f"{len(anc)} applied_no_change в допуске")


def main():
    # Защита от дорогой ошибки: в действующей ревизии судья повторно не
    # вызывается, её файлы получаются деривацией из r4. Без этой проверки
    # запуск цепочки в r5 потратил бы три часа на вызовы, дающие те же ответы.
    if sp.REVISION != JUDGE_RUN_REVISION:
        raise SystemExit(
            f"P4 в ревизии {sp.REVISION} не запускается: судья вызывался в "
            f"{JUDGE_RUN_REVISION}, результаты {sp.REVISION} получаются "
            f"деривацией — python 09-tools/derive_p4_r5.py. "
            f"Повторный вызов судьи требует решения PI и новой ревизии.")

    cfg_all = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not cfg_all.get("preflight_passed"):
        raise SystemExit("preflight не пройден")
    cfg   = cfg_all["judge"]
    seeds = cfg["seeds"]
    prompt_template = judge_prompt()

    rows     = read_csv_rows(PANEL)
    baseline = {r["document_id"]: float(r["median"])
                for r in read_csv_rows(BASELINE_CSV) if r.get("median")}

    # SHA256 оригинальных prose-текстов для applied_no_change
    orig_sha = {}
    for row in rows:
        orig = ORIG_PROSE / f"{row['document_id']}.txt"
        if orig.exists():
            orig_sha[row["document_id"]] = sha256_file(orig)

    done  = done_calls()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_calls = len(rows) * len(st.TRANSFORMS) * len(seeds)
    print(f"  P4 стресс-тест, {stamp}")
    print(f"  документов {len(rows)}, преобразований {len(st.TRANSFORMS)}, "
          f"сидов {len(seeds)}, всего вызовов {total_calls}")
    print(f"  судья {cfg['model']} @ {cfg['digest'][:12]}")
    if done:
        print(f"  уже выполнено вызовов: {len(done)}")

    # Восстанавливаем per_doc из уже готовых вызовов
    per_doc = {}   # (doc_id, number) → {seed: score}
    for (doc_id, number, seed), rec in done.items():
        if rec.get("status") == "ok":
            per_doc.setdefault((doc_id, number), {})[seed] = rec["score"]

    raw_fh    = OUT_RAW.open("a", encoding="utf-8")
    new_calls = pair_num = 0

    for number in sorted(st.TRANSFORMS):
        for row in rows:
            doc_id = row["document_id"]
            prose  = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
            if not prose.exists():
                continue
            text       = prose.read_text(encoding="utf-8")
            text_hash  = sha256_file(prose)
            prompt     = prompt_template.replace("<<<TEXT>>>", text)
            pair_num  += 1

            for seed in seeds:
                # Пропуск только при совпадении хеша текста: тройка ключа без
                # него вернула бы оценку, полученную по другому тексту — тот же
                # класс ошибки, что адресация кеша по document_id (амендмент r5,
                # изменение 3). Запись без text_hash доверия не даёт и считается
                # заново.
                previous = done.get((doc_id, number, seed))
                if previous is not None:
                    if previous.get("text_hash") == text_hash:
                        continue
                    print(f"  ПЕРЕСЧЁТ t{number:02d}/{doc_id}/seed={seed}: "
                          f"хеш текста изменился", flush=True)
                response = call_judge(cfg, prompt, seed)
                score, status = parse_response(response)
                attempt = 0
                if status == "invalid_json":
                    # §4: один повтор с тем же seed
                    response = call_judge(cfg, prompt, seed)
                    score, status = parse_response(response)
                    attempt = 1
                    if status == "invalid_json":
                        status = "invalid"
                rec = {
                    "document_id":       doc_id,
                    "transform_number":  number,
                    "seed":              seed,
                    "attempt":           attempt,
                    "text_hash":         text_hash,
                    "score":             score,
                    "status":            status,
                    "response":          response.get("response"),
                    "total_duration_ns": response.get("total_duration"),
                }
                raw_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                raw_fh.flush()
                if status == "ok":
                    per_doc.setdefault((doc_id, number), {})[seed] = score
                new_calls += 1

            if pair_num % 60 == 0:
                print(f"  пар {pair_num}/{len(rows) * len(st.TRANSFORMS)}, "
                      f"новых вызовов {new_calls}", flush=True)

    raw_fh.close()
    print(f"  новых вызовов выполнено: {new_calls}")

    # ── Сводим в итоговые строки ──────────────────────────────────────────────
    rows_out = []
    for number in sorted(st.TRANSFORMS):
        for row in rows:
            doc_id = row["document_id"]
            prose  = TEXTS / f"t{number:02d}" / "prose" / f"{doc_id}.txt"
            if not prose.exists():
                continue
            got    = per_doc.get((doc_id, number), {})
            values = [got[s] for s in seeds if s in got]
            if len(values) >= 2:
                median_t   = statistics.median(values)
                spread     = max(values) - min(values)
                doc_status = "ok" if len(values) == 3 else "partial"
            else:
                median_t = spread = None
                doc_status = "missing"
            median_b = baseline.get(doc_id)

            # applied_no_change: стресс-текст идентичен оригиналу
            text_hash_cur = sha256_file(prose)
            if doc_status in ("ok", "partial") and text_hash_cur == orig_sha.get(doc_id):
                doc_status = "applied_no_change"

            rows_out.append({
                "document_id":        doc_id,
                "transform_number":   number,
                "origin_class":       row["origin_class"],
                "generation_channel": row["generation_channel"],
                "median_baseline":    f"{median_b:.1f}" if median_b is not None else "",
                "score_seed1":        got.get(seeds[0], ""),
                "score_seed2":        got.get(seeds[1], ""),
                "score_seed3":        got.get(seeds[2], ""),
                "n_valid":            len(values),
                "median_transformed": (f"{median_t:.1f}"
                                       if median_t is not None else ""),
                "delta_judge":        (f"{median_t - median_b:.1f}"
                                       if median_t is not None and median_b is not None
                                       else ""),
                "range_transformed":  spread if spread is not None else "",
                "status":             doc_status if median_b is not None else "no_baseline",
            })

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows_out)
    ok_rows = sum(1 for r in rows_out if r["status"] in ("ok", "partial",
                                                          "applied_no_change"))
    print(f"  оценки: {OUT_CSV.name}, строк {len(rows_out)}, "
          f"с медианой {ok_rows}")

    # ── Шлюз завершения ───────────────────────────────────────────────────────
    done_final = done_calls()   # перечитываем журнал: он дополнен этим прогоном
    gate_ok, gate_detail = check_completion_gate_p4(
        rows_out, len(rows) * len(st.TRANSFORMS), seeds, done_final)
    status_str = "completed" if gate_ok else "incomplete"
    print(f"  шлюз завершения: {status_str} — {gate_detail}")

    anc_count = sum(1 for r in rows_out if r.get("status") == "applied_no_change")

    manifest = {
        "created_at":    stamp,
        "procedure":     "P4-stress",
        "status":        status_str,
        "gate_detail":   gate_detail,
        "panel":         "stress-panel-v1.csv",
        "documents":     len(rows),
        "transformations": sorted(st.TRANSFORMS),
        "rows":          len(rows_out),
        "applied_no_change_count": anc_count,
        "applied_no_change_sentinel_tol": JUDGE_SENTINEL_TOL,
        "judge": {
            "model":    cfg["model"],
            "digest":   cfg["digest"],
            "seeds":    seeds,
            "sampling": cfg["sampling"],
            "num_ctx":  cfg["num_ctx"],
        },
        "raw_record_fields": ["document_id", "transform_number", "seed",
                              "attempt", "text_hash", "score", "status",
                              "response", "total_duration_ns"],
        "prompt_sha256": hashlib.sha256(
            prompt_template.encode("utf-8")).hexdigest(),
        "threshold":      "not applicable",
        "threshold_note": (
            "analysis-closure.md §6.1: к процедурам вне шкалы 0–100 "
            "порог напрямую не применяется"),
        "inputs": {
            "stress-panel-v1.csv": sha256_file(PANEL),
            "judge-v2-scores.csv": sha256_file(BASELINE_CSV),
            "proc4-judge-spec.md": sha256_file(SPEC),
        },
        "code_sha256": sha256_file(Path(__file__)),
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  манифест: {OUT_JSON.name}")
    # Данные и сырой журнал сохраняются всегда: прогон возобновляем.
    # Ненулевой код — сигнал статуса incomplete.
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
