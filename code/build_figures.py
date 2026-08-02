#!/usr/bin/env python3
"""Построение шести иллюстраций рукописи из действующих данных серии v2.

    python 09-tools/build_figures.py

Комплект утверждён PI 2026-08-01. Прежний план `figures-list.md` из девяти
иллюстраций **не используется**: его источники указаны по серии v1
(`score-v1-o1-contrasts.csv`, `clf-v1-p2a-metrics.csv`, `fairness-v1-groups.csv`),
а действующая серия — v2, где числа другие.

Каждый рисунок строится из зафиксированного файла прогона; хеши всех входов
пишутся в манифест. Ни одно число здесь не пересчитывается — скрипт только читает
и рисует.

Выход: `08-paper/figures/fig-NN-*.png` и `.pdf`, манифест
`08-paper/figures/figures-manifest.json`.
"""

import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib.transforms import blended_transform_factory

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "08-paper" / "figures"
ANALYSIS = ROOT / "07-analysis"
ATTEMPT = ANALYSIS / "stress-r5-attempts" / "20260731T220132Z"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

plt.rcParams.update({
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "figure.dpi": 200,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.5,
})

INK = "#1a1a1a"
ACCENT = "#b5432f"
MUTED = "#8a8a8a"
SOFT = "#c8c8c8"

INPUTS = {}


def read(path, encoding="utf-8"):
    INPUTS[str(path.relative_to(ROOT)).replace("\\", "/")] = hashlib.sha256(
        path.read_bytes()).hexdigest()
    with path.open(encoding=encoding) as fh:
        return list(csv.DictReader(fh))


def save(fig, number, slug):
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"fig-{number:02d}-{slug}.{ext}")
    plt.close(fig)
    print(f"  рисунок {number}: fig-{number:02d}-{slug}")


# ── 1. Схема дизайна и корпуса ───────────────────────────────────────────────

def fig1():
    reg = read(ROOT / "04-corpus" / "documents-registry.csv", "utf-8-sig")
    machine = sum(1 for r in reg if r["origin_class"] == "A")
    human = sum(1 for r in reg if r["origin_class"] == "H")
    by_genre = defaultdict(int)
    for r in reg:
        if r["origin_class"] == "H":
            by_genre[r["genre"]] += 1

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.axis("off")

    def box(x, y, w, h, text, fc="white", ec=INK, fs=8.5, weight="normal"):
        ax.add_patch(Rectangle((x, y), w, h, facecolor=fc, edgecolor=ec, linewidth=0.9))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, color=INK, weight=weight)

    def arrow(x1, y1, x2, y2):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                     mutation_scale=9, color=MUTED, linewidth=0.8))

    box(0.01, 0.60, 0.30, 0.30,
        f"Машинная часть\n45 заданий × 4 канала\n× 3 режима × 2 повтора\n{machine} документов",
        fc="#f4f1ee", fs=7.8)
    box(0.01, 0.12, 0.30, 0.30,
        "Человеческая часть\nархив до 2022-01-01\nуровень регламента\nпо источнику",
        fc="#f4f1ee", fs=7.8)

    box(0.375, 0.36, 0.235, 0.30,
        "Препроцессинг\nprep-v5\nдва профиля:\nprose и full", fc="white", fs=7.8)

    box(0.68, 0.60, 0.30, 0.30,
        f"Аналитический корпус\n{machine + human} документов\nA = {machine}, H = {human}",
        fc="#f0efe8", weight="bold")
    box(0.68, 0.12, 0.30, 0.30,
        "Исключено 34\nвсе человеческие\nпосле коррекции\nизвлечения < 700 слов",
        fc="white", ec=SOFT)

    arrow(0.31, 0.75, 0.375, 0.56)
    arrow(0.31, 0.27, 0.375, 0.46)
    arrow(0.61, 0.51, 0.68, 0.70)
    arrow(0.61, 0.45, 0.68, 0.30)

    genres = ", ".join(f"{g} {n}" for g, n in sorted(by_genre.items(), key=lambda kv: -kv[1]))
    ax.text(0.5, 0.02, f"Человеческие страты: {genres}", ha="center",
            fontsize=7.5, color=MUTED)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    save(fig, 1, "design-and-corpus")


# ── 2. Четыре процедуры: контрасты O1 ────────────────────────────────────────

def fig2():
    rows = [r for r in read(ANALYSIS / "synthesis-o1-v2.csv")
            if r["estimand"] == "full" and r["contrast"] == "P3-P1"]
    rows.sort(key=lambda r: r["procedure"])

    fig, axes = plt.subplots(1, 4, figsize=(7.8, 2.9))
    fig.subplots_adjust(wspace=0.62)
    for ax, r in zip(axes, rows):
        est = float(r["mean_diff_raw"])
        lo, hi = float(r["ci_low_raw"]), float(r["ci_high_raw"])
        sign = r["sign_after_convention"]
        colour = ACCENT if sign.strip() == "+" else INK
        ax.errorbar([0], [est], yerr=[[est - lo], [hi - est]], fmt="o",
                    color=colour, capsize=3, markersize=5, linewidth=1.2)
        ax.axhline(0, color=MUTED, linewidth=0.8, linestyle="--")
        name = r["procedure"].split("—")[1].strip() if "—" in r["procedure"] else r["procedure"]
        ax.set_title(f"{r['procedure'].split('—')[0].strip()}. {name}", fontsize=8)
        ax.set_xticks([])
        ax.set_ylabel(r["unit"], fontsize=7.5)
        ax.tick_params(labelsize=7.5)
        ax.text(0.5, -0.06, f"{est:+.4g}   p = {float(r['p_value']):.4f}\n"
                f"знак после конвенции: {sign.strip()}",
                transform=ax.transAxes, ha="center", va="top",
                fontsize=7.5, color=colour)
    fig.suptitle("Контраст P3 − P1, estimand full: четыре операционализации в своих шкалах",
                 fontsize=9.5, y=1.30)
    fig.text(0.5, 1.19,
             "Панели построены в разных шкалах: единица своя у каждой процедуры.\n"
             "Величины между процедурами не сравниваются — сопоставляется только "
             "знак и то, накрывает ли интервал ноль.\n"
             "Показанное значение — сырая метрика; строка «знак после конвенции» "
             "относится к общему правилу интерпретации\n«больше значит более "
             "AI-подобно» и у процедуры 3 не совпадает со знаком сырой величины.",
             ha="center", va="top", fontsize=7.2, color=MUTED, linespacing=1.5)
    save(fig, 2, "o1-four-procedures")


# ── 3. Неоднородность классификатора по holdout ──────────────────────────────

def fig3():
    rows = [r for r in read(ANALYSIS / "clf-v2-p2a-metrics.csv")
            if r.get("model") == "main" and r.get("estimand") == "full"]
    rows.sort(key=lambda r: float(r["fpr"]))
    names = [r["split"].replace("holdout_", "") for r in rows]
    fpr = [float(r["fpr"]) for r in rows]
    lo = [float(r["fpr_ci_low"]) for r in rows]
    hi = [float(r["fpr_ci_high"]) for r in rows]

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    y = range(len(rows))
    colours = [ACCENT if n in ("genre_seo", "genre_prose") else INK for n in names]
    for i, (f, l, h, c) in enumerate(zip(fpr, lo, hi, colours)):
        ax.plot([l, h], [i, i], color=c, linewidth=1.0, alpha=0.55)
        ax.plot([f], [i], "o", color=c, markersize=4.5)
    ax.set_yticks(list(y)); ax.set_yticklabels(names, fontsize=8)
    ax.axvline(median(fpr), color=MUTED, linestyle="--", linewidth=0.8)
    tr = blended_transform_factory(ax.transData, ax.transAxes)
    ax.text(median(fpr), 1.01, f" медиана {median(fpr):.4f}", transform=tr,
            ha="left", va="bottom", fontsize=7.5, color=MUTED)
    ax.set_xlabel("Доля ложных срабатываний на человеческих текстах, 95% интервал")
    ax.set_title("Цена ошибки распределена неравномерно: 18 групповых holdout",
                 fontsize=9.5, pad=16)
    save(fig, 3, "classifier-heterogeneity")


# ── 4. Калибровка и risk–coverage ────────────────────────────────────────────

def fig4():
    rows = read(ANALYSIS / "calibration-v1-by-holdout.csv")
    manifest = json.loads((ANALYSIS / "calibration-v1-manifest.json").read_text(encoding="utf-8"))
    INPUTS["07-analysis/calibration-v1-manifest.json"] = hashlib.sha256(
        (ANALYSIS / "calibration-v1-manifest.json").read_bytes()).hexdigest()
    s = manifest["summary"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.4, 3.2))

    ece = sorted(((float(r["ece_10"]), r["split_name"].replace("holdout_", "")) for r in rows),
                 reverse=True)
    ax1.barh([n for _, n in ece][::-1], [v for v, _ in ece][::-1],
             color=[ACCENT if n in ("genre_seo", "genre_prose") else SOFT
                    for _, n in ece][::-1], height=0.7)
    ax1.axvline(s["ece_10_macro"], color=INK, linestyle="--", linewidth=0.9)
    ax1.text(s["ece_10_macro"], -0.7, f" macro {s['ece_10_macro']:.4f}",
             fontsize=7.5, color=INK)
    ax1.set_xlabel("ECE, 10 равночастотных бинов")
    ax1.set_title("Калибровка неоднородна", fontsize=9.5)
    ax1.tick_params(labelsize=7.5)

    cov = [1.0, 0.9, 0.8, 0.7, 0.5]
    macro = [s["risk_macro"][f"at_{int(c*100)}"] for c in cov]
    pooled = [s["risk_pooled"][f"at_{int(c*100)}"] for c in cov]
    ax2.plot(cov, macro, "o-", color=INK, markersize=4, linewidth=1.2,
             label="macro по 18 holdout")
    ax2.plot(cov, pooled, "s--", color=MUTED, markersize=3.5, linewidth=1.0,
             label="pooled, sensitivity")
    ax2.set_xlabel("Покрытие"); ax2.set_ylabel("Доля ошибочных решений")
    ax2.set_title("Risk–coverage: порог не выбран", fontsize=9.5)
    ax2.invert_xaxis(); ax2.legend(fontsize=7.5, frameon=False)
    ax2.tick_params(labelsize=7.5)
    save(fig, 4, "calibration-and-risk-coverage")


# ── 5. Стресс-тесты: десять преобразований ───────────────────────────────────

def fig5():
    cells = read(ATTEMPT / "stress-p2a-r11-cells.csv")
    by_t = defaultdict(list)
    for r in cells:
        by_t[int(r["transform_number"])].append(r)

    names = {1: "t01 замена тире", 2: "t02 markdown", 3: "t03 заголовки",
             4: "t04 перестановка", 5: "t05 пунктуация", 10: "t10 сокращение",
             11: "t11 расширение", 13: "t13 опечатки", 15: "t15 гомоглифы",
             16: "t16 форматы"}
    stats = []
    for t, rows in by_t.items():
        flip = sum(float(r["flip_rate"]) for r in rows) / len(rows)
        inst = sum(float(r["instability_rate"]) for r in rows) / len(rows)
        stats.append((names.get(t, f"t{t:02d}"), flip, inst))
    stats.sort(key=lambda x: x[1])

    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    y = range(len(stats))
    ax.barh([s[0] for s in stats], [s[1] for s in stats], height=0.55,
            color=[ACCENT if s[1] > 0.1 else SOFT for s in stats], label="смена решения")
    ax.plot([s[2] for s in stats], list(y), "o", color=INK, markersize=4,
            label="нестабильность |Δp| > 0.05")
    ax.set_xlabel("Средняя по 60 документам доля допустимых моделей (всего 464 пары)")
    ax.set_title("Устойчивость решения к десяти преобразованиям, процедура 2", fontsize=9.5)
    ax.legend(fontsize=7.5, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, -0.16), ncol=2)
    ax.tick_params(labelsize=8)
    save(fig, 5, "stress-ten-transformations")


# ── 6. Механизм t02 ──────────────────────────────────────────────────────────

def fig6():
    doc = "nemotron_b016_P1_r1"
    scores = [r for r in read(ATTEMPT / "stress-p2a-r11-scores.csv")
              if r["document_id"] == doc and int(r["transform_number"]) == 2]
    before = [float(r["prob_baseline"]) for r in scores]
    after = [float(r["prob_transformed"]) for r in scores]

    orig_full = (ROOT / "04-corpus/derived/prep-v5/full" / f"{doc}.txt")
    tran_full = (ROOT / "04-corpus/derived/stress-v3/t02/full" / f"{doc}.txt")
    orig_prose = (ROOT / "04-corpus/derived/prep-v5/prose" / f"{doc}.txt")
    tran_prose = (ROOT / "04-corpus/derived/stress-v3/t02/prose" / f"{doc}.txt")
    for p in (orig_full, tran_full, orig_prose, tran_prose):
        INPUTS[str(p.relative_to(ROOT)).replace("\\", "/")] = hashlib.sha256(
            p.read_bytes()).hexdigest()
    lens = (len(orig_full.read_text(encoding="utf-8")),
            len(tran_full.read_text(encoding="utf-8")),
            len(orig_prose.read_text(encoding="utf-8")),
            len(tran_prose.read_text(encoding="utf-8")))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.6, 3.4),
                                   gridspec_kw={"width_ratios": [1.2, 1], "wspace": 0.32})

    ax1.axis("off")
    num = [f"{n:,}".replace(",", " ") for n in lens]
    steps = ["Сняты маркеры\nвыделения",
             f"Профиль full\nне изменился\n{num[0]} = {num[1]} знаков",
             f"Граница heading/prose\nсдвинулась\nprose {num[2]} → {num[3]}",
             "Форматные счётчики\nвыделений 76 → 0",
             "Решение всех\nшести моделей\nперевернулось"]
    for i, s in enumerate(steps):
        y = 0.86 - i * 0.19
        ax1.add_patch(Rectangle((0.06, y - 0.075), 0.88, 0.15, facecolor="white",
                                edgecolor=ACCENT if i in (0, 4) else INK, linewidth=0.9))
        ax1.text(0.5, y, s, ha="center", va="center", fontsize=7.8, color=INK)
        if i < len(steps) - 1:
            ax1.add_patch(FancyArrowPatch((0.5, y - 0.077), (0.5, y - 0.113),
                                          arrowstyle="-|>", mutation_scale=8,
                                          color=MUTED, linewidth=0.8))
    ax1.set_xlim(0, 1); ax1.set_ylim(0, 1)
    ax1.set_title("Воздействие идёт через конвейер,\nа не через слова", fontsize=9)

    for b, a in zip(before, after):
        ax2.plot([0, 1], [b, a], "-o", color=INK, markersize=4, linewidth=1.0, alpha=0.75)
    ax2.axhline(0.5, color=MUTED, linestyle="--", linewidth=0.9)
    ax2.text(1.02, 0.5, " порог", fontsize=7.5, color=MUTED, va="center")
    ax2.set_xticks([0, 1]); ax2.set_xticklabels(["до", "после"])
    ax2.set_ylabel("Вероятность машинного происхождения")
    ax2.set_ylim(-0.05, 1.05)
    ax2.set_title(f"Все {len(scores)} применимых моделей\nперевернули решение", fontsize=9)
    ax2.tick_params(labelsize=8)
    save(fig, 6, "t02-pipeline-mechanism")


def main():
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"построение иллюстраций, {stamp}")
    for fn in (fig1, fig2, fig3, fig4, fig5, fig6):
        fn()

    outputs = {}
    for p in sorted(OUT.glob("fig-*")):
        outputs[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()

    manifest = {
        "series": "figures-v1",
        "approved": "комплект из шести иллюстраций, решение PI 2026-08-01",
        "supersedes": ("08-paper/figures-list.md — план из девяти иллюстраций по серии v1, "
                       "не используется: источники указывают на устаревшие файлы"),
        "data_series": "v2",
        "note": "скрипт только читает и рисует; ни одно число не пересчитывается",
        "inputs_sha256": INPUTS,
        "outputs_sha256": outputs,
        "code_sha256": {Path(__file__).name:
                        hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "created_at": stamp,
    }
    (OUT / "figures-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"  входов зафиксировано: {len(INPUTS)}, файлов на выходе: {len(outputs)}")
    print(f"  манифест: {(OUT / 'figures-manifest.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
