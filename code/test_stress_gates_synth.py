#!/usr/bin/env python3
"""Синтетический тест шлюзов и агрегации четырёх стресс-процедур.

    python 09-tools/test_stress_gates_synth.py

Проверяет расчётные функции до прогона на реальных данных: шлюзы завершения
P1–P4, блокирующий шлюз воспроизводимости P2 и правило агрегации из
`amendment-p2-stress-units.md` §3.

Каждый шлюз прогоняется на обоих типах поля-флага — целом (как их пишет
`score_stage` в память) и строковом (как они приходят из CSV). Первая
редакция сравнивала только со строкой, и на живом прогоне шлюз
`applied_no_change` молча пропускался.
"""

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import stress_transforms as st  # noqa: E402

# Ячеек в панели: 60 документов × выполнимые преобразования. Берётся из состава,
# а не пишется числом: после перевода t14 в not executable зашитые 660 разошлись
# бы с тем, что проверяет шлюз (амендмент r5).
PANEL_DOCUMENTS = 60
N_TRANSFORMS = len(st.TRANSFORMS)
CELLS = PANEL_DOCUMENTS * N_TRANSFORMS

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FAILED = 0


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(label, got, want, detail=""):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    print(f"  [{'ok' if ok else 'СБОЙ'}] {label}: passed={got}")
    if not ok and detail:
        print(f"         {detail}")


# ── P1 ────────────────────────────────────────────────────────────────────────

def test_p1():
    p1 = load("stress_run_p1")
    print("\nP1 — шлюз завершения")

    def row(fc=18, ff=4, wc="0.5700", wf="0.1200", dropped="", m02=0, anc=0,
            delta="2.0", as_str=False, iu=None):
        # iu по умолчанию повторяет anc: ячейка, у которой не менялся вход,
        # обязана иметь и неизменённый prose.
        iu = anc if iu is None else iu
        return {"features_common": fc, "features_format": ff,
                "weight_common": wc, "weight_format": wf,
                "dropped_categories": dropped,
                "m02_missing": str(m02) if as_str else m02,
                "applied_no_change": str(anc) if as_str else anc,
                "input_unchanged": str(iu) if as_str else iu,
                "delta": delta}

    def with_sentinel(rows):
        """Шлюз требует, чтобы инвариант input_unchanged был проверен хотя бы
        на одной ячейке: пустая выборка означает, что проверка не выполнялась.

        Опорная строка ставится первой, иначе срез rows[:CELLS - 1] в проверках ниже
        отрезал бы её вместе с хвостом.
        """
        return [row(anc=1, iu=1, delta="0.0000")] + rows[1:]

    def run(rows, label, want):
        ok, detail, _ = p1.check_completion_gate(rows, CELLS)
        check(label, ok, want, detail)

    full = with_sentinel([row() for _ in range(CELLS)])
    run(full, f"{CELLS} строк, полное покрытие 18/4", True)
    run(full[:CELLS - 1], f"{CELLS - 1} строк", False)
    # 17 признаков допустимо только при валидном пропуске M02
    run(with_sentinel([row(fc=17, m02=1) for _ in range(CELLS)]),
        "17 при m02_missing=1 (int)", True)
    run(with_sentinel([row(fc=17, m02=1, as_str=True) for _ in range(CELLS)]),
        "17 при m02_missing='1' (str)", True)
    run(full[:CELLS - 1] + [row(fc=17, m02=0)], "17 без m02_missing", False)
    run(full[:CELLS - 1] + [row(fc=16, m02=1)], "16 признаков common", False)
    run(full[:CELLS - 1] + [row(ff=3)], "features_format = 3", False)
    # Знаменатели
    run(full[:CELLS - 1] + [row(wc="0.5100")], "знаменатель common 0.51", False)
    run(full[:CELLS - 1] + [row(wf="0.1000")], "знаменатель format 0.10", False)
    run(full[:CELLS - 1] + [row(dropped="Semantic")], "выпавшая категория", False)
    # applied_no_change — диагностика: prose совпал, но full или счётчики
    # препроцессинга могли измениться, поэтому ненулевая delta законна.
    run([row(anc=1, delta="0.0000") for _ in range(CELLS)],
        "applied_no_change, delta=0 (int)", True)
    run(full[:CELLS - 1] + [row(anc=1, iu=0, delta="2.0")],
        "applied_no_change, delta=2.0 при изменившемся входе: не блокирует", True)
    run(full[:CELLS - 1] + [row(anc=1, iu=0, delta="2.0", as_str=True)],
        "то же строкой: не блокирует", True)

    # input_unchanged — блокирующий: совпал весь вход, delta обязана быть нулевой
    run([row(anc=1, iu=1, delta="0.0000") for _ in range(CELLS)],
        "input_unchanged, delta=0 (int)", True)
    run([row(anc=1, iu=1, delta="0.0000", as_str=True) for _ in range(CELLS)],
        "input_unchanged, delta=0 (str)", True)
    run(full[:CELLS - 1] + [row(anc=1, iu=1, delta="2.0")],
        "input_unchanged, delta=2.0 (int)", False)
    run(full[:CELLS - 1] + [row(anc=1, iu=1, delta="2.0", as_str=True)],
        "input_unchanged, delta=2.0 (str)", False)
    run(full[:CELLS - 1] + [row(anc=1, iu=1, delta="0.0001")],
        "input_unchanged, delta=0.0001 вне допуска 1e-9", False)
    run(full[:CELLS - 1] + [row(anc=1, iu=1, delta="")],
        "input_unchanged без оценки", False)
    run([row() for _ in range(CELLS)],
        "ни одной ячейки input_unchanged: инвариант не проверен", False)
    # Прежний прогон не перезаписывается
    check("новый манифест отличается от прежнего",
          p1.OUT_JSON != p1.PREV_JSON, True)


# ── P2 ────────────────────────────────────────────────────────────────────────

def test_p2():
    p2 = load("stress_run_p2")
    print("\nP2 — агрегация (амендмент §3)")

    transforms = sorted(p2.st.TRANSFORMS)
    n_transforms = len(transforms)
    # Документы с 1, 2 и 18 моделями: последний воспроизводит подгруппу
    # human_hard_rusltc_*, которая held-out у всех восемнадцати holdout.
    eligible = {"docA": ["h01"],
                "docB": ["h01", "h02"],
                "docC": [f"h{i:02d}" for i in range(1, 19)]}
    panel = [{"document_id": d, "origin_class": "H", "genre": "g",
              "generation_channel": ""} for d in eligible]
    expected_rows = n_transforms * sum(len(v) for v in eligible.values())

    def mkrows(anc=0, delta=0.01, as_str=False, delta_fn=None, iu=None):
        out = []
        for number in transforms:
            for doc_id, splits in eligible.items():
                for split in splits:
                    value = delta_fn(doc_id, split) if delta_fn else delta
                    out.append({
                        "split_name": split, "document_id": doc_id,
                        "transform_number": number, "origin_class": "H",
                        "generation_channel": "",
                        "prob_baseline": "0.4000000000",
                        "prob_transformed": f"{0.4 + value:.10f}",
                        "delta_prob": f"{value:.10f}",
                        "applied_no_change": str(anc) if as_str else anc,
                        "input_unchanged": str(anc if iu is None else iu)
                                           if as_str else (anc if iu is None else iu),
                        "status": "ok"})
        return out

    def with_sentinel(rows_in):
        """Инвариант input_unchanged обязан быть проверен хотя бы на одной
        строке: пустая выборка означает, что проверка не выполнялась."""
        head = dict(rows_in[0], applied_no_change=1, input_unchanged=1,
                    prob_baseline="0.4000000000",
                    prob_transformed="0.4000000000", delta_prob="0.0000000000")
        return [head] + rows_in[1:]

    # Равный вес документов: docC даёт 18 строк на ячейку, docA — одну
    rows = mkrows(delta_fn=lambda d, s: 0.20 if d == "docC" else 0.0)
    cells, docs, holdouts = p2.aggregate(rows, panel, eligible)
    check("ячеек = документы × преобразования",
          len(cells), len(eligible) * n_transforms)
    check("документов после свёртки = 3", len(docs), len(eligible))
    check("holdout-строк = 18", len(holdouts), 18)
    rows_c = sum(1 for r in rows if r["document_id"] == "docC")
    rows_a = sum(1 for r in rows if r["document_id"] == "docA")
    check(f"docC дал в {rows_c // rows_a} раз больше строк, но одну строку "
          "в таблице документов",
          sum(1 for d in docs if d["document_id"] == "docC"), 1)

    # Знаки: mean_delta гасится, instability_rate — нет
    rows2 = mkrows(delta_fn=lambda d, s: 0.20 if int(s[1:]) % 2 == 0 else -0.20)
    _, docs2, _ = p2.aggregate(rows2, panel, eligible)
    doc_c = [d for d in docs2 if d["document_id"] == "docC"][0]
    check("mean_delta_prob гасится при разных знаках",
          abs(float(doc_c["mean_delta_prob"])) < 1e-9, True)
    check("instability_rate не гасится (считается по модулю)",
          float(doc_c["instability_rate"]), 1.0)

    print("\nP2 — шлюзы завершения (амендмент §4)")

    def run(rows_in, label, want, max_diff=1e-12):
        cells_in, docs_in, _ = p2.aggregate(rows_in, panel, eligible)
        ok, detail, _ = p2.check_completion_gate(
            rows_in, cells_in, docs_in, eligible, expected_rows, panel, max_diff)
        check(label, ok, want, detail)

    def run_holdouts(holdout_rows, expected_names, label, want):
        """Шлюз сверяет множество имён holdout, а не их количество."""
        cells_in, docs_in, _ = p2.aggregate(with_sentinel(mkrows()), panel,
                                            eligible)
        ok, detail, _ = p2.check_completion_gate(
            with_sentinel(mkrows()), cells_in, docs_in, eligible, expected_rows,
            panel, 1e-12, holdout_rows=holdout_rows,
            expected_split_names=expected_names)
        check(label, ok, want, detail)

    full = with_sentinel(mkrows())
    run(full, f"{expected_rows} строк, все пары полные", True)

    # Квантизация входа: модель обучена на значениях %.6g, поэтому вход
    # приводится к той же точности до импутации и масштабирования.
    q = p2.quantize_features({"L01": 94.389544, "S01": 14.129630,
                              "C01": 7.653061, "M01": None, "F01": 0.0})
    check("квантизация до шести значащих цифр",
          (q["L01"], q["S01"], q["C01"]), (94.3895, 14.1296, 7.65306))
    check("пропуск остаётся пропуском", "M01" in q, False)
    check("ноль сохраняется", q["F01"], 0.0)
    check("квантизация идемпотентна",
          p2.quantize_features(q) == q, True)

    # Подмена holdout сохраняет их количество, поэтому проверка на длину её не
    # ловит. Шлюз сверяет множество имён из замороженной схемы inner CV.
    names = [f"h{i:02d}" for i in range(1, 19)]
    rows_ok = [{"split_name": n} for n in names]
    run_holdouts(rows_ok, names, "18 holdout, имена совпадают", True)
    run_holdouts(rows_ok[:-1], names, "отсутствует один holdout", False)
    run_holdouts(rows_ok + [{"split_name": "h99"}], names,
                 "лишний holdout сверх схемы", False)
    swapped = rows_ok[:-1] + [{"split_name": "h99"}]
    run_holdouts(swapped, names,
                 "подменённый holdout при том же количестве", False)
    run(full[:-1], "одна строка отсутствует", False)
    run(full + [dict(full[0], split_name="h99")],
        "лишнее сочетание документ × split вне аудита", False)
    run(full, "max|Δ| = 3e-5 вне допуска 1e-8", False, max_diff=3e-5)
    partial = [r for r in full
               if not (r["document_id"] == "docB" and r["split_name"] == "h02"
                       and r["transform_number"] == transforms[0])]
    run(partial, f"пара без полных {N_TRANSFORMS} преобразований", False)

    # applied_no_change — диагностика: prose совпал, вход мог измениться
    run(with_sentinel(mkrows(anc=1, delta=0.01, iu=0)),
        "applied_no_change, delta=0.01 при изменившемся входе: не блокирует", True)
    run(with_sentinel(mkrows(anc=1, delta=0.01, as_str=True, iu=0)),
        "то же строкой: не блокирует", True)
    run(mkrows(anc=1, delta=0.0), "applied_no_change, delta=0", True)

    # input_unchanged — блокирующий
    run(mkrows(anc=1, delta=0.0, iu=1), "input_unchanged, delta=0 (int)", True)
    run(mkrows(anc=1, delta=0.0, iu=1, as_str=True),
        "input_unchanged, delta=0 (str)", True)
    run(mkrows(anc=1, delta=0.01, iu=1), "input_unchanged, delta=0.01 (int)", False)
    run(mkrows(anc=1, delta=0.01, iu=1, as_str=True),
        "input_unchanged, delta=0.01 (str)", False)
    run(mkrows(anc=0, delta=0.01, iu=0),
        "ни одной строки input_unchanged: инвариант не проверен", False)

    print("\nP2 — блокирующий шлюз воспроизводимости")
    import json
    scratch = ROOT / "07-analysis" / "_test-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    saved = (p2.OUT_JSON, p2.OUT_CSV, p2.OUT_ELIGIBLE, p2.OUT_BASELINE)
    p2.OUT_JSON = scratch / "manifest.json"
    p2.OUT_CSV = scratch / "scores.csv"
    p2.OUT_ELIGIBLE = scratch / "eligible.csv"
    p2.OUT_BASELINE = scratch / "baseline.csv"
    p2.OUT_ELIGIBLE.write_text("stub", encoding="utf-8")
    p2.OUT_BASELINE.write_text("stub", encoding="utf-8")
    code = None
    try:
        p2.blocked_exit(3.2e-5, 7, 11894, 464, 5104, 11894)
    except SystemExit as exc:
        code = exc.code
    check("blocked_exit завершает прогон кодом 1", code, 1)
    check("stress-p2a-scores.csv не создан", p2.OUT_CSV.exists(), False)
    check("манифест записан", p2.OUT_JSON.exists(), True)
    if p2.OUT_JSON.exists():
        m = json.loads(p2.OUT_JSON.read_text(encoding="utf-8"))
        check("status = blocked", m["status"], "blocked")
        check("reason = frozen_model_not_reproducible",
              m["reason"], "frozen_model_not_reproducible")
        check("scores_written = false", m["scores_written"], False)
    p2.OUT_JSON, p2.OUT_CSV, p2.OUT_ELIGIBLE, p2.OUT_BASELINE = saved


# ── P3 ────────────────────────────────────────────────────────────────────────

def test_p3():
    p3 = load("stress_run_p3")
    print("\nP3 — шлюз завершения")

    def rows(n, status="ok", delta="0.5"):
        return [{"status": status, "delta_nll": delta} for _ in range(n)]

    def run(data, label, want):
        ok, detail = p3.check_completion_gate(data, CELLS)
        check(label, ok, want, detail)

    run(rows(CELLS), f"{CELLS} строк, все ok", True)
    run(rows(CELLS - 1), f"{CELLS - 1} строк", False)
    run(rows(CELLS - 1) + [{"status": "coverage:100/101", "delta_nll": "0.5"}],
        "одна ошибка покрытия токенов", False)
    run(rows(CELLS - 1) + [{"status": "no_baseline", "delta_nll": ""}],
        "одна строка no_baseline", False)
    run(rows(CELLS - 160) + [{"status": "applied_no_change", "delta_nll": "0.0000001"}] * 160,
        "applied_no_change в допуске 1e-6", True)
    run(rows(CELLS - 1) + [{"status": "applied_no_change", "delta_nll": "0.5"}],
        "applied_no_change вне допуска", False)


# ── P4 ────────────────────────────────────────────────────────────────────────

def test_p4():
    p4 = load("stress_run_p4")
    print("\nP4 — шлюз завершения")
    seeds = [20260727, 20260728, 20260729]

    def cell(i, n_valid=3, status="ok", mb="50.0", mt="50.0"):
        return {"document_id": f"d{i // N_TRANSFORMS}", "transform_number": i % N_TRANSFORMS,
                "n_valid": n_valid, "status": status,
                "median_baseline": mb, "median_transformed": mt}

    def journal(cells, per_cell=3, attempt=0):
        out = {}
        for c in cells:
            for seed in seeds[:per_cell]:
                out[(c["document_id"], c["transform_number"], seed)] = {
                    "attempt": attempt, "status": "ok"}
        return out

    def run(data, jr, label, want):
        ok, detail = p4.check_completion_gate_p4(data, CELLS, seeds, jr)
        check(label, ok, want, detail)

    full = [cell(i) for i in range(CELLS)]
    run(full, journal(full), f"{CELLS} ячеек, у каждой n_valid=3", True)
    run(full[:CELLS - 1], journal(full[:CELLS - 1]), f"{CELLS - 1} ячеек", False)
    run(full[:CELLS - 1] + [cell(CELLS - 1, n_valid=2)], journal(full),
        "одна ячейка с n_valid=2", False)
    run(full, journal(full, per_cell=2),
        "журнал: 2 записи на ячейку вместо 3", False)
    run(full, journal(full, attempt=2),
        "журнал: attempt=2 (допустим один повтор)", False)
    run(full[:CELLS - 1] + [cell(CELLS - 1, status="missing")], journal(full),
        "статус missing", False)
    run([cell(i, status="applied_no_change") for i in range(CELLS)], journal(full),
        "applied_no_change совпал с baseline", True)
    run(full[:CELLS - 1] + [cell(CELLS - 1, status="applied_no_change", mb="50.0", mt="53.0")],
        journal(full), "applied_no_change разошёлся на 3 пункта", False)


# ── Статус жизненного цикла прежней ревизии ──────────────────────────────────

def test_lifecycle():
    """Условие 7 шлюза P1 после дополнения к амендменту r5.

    Прежняя ревизия допускает смену, только если рядом с её манифестом лежит
    неизменяемая запись жизненного цикла, ссылающаяся на точный хеш этого
    манифеста. Статус `completed` без такой записи означает, что две ревизии
    одновременно считают себя действующими, и запуск блокируется.
    """
    import hashlib
    import json
    import tempfile

    p1 = load("stress_run_p1")
    print("\nЖизненный цикл прежней ревизии — условие 7")

    def run(label, want, lifecycle=None, break_hash=False, no_sidecar=False):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "stress-p1-rX-manifest.json"
            manifest.write_text(json.dumps({"status": "completed"}),
                                encoding="utf-8")
            if not no_sidecar:
                digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
                record = dict(lifecycle or {})
                record["manifest_sha256"] = ("0" * 64 if break_hash else digest)
                (Path(tmp) / "stress-p1-rX-lifecycle.json").write_text(
                    json.dumps(record, ensure_ascii=False), encoding="utf-8")
            ok, note = p1.check_previous_lifecycle(manifest)
            check(label, ok, want, note)

    full_superseded = {
        "execution_status": "completed", "lifecycle_status": "superseded",
        "reason": "t14 переведено в not executable",
        "reusable_scope": "десять преобразований",
        "superseded_by": "stress-p1-r5-manifest.json",
    }
    run("корректный superseded со всеми полями", True, full_superseded)
    run("корректный invalidated", True,
        {"execution_status": "incomplete", "lifecycle_status": "invalidated",
         "reason": "шлюз не пройден"})
    run("прежняя ревизия completed без записи", False, no_sidecar=True)
    run("запись с чужим хешем манифеста", False, full_superseded,
        break_hash=True)
    run("superseded без superseded_by", False,
        {k: v for k, v in full_superseded.items() if k != "superseded_by"})
    run("superseded без области пригодности", False,
        {k: v for k, v in full_superseded.items() if k != "reusable_scope"})
    run("статус current", False,
        {"execution_status": "completed", "lifecycle_status": "current",
         "reason": "действующая"})


def test_r6_inputs():
    """Ревизия r6: вход P2 и сверка хеша матрицы до расчёта."""
    import shutil
    import tempfile
    import stress_paths as sp
    p2 = load("stress_run_p2")
    print("\nP2 — вход ревизии r6 (амендмент stress-r6)")

    check("матрица P2 — серии v2",
          p2.MATRIX_V5.name, "feature-matrix-v5.csv")
    check("D04 и D05 входят в состав признаков",
          {"D04", "D05"} <= set(p2.FEATURES_FULL), True)
    check("метка ревизии P2", sp.PROCEDURE_REVISION.get("p2a"), "r11")
    check("P1 и P3 остаются в прежней ревизии", sp.REVISION, "r5")
    check("имя выхода P2 несёт свою ревизию",
          sp.analysis("p2a", "manifest.json", attempt=False).name,
          "stress-p2a-r11-manifest.json")
    check("имя выхода P1 несёт r5",
          sp.analysis("p1", "manifest.json", attempt=False).name,
          "stress-p1-r5-manifest.json")
    check("таблица хешей — своей ревизии", p2.HASHFILE.name,
          "stress-r11-code.sha256.md")
    check("серия манифеста — прогон серии v2",
          "clf-v2-valid" in Path(p2.__file__).read_text(encoding="utf-8"),
          True)

    # Сверка хеша матрицы: подмена файла обязана останавливать прогон.
    tmp = Path(tempfile.mkdtemp(prefix="r6-hash-"))
    try:
        matrix = tmp / "feature-matrix-v5.csv"
        matrix.write_text("document_id,feature_id\n", encoding="utf-8")
        real = p2.sha256_file(matrix)
        table = tmp / "stress-r6-code.sha256.md"
        table.write_text(
            "| Файл | Что это | sha256 |\n|---|---|---|\n"
            f"| `06-features/feature-matrix-v5.csv` | матрица | `{real}` |\n",
            encoding="utf-8")
        saved_matrix, saved_table = p2.MATRIX_V5, p2.HASHFILE
        try:
            p2.MATRIX_V5, p2.HASHFILE = matrix, table
            check("совпадение хеша пропускает", p2.verify_matrix(), real)
            matrix.write_text("document_id,feature_id\nX,Y\n",
                              encoding="utf-8")
            try:
                p2.verify_matrix()
                check("подмена матрицы останавливает прогон", False, True)
            except SystemExit as exc:
                check("подмена матрицы останавливает прогон",
                      "не совпадает" in str(exc), True)
            table.write_text("| Файл | Что это | sha256 |\n|---|---|---|\n",
                             encoding="utf-8")
            try:
                p2.verify_matrix()
                check("матрица вне таблицы хешей отклоняется", False, True)
            except SystemExit as exc:
                check("матрица вне таблицы хешей отклоняется",
                      "не значится" in str(exc), True)
        finally:
            p2.MATRIX_V5, p2.HASHFILE = saved_matrix, saved_table
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_r7_features():
    """Ревизия r7: досчёт пяти признаков, которые раньше приходили пропусками."""
    p2 = load("stress_run_p2")
    print("\nP2 — досчёт признаков (амендмент stress-r7)")

    # Заглушки вместо экстракторов: проверяется проводка и нормировка,
    # а не сами счётчики — их корректность закрыта тестами экстракторов.
    saved = (p2.ef.document_features, p2.ef.surface_features,
             p2.disc.document_features, p2.art.scan)
    try:
        p2.ef.document_features = lambda parsed, row: ({}, None)
        p2.ef.surface_features = lambda text, words: {}
        p2.disc.document_features = lambda parsed: {
            "words": 500, "D04": 3, "D05": 1}
        p2.art.scan = lambda text, fid: ["hit"] if fid == "F04" else []
        row = {"prose_words": 500, "full_words": 500, "heading_md": 0,
               "list_items": 0, "full_bold_spans": 0}
        feat = p2.extract_feature_values(
            {}, "текст", row, None, None, registry_words=1000.0,
            f06_original=1.0)
        check("D04 нормирован на 1000 слов разбора", feat.get("D04"), 6.0)
        check("D05 нормирован на 1000 слов разбора", feat.get("D05"), 2.0)
        check("F04 нормирован на word_count реестра", feat.get("F04"), 1.0)
        check("F05 без срабатываний — ноль", feat.get("F05"), 0.0)
        check("F06 перенесён от исходного документа", feat.get("F06"), 1.0)

        # Без реестра F04 и F05 не выдумываются, а остаются пропуском.
        feat2 = p2.extract_feature_values(
            {}, "текст", row, None, None, registry_words=None,
            f06_original=None)
        check("без word_count F04 остаётся пропуском", "F04" in feat2, False)
        check("без значения оригинала F06 остаётся пропуском",
              "F06" in feat2, False)

        # Нулевое число слов в разборе не даёт деления на ноль.
        p2.disc.document_features = lambda parsed: {
            "words": 0, "D04": 0, "D05": 0}
        feat3 = p2.extract_feature_values(
            {}, "текст", row, None, None, registry_words=1000.0,
            f06_original=0.0)
        check("пустой разбор не роняет расчёт", feat3.get("D04"), 0)
    finally:
        (p2.ef.document_features, p2.ef.surface_features,
         p2.disc.document_features, p2.art.scan) = saved

    check("все 22 признака объявлены", len(p2.FEATURES_FULL), 22)
    check("пять прежде пропущенных входят в состав",
          {"D04", "D05", "F04", "F05", "F06"} <= set(p2.FEATURES_FULL), True)


def test_r9_tolerance():
    """Ревизия r9: допуск последнего разряда для признаков на эмбеддингах."""
    p2 = load("stress_run_p2")
    print("\nP2 — допуск инварианта (амендмент stress-r9)")

    check("состав признаков с допуском", p2.EMBEDDING_FEATURES,
          ("M01", "M02", "M05"))
    check("разность в один разряд принимается",
          p2.last_digit_equal(0.0669844, 0.0669843), True)
    check("разность в два разряда отклоняется",
          p2.last_digit_equal(0.0669844, 0.0669842), False)
    check("равные значения совпадают", p2.last_digit_equal(1.0, 1.0), True)
    check("ноль против ненуля не проходит",
          p2.last_digit_equal(0.0, 1e-9), False)
    check("порог масштабируется с величиной",
          p2.last_digit_equal(1.0, 1.00001), True)
    check("на порядок больше порога не проходит",
          p2.last_digit_equal(1.0, 1.0001), False)

    # Допуск применяется только к трём признакам: детерминированные сравнения
    # остаются точными.
    src = Path(p2.__file__).read_text(encoding="utf-8")
    check("допуск привязан к списку признаков",
          "f in EMBEDDING_FEATURES" in src, True)
    check("M01 в составе модели", "M01" in p2.FEATURES_FULL, True)
    check("детерминированные признаки допуска не получают",
          "L01" in p2.EMBEDDING_FEATURES, False)


def test_r10_bound():
    """Ревизия r10: граница инварианта считается моделью для каждой ячейки."""
    import numpy as np
    p2 = load("stress_run_p2")
    print("\nP2 — граница инварианта (амендмент stress-r10)")

    class FakeModel:
        """Логистическая по одному признаку: вероятность зависит от M01."""
        def __init__(self, weight):
            self.weight = weight
        def predict_proba(self, x):
            i = p2.FEATURES_FULL.index("M01")
            z = self.weight * float(x[0, i])
            p = 1 / (1 + np.exp(-z))
            return np.array([[1 - p, p]])

    medians = np.zeros(len(p2.FEATURES_FULL))
    values = {f: 0.0 for f in p2.FEATURES_FULL}
    values["M01"] = 0.0669844

    weak = p2.embedding_bound(FakeModel(1.0), medians, values)
    strong = p2.embedding_bound(FakeModel(10.0), medians, values)
    check("граница положительна", weak > 0, True)
    # Веса берутся из линейной области сигмоиды: при большом весе она
    # насыщается, производная падает и граница снова становится малой — это
    # свойство модели, а не дефект расчёта.
    check("граница растёт с весом признака", strong > weak, True)
    check("граница мала при слабом весе", weak < 1e-6, True)
    saturated = p2.embedding_bound(FakeModel(1000.0), medians, values)
    check("в насыщении граница падает", saturated < strong, True)

    # Без значений sem-v1 возмущать нечего: граница нулевая.
    zero = dict(values, M01=0.0)
    check("без признаков на эмбеддингах граница нулевая",
          p2.embedding_bound(FakeModel(10.0), medians, zero), 0.0)

    # Шлюз обязан сравнивать строку со своей границей, а не с общим числом.
    src = Path(p2.__file__).read_text(encoding="utf-8")
    check("колонка границы пишется в результат",
          "sentinel_bound" in src, True)
    check("шлюз читает границу строки",
          "iu_tolerance(r)" in src, True)
    check("базовый допуск остаётся нижней планкой",
          "max(SENTINEL_TOL, float(bound))" in src, True)

    # Граница обязана покрывать односторонний сдвиг: иначе строка с отличием
    # ровно в один разряд уходит в провал из-за нелинейности вокруг точки.
    model = FakeModel(10.0)
    bound = p2.embedding_bound(model, medians, values)
    i = p2.FEATURES_FULL.index("M01")
    base = np.zeros((1, len(p2.FEATURES_FULL)))
    base[0, i] = values["M01"]
    ulp = 10 ** -7
    p_center = float(model.predict_proba(base)[0, 1])
    shifted = base.copy()
    shifted[0, i] = values["M01"] + ulp
    p_shift = float(model.predict_proba(shifted)[0, 1])
    check("граница покрывает односторонний сдвиг",
          abs(p_shift - p_center) <= bound, True)


def main():
    print("Синтетический тест шлюзов стресс-теста")
    test_p1()
    test_p2()
    test_p3()
    test_p4()
    test_lifecycle()
    test_r6_inputs()
    test_r7_features()
    test_r9_tolerance()
    test_r10_bound()
    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
