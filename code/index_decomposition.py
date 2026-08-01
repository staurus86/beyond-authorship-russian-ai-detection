#!/usr/bin/env python3
"""Алгебраическая декомпозиция контраста индекса на common и format.

    python 09-tools/index_decomposition.py --series score-v2
    python 09-tools/index_decomposition.py --series score-v1   # проверка устойчивости

Формула не вводится здесь заново, а берётся из замороженного `score_style_index`:
полный индекс собран как `(w_c·common + w_f·format) / (w_c + w_f)`, где веса —
суммы весов категорий. Знаменатель одинаков у всех документов ровно тогда, когда
ни одна категория ни у одного документа не осталась без признаков; скрипт это
проверяет и останавливается, если условие нарушено.

Тогда вклад части в парную разность полного индекса равен доле веса, умноженной
на парную разность этой части. Расхождение с записанным Δfull публикуется рядом:
оно показывает, что декомпозиция точная, а не приближённая.

Статус — `post hoc descriptive`. Величина исхода не пересчитывается: контрасты
берутся из замороженного выхода серии, новых параметров скрипт не выбирает.
"""

import argparse
import csv
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_style_index as sc  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ANALYSIS = ROOT / "07-analysis"
PAIRS = ANALYSIS / "score-v1-pairs.csv"
CHILD_MANIFEST = ANALYSIS / "manifests-v2" / "proc1-v2-manifest.json"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def weights():
    """Суммы весов категорий из замороженного кода, а не из отчёта."""
    return (sum(w for w, _ in sc.COMMON.values()),
            sum(w for w, _ in sc.FORMAT.values()))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", default="score-v2",
                        choices=["score-v1", "score-v2"])
    args = parser.parse_args()
    series = args.series

    scores_path = ANALYSIS / f"{series}-scores.csv"
    contrasts_path = ANALYSIS / f"{series}-o1-contrasts.csv"
    out_path = ANALYSIS / f"{series}-index-decomposition.md"
    for path in (scores_path, contrasts_path, PAIRS):
        if not path.exists():
            raise SystemExit(f"нет входа {path.name}")

    w_common, w_format = weights()
    rows = {r["document_id"]: r for r in read_csv(scores_path)}
    complete = {d: r for d, r in rows.items()
                if r["index_common"] and r["score_format"]
                and r["index_common_plus_format"]}

    # Знаменатель одинаков только при полном наборе категорий у всех документов.
    incomplete = [d for d, r in complete.items()
                  if int(r["n_features_common"]) == 0
                  or int(r["n_features_format"]) == 0]
    if incomplete:
        raise SystemExit(f"у {len(incomplete)} документов категория пуста: "
                         "знаменатель различается, декомпозиция неприменима")

    share_common = w_common / (w_common + w_format)
    share_format = w_format / (w_common + w_format)

    frozen = {(r["variant"], r["contrast"]): r for r in read_csv(contrasts_path)}
    pairs = read_csv(PAIRS)

    results = []
    for contrast in ("P3-P1", "P2-P1"):
        diffs_common, diffs_format, diffs_full, gaps = [], [], [], []
        for pair in pairs:
            if pair["contrast"] != contrast:
                continue
            left, right = complete.get(pair["doc_left"]), complete.get(pair["doc_right"])
            if left is None or right is None:
                continue
            d_common = float(left["index_common"]) - float(right["index_common"])
            d_format = float(left["score_format"]) - float(right["score_format"])
            d_full = (float(left["index_common_plus_format"])
                      - float(right["index_common_plus_format"]))
            diffs_common.append(d_common)
            diffs_format.append(d_format)
            diffs_full.append(d_full)
            gaps.append(abs(share_common * d_common + share_format * d_format - d_full))
        if not diffs_full:
            continue
        contribution_common = share_common * statistics.fmean(diffs_common)
        contribution_format = share_format * statistics.fmean(diffs_format)
        recorded = frozen.get(("O1-full", contrast))
        results.append({
            "contrast": contrast, "pairs": len(diffs_full),
            "mean_format_score": statistics.fmean(diffs_format),
            "contribution_common": contribution_common,
            "contribution_format": contribution_format,
            "sum": contribution_common + contribution_format,
            "mean_full": statistics.fmean(diffs_full),
            "recorded_full": float(recorded["mean_diff"]) if recorded else None,
            "max_gap": max(gaps),
        })

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [f"# Декомпозиция контраста индекса, {series}", "",
             f"Собрано {stamp} скриптом `09-tools/index_decomposition.py`. "
             "Статус — `post hoc descriptive`: величина исхода не пересчитывается, "
             "новые параметры не выбираются.", "",
             f"Веса взяты из `score_style_index`: common {w_common:.2f}, "
             f"format {w_format:.2f}, доли {share_common:.4f} и {share_format:.4f}. "
             "У всех документов обе категории посчитаны, поэтому знаменатель "
             "одинаков и декомпозиция точная.", "",
             "| Контраст | Пар | Вклад common | Вклад format | Сумма | Δfull "
             "замороженный | Максимальное расхождение |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        recorded = "—" if r["recorded_full"] is None else f"{r['recorded_full']:.4f}"
        lines.append(f"| {r['contrast']} | {r['pairs']} | "
                     f"**{r['contribution_common']:+.4f}** | "
                     f"**{r['contribution_format']:+.4f}** | {r['sum']:+.4f} | "
                     f"{recorded} | {r['max_gap']:.1e} |")
    lines += ["", "Format-sensitive score отдельным выходом, парные разности:", "",
              "| Контраст | Пар | Средняя разность |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['contrast']} | {r['pairs']} | "
                     f"**{r['mean_format_score']:+.2f}** |")

    inputs = {p.name: sha256(p) for p in (scores_path, contrasts_path, PAIRS)}
    inputs["09-tools/index_decomposition.py"] = sha256(Path(__file__))
    inputs["09-tools/score_style_index.py"] = sha256(ROOT / "09-tools" /
                                                     "score_style_index.py")
    lines += ["", "## Входы и код", "", "| Файл | sha256 |", "|---|---|"]
    for name, digest in inputs.items():
        lines.append(f"| `{name}` | `{digest}` |")
    if series == "score-v2" and CHILD_MANIFEST.exists():
        child = json.loads(CHILD_MANIFEST.read_text(encoding="utf-8"))
        lines += ["", f"Прогон-источник — `manifests-v2/{CHILD_MANIFEST.name}`, "
                      f"родитель `{child.get('parent')}`, статус "
                      f"`{child.get('status')}`.", ""]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for r in results:
        print(f"  {r['contrast']}: common {r['contribution_common']:+.4f}, "
              f"format {r['contribution_format']:+.4f}, сумма {r['sum']:+.4f}, "
              f"Δfull {r['recorded_full']}, расхождение {r['max_gap']:.1e}")
    print(f"записано: {out_path.name}")


if __name__ == "__main__":
    main()
