#!/usr/bin/env python3
"""Подгонка зарегистрированной модели M1 на машинном корпусе, серия v2.

    python 09-tools/mixed_m1_fit.py

Спецификация — `preregistration.md` §11, порядок исполнения —
`analysis-closure.md` §6.4 и §7: подгонка идёт только после проверки
идентифицируемости, её вывод читается отсюда и обязан быть на месте.

Отклик — операционализированный индекс стиля серии v2 (`score-v2-scores.csv`,
колонка `index_common_plus_format`). Модель даёт O1: контрасты `prompt_condition`
при контроле жанра, канала, версии обёртки и длины.

Случайные эффекты зарегистрированы как свободные члены задания и повтора.
`repeat_index` имеет два уровня, поэтому входит компонентой дисперсии, а не
вторым группирующим фактором: у `MixedLM` группирующий фактор один.

**Что скрипт не делает.** Он не упрощает модель при отказе сходимости и не
подбирает спецификацию по результату. Отказ фиксируется как отказ.
"""

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import warnings

import numpy as np
import pandas as pd
import patsy
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mixed_identifiability as ident  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
SCORES = ROOT / "07-analysis" / "score-v2-scores.csv"
IDENT = ROOT / "07-analysis" / "mixed-identifiability.json"
CLOSURE = ROOT / "07-analysis" / "analysis-closure.md"
OUT_REPORT = ROOT / "07-analysis" / "mixed-m1-v2.md"
OUT_JSON = ROOT / "07-analysis" / "mixed-m1-v2.json"

# `wrapper_version` определён каналом везде, кроме `gpt`: у deepseek_pro и
# nemotron всегда `none`, у real_claude всегда `unknown`, и только gpt делится на
# три версии. Поэтому фактор даёт ровно одну лишнюю степень свободы при любом
# выборе базового уровня — дефицит структурный, а не следствие кодировки.
#
# Детерминированное удаление алиасного столбца: фактор кодируется вложенно в
# канал, а внутри gpt базовым берётся алфавитно первый уровень. Столбцовое
# пространство то же, что после удаления одного вырожденного индикатора;
# наблюдения не выбрасываются, оценки остальных уровней сохраняются.
NESTED_CHANNEL = "gpt"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path, encoding="utf-8"):
    with Path(path).open(encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def build_frame():
    registry = {r["document_id"]: r for r in read_csv(REGISTRY, "utf-8-sig")}
    rows = []
    for r in read_csv(SCORES):
        meta = registry.get(r["document_id"])
        if meta is None or meta["origin_class"] != "A":
            continue
        if not r["index_common_plus_format"]:
            continue
        words = int(meta["word_count"] or 0)
        if words <= 0:
            continue
        rows.append({
            "document_id": r["document_id"],
            "index": float(r["index_common_plus_format"]),
            "prompt": meta["prompt_condition"],
            "genre": meta["genre"],
            "channel": meta["generation_channel"],
            "wrapper": meta["wrapper_version"] or "unknown",
            "brief": meta["brief_id"],
            "repeat": str(meta["repeat_index"]),
            "log_length": float(np.log(words)),
        })
    frame = pd.DataFrame(rows)
    knots = np.percentile(frame["log_length"], [10, 50, 90])
    frame["log_length_rcs1"] = restricted_cubic(frame["log_length"].to_numpy(), knots)
    inside = sorted(frame.loc[frame["channel"] == NESTED_CHANNEL, "wrapper"].unique())
    base_inside = inside[0] if inside else None
    frame["wrapper"] = [
        f"{NESTED_CHANNEL}:{w}"
        if ch == NESTED_CHANNEL and w != base_inside else "base"
        for ch, w in zip(frame["channel"], frame["wrapper"])]
    levels = ["base"] + sorted(set(frame["wrapper"]) - {"base"})
    frame["wrapper"] = pd.Categorical(frame["wrapper"], categories=levels)
    return frame, knots


def restricted_cubic(x, k):
    cube = lambda t: np.where(t > 0, t ** 3, 0.0)  # noqa: E731
    return (cube(x - k[0]) - cube(x - k[1]) * (k[2] - k[0]) / (k[2] - k[1])
            + cube(x - k[2]) * (k[1] - k[0]) / (k[2] - k[1])) / (k[2] - k[0]) ** 2


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not IDENT.exists():
        raise SystemExit("нет вывода проверки идентифицируемости — подгонка запрещена")
    ident_report = json.loads(IDENT.read_text(encoding="utf-8"))
    m1 = next(m for m in ident_report["models"] if m["model"] == "M1")
    if [a for a in m1["aliased"] if not a.startswith("wrapper=")]:
        raise SystemExit("в M1 вырождены столбцы помимо уровня обёртки — "
                         "подгонка по правилу §6.4 запрещена")

    frame, knots = build_frame()
    print(f"M1 на серии v2, {stamp}")
    print(f"  наблюдений {len(frame)}, заданий {frame['brief'].nunique()}, "
          f"повторов {frame['repeat'].nunique()}")

    formula = ("index ~ C(prompt) + C(genre) + C(channel) + C(wrapper) "
               "+ log_length + log_length_rcs1 "
               "+ C(prompt):C(genre) + C(prompt):C(channel)")

    # Ранг проверяется на той матрице, которая пойдёт в подгонку. Вырожденную
    # матрицу оптимизатор не чинит, поэтому расчёт останавливается здесь.
    design = patsy.dmatrices(formula, frame, return_type="dataframe")[1]
    rank = int(np.linalg.matrix_rank(design.to_numpy()))
    print(f"  матрица плана: столбцов {design.shape[1]}, ранг {rank}")
    if rank != design.shape[1]:
        raise SystemExit(f"матрица вырождена: столбцов {design.shape[1]}, "
                         f"ранг {rank}. Подгонка запрещена до устранения "
                         "структурной неидентифицируемости")
    # re_formula задаётся явно: со списком компонент дисперсии statsmodels
    # свободный член группы по умолчанию не добавляет, а он зарегистрирован.
    model = smf.mixedlm(formula, frame, groups=frame["brief"], re_formula="1",
                        vc_formula={"repeat": "0 + C(repeat)"})
    # Численный fallback, разрешённый PI 2026-07-29: стандартная цепочка
    # statsmodels BFGS → L-BFGS → CG. Формула, данные, контрасты, стартовые
    # значения, REML и критерии остановки не меняются. Принимается первый
    # сошедшийся результат, а не лучший по коэффициентам.
    attempts, result, messages = [], None, []
    for method in ("bfgs", "lbfgs", "cg"):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            candidate = model.fit(reml=True, method=method, maxiter=2000)
        notes = sorted({f"{w.category.__name__}: {w.message}" for w in caught})
        ok = bool(getattr(candidate, "converged", False))
        attempts.append({"method": method, "converged": ok, "warnings": notes})
        messages += [f"{method}: {n}" for n in notes]
        print(f"  {method}: сходимость {ok}")
        if ok and result is None:
            result = candidate
    if result is None:
        result = candidate

    converged = bool(getattr(result, "converged", False))
    params, ci = result.params, result.conf_int()
    prompt_terms = [name for name in params.index if name.startswith("C(prompt)")
                    and ":" not in name]

    rows_out = []
    for name in prompt_terms:
        rows_out.append({
            "term": name, "estimate": float(params[name]),
            "ci_low": float(ci.loc[name].iloc[0]),
            "ci_high": float(ci.loc[name].iloc[1]),
            "p_value": float(result.pvalues[name]),
        })
        print(f"  {name}: {params[name]:+.4f} "
              f"[{ci.loc[name].iloc[0]:+.4f}; {ci.loc[name].iloc[1]:+.4f}], "
              f"p = {result.pvalues[name]:.4f}")

    manifest = {
        "created_at": stamp, "model": "M1", "series": "v2", "converged": converged,
        "method": "REML, цепочка bfgs → lbfgs → cg, принят первый сошедшийся",
        "n_obs": int(len(frame)),
        "n_briefs": int(frame["brief"].nunique()),
        "response": "index_common_plus_format, score-v2-scores.csv",
        "formula": formula,
        "random_effects": {"groups": "brief_id",
                           "variance_components": "repeat_index"},
        "spline_knots_log_length": [float(k) for k in knots],
        "nested_coding": {"factor": "wrapper_version",
                          "nested_in": NESTED_CHANNEL,
                          "rule": "внутри канала базовым берётся алфавитно "
                                  "первый уровень; вне канала все наблюдения "
                                  "в базовой категории",
                          "reason": "вне gpt версия обёртки определена каналом, "
                                    "поэтому фактор даёт одну лишнюю степень "
                                    "свободы при любом выборе базового уровня"},
        "design_rank_checked": True,
        "prompt_terms": rows_out,
        "group_variance": (float(result.cov_re.iloc[0, 0])
                           if result.cov_re.size else None),
        "scale": float(result.scale),
        "inputs": {p.name: sha256(p) for p in (REGISTRY, SCORES, IDENT, CLOSURE)},
        "code_sha256": sha256(Path(__file__)),
        "optimizer_attempts": attempts,
        "optimizer_warnings": messages,
        "status": ("оценки получены" if converged else
                   "отказ сходимости: коэффициенты не интерпретируются"),
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    lines = ["# Модель M1 на серии v2: контрасты режима задания", "",
             f"Собрано {stamp} скриптом `09-tools/mixed_m1_fit.py`. Подгонка "
             "разрешена выводом `mixed-identifiability.md`; порядок задан "
             "`analysis-closure.md` §6.4.", "",
             f"Отклик — операционализированный индекс стиля серии v2. Наблюдений "
             f"{len(frame)}, заданий {frame['brief'].nunique()}, метод REML, "
             f"сходимость: {'достигнута' if converged else '**не достигнута**'}.", "",
             *([] if converged else [
                 "> **Оптимизатор не сошёлся. Числа ниже не являются оценками "
                 "модели и в выводы не идут.** По правилу `analysis-closure.md` "
                 "§1 отказ сходимости фиксируется как отказ: модель не "
                 "упрощается, спецификация по результату не подбирается. "
                 "Таблица приведена как часть диагностики.", ""]),
             "| Терм | Оценка | 95% CI | p |", "|---|---|---|---|"]
    for r in rows_out:
        lines.append(f"| `{r['term']}` | {r['estimate']:+.4f} | "
                     f"[{r['ci_low']:+.4f}; {r['ci_high']:+.4f}] | "
                     f"{r['p_value']:.4f} |")
    variance = (f"{result.cov_re.iloc[0, 0]:.4f}" if result.cov_re.size
                else "не оценена")
    lines += ["", "| Оптимизатор | Сходимость |", "|---|---|"]
    for a in attempts:
        lines.append(f"| {a['method']} | {'да' if a['converged'] else 'нет'} |")
    lines += ["", "Цепочка BFGS → L-BFGS → CG зафиксирована PI 2026-07-29 как "
                  "единственный разрешённый численный fallback. Принимается "
                  "первый сошедшийся результат, а не лучший по коэффициентам.", "",
              f"Дисперсия свободного члена задания — {variance}, "
                  f"остаточная — {result.scale:.4f}.", "",
              "**`wrapper_version` закодирован вложенно в канал.** Вне `gpt` "
              "версия обёртки определена каналом: у `deepseek_pro` и `nemotron` "
              "всегда `none`, у `real_claude` всегда `unknown`. Поэтому фактор "
              "давал ровно одну лишнюю степень свободы при любом выборе базового "
              "уровня. Внутри `gpt` базовым взят алфавитно первый уровень; "
              "столбцовое пространство то же, что после удаления одного "
              "вырожденного индикатора, наблюдения не выбрасываются.", "",
              "Модель не упрощалась и по результату не подбиралась. Спецификация "
              "взята из `preregistration.md` §11 без изменений.", ""]
    if messages:
        lines += ["## Диагностика оптимизатора", ""]
        lines += [f"- {m};" for m in messages]
        lines.append("")
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  сходимость: {converged}")
    print(f"  отчёт: {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
