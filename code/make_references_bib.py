#!/usr/bin/env python3
"""Собирает references.bib из evidence-matrix.csv.

Источник — только сверенные поля матрицы: title, authors, year, venue_full,
code_data. Ничего не досочиняется: чего в матрице нет, того нет и в bib.
"""
import csv
import re
import unicodedata
from pathlib import Path

ROOT = Path(r"<PROJECT_ROOT>")
MATRIX = ROOT / "01-literature" / "evidence-matrix.csv"
OUT = ROOT / "01-literature" / "references.bib"

INITIALS = re.compile(r"^(?:[A-ZА-ЯЁ]\.\s*)+(?:[A-ZА-ЯЁ][a-zа-яё]+)?$")
STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "via",
        "under", "with", "is", "are", "can", "do", "does", "how", "what"}


def split_authors(raw):
    """«Wu, J., Yang, S., Chao, L. S.» -> ['Wu, J.', 'Yang, S.', 'Chao, L. S.']

    Хвост «и др.» снимается до разбора: иначе он прилипает к инициалам
    последнего автора и разрывает его на две фамилии.
    """
    tail = ""
    for marker in (" и др.", " и др", " et al.", " et al"):
        if raw.endswith(marker):
            raw = raw[: -len(marker)].rstrip().rstrip(",")
            tail = "others"
            break

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out, i = [], 0
    while i < len(parts):
        surname = parts[i]
        if i + 1 < len(parts) and INITIALS.match(parts[i + 1]):
            out.append(f"{surname}, {parts[i + 1]}")
            i += 2
        else:
            out.append(surname)
            i += 1
    if tail:
        out.append(tail)
    return out


LATEX_SPECIALS = {"%": r"\%", "&": r"\&", "$": r"\$", "#": r"\#",
                  "_": r"\_", "^": r"\^{}", "~": r"\~{}"}


def latex_escape(text):
    """Экранирует спецсимволы LaTeX в текстовых полях.

    Обратный слэш обрабатывается первым, иначе он удвоится в уже вставленных
    заменах. Фигурные скобки в значениях матрицы не встречаются и не трогаются:
    в BibTeX они защищают регистр, и слепое экранирование сломало бы это.
    """
    out = text.replace("\\", r"\textbackslash{}")
    for ch, repl in LATEX_SPECIALS.items():
        out = out.replace(ch, repl)
    return out


def ascii_key(text):
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if c.isalnum() and ord(c) < 128).lower()


def make_key(authors, year, title, used):
    first = authors[0].split(",")[0] if authors else "anon"
    surname = ascii_key(first) or "anon"
    word = ""
    for token in re.findall(r"[A-Za-z][A-Za-z-]+", title):
        if token.lower() not in STOP and len(token) > 3:
            word = ascii_key(token)
            break
    key = f"{surname}{year or 'nd'}{word}"
    base, n = key, 1
    while key in used:
        n += 1
        key = f"{base}{chr(ord('a') + n - 2)}"
    used.add(key)
    return key


def entry_type(venue):
    v = venue.lower()
    if any(k in v for k in ("acl", "naacl", "emnlp", "eacl", "coling", "icml",
                            "neurips", "iclr", "chi", "lrec", "semeval",
                            "workshop", "conference", "proceedings", "ijcai",
                            "dialogue")):
        return "inproceedings"
    if "arxiv" in v or "working paper" in v or "preprint" in v:
        return "misc"
    return "article"


def field_from_venue(venue, year=None):
    """Разбирает venue_full на название площадки, том, номер и страницы.

    Возвращает (место, {volume, number, pages}). Хвосты «; arXiv...»,
    «, DOI ...» и продублированный год из названия убираются: DOI и год
    выносятся в свои поля.
    """
    main = re.split(r";\s*arXiv", venue)[0].strip().rstrip(",")
    main = re.split(r",?\s*DOI\s+10\.", main)[0].strip().rstrip(",")

    extra = {}
    vol = re.search(r"(\d+)\((\d+)\)", main)
    if vol:
        extra["volume"], extra["number"] = vol.group(1), vol.group(2)
        main = main.replace(vol.group(0), "").strip().rstrip(",")
    pages = re.search(r"(?:pp\.\s*)?(\d+\s*[–—-]\s*\d+)\s*$", main)
    if pages:
        extra["pages"] = pages.group(1).replace(" ", "")
        main = main[: pages.start()].strip().rstrip(",")
        main = re.sub(r",?\s*pp\.?\s*$", "", main).strip().rstrip(",")
    # Год из названия площадки не вырезается: «SemEval-2024» и «ACL 2024» —
    # часть имени, а не дубль поля year. Попытка чистки ломала названия.
    main = re.sub(r"\s{2,}", " ", main).strip().strip(",").strip()
    return main or venue, extra


def main():
    rows = list(csv.DictReader(MATRIX.open(encoding="utf-8", newline="")))
    used, entries, skipped = set(), [], []

    for row in sorted(rows, key=lambda r: r["ref_id"]):
        title = (row.get("title") or "").strip()
        raw_authors = (row.get("authors") or "").strip()
        if not title or not raw_authors:
            skipped.append(row["ref_id"])
            continue
        authors = split_authors(raw_authors)
        year = (row.get("year") or "").strip()
        venue = (row.get("venue_full") or "").strip()
        key = make_key(authors, year, title, used)
        etype = entry_type(venue)

        lines = [f"@{etype}{{{key},",
                 f"  title = {{{latex_escape(title)}}},",
                 f"  author = {{{latex_escape(' and '.join(authors))}}},"]
        if year:
            lines.append(f"  year = {{{year}}},")
        place, extra = field_from_venue(venue, year)
        if place:
            tag = {"inproceedings": "booktitle", "article": "journal",
                   "misc": "howpublished"}[etype]
            lines.append(f"  {tag} = {{{latex_escape(place)}}},")
        for field in ("volume", "number", "pages"):
            if field in extra:
                lines.append(f"  {field} = {{{extra[field]}}},")
        doi = re.search(r"10\.\d{4,9}/[^\s,;]+", venue)
        if doi:
            lines.append(f"  doi = {{{doi.group(0)}}},")
        url = (row.get("code_data") or "").strip()
        if url.startswith("http"):
            lines.append(f"  url = {{{url.split(';')[0].strip()}}},")
        lines.append(f"  note = {{ref_id: {row['ref_id']}; "
                     f"verification: {row.get('verification_level', '')}}},")
        lines.append("}")
        entries.append("\n".join(lines))

    header = [
        "% references.bib — собран из 01-literature/evidence-matrix.csv",
        "% Дата сборки: 2026-07-31. Записей: {}.".format(len(entries)),
        "% Поля перенесены из матрицы без досочинения: чего нет в матрице,",
        "% того нет и здесь. Уровень проверки каждой записи — в поле note.",
        "",
    ]
    OUT.write_text("\n".join(header) + "\n\n".join(entries) + "\n",
                   encoding="utf-8")
    print(f"записей в bib: {len(entries)}")
    if skipped:
        print(f"пропущено (нет названия или авторов): {skipped}")
    print(f"файл: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
