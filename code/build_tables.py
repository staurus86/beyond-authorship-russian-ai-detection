#!/usr/bin/env python3
"""Сборка таблиц рукописи из замороженных источников.

    python 09-tools/build_tables.py

Состав и распределение по основному тексту и Supplementary заданы решением PI
2026-08-01 и записаны в `08-paper/tables-registry.md`: восемь основных таблиц,
шестнадцать в Supplementary.

Скрипт читает выходы расчёта и описательные наборы, ничего не пересчитывает и
пишет три вещи: `.csv` с данными, `.tex` с подписью и примечанием, манифест с
хешами входов, выходов и кода. **Числа после сборки руками не правятся** — правка
идёт в источник, затем скрипт запускается заново.

Каждая таблица обязана заполнить девять полей метаданных: номер, заголовок,
единицу анализа, знаменатель, серию данных, определение статистик, направление
шкалы, расшифровку сокращений, правило NA и поправку на множественность. Пустое
поле останавливает сборку.

Источник данных пишется в манифест и в публикуемую подпись не попадает.
"""

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "08-paper" / "tables"
ANALYSIS = ROOT / "07-analysis"
ATTEMPT = ANALYSIS / "stress-r5-attempts" / "20260731T220132Z"
SOURCES = OUT / "sources"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

INPUTS = {}
TABLES = []

# Преобразования действующего знаменателя: t14 переведено в not executable
# амендментом r5, строки t14 в области пригодности прогонов r4 не лежат.
TRANSFORMS = [1, 2, 3, 4, 5, 10, 11, 13, 15, 16]

TRANSFORM_NAMES = {
    1: "t01 замена тире", 2: "t02 снятие markdown", 3: "t03 срезание заголовков",
    4: "t04 перестановка абзацев", 5: "t05 исправление пунктуации",
    10: "t10 сокращение текста", 11: "t11 расширение текста",
    13: "t13 опечатки", 15: "t15 гомоглифы", 16: "t16 перенос между форматами",
}

# Общие примечания, повторяемые дословно (реестр §5).
NOTE_CI = ("Интервалы 95%, кластерный бутстрап по автору и документу; кластер — "
           "задание, заданий 45.")
NOTE_BONF = ("Семейство первичных исходов закрыто поправкой Бонферрони на два "
             "зарегистрированных теста, α = 0.025 на тест; интервалы "
             "нескорректированные.")
NOTE_NO_MULT = "Поправка на множественные сравнения не применялась."
NOTE_CONV = ("Конвенция интерпретации: больше значит более AI-подобно. У процедуры 3 "
             "она инвертирует знак сырой метрики, поэтому величины между "
             "процедурами не сравниваются.")


def register(path):
    INPUTS[str(path.relative_to(ROOT)).replace("\\", "/")] = hashlib.sha256(
        path.read_bytes()).hexdigest()


def read_csv(path, encoding="utf-8"):
    register(path)
    with path.open(encoding=encoding) as fh:
        return list(csv.DictReader(fh))


def read_json(path):
    register(path)
    return json.loads(path.read_text(encoding="utf-8"))


_DESC = None


def desc(key):
    global _DESC
    if _DESC is None:
        _DESC = read_json(SOURCES / "descriptive-tables.json")
    block = _DESC["tables"][key]
    return block["columns"], [list(r) for r in block["rows"]]


REQUIRED = ("number", "kind", "slug", "title", "unit", "denominator", "series",
            "statistics", "direction", "abbreviations", "na_rule", "multiplicity",
            "columns", "rows")


def table(**kw):
    missing = [f for f in REQUIRED if not kw.get(f)]
    if missing:
        raise SystemExit(f"таблица {kw.get('slug', '?')}: не заполнены поля {missing}")
    width = len(kw["columns"])
    for i, row in enumerate(kw["rows"]):
        if len(row) != width:
            raise SystemExit(f"таблица {kw['slug']}: строка {i} имеет {len(row)} "
                             f"ячеек при {width} колонках")
    TABLES.append(kw)
    return kw


# ── запись ───────────────────────────────────────────────────────────────────

TEX_ESCAPE = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
              "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
              "^": r"\textasciicircum{}", ">": r"$>$", "<": r"$<$",
              "|": r"$|$"}


def tex_escape(s):
    s = str(s)
    if s.startswith("$") and s.endswith("$") and len(s) > 2:
        return s
    return "".join(TEX_ESCAPE.get(ch, ch) for ch in s)


def name_of(t):
    prefix = "S" if t["kind"] == "supp" else ""
    return f"tab-{prefix}{t['number']:02d}-{t['slug']}"


def label_of(t):
    return ("Таблица S" if t["kind"] == "supp" else "Таблица ") + str(t["number"])


def note_of(t):
    parts = [f"Единица анализа — {t['unit']}.", f"Знаменатель: {t['denominator']}.",
             f"Данные: {t['series']}.", t["statistics"], f"Шкала: {t['direction']}",
             f"Сокращения: {t['abbreviations']}", f"Пропуски: {t['na_rule']}",
             t["multiplicity"]]
    return " ".join(p.rstrip() if p.endswith(".") else p + "." for p in parts)


def write_table(t):
    OUT.mkdir(parents=True, exist_ok=True)
    base = name_of(t)

    with (OUT / f"{base}.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(t["columns"])
        w.writerows(t["rows"])

    ncol, nrow = len(t["columns"]), len(t["rows"])
    label = r"\label{tab:" + ("s" if t["kind"] == "supp" else "") + str(t["number"]) + "}"
    header = " & ".join(tex_escape(c) for c in t["columns"]) + r" \\"
    body = [" & ".join(tex_escape(c) for c in row) + r" \\" for row in t["rows"]]
    note = r"\textit{Примечание.} " + tex_escape(note_of(t))

    # Колонки с длинным текстом переносятся по словам, иначе строка уезжает за поле.
    wide_text = (any(len(str(c)) > 40 for row in t["rows"] for c in row)
                 or (ncol >= 6 and any(len(str(c)) > 22 for c in t["columns"])))
    if wide_text:
        # 0.92 оставляет место под \tabcolsep между колонками, иначе таблица
        # выходит за наборное поле.
        width = f"{0.92 / ncol:.3f}"
        align = ">{\\raggedright\\arraybackslash}p{" + width + r"\linewidth}"
        align = align * ncol
    else:
        align = "l" * ncol

    if nrow > 22:
        lines = [r"\begingroup\scriptsize",
                 r"\begin{longtable}{" + align + "}",
                 r"\caption{" + tex_escape(t["title"]) + "}" + label + r" \\",
                 r"\toprule", header, r"\midrule", r"\endfirsthead",
                 r"\toprule", header, r"\midrule", r"\endhead",
                 r"\bottomrule", r"\endfoot"]
        lines += body
        lines += [r"\end{longtable}",
                  r"\begin{minipage}{\linewidth}\footnotesize\vspace{2pt}", note,
                  r"\end{minipage}", r"\endgroup", ""]
    else:
        size = r"\scriptsize" if ncol >= 8 else r"\small"
        env = "table*" if ncol >= 7 else "table"
        lines = [f"\\begin{{{env}}}[htbp]", r"\centering", size,
                 r"\caption{" + tex_escape(t["title"]) + "}", label,
                 r"\begin{tabular}{" + align + "}", r"\toprule", header, r"\midrule"]
        lines += body
        lines += [r"\bottomrule", r"\end{tabular}",
                  r"\begin{minipage}{\linewidth}\footnotesize\vspace{2pt}", note,
                  r"\end{minipage}", f"\\end{{{env}}}", ""]

    (OUT / f"{base}.tex").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"  {label_of(t):>14}  {ncol}×{nrow}  {t['title'][:56]}")


# ── форматирование чисел ─────────────────────────────────────────────────────

def minus(s):
    """Единый знак минуса U+2212 во всех числах таблиц."""
    return str(s).replace("-", "−")


def f4(x):
    return "" if x in (None, "") else minus(f"{float(x):.4f}")


def f3(x):
    return "" if x in (None, "") else minus(f"{float(x):.3f}")


def signed(x, digits=4):
    return minus(f"{float(x):+.{digits}f}")


def ci(lo, hi, digits=4):
    if lo in (None, "") or hi in (None, ""):
        return ""
    fmt = f"{{:.{digits}f}}"
    return minus(f"[{fmt.format(float(lo))}; {fmt.format(float(hi))}]")


# ── основные таблицы ─────────────────────────────────────────────────────────

def t1_corpus():
    reg = read_csv(ROOT / "04-corpus" / "documents-registry.csv", "utf-8-sig")
    machine = [r for r in reg if r["origin_class"] == "A"]
    human = [r for r in reg if r["origin_class"] == "H"]
    by_channel = Counter(r["generation_channel"] for r in machine)
    by_prompt = Counter(r["prompt_condition"] for r in machine)
    by_repeat = Counter(r["repeat_index"] for r in machine)
    by_genre = Counter(r["genre"] for r in human)

    _, strata = desc("human_strata")
    level_of = {row[2]: row[1] for row in strata}
    name_of_genre = {row[2]: row[0] for row in strata}

    rows = []
    for ch, n in sorted(by_channel.items(), key=lambda kv: -kv[1]):
        rows.append(["машинная", "канал генерации", ch, str(n)])
    for p in ("P1", "P2", "P3"):
        rows.append(["машинная", "режим задания", p, str(by_prompt[p])])
    for rep in sorted(by_repeat):
        rows.append(["машинная", "повтор", rep, str(by_repeat[rep])])
    for genre, n in sorted(by_genre.items(), key=lambda kv: -kv[1]):
        rows.append(["человеческая", f"уровень {level_of[genre]}",
                     f"{name_of_genre[genre]}, {genre}", str(n)])
    rows.append(["обе", "итог", "аналитический корпус",
                 str(len(machine) + len(human))])

    return table(
        number=1, kind="main", slug="corpus-and-conditions",
        title="Состав корпуса и условия производства текста",
        columns=["Часть корпуса", "Условие производства", "Категория", "Документов"],
        rows=rows,
        unit="документ",
        denominator=f"{len(reg)} документов аналитического корпуса, "
                    f"{len(machine)} машинных и {len(human)} человеческих",
        series="реестр корпуса, профиль препроцессинга prep-v5",
        statistics="Приведены счётчики документов; оценок и интервалов в таблице нет.",
        direction="шкалы нет, таблица описывает состав",
        abbreviations="P1, P2 и P3 — режимы задания, от жёсткого технического задания "
                      "к свободной формулировке; уровень — степень регламентированности "
                      "источника, присвоенная по источнику до чтения текста",
        na_rule="страта translation уровень не получает: у переводов регламент задаёт "
                "исходный текст, а не редакция",
        multiplicity=NOTE_NO_MULT,
    )


def t2_procedures():
    cols, rows = desc("procedures")
    return table(
        number=2, kind="main", slug="four-operationalisations",
        title="Четыре операционализации машинности и направление их шкал",
        columns=cols, rows=rows,
        unit="процедура оценивания",
        denominator="четыре процедуры, применённые к одному и тому же корпусу",
        series="серия v2",
        statistics="Таблица описывает процедуры; числовых оценок в ней нет.",
        direction=NOTE_CONV,
        abbreviations="NLL — negative log-likelihood, средняя по токенам; "
                      "confirmatory — предрегистрированный анализ, exploratory — "
                      "анализ, операционализация которого написана после просмотра "
                      "результатов процедуры 1",
        na_rule="пропусков нет",
        multiplicity=NOTE_NO_MULT,
    )


def t3_o1():
    rows_src = read_csv(ANALYSIS / "synthesis-o1-v2.csv")
    order = {"P3-P1": 0, "P2-P1": 1}
    rows_src.sort(key=lambda r: (order[r["contrast"]], r["estimand"] != "full",
                                 r["procedure"]))
    rows = []
    for r in rows_src:
        raw = r["mean_diff_raw"]
        rows.append([
            r["contrast"].replace("-", " − "),
            r["estimand"],
            r["procedure"],
            r["population"],
            r["n_pairs"] or "—",
            signed(raw) if raw else "не определён",
            ci(r["ci_low_raw"], r["ci_high_raw"]) or "—",
            f4(r["p_value"]) if r["p_value"] else "—",
            r["sign_after_convention"] or "—",
        ])
    return table(
        number=3, kind="main", slug="o1-contrasts",
        title="Контрасты первичного исхода O1 по четырём процедурам, "
              "оба estimand и оба контраста",
        columns=["Контраст", "Estimand", "Процедура", "Популяция контраста", "Пар",
                 "Эффект, сырая шкала", "95% CI", "p", "Знак после конвенции"],
        rows=rows,
        unit="пара текстов, порождённых одной моделью по одному заданию в двух "
             "режимах",
        denominator="359 или 360 пар у процедур 1, 3 и 4 при 45 кластерах; 120 пар "
                    "у процедуры 2 при 15 кластерах, поскольку она считает контраст "
                    "только на жанре seo",
        series="серия v2, статус current",
        statistics=NOTE_CI + " Публикуется абсолютная разница со своим интервалом; "
                             "единого числа по четырём процедурам нет, сводный эффект "
                             "не рассчитывался.",
        direction=NOTE_CONV,
        abbreviations="estimand full — полный балл, net — балл без структурных "
                      "компонентов; P1, P2 и P3 — режимы задания",
        na_rule="estimand net определён только у процедур 1 и 2; у zero-shot NLL и "
                "у судьи разложения на компоненты нет по построению, и ячейка "
                "означает «величина не определена», а не «не посчитана»",
        multiplicity=NOTE_BONF,
    )


def t4_classifier():
    metrics = read_csv(ANALYSIS / "clf-v2-p2a-metrics.csv")
    contrasts = read_csv(ANALYSIS / "clf-v2-p2b-o1-contrasts.csv")

    by_model = defaultdict(list)
    for r in metrics:
        if r.get("estimand") == "full":
            by_model[r["model"]].append(r)
    meaning = {
        "main": "22 признака стиля",
        "main+M02": "плюс признак сходства абзацев",
        "source-only": "только идентификатор площадки",
        "genre-only": "только жанр",
        "length-only": "только длина текста",
        "format-only": "четыре структурных признака",
        "negative-control": "перемешанные метки классов",
    }

    def auroc_of(rr):
        return [float(x["auroc"]) for x in rr if x["auroc"] not in ("", None)]

    contrast_by_estimand = {r["estimand"]: r for r in contrasts
                            if r["contrast"] == "P3-P1"}

    def contrast_cell(estimand):
        r = contrast_by_estimand[estimand]
        return (f"{signed(r['mean_diff_prob'])} "
                f"{ci(r['ci_low'], r['ci_high'])}, p = {f4(r['p_wild_cluster'])}")

    rows = []
    for model, rr in sorted(by_model.items(),
                            key=lambda kv: -(median(auroc_of(kv[1])) if auroc_of(kv[1]) else 0)):
        au = auroc_of(rr)
        fp = [float(x["fpr"]) for x in rr if x["fpr"] not in ("", None)]
        rows.append([
            model, meaning.get(model, ""), f"{len(au)} из {len(rr)}",
            f4(median(au)) if au else "—",
            f"{min(au):.4f}–{max(au):.4f}" if au else "—",
            f4(median(fp)) if fp else "—",
            contrast_cell("full") if model == "main" else "—",
            contrast_cell("net") if model == "main" else "—",
        ])

    return table(
        number=4, kind="main", slug="classifier-and-ablation",
        title="Классификатор: качество по вариантам модели и ablation "
              "структурных признаков",
        columns=["Вариант модели", "Что входит в признаки",
                 "Holdout с определённым AUROC", "Медиана AUROC", "Размах AUROC",
                 "Медиана FPR", "Контраст P3 − P1, полная матрица",
                 "Контраст P3 − P1, без структурных признаков"],
        rows=rows,
        unit="метрики качества — групповой holdout; контрасты — пара текстов, "
             "порождённых одной моделью по одному заданию",
        denominator="18 групповых holdout; контраст считается на 120 парах при "
                    "15 кластерах, только на жанре seo",
        series="серия v2, статус current",
        statistics="Медиана и размах по holdout; контраст — абсолютная разница "
                   "вероятностей с 95% интервалом и p дикого кластерного "
                   "бутстрапа. " + NOTE_CI,
        direction="AUROC: больше значит лучше разделение; FPR: меньше значит меньше "
                  "ложных обвинений человека; контраст в вероятности класса A: "
                  "больше значит более AI-подобно",
        abbreviations="AUROC — площадь под ROC-кривой; FPR — доля ложных "
                      "срабатываний на человеческих текстах; ablation — сравнение "
                      "модели с признаками и без них, не разложение балла на "
                      "слагаемые",
        na_rule="AUROC не определён на пяти одноклассовых holdout, поэтому в "
                "колонке указано, на скольких он посчитан; контраст считался только "
                "для основной модели, у прочих вариантов стоит прочерк; у модели "
                "negative-control FPR не считался, а holdout тринадцать: она "
                "прогонялась серией кластерных перестановок и получила статус "
                "post hoc. У модели source-only AUROC 1.0000 сочетается с FPR "
                "1.0000: ранжирование по площадке безошибочно, но при пороге 0.5 "
                "модель объявляет машинным весь человеческий тест невиданной "
                "площадки",
        multiplicity=NOTE_BONF,
    )


def t5_fairness():
    groups = read_csv(ANALYSIS / "fairness-v2-groups.csv")
    rows_src = [r for r in groups
                if r["model"] == "main" and r["estimand"] == "full"]
    by_group = defaultdict(list)
    for r in rows_src:
        by_group[r["group"]].append(r)

    # Пометы hard-human совпадают с жанрами один в один (проверено по реестру:
    # HH-formal-register = science 167, HH-polished = news 152,
    # HH-translation = translation 73), поэтому строки не дублируются, а
    # соответствие выносится в отдельную колонку.
    hh_of_genre = {"genre=science": "HH-formal-register",
                   "genre=news": "HH-polished",
                   "genre=translation": "HH-translation"}
    keep = ["весь человеческий тест", "genre=science", "genre=translation",
            "genre=news", "genre=seo", "genre=prose"]
    rows = []
    for g in keep:
        rr = by_group.get(g)
        if not rr:
            continue
        fprs = [float(x["fpr"]) for x in rr]
        deltas = [float(x["delta"]) for x in rr if x["delta"] not in ("", None)]
        worst = max(rr, key=lambda x: float(x["fpr"]))
        rows.append([
            g, hh_of_genre.get(g, "—"), str(len(rr)), f4(median(fprs)),
            minus(f"{min(fprs):.4f}–{max(fprs):.4f}"),
            f4(median(deltas)) if deltas else "—",
            worst["split"].replace("holdout_", ""),
        ])
    return table(
        number=5, kind="main", slug="formal-register-fairness",
        title="Цена ошибки по подгруппам человеческого корпуса: "
              "формальный регистр, перевод и жанры",
        columns=["Подгруппа", "Помета hard-human", "Holdout с подгруппой",
                 "Медиана FPR", "Размах FPR",
                 "Медиана разности с остальным тестом", "Худший holdout"],
        rows=rows,
        unit="человеческий документ внутри группового holdout",
        denominator="18 групповых holdout; в каждом подгруппа считается на "
                    "человеческой части своего теста",
        series="серия v2, статус current",
        statistics="Медиана и размах FPR по holdout, где подгруппа представлена; "
                   "разность — FPR подгруппы минус FPR остального человеческого "
                   "теста того же holdout. " + NOTE_CI,
        direction="меньше значит меньше ложных обвинений человека",
        abbreviations="FPR — доля ложных срабатываний; HH — hard-human, пометы "
                      "поверх корпуса: formal-register — формальный научный "
                      "регистр, translation — переводная проза, polished — "
                      "редакторски вычищенный текст",
        na_rule="каждая помета hard-human совпадает с жанром один в один: "
                "formal-register — все 167 документов science, polished — все 152 "
                "news, translation — все 73 translation. Поэтому строки не "
                "дублируются, а помета показана колонкой; отделить эффект пометы "
                "от эффекта жанра этот дизайн не позволяет. Прочерк в колонке "
                "разности означает, что подгруппа совпала со всем человеческим "
                "тестом",
        multiplicity=NOTE_NO_MULT + " Подгрупповой анализ описательный.",
    )


def t6_calibration():
    rows_src = read_csv(ANALYSIS / "calibration-v1-by-holdout.csv")
    man = read_json(ANALYSIS / "calibration-v1-manifest.json")
    статусы = man["statuses"]

    def col(k):
        return [float(r[k]) for r in rows_src]

    worst = max(rows_src, key=lambda r: float(r["brier"]))
    rows = [
        ["Brier", f4(mean(col("brier"))), f"{min(col('brier')):.4f}–{max(col('brier')):.4f}",
         worst["split_name"].replace("holdout_", ""), статусы["brier"]],
        ["ECE, 10 бинов", f4(mean(col("ece_10"))),
         f"{min(col('ece_10')):.4f}–{max(col('ece_10')):.4f}",
         max(rows_src, key=lambda r: float(r["ece_10"]))["split_name"].replace("holdout_", ""),
         "описательная"],
        ["MCE, 10 бинов", f4(mean(col("mce_10"))),
         f"{min(col('mce_10')):.4f}–{max(col('mce_10')):.4f}",
         max(rows_src, key=lambda r: float(r["mce_10"]))["split_name"].replace("holdout_", ""),
         "описательная"],
    ]
    for cov in (100, 90, 80, 70, 50):
        r = col(f"risk_at_{cov}")
        rows.append([f"Риск при покрытии {cov}%", f4(mean(r)),
                     f"{min(r):.4f}–{max(r):.4f}",
                     max(rows_src, key=lambda x: float(x[f"risk_at_{cov}"]))["split_name"].replace("holdout_", ""),
                     "описательная"])
    return table(
        number=6, kind="main", slug="calibration-and-risk-coverage",
        title="Калибровка вероятностей и обмен покрытия на риск",
        columns=["Величина", "Macro-среднее по 18 holdout", "Размах по holdout",
                 "Худший holdout", "Статус величины"],
        rows=rows,
        unit="документ внутри группового holdout",
        denominator="18 групповых holdout; macro-среднее берётся по holdout, а не "
                    "по документам, поэтому крупные holdout не перевешивают мелкие",
        series="серия calibration-v1, посчитана read-only по предсказаниям "
               "fairness-v2",
        statistics="Brier — средний квадрат отклонения вероятности от исхода; ECE и "
                   "MCE — средний и максимальный разрыв между вероятностью и "
                   "частотой на равночастотных бинах; риск — доля ошибочных решений "
                   "среди оставленных при сокращении покрытия. Интервалы не "
                   "считались.",
        direction="меньше значит лучше у всех величин таблицы",
        abbreviations="ECE — expected calibration error; MCE — maximum calibration "
                      "error; покрытие — доля документов, по которым решение "
                      "принимается, остальные уходят в отказ",
        na_rule="пропусков нет; порог по этой таблице не выбирается",
        multiplicity=NOTE_NO_MULT,
    )


def t7_stress():
    cells = read_csv(ATTEMPT / "stress-p2a-r11-cells.csv")
    p1 = read_csv(ANALYSIS / "stress-p1-r4-scores.csv")
    p3 = read_csv(ANALYSIS / "stress-p3-r4-scores.csv")
    p4 = read_csv(ANALYSIS / "stress-p4-r5-scores.csv")

    def num(row, *keys):
        for k in keys:
            if k in row and row[k] not in ("", None):
                return float(row[k])
        return None

    p2_by_t = defaultdict(list)
    for r in cells:
        p2_by_t[int(r["transform_number"])].append(r)

    p1_by_t, p3_by_t, p4_by_t = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in p1:
        t = int(r.get("transform_number") or r["transformation_id"].lstrip("t"))
        p1_by_t[t].append(r)
    for r in p3:
        p3_by_t[int(r["transform_number"])].append(r)
    for r in p4:
        p4_by_t[int(r["transform_number"])].append(r)

    rows = []
    for t in TRANSFORMS:
        c = p2_by_t[t]
        flip = mean(float(r["flip_rate"]) for r in c)
        inst = mean(float(r["instability_rate"]) for r in c)

        d1 = [abs(num(r, "index_transformed") - num(r, "index_baseline"))
              for r in p1_by_t[t] if num(r, "index_baseline") is not None
              and num(r, "index_transformed") is not None]
        share1 = sum(1 for x in d1 if x > 5.0) / len(d1) if d1 else None

        d3 = [abs(float(r["delta_nll"])) for r in p3_by_t[t]
              if r.get("delta_nll") not in ("", None)]

        d4 = []
        for r in p4_by_t[t]:
            base = num(r, "median_baseline")
            seeds = [num(r, f"score_seed{i}") for i in (1, 2, 3)]
            seeds = [s for s in seeds if s is not None]
            if base is not None and seeds:
                d4.append(abs(median(seeds) - base))
        share4 = sum(1 for x in d4 if x > 5.0) / len(d4) if d4 else None

        unchanged = [r for r in p1_by_t[t]
                     if str(r.get("input_unchanged", "")).strip() in ("1", "true", "True")]
        share_unchanged = len(unchanged) / len(p1_by_t[t]) if p1_by_t[t] else None

        rows.append([
            TRANSFORM_NAMES[t],
            f3(share_unchanged) if share_unchanged is not None else "—",
            f3(inst), f3(flip),
            f3(share1) if share1 is not None else "—",
            f4(median(d3)) if d3 else "—",
            f3(share4) if share4 is not None else "—",
        ])
    rows.sort(key=lambda r: -float(r[3]))
    return table(
        number=7, kind="main", slug="stress-ten-transformations",
        title="Устойчивость решения к десяти преобразованиям текста "
              "по четырём процедурам",
        columns=["Преобразование", "Вход признаков не изменился",
                 "Процедура 2: нестабильность",
                 "Процедура 2: смена решения", "Процедура 1: доля |Δ| > 5 пунктов",
                 "Процедура 3: медиана |ΔNLL|", "Процедура 4: доля |Δ| > 5 пунктов"],
        rows=rows,
        unit="у процедуры 2 — пара «документ × допустимая модель»; у процедур 1, 3 "
             "и 4 — документ",
        denominator="60 документов панели; 464 пары на преобразование у процедуры 2, "
                    "60 документов на преобразование у остальных",
        series="процедура 2 — стресс-ревизия r5, процедура 4 — r5, процедуры 1 и 3 — "
               "r4 в области пригодности десяти преобразований",
        statistics="Доли и медианы по ячейкам; интервалы не считались. Событие "
                   "нестабильности определяется правилом своей процедуры, поэтому "
                   "колонки между собой не складываются.",
        direction="меньше значит устойчивее решение",
        abbreviations="Δ — сдвиг оценки после преобразования; NLL — negative "
                      "log-likelihood; смена решения — переход через порог 0.5; "
                      "«вход признаков не изменился» — доля документов, у которых "
                      "после преобразования совпали профиль prose, профиль full и "
                      "все счётчики разметки, то есть процедура получила прежний вход",
        na_rule="преобразование t14 исключено: амендмент ревизии r5 перевёл его в "
                "статус not executable, поэтому знаменатель равен десяти, а не "
                "одиннадцати",
        multiplicity=NOTE_NO_MULT + " Стресс-тест описательный.",
    )


def t8_mixed():
    ident = read_json(ANALYSIS / "mixed-identifiability.json")
    m1 = read_json(ANALYSIS / "mixed-m1-v2.json")
    _, design = desc("mixed_models_design")
    purpose = {row[0]: (row[1], row[2], row[3]) for row in design}

    rows = []
    for m in ident["models"]:
        corpus, effects, outcome = purpose[m["model"]]
        aliased = len(m["aliased"])
        verdict = m["verdict"]
        if m["model"] == "M1":
            verdict += ("; подгонка запущена и не сошлась"
                        if not m1["converged"] else "; подгонка сошлась")
        rows.append([m["model"], corpus, outcome, str(m["n"]),
                     f"{m['rank']} из {m['columns_candidate']}", str(aliased), verdict])
    return table(
        number=8, kind="main", slug="mixed-effects-status",
        title="Три предрегистрированные mixed-effects модели: "
              "идентифицируемость и статус",
        columns=["Модель", "Корпус", "Первичный исход", "Наблюдений",
                 "Ранг матрицы плана", "Слитых уровней", "Статус"],
        rows=rows,
        unit="наблюдение — документ",
        denominator="1079 машинных, 803 человеческих и 1882 объединённых документа",
        series="серия v2, диагностика идентифицируемости от 2026-07-29",
        statistics="Ранг матрицы плана против числа кандидатных колонок; слитые "
                   "уровни — факторы, не отличимые по дизайну. Оценок эффектов "
                   "таблица не приводит: ни одна модель не дала первичный исход.",
        direction="шкалы нет, таблица описывает статус процедуры",
        abbreviations="REML — restricted maximum likelihood; слитый уровень — "
                      "уровень фактора, линейно зависимый от остальных колонок плана",
        na_rule="колонка оценок отсутствует намеренно: M1 не сошлась, M2 и M3 "
                "неидентифицируемы по дизайну, поэтому чисел, которые можно было бы "
                "опубликовать, нет",
        multiplicity=NOTE_NO_MULT,
    )


# ── Supplementary ────────────────────────────────────────────────────────────

def read_md_table(path, first_cell):
    """Читает markdown-таблицу замороженного отчёта по первой ячейке шапки."""
    register(path)
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        if line.startswith("|") and line.split("|")[1].strip() == first_cell:
            cols = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                rows.append([c.strip().replace("**", "")
                             for c in row.strip("|").split("|")])
            return cols, rows
    raise SystemExit(f"{path.name}: не найдена таблица с шапкой «{first_cell}»")


def s1_flow():
    _, flow = desc("corpus_flow")
    _, strata = desc("corpus_flow_by_stratum")
    before, after = flow[0], flow[2]

    def delta(b, a):
        d = int(a) - int(b)
        return "без изменений" if d == 0 else minus(f"{d:+d}")

    rows = []
    for i, name in enumerate(("машинная часть", "человеческая часть", "весь корпус"), 1):
        rows.append([name, before[i], after[i], delta(before[i], after[i])])
    for r in strata:
        rows.append(["страта: " + r[0], r[1], r[2], r[3]])
    return table(
        number=1, kind="supp", slug="corpus-flow",
        title="Поток данных и исключения по стратам",
        columns=["Срез", "Было", "Стало", "Изменение"],
        rows=rows,
        unit="документ",
        denominator="1916 документов до коррекции извлечения, 1882 после",
        series="реестр корпуса, коррекция correction-v5.0 от 2026-07-29",
        statistics="Счётчики документов; оценок и интервалов нет.",
        direction="шкалы нет",
        abbreviations="prep-v5 — действующий профиль препроцессинга; страта "
                      "человеческой части названа вместе с её кодом жанра",
        na_rule="исключены 34 документа, все человеческие: после коррекции дефекта "
                "извлечения текст стал короче зарегистрированного порога в 700 слов",
        multiplicity=NOTE_NO_MULT,
    )


def s2_channels():
    cols, rows = desc("machine_channels")
    return table(
        number=2, kind="supp", slug="machine-channels",
        title="Каналы генерации машинной части: модели, доступ и параметры",
        columns=cols, rows=rows,
        unit="канал генерации",
        denominator="четыре канала, 1079 машинных документов; каждый канал дал "
                    "270 документов, кроме nemotron — 269",
        series="реестр корпуса",
        statistics="Таблица описывает условия генерации; чисел прогона в ней нет.",
        direction="шкалы нет",
        abbreviations="top-p — доля вероятностной массы при сэмплировании; "
                      "«по умолчанию канала» означает, что оболочка параметры не "
                      "раскрывает",
        na_rule="у двух каналов температура и top-p недоступны: оболочка их не "
                "сообщает, поэтому межканальные различия нельзя приписывать модели",
        multiplicity=NOTE_NO_MULT,
    )


def s3_preprocessing():
    cols, rows = desc("preprocessing_verdicts")
    return table(
        number=3, kind="supp", slug="preprocessing-verdicts",
        title="Вердикты коррекции извлечения по 113 кандидатам",
        columns=cols, rows=rows,
        unit="документ",
        denominator="113 кандидатов с долей дословно повторённых предложений выше "
                    "0.1, найденные при просмотре 1916 документов; скорректированы 103",
        series="препроцессинг prep-v5, коррекция correction-v5.0",
        statistics="Счётчики документов по вердикту.",
        direction="шкалы нет",
        abbreviations="extraction-defect — дефект извлечения текста; "
                      "intermediary-defect — дефект промежуточного файла; "
                      "unresolved — происхождение повтора не установлено; "
                      "source-property — повтор присутствует в источнике",
        na_rule="десять документов из 113 остались без правки: девять unresolved и "
                "один source-property",
        multiplicity=NOTE_NO_MULT,
    )


def s4_fpr_by_holdout():
    rows_src = [r for r in read_csv(ANALYSIS / "clf-v2-p2a-metrics.csv")
                if r["model"] == "main" and r["estimand"] == "full"]
    rows_src.sort(key=lambda r: -float(r["fpr"]))
    rows = []
    for r in rows_src:
        rows.append([r["split"].replace("holdout_", ""), r["n"], r["n_human"],
                     f4(r["fpr"]), ci(r["fpr_ci_low"], r["fpr_ci_high"]),
                     f4(r["fpr_HH-formal-register"]) or "—",
                     f4(r["fpr_HH-translation"]) or "—",
                     f4(r["fpr_HH-polished"]) or "—"])
    return table(
        number=4, kind="supp", slug="fpr-by-holdout",
        title="Доля ложных срабатываний основной модели по каждому "
              "из 18 групповых holdout",
        columns=["Holdout", "Документов в тесте", "Человеческих", "FPR", "95% CI",
                 "FPR формального регистра", "FPR перевода", "FPR вычищенного текста"],
        rows=rows,
        unit="человеческий документ внутри holdout",
        denominator="18 групповых holdout; человеческая часть теста своя у каждого",
        series="серия v2, статус current",
        statistics=NOTE_CI,
        direction="меньше значит меньше ложных обвинений человека",
        abbreviations="FPR — доля ложных срабатываний; HH-подгруппы — пометы "
                      "поверх корпуса, документ входит в общий расчёт один раз",
        na_rule="прочерк означает, что подгруппа в этом holdout не представлена. "
                "Три holdout — genre_news, genre_science и genre_translation — "
                "дают одинаковые числа: их тестовые части совпадают по составу, "
                "и это ограничение дизайна, а не опечатка",
        multiplicity=NOTE_NO_MULT,
    )


def s5_quality_by_holdout():
    rows_src = [r for r in read_csv(ANALYSIS / "clf-v2-p2a-metrics.csv")
                if r["model"] == "main" and r["estimand"] == "full"]
    rows_src.sort(key=lambda r: r["split"])
    rows = []
    for r in rows_src:
        rows.append([r["split"].replace("holdout_", ""), r["n"], r["n_machine"],
                     r["n_human"], f4(r["auroc"]) or "не определён",
                     f4(r["balanced_accuracy"]), f4(r["mcc"]),
                     f4(r["tpr_at_1pct_fpr"]) or "—", r["C"], r["n_features"]])
    return table(
        number=5, kind="supp", slug="classifier-quality-by-holdout",
        title="Качество основной модели по каждому из 18 групповых holdout",
        columns=["Holdout", "Документов", "Машинных", "Человеческих", "AUROC",
                 "Сбалансированная точность", "MCC", "TPR при FPR 1%",
                 "Выбранное C", "Признаков"],
        rows=rows,
        unit="групповой holdout",
        denominator="18 holdout; состав теста задан группирующей переменной",
        series="серия v2, статус current",
        statistics="Метрики посчитаны на тесте своего holdout; C выбрано вложенной "
                   "кросс-валидацией на обучающей части.",
        direction="AUROC, сбалансированная точность, MCC и TPR: больше значит лучше",
        abbreviations="MCC — коэффициент корреляции Мэтьюса; TPR — доля верно "
                      "распознанных машинных текстов; C — параметр регуляризации",
        na_rule="AUROC не определён на пяти одноклассовых holdout: в тесте нет "
                "одного из двух классов, и ранжирование сравнивать не с чем",
        multiplicity=NOTE_NO_MULT,
    )


def s6_calibration_by_holdout():
    rows_src = read_csv(ANALYSIS / "calibration-v1-by-holdout.csv")
    rows_src.sort(key=lambda r: -float(r["brier"]))
    rows = []
    for r in rows_src:
        rows.append([r["split_name"].replace("holdout_", ""), r["n"], r["n_machine"],
                     f4(r["brier"]), f4(r["ece_10"]), f4(r["mce_10"]),
                     r["bins_nonempty_10"], f4(r["ece_5"]), f4(r["ece_20"]),
                     f4(r["risk_at_100"]), f4(r["risk_at_50"])])
    return table(
        number=6, kind="supp", slug="calibration-by-holdout",
        title="Калибровка вероятностей по каждому из 18 групповых holdout",
        columns=["Holdout", "Документов", "Машинных", "Brier", "ECE-10", "MCE-10",
                 "Непустых бинов", "ECE-5", "ECE-20", "Риск при покрытии 100%",
                 "Риск при покрытии 50%"],
        rows=rows,
        unit="документ внутри группового holdout",
        denominator="18 групповых holdout",
        series="серия calibration-v1, read-only по предсказаниям fairness-v2",
        statistics="Бины равночастотные; при совпадении вероятностей документ "
                   "попадает в бин по правилу середины ранга. ECE-5 и ECE-20 — "
                   "sensitivity к числу бинов. Интервалы не считались.",
        direction="меньше значит лучше",
        abbreviations="ECE — expected calibration error; MCE — maximum calibration "
                      "error; число после дефиса — количество бинов",
        na_rule="колонка непустых бинов показывает, сколько бинов из десяти "
                "получили хотя бы один документ: на мелких holdout часть бинов "
                "пуста, и ECE считается по заполненным",
        multiplicity=NOTE_NO_MULT,
    )


def s7_stress_full():
    cells = read_csv(ATTEMPT / "stress-p2a-r11-cells.csv")
    scores2 = read_csv(ATTEMPT / "stress-p2a-r11-scores.csv")
    p1 = read_csv(ANALYSIS / "stress-p1-r4-scores.csv")
    p3 = read_csv(ANALYSIS / "stress-p3-r4-scores.csv")
    p4 = read_csv(ANALYSIS / "stress-p4-r5-scores.csv")

    p2_by_t = defaultdict(list)
    for r in cells:
        p2_by_t[int(r["transform_number"])].append(r)
    p2_pairs = defaultdict(list)
    for r in scores2:
        p2_pairs[int(r["transform_number"])].append(
            abs(float(r["prob_transformed"]) - float(r["prob_baseline"])))
    p1_by_t, p3_by_t, p4_by_t = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in p1:
        p1_by_t[int(r["transformation_id"])].append(r)
    for r in p3:
        p3_by_t[int(r["transform_number"])].append(r)
    for r in p4:
        p4_by_t[int(r["transform_number"])].append(r)

    rows = []
    for t in TRANSFORMS:
        name = TRANSFORM_NAMES[t]

        d1 = [float(r["index_transformed"]) - float(r["index_baseline"])
              for r in p1_by_t[t]]
        rows.append([name, "1 — индекс стиля", str(len(d1)), "документ",
                     f3(median([abs(x) for x in d1])),
                     f3(max([abs(x) for x in d1])),
                     f3(sum(1 for x in d1 if abs(x) > 5.0) / len(d1)),
                     "|Δ| > 5.0 пункта"])

        c = p2_by_t[t]
        d2 = p2_pairs[t]
        rows.append([name, "2 — классификатор", str(len(d2)),
                     "пара документ × модель",
                     f3(median(d2)), f3(max(d2)),
                     f3(mean(float(r["flip_rate"]) for r in c)),
                     "|Δp| > 0.05 и смена решения"])

        d3 = [abs(float(r["delta_nll"])) for r in p3_by_t[t]
              if r["delta_nll"] not in ("", None)]
        rows.append([name, "3 — zero-shot NLL", str(len(d3)), "документ",
                     f4(median(d3)), f4(max(d3)), "—",
                     "порог не применяется"])

        d4 = []
        for r in p4_by_t[t]:
            seeds = [float(r[f"score_seed{i}"]) for i in (1, 2, 3)
                     if r[f"score_seed{i}"] not in ("", None)]
            if seeds and r["median_baseline"] not in ("", None):
                d4.append(median(seeds) - float(r["median_baseline"]))
        rows.append([name, "4 — модель-судья", str(len(d4)), "документ",
                     f3(median([abs(x) for x in d4])),
                     f3(max([abs(x) for x in d4])),
                     f3(sum(1 for x in d4 if abs(x) > 5.0) / len(d4)),
                     "|Δ| > 5.0 пункта по медиане трёх сидов"])
    return table(
        number=7, kind="supp", slug="stress-by-transform-and-procedure",
        title="Стресс-тест построчно: десять преобразований × четыре процедуры",
        columns=["Преобразование", "Процедура", "Ячеек", "Единица", "Медиана |Δ|",
                 "Максимум |Δ|", "Доля событий нестабильности", "Правило события"],
        rows=rows,
        unit="указана в отдельной колонке: документ у процедур 1, 3 и 4, пара "
             "«документ × допустимая модель» у процедуры 2",
        denominator="60 документов панели; 464 пары на преобразование у процедуры 2",
        series="процедуры 2 и 4 — ревизия r5, процедуры 1 и 3 — r4 в области "
               "пригодности десяти преобразований",
        statistics="Медианы и максимумы модуля сдвига по ячейкам своей процедуры: "
                   "у процедур 1, 3 и 4 — по 60 документам, у процедуры 2 — по 464 "
                   "парам. Доля событий считается по правилу своей процедуры. "
                   "Интервалы не считались.",
        direction="меньше значит устойчивее решение",
        abbreviations="Δ — сдвиг оценки после преобразования; Δp — сдвиг "
                      "вероятности класса A",
        na_rule="у процедуры 3 доля событий не определена: бинарный порог к "
                "непрерывному NLL не применяется; преобразование t14 исключено "
                "как not executable по амендменту ревизии r5",
        multiplicity=NOTE_NO_MULT,
    )


def s8_identifiability():
    ident = read_json(ANALYSIS / "mixed-identifiability.json")
    rows = []
    for m in ident["models"]:
        for i, (cname, c) in enumerate(m["clusters"].items()):
            first = i == 0
            rows.append([m["model"], str(m["n"]) if first else "",
                         f"{m['rank']} из {m['columns_candidate']}" if first else "",
                         cname, str(c["clusters"]), str(c["min_size"]),
                         str(c["median_size"]), str(c["singletons"]),
                         ("; ".join(m["aliased"]) if m["aliased"] else "нет")
                         if first else "",
                         m["verdict"] if first else ""])
    return table(
        number=8, kind="supp", slug="mixed-identifiability",
        title="Диагностика идентифицируемости трёх mixed-effects моделей",
        columns=["Модель", "Наблюдений", "Ранг плана", "Случайный эффект",
                 "Кластеров", "Минимальный размер", "Медианный размер",
                 "Кластеров из одного наблюдения", "Слитые уровни", "Вердикт"],
        rows=rows,
        unit="строка описывает один случайный эффект одной модели",
        denominator="три модели предрегистрации; 1079, 803 и 1882 наблюдения",
        series="серия v2, диагностика от 2026-07-29",
        statistics="Ранг матрицы плана против числа кандидатных колонок; слитые "
                   "уровни выявлены до подгонки.",
        direction="шкалы нет",
        abbreviations="слитый уровень — уровень фактора, линейно зависимый от "
                      "остальных колонок плана; кластер из одного наблюдения "
                      "не даёт оценить дисперсию случайного эффекта",
        na_rule="наблюдения, ранг, слитые уровни и вердикт относятся к модели "
                "целиком и стоят в её первой строке; у M2 случайный эффект "
                "«задание» имеет ноль кластеров, поскольку человеческие тексты "
                "заданий не получают. Оценок эффектов таблица не содержит: "
                "проверка идентифицируемости шла до подгонки, и часть моделей до "
                "подгонки не дошла",
        multiplicity=NOTE_NO_MULT,
    )


def s9_repeats():
    rows_src = read_csv(ANALYSIS / "repeat-scan-v2-verdicts.csv")
    rows_src.sort(key=lambda r: -float(r["repeat_share"]))
    rows = []
    for r in rows_src:
        rows.append([r["document_id"], r["source"], r["genre"],
                     f3(r["repeat_share"]), r["max_multiplicity"],
                     r["longest_repeated_block"], r["modal_offset"],
                     r["mechanism"], r["verdict"]])
    return table(
        number=9, kind="supp", slug="repeat-audit",
        title="Аудит дословных повторов: документы, прошедшие симметричный скан",
        columns=["Документ", "Источник", "Жанр", "Доля повторов",
                 "Максимальная кратность", "Длиннейший повторённый блок",
                 "Модальный сдвиг", "Механизм", "Вердикт"],
        rows=rows,
        unit="документ",
        denominator="скан прошли обе части корпуса, 1882 документа; порог отбора — "
                    "доля дословно повторённых предложений выше 0.1",
        series="скан repeat-scan-v2, профили prep-v4 и prep-v5",
        statistics="Доля повторов — сумма кратностей повторяющихся предложений, "
                   "делённая на число предложений документа.",
        direction="больше значит сильнее засорён повторами",
        abbreviations="модальный сдвиг — наиболее частое расстояние между "
                      "повторяющимися предложениями в позициях",
        na_rule="в таблице только документы, прошедшие порог; остальные в скан не "
                "попали и строк не имеют",
        multiplicity=NOTE_NO_MULT,
    )


def s10_blind():
    rows_src = read_csv(ANALYSIS / "instability-v1-blind-verdicts.csv")
    rows_src.sort(key=lambda r: r["card_id"])
    rows = []
    for r in rows_src:
        rows.append([r["card_id"], r["expected_change_present"],
                     r["unexpected_changes"], r["change_type"], r["verdict"]])
    return table(
        number=10, kind="supp", slug="blind-instability-cards",
        title="Слепой разбор тридцати случаев нестабильности: вердикты оценщика",
        columns=["Карточка", "Ожидаемое изменение присутствует",
                 "Есть неожиданные изменения", "Тип изменения", "Вердикт"],
        rows=rows,
        unit="карточка: пара текстов до и после преобразования",
        denominator="30 карточек, отобранных по правилу «сортировка по доле смены "
                    "решения, затем по максимальному сдвигу вероятности»",
        series="разбор instability-v1, слепая выдача с сидом 20260801",
        statistics="Вердикты качественные; величин в таблице нет.",
        direction="шкалы нет",
        abbreviations="составное воздействие — преобразование изменило текст не "
                      "только заявленным способом; интерпретируемый стресс-случай — "
                      "изменение соответствует объявленному",
        na_rule="оценщик один, разметка не дублировалась, поэтому согласие "
                "оценщиков не считалось",
        multiplicity=NOTE_NO_MULT,
    )


def s11_invariants():
    cols, rows = desc("gate_invariants")
    return table(
        number=11, kind="supp", slug="preflight-invariants",
        title="Шесть инвариантов допуска к расчёту серии v2",
        columns=cols, rows=rows,
        unit="инвариант",
        denominator="шесть инвариантов; допуск выставляется только при шести "
                    "пройденных",
        series="манифест допуска, ревизия 6",
        statistics="Таблица описывает шлюз; оценок в ней нет.",
        direction="шкалы нет",
        abbreviations="raw и normalized — сырое и нормированное значение признака; "
                      "percentile-реализация — расчёт перцентилей на референсной "
                      "выборке",
        na_rule="пропусков нет",
        multiplicity=NOTE_NO_MULT,
    )


def s12_revisions():
    cols, rows = desc("manifest_revisions")
    return table(
        number=12, kind="supp", slug="manifest-revisions",
        title="Шесть ревизий манифеста допуска и причины перехода",
        columns=cols, rows=rows,
        unit="ревизия манифеста",
        denominator="шесть ревизий за 29 июля 2026",
        series="манифесты допуска серии v2",
        statistics="Таблица описывает историю прогонов; чисел в ней нет.",
        direction="шкалы нет",
        abbreviations="inner CV — вложенная кросс-валидация; GroupKFold — "
                      "разбиение с непересекающимися группами",
        na_rule="ошибочный манифест не удаляется, а помечается invalidated с "
                "причиной, поэтому строк ровно столько, сколько было ревизий",
        multiplicity=NOTE_NO_MULT,
    )


def s13_reuse():
    cols, rows = desc("reuse_gate")
    return table(
        number=13, kind="supp", slug="reuse-gate",
        title="Шлюз переиспользования разборов: сверка на sentinel-выборке",
        columns=cols, rows=rows,
        unit="этап разбора текста",
        denominator="шесть документов sentinel-выборки, по три каждого класса",
        series="шлюз feature_reuse_gate, серия v2",
        statistics="Сверка построена на пересчёте с нуля и сравнении с кешем.",
        direction="меньше расхождение значит надёжнее переиспользование",
        abbreviations="NER — распознавание именованных сущностей; sem-v1 — "
                      "семейство семантических признаков",
        na_rule="побитового совпадения у эмбеддингов не бывает: кеш считался "
                "пачками через границы документов, и порядок суммирования другой",
        multiplicity=NOTE_NO_MULT,
    )


def s14_decomposition():
    cols_v2, rows_v2 = read_md_table(ANALYSIS / "score-v2-index-decomposition.md",
                                     "Контраст")
    cols_v1, rows_v1 = read_md_table(ANALYSIS / "score-v1-index-decomposition.md",
                                     "Контраст")
    rows = [[r[0], "v2"] + [minus(c) for c in r[1:]] for r in rows_v2]
    rows += [[r[0], "v1"] + [minus(c) for c in r[1:]] for r in rows_v1]
    return table(
        number=14, kind="supp", slug="index-decomposition",
        title="Декомпозиция индекса стиля на стилевую и структурную части, "
              "две серии",
        columns=[cols_v2[0], "Серия"] + cols_v2[1:],
        rows=rows,
        unit="пара текстов контраста",
        denominator="359 или 360 пар в зависимости от контраста",
        series="серии v2 и v1; v1 приводится как проверка устойчивости "
               "декомпозиции, а не как действующий результат",
        statistics="Разложение алгебраическое и точное: сумма вкладов совпадает с "
                   "замороженным контрастом с точностью до 1.6·10⁻⁴, что и "
                   "показывает колонка расхождения.",
        direction="знак вклада читается по конвенции «больше значит более "
                  "AI-подобно»",
        abbreviations="common — стилевые категории признаков; format — структурная "
                      "категория; Δfull — контраст полного балла из замороженного "
                      "прогона",
        na_rule="пропусков нет",
        multiplicity=NOTE_NO_MULT,
    )


def s15_volume():
    cols, rows_src = read_md_table(ANALYSIS / "stress-t10-t11-volume-defect.md",
                                   "Признак")
    rows = [[r[0], minus(r[1]), minus(r[2])] for r in rows_src]
    return table(
        number=15, kind="supp", slug="t10-t11-feature-shift",
        title="Сдвиг признаков при сокращении и расширении текста: "
              "снятая интерпретация через объём",
        columns=["Признак", "t10 сокращение: медиана Δz (документов)",
                 "t11 расширение: медиана Δz (документов)"],
        rows=rows,
        unit="документ панели",
        denominator="60 документов панели; в скобках — число документов, у которых "
                    "признак сдвинулся",
        series="стресс-ревизия r4, процедура 1; стандартизация по 1882 документам "
               "матрицы признаков v5",
        statistics="Медиана стандартизованного сдвига Δz по документам панели.",
        direction="знак показывает направление сдвига признака, не решения",
        abbreviations="Δz — сдвиг в стандартных отклонениях признака; M01 — "
                      "стандартное отклонение косинусов соседних пар предложений, "
                      "а не среднее сходство",
        na_rule="интерпретация «оба преобразования меняют объём, поэтому решение "
                "рушится» снята: форматная четвёрка сдвигается у обоих одинаково, "
                "а расходятся преобразования на M01",
        multiplicity=NOTE_NO_MULT,
    )


def s16_limitations():
    cols, rows = desc("limitations_groups")
    return table(
        number=16, kind="supp", slug="limitations-groups",
        title="Двадцать один пункт ограничений, свёрнутый в шесть групп "
              "основного текста",
        columns=cols, rows=rows,
        unit="группа ограничений",
        denominator="21 пункт полного списка, шесть групп в основном тексте",
        series="список ограничений от 2026-07-29",
        statistics="Таблица описывает состав; чисел прогона в ней нет.",
        direction="шкалы нет",
        abbreviations="ICC(2,1) — коэффициент внутриклассовой корреляции для "
                      "случайных оценщиков; bridge set — независимая выборка для "
                      "переноса выводов",
        na_rule="пункты 16, 18 и 20 в основной текст не вошли и остаются только в "
                "полном списке",
        multiplicity=NOTE_NO_MULT,
    )


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"сборка таблиц, {stamp}")
    print("основные:")
    for fn in (t1_corpus, t2_procedures, t3_o1, t4_classifier, t5_fairness,
               t6_calibration, t7_stress, t8_mixed):
        write_table(fn())
    print("supplementary:")
    for fn in (s1_flow, s2_channels, s3_preprocessing, s4_fpr_by_holdout,
               s5_quality_by_holdout, s6_calibration_by_holdout, s7_stress_full,
               s8_identifiability, s9_repeats, s10_blind, s11_invariants,
               s12_revisions, s13_reuse, s14_decomposition, s15_volume,
               s16_limitations):
        write_table(fn())

    outputs = {}
    for p in sorted(OUT.glob("tab-*.*")):
        outputs[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()

    manifest = {
        "series": "tables-v1",
        "approved": "восемь основных таблиц и шестнадцать Supplementary, "
                    "решение PI 2026-08-01",
        "registry": "08-paper/tables-registry.md",
        "rule": "скрипт только читает и форматирует; ручная правка чисел в выходных "
                "файлах запрещена, правка идёт в источник",
        "transform_denominator": {
            "transforms": TRANSFORMS,
            "excluded": "t14 — not executable по амендменту ревизии r5",
        },
        "tables": [{"number": t["number"], "kind": t["kind"], "file": name_of(t),
                    "title": t["title"]} for t in TABLES],
        "inputs_sha256": INPUTS,
        "outputs_sha256": outputs,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "created_at": stamp,
    }
    (OUT / "tables-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        newline="\n")
    print(f"  таблиц: {len(TABLES)}, входов: {len(INPUTS)}, файлов на выходе: "
          f"{len(outputs)}")
    print(f"  манифест: {(OUT / 'tables-manifest.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
