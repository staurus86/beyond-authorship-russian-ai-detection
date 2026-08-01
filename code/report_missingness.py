#!/usr/bin/env python3
"""Отчёт по пропускам матрицы признаков (qa-miss-v1).

Процедура зафиксирована в `06-features/missingness-spec.md` до прогона.
Здесь только её реализация — расхождение между спецификацией и кодом считается
ошибкой кода.

Запуск из корня папки исследования:
    python 09-tools/report_missingness.py
    python 09-tools/report_missingness.py --check   # только сверка причин, файлы не пишутся

Отчёт ничего не подставляет и матрицу не трогает. Он сводит, у каких признаков
сколько пропусков, чем они объясняются и связаны ли с классом, источником и жанром.
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
MATRIX = ROOT / "06-features" / "feature-matrix.csv"
CODEBOOK = ROOT / "06-features" / "codebook.md"
REPORT_MD = ROOT / "06-features" / "missingness-report.md"
REPORT_CSV = ROOT / "06-features" / "missingness-by-feature.csv"

REPORT_VERSION = "qa-miss-v1"

# §2 спецификации: таблица соответствия «формулировка → разряд».
# Формулировка, которой здесь нет, останавливает прогон.
REASON_CLASS = {
    "список единиц измерения не зафиксирован": "instrument",
    "ручная схема": "instrument",
    "список хеджей не зафиксирован амендментом": "instrument",
    "список приблизителей не зафиксирован амендментом": "instrument",
    "список signposting не зафиксирован амендментом": "instrument",
    "список конструкций баланса не зафиксирован амендментом": "instrument",
    "составная шкала 0–3, ручная схема": "instrument",
    "ручная проверка": "instrument",
    "требует ручной разметки: автоматический прокси проверен и отклонён": "instrument",
    "межтекстовый признак, отдельный этап": "instrument",
    "требует прогона нейтрального рерайта": "instrument",
    "задание не задано: человеческая часть собрана из архивов": "design",
    "тема не задана: сравнивать не с чем": "design",
    "тема не задана: человеческая часть собрана из архивов без общего задания": "design",
    "меньше двух абзацев": "material",
    "меньше двух абзацев с пригодными предложениями": "material",
    "заголовков нет": "material",
    "в группе нет партнёров": "material",
}

RANK_LABEL = {
    "label": "пропуск равен метке класса",
    "strong": "пропуск сильно связан с классом",
    "moderate": "связь заметна",
    "neutral": "связи нет",
    "subsample": "признак измерен на подвыборке, связь с классом не оценивается",
}

SUBSAMPLE_SHARE = 0.95  # §4 спецификации, поправка после первого прогона

MIN_SOURCE_DOCS = 10  # §5 спецификации


def read_codebook_names():
    """Названия признаков из кодбука.

    У неизмеренного признака поле `feature_name` в матрице пустое у всех строк:
    экстрактор имени не знает. Без кодбука отчёт печатал бы пустые ячейки.
    """
    text = CODEBOOK.read_text(encoding="utf-8")
    names = {}
    for match in re.finditer(r"^\|\s*([A-Z]\d{2})\s*\|\s*([^|]+?)\s*\|", text, re.M):
        names.setdefault(match.group(1), match.group(2))
    return names


def read_registry():
    """document_id → класс, источник, жанр."""
    docs = {}
    with DOCUMENTS.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            # Реестр содержит только действующие документы: исключённые лежат
            # в `00-admin/exclusion-log.csv`. Фильтр по `status` не ставится —
            # он молча резал бы корпус, если поле сменит словарь значений.
            doc_id = row["document_id"]
            origin = row["origin_class"].strip()
            if origin == "A":
                source = row.get("generation_channel", "").strip() or "—"
            else:
                source = row.get("source_platform", "").strip() or "—"
            docs[doc_id] = {
                "origin_class": origin,
                "source": source,
                "genre": row.get("genre", "").strip() or "—",
            }
    return docs


def read_matrix(docs):
    """Пропуски и общее число строк по признакам."""
    features = {}
    order = []
    unknown_reasons = Counter()
    missing_docs = defaultdict(list)
    with MATRIX.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            fid = row["feature_id"]
            if fid not in features:
                features[fid] = {
                    "feature_name": row["feature_name"],
                    "extractor_version": row["extractor_version"],
                    "total": 0,
                    "missing": 0,
                    "reasons": Counter(),
                    "by_class": Counter(),
                    "by_source": Counter(),
                    "by_genre": Counter(),
                }
                order.append(fid)
            entry = features[fid]
            entry["total"] += 1
            if not entry["feature_name"] and row["feature_name"]:
                entry["feature_name"] = row["feature_name"]
            if row["raw_value"] != "" or row["normalized_value"] != "":
                continue
            reason = row["missing_reason"].strip()
            if reason not in REASON_CLASS:
                unknown_reasons[reason] += 1
                continue
            meta = docs.get(row["document_id"])
            if meta is None:
                unknown_reasons[f"документ вне реестра: {row['document_id']}"] += 1
                continue
            entry["missing"] += 1
            entry["reasons"][reason] += 1
            entry["by_class"][meta["origin_class"]] += 1
            entry["by_source"][meta["source"]] += 1
            entry["by_genre"][meta["genre"]] += 1
            missing_docs[(fid, reason)].append(row["document_id"])
    return features, order, unknown_reasons, missing_docs


def class_rank(missing_by_class, class_sizes, n_missing=None, n_total=None):
    """§4 спецификации."""
    p = {cls: missing_by_class.get(cls, 0) / n for cls, n in class_sizes.items()}
    hit = [cls for cls in class_sizes if missing_by_class.get(cls, 0) > 0]
    if len(hit) == 1 and p[hit[0]] >= 0.95:
        return "label", p
    if n_total and n_missing / n_total >= SUBSAMPLE_SHARE:
        return "subsample", p
    delta = abs(p.get("A", 0.0) - p.get("H", 0.0))
    if delta >= 0.20:
        return "strong", p
    if delta >= 0.05:
        return "moderate", p
    return "neutral", p


def source_spread(entry, source_sizes):
    """§5 спецификации: разброс доли пропуска по источникам от десяти документов."""
    shares = {
        src: entry["by_source"].get(src, 0) / n
        for src, n in source_sizes.items()
        if n >= MIN_SOURCE_DOCS
    }
    if not shares:
        return None
    hi = max(shares.items(), key=lambda kv: kv[1])
    lo = min(shares.items(), key=lambda kv: kv[1])
    return {"spread": hi[1] - lo[1], "max": hi, "min": lo, "shares": shares}


def pct(x):
    return f"{100 * x:.1f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="только сверка причин")
    args = parser.parse_args()

    docs = read_registry()
    features, order, unknown, missing_docs = read_matrix(docs)

    codebook_names = read_codebook_names()
    nameless = []
    for fid in order:
        if not features[fid]["feature_name"]:
            features[fid]["feature_name"] = codebook_names.get(fid, "")
        if not features[fid]["feature_name"]:
            nameless.append(fid)
    if nameless:
        print("названия не найдены ни в матрице, ни в кодбуке: " + ", ".join(nameless))
        return 1

    if unknown:
        print("формулировки вне таблицы соответствия — прогон остановлен:")
        for reason, n in unknown.most_common():
            print(f"  {n:>6}  {reason!r}")
        return 1

    class_sizes = Counter(m["origin_class"] for m in docs.values())
    source_sizes = Counter(m["source"] for m in docs.values())
    genre_sizes = Counter(m["genre"] for m in docs.values())
    n_docs = len(docs)

    print(f"документов в реестре: {n_docs} (A={class_sizes['A']}, H={class_sizes['H']})")
    print(f"признаков в матрице: {len(order)}")
    if args.check:
        print("сверка причин прошла: все формулировки известны")
        return 0

    full, partial, empty = [], [], []
    for fid in order:
        e = features[fid]
        if e["missing"] == 0:
            full.append(fid)
        elif e["missing"] >= e["total"]:
            empty.append(fid)
        else:
            partial.append(fid)

    # CSV по признакам
    with REPORT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "feature_id", "feature_name", "extractor_version", "n_documents",
            "n_missing", "share_missing", "reason_class", "reasons",
            "n_missing_A", "share_missing_A", "n_missing_H", "share_missing_H",
            "class_rank", "source_spread", "source_max", "source_min", "measured",
        ])
        for fid in order:
            e = features[fid]
            reasons = "; ".join(f"{r} ({n})" for r, n in e["reasons"].most_common())
            rclasses = sorted({REASON_CLASS[r] for r in e["reasons"]})
            measured = "no" if e["missing"] >= e["total"] else "yes"
            if e["missing"] == 0:
                rank, p = "neutral", {"A": 0.0, "H": 0.0}
                spread = None
            elif measured == "no":
                rank, p = "—", {
                    "A": e["by_class"].get("A", 0) / class_sizes["A"],
                    "H": e["by_class"].get("H", 0) / class_sizes["H"],
                }
                spread = None
            else:
                rank, p = class_rank(e["by_class"], class_sizes, e["missing"], e["total"])
                spread = source_spread(e, source_sizes)
            writer.writerow([
                fid, e["feature_name"], e["extractor_version"], e["total"],
                e["missing"], f"{e['missing'] / e['total']:.4f}",
                ",".join(rclasses), reasons,
                e["by_class"].get("A", 0), f"{p.get('A', 0):.4f}",
                e["by_class"].get("H", 0), f"{p.get('H', 0):.4f}",
                rank,
                f"{spread['spread']:.4f}" if spread else "",
                f"{spread['max'][0]} {spread['max'][1]:.4f}" if spread else "",
                f"{spread['min'][0]} {spread['min'][1]:.4f}" if spread else "",
                measured,
            ])

    # Markdown-отчёт
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    out = []
    w = out.append
    w(f"# Отчёт по пропускам матрицы признаков ({REPORT_VERSION})")
    w("")
    w(f"Процедура — `06-features/missingness-spec.md`, зафиксирована до прогона. "
      f"Матрица `06-features/feature-matrix.csv`, {n_docs} документов × {len(order)} признаков "
      f"= {n_docs * len(order)} строк. Прогон {stamp}.")
    w("")
    w("Отчёт ничего не подставляет вместо пропусков и состав признаков не меняет. "
      "Он готовит одно решение — как признак входит в анализ этапа 11.")
    w("")
    w("Ручная проверка по одному документу на каждую формулировку причины — "
      "`06-features/missingness-manual-check.md`. Она лежит отдельным файлом: "
      "этот отчёт собирается скриптом целиком и вписанные в него вердикты стёрлись бы "
      "следующим прогоном.")
    w("")
    w("## 1. Сводка")
    w("")
    w("| Разряд | Признаков | Что означает |")
    w("|---|---|---|")
    w(f"| без пропусков | {len(full)} | значение есть у всех {n_docs} документов |")
    w(f"| с пропусками | {len(partial)} | значение есть у части документов |")
    w(f"| не измерены | {len(empty)} | значения нет ни у одного документа |")
    w("")
    total_missing = sum(features[f]["missing"] for f in order)
    w(f"Пропусков всего {total_missing} из {n_docs * len(order)} строк "
      f"({pct(total_missing / (n_docs * len(order)))}), из них "
      f"{sum(features[f]['missing'] for f in empty)} приходится на {len(empty)} неизмеренных признаков.")
    w("")

    w("## 2. Признаки с пропусками")
    w("")
    w("Доли считаются от размера класса: A = "
      f"{class_sizes['A']}, H = {class_sizes['H']}.")
    w("")
    w("| ID | Признак | Пропусков | Доля | A | H | Δ | Разряд | Разряд причины |")
    w("|---|---|---|---|---|---|---|---|---|")
    partial_sorted = sorted(partial, key=lambda f: -features[f]["missing"])
    for fid in partial_sorted:
        e = features[fid]
        rank, p = class_rank(e["by_class"], class_sizes, e["missing"], e["total"])
        delta = abs(p.get("A", 0) - p.get("H", 0))
        rclasses = ", ".join(sorted({REASON_CLASS[r] for r in e["reasons"]}))
        w(f"| {fid} | {e['feature_name']} | {e['missing']} | {pct(e['missing'] / e['total'])} | "
          f"{e['by_class'].get('A', 0)} ({pct(p.get('A', 0))}) | "
          f"{e['by_class'].get('H', 0)} ({pct(p.get('H', 0))}) | "
          f"{delta:.2f} | `{rank}` | `{rclasses}` |")
    w("")

    for fid in partial_sorted:
        e = features[fid]
        rank, p = class_rank(e["by_class"], class_sizes, e["missing"], e["total"])
        spread = source_spread(e, source_sizes)
        w(f"### {fid} — {e['feature_name']}")
        w("")
        w(f"Пропусков {e['missing']} из {e['total']}, экстрактор `{e['extractor_version']}`. "
          f"Разряд связи с классом — `{rank}`: {RANK_LABEL[rank]}.")
        w("")
        w("Причины:")
        w("")
        for reason, n in e["reasons"].most_common():
            w(f"- {n} — «{reason}», разряд `{REASON_CLASS[reason]}`")
        w("")
        if spread and spread["spread"] > 0:
            note = (" Пропуск определяется источником — помета `source-driven`."
                    if spread["spread"] >= 0.20 and rank in ("neutral", "moderate")
                    else "")
            w(f"По источникам разброс {spread['spread']:.2f}: "
              f"{spread['max'][0]} {pct(spread['max'][1])}, "
              f"{spread['min'][0]} {pct(spread['min'][1])}.{note}")
            w("")
            hot = [(s, v) for s, v in sorted(spread["shares"].items(), key=lambda kv: -kv[1]) if v > 0]
            if hot:
                w("| Источник | Документов | Пропусков | Доля |")
                w("|---|---|---|---|")
                for src, share in hot:
                    w(f"| {src} | {source_sizes[src]} | {e['by_source'].get(src, 0)} | {pct(share)} |")
                w("")
        genres = [(g, e["by_genre"].get(g, 0)) for g in sorted(genre_sizes) if e["by_genre"].get(g, 0)]
        if genres:
            w("По жанрам: " + ", ".join(
                f"{g} {n} из {genre_sizes[g]} ({pct(n / genre_sizes[g])})" for g, n in genres) + ".")
            w("")

    w("## 3. Не измерены")
    w("")
    w("У этих признаков пропуск стоит у всех документов. Разряд §4 к ним не применяется: "
      "`neutral` читался бы как «с пропусками всё в порядке», тогда как признак просто не измерен.")
    w("")
    w("| ID | Признак | Причина | Разряд причины |")
    w("|---|---|---|---|")
    for fid in sorted(empty):
        e = features[fid]
        reason = e["reasons"].most_common(1)[0][0]
        w(f"| {fid} | {e['feature_name']} | {reason} | `{REASON_CLASS[reason]}` |")
    w("")

    w("## 4. Без пропусков")
    w("")
    w(f"Значение есть у всех {n_docs} документов, признаков — {len(full)}: "
      + ", ".join(sorted(full)) + ".")
    w("")

    REPORT_MD.write_text("\n".join(out) + "\n", encoding="utf-8")

    print(f"без пропусков {len(full)}, с пропусками {len(partial)}, не измерены {len(empty)}")
    for fid in partial_sorted:
        e = features[fid]
        rank, _ = class_rank(e["by_class"], class_sizes, e["missing"], e["total"])
        print(f"  {fid}: {e['missing']} пропусков, разряд {rank}")
    print(f"отчёт: {REPORT_MD.relative_to(ROOT)}")
    print(f"таблица: {REPORT_CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
