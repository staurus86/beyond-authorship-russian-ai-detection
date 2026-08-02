# -*- coding: utf-8 -*-
"""Сборка LaTeX-источника препринта из замороженной рукописи.

Читает manuscript-final.md (заморожена, хеш в publication-record), собирает
08-paper/latex/: main.tex, references.bib, tables/, figures/, манифест.
Текст рукописи не редактируется — только конверсия разметки. Цитаты остаются
дословным текстом, список литературы верстается из references-section.md;
references.bib — параллельный машиночитаемый артефакт для этапа журнала.

Шлюзы (любой провал останавливает сборку с ненулевым кодом):
  G1 — все 24 якоря таблиц распознаны, файлы .tex существуют;
  G2 — в выходе не осталось markdown-остатков (**, |---, <!--, ###);
  G3 — все символы выхода в белом списке (ASCII, кириллица, разрешённые знаки);
  G4 — последовательность словесных токенов тела .tex совпадает с рукописью
       (независимая нормализация обеих сторон);
  G5 — число записей библиографии в .bib и в свёрстанном разделе совпадает
       с числом записей источника.
"""
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "08-paper"
LANG = "en" if "--en" in sys.argv else "ru"
if LANG == "ru":
    OUT = PAPER / "latex"
    MANUSCRIPT = PAPER / "manuscript-final.md"
    ABSTRACT = PAPER / "abstract.md"
    TABLES_DIR = PAPER / "tables"
else:
    OUT = PAPER / "latex-en"
    MANUSCRIPT = PAPER / "manuscript-final-en.md"
    ABSTRACT = PAPER / "translation-en" / "abstract-en.md"
    TABLES_DIR = PAPER / "tables-en"
REFS = PAPER / "references-section.md"
REFS_INTRO_EN = PAPER / "translation-en" / "references-intro-en.md"
DECLARATIONS = PAPER / "declarations.md"
TITLE_KEYWORDS = PAPER / "title-and-keywords.md"
FIGURES_DIR = PAPER / ("figures" if LANG == "ru" else "figures-en")

FIGURE_FILES = {
    1: "fig-01-design-and-corpus",
    2: "fig-02-o1-four-procedures",
    3: "fig-03-classifier-heterogeneity",
    4: "fig-04-calibration-and-risk-coverage",
    5: "fig-05-stress-ten-transformations",
    6: "fig-06-t02-pipeline-mechanism",
}

KEYWORDS = [  # решение PI от 2026-08-01, title-and-keywords.md
    "AI-generated text detection",
    "Russian-language text",
    "production conditions",
    "source confounding",
    "preprocessing sensitivity",
]

ANCHOR_RE = re.compile(r"<!-- ТАБЛИЦА ([\wS]+): ([\w-]+) -->")

# Адаптация копий таблиц к ширине полосы. Правится только вёрстка
# (окружение или спецификация колонок), содержимое ячеек не меняется —
# это сверяется токен-шлюзом G6. Канонические файлы 08-paper/tables
# не редактируются.
TABLE_ADAPTATIONS = {
    "tab-03-o1-contrasts.tex": [
        (r"\begin{table*}[htbp]", r"\begin{sidewaystable}[p]"),
        (r"\end{table*}", r"\end{sidewaystable}"),
    ],
    "tab-06-calibration-and-risk-coverage.tex": [
        (r"\begin{tabular}{lllll}",
         r"\begin{tabular}{l>{\hspace{0pt}}p{2.4cm}>{\hspace{0pt}}p{2.4cm}"
         r">{\hspace{0pt}}p{1.9cm}>{\hspace{0pt}}p{2.6cm}}"),
    ],
    # genre=translation неразрывно: '=' не даёт точки переноса
    "tab-05-formal-register-fairness.tex": [
        (r"genre=", r"genre=\allowbreak{}"),
    ],
    "tab-S10-blind-instability-cards.tex": [
        (r"\begin{longtable}{lllll}",
         r"\begin{longtable}{lp{2.5cm}p{2.5cm}p{3.3cm}p{3.1cm}}"),
    ],
    # широкие таблицы «по holdout» и аудит повторов — в landscape:
    # у них не только шапка шире полосы, но и неразрывные идентификаторы в ячейках
    "tab-S04-fpr-by-holdout.tex": [
        (r"\begin{table*}[htbp]", r"\begin{sidewaystable}[p]"),
        (r"\end{table*}", r"\end{sidewaystable}"),
    ],
    "tab-S05-classifier-quality-by-holdout.tex": [
        (r"\begin{table*}[htbp]", r"\begin{sidewaystable}[p]"),
        (r"\end{table*}", r"\end{sidewaystable}"),
    ],
    "tab-S06-calibration-by-holdout.tex": [
        (r"\begin{table*}[htbp]", r"\begin{sidewaystable}[p]"),
        (r"\end{table*}", r"\end{sidewaystable}"),
    ],
    "tab-S08-mixed-identifiability.tex": [
        (r"\begin{table*}[htbp]", r"\begin{sidewaystable}[p]"),
        (r"\end{table*}", r"\end{sidewaystable}"),
        (r"genre=", r"genre=\allowbreak{}"),
    ],
    "tab-S09-repeat-audit.tex": [
        (r"\begin{table*}[htbp]", r"\begin{sidewaystable}[p]"),
        (r"\end{table*}", r"\end{sidewaystable}"),
        # первая колонка несёт неразрывные идентификаторы документов
        (r"p{0.102\linewidth}", r"p{0.19\linewidth}", 1),
        (r"p{0.102\linewidth}", r"p{0.088\linewidth}"),
    ],
    "tab-S02-machine-channels.tex": [
        (r"\begin{tabular}{lllll}",
         r"\begin{tabular}{ll>{\hspace{0pt}}p{3.3cm}>{\hspace{0pt}}p{1.4cm}"
         r">{\hspace{0pt}}p{2.4cm}}"),
    ],
}

COLSEP_LW = 0.0176  # 2 * \tabcolsep(4pt) в долях \linewidth (455pt)


def rescale_p_columns(text):
    """Сужает p{X\\linewidth}-колонки, если их сумма с отступами шире полосы.
    Пропорции колонок сохраняются, содержимое не меняется."""
    out = []
    for line in text.split("\n"):
        if line.startswith(("\\begin{tabular}", "\\begin{longtable}")):
            vals = re.findall(r"p\{([\d.]+)\\linewidth\}", line)
            if vals:
                n, s = len(vals), sum(map(float, vals))
                budget = 0.96 - n * COLSEP_LW
                if s > budget:
                    k = budget / s
                    line = re.sub(
                        r"p\{([\d.]+)\\linewidth\}",
                        lambda m: "p{%.3f\\linewidth}" % (float(m.group(1)) * k),
                        line)
        out.append(line)
    return "\n".join(out)

failures = []


def gate(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


# ---------------------------------------------------------------- инлайн-текст

TEXT_ESCAPES = [
    ("\\", r"\textbackslash{}"),
    ("%", r"\%"), ("#", r"\#"), ("&", r"\&"), ("$", r"\$"),
    ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
    ("~", r"$\sim$"), ("^", r"\textasciicircum{}"),
]

UNICODE_MAP = {
    "±": r"$\pm$", "·": r"$\cdot$", "×": r"$\times$", "č": r"\v{c}",
    "Δ": r"$\Delta$", "α": r"$\alpha$", "δ": r"$\delta$", "σ": r"$\sigma$",
    "→": r"$\to$", "−": r"$-$", "“": "''", "„": r"\quotedblbase{}",
    # латинские акценты из имён авторов библиографии
    "Á": r"\'A", "á": r"\'a", "é": r"\'e", "í": r"\'i", "ó": r"\'o",
    "ć": r"\'c", "ń": r"\'n", "š": r"\v{s}",
}
UNICODE_MAP["ö"] = "\\\"o"
ACCENT_FOLD = {"Á": "A", "á": "a", "é": "e", "í": "i", "ó": "o", "ö": "o",
               "ć": "c", "ń": "n", "š": "s", "č": "c"}
SUPERSCRIPTS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-"}
SUP_RUN = re.compile("[" + "".join(SUPERSCRIPTS) + "]+")

# символы, которым разрешено остаться в main.tex как есть
ALLOWED_LITERAL = set("«»–—§№")


def esc_code(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append(r"\textbackslash{}")
        elif ch in "%#&$_{}":
            out.append("\\" + ch)
        elif ch == "~":
            out.append(r"\textasciitilde{}")
        elif ch == "^":
            out.append(r"\textasciicircum{}")
        elif ch == "-":
            out.append(r"-\allowbreak{}")  # перенос в длинных именах файлов
        else:
            out.append(ch)
    return "".join(out)


def map_unicode(s):
    s = SUP_RUN.sub(lambda m: "$^{" + "".join(SUPERSCRIPTS[c] for c in m.group(0)) + "}$", s)
    for ch, rep in UNICODE_MAP.items():
        s = s.replace(ch, rep)
    return s


CYR_RUN = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё\s]*[А-Яа-яЁё]|[А-Яа-яЁё]")


def wrap_cyrillic(s):
    """В английском документе кириллица требует T2A: обёртка в
    \\foreignlanguage{russian}{...}."""
    return CYR_RUN.sub(lambda m: r"\foreignlanguage{russian}{" + m.group(0) + "}", s)


def inline(s):
    """Конверсия инлайн-разметки абзаца: код-спаны, экранирование, жирный."""
    s = s.replace(r"\|", "|")  # markdown-экранирование пайпа
    parts = re.split(r"(`[^`]*`)", s, flags=re.S)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # код-спан
            content = part[1:-1]
            if re.match(r"^https?://\S+$", content):
                out.append(r"\url{" + content + "}")  # xurl переносит внутри URL
            else:
                body = map_unicode(esc_code(content))
                if LANG == "en" and re.search(r"[А-Яа-яЁё]", body):
                    body = r"\foreignlanguage{russian}{" + body + "}"
                out.append(r"\texttt{" + body + "}")
        else:
            for ch, rep in TEXT_ESCAPES:
                part = part.replace(ch, rep)
            part = map_unicode(part)
            if LANG == "en":
                part = wrap_cyrillic(part)
            out.append(part)
    s = "".join(out)
    s = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", s, flags=re.S)
    return s


# ---------------------------------------------------------------- md-таблицы

def split_row(line):
    line = line.strip().strip("|")
    cells, cur, escaped = [], [], False
    for ch in line:
        if escaped:
            cur.append("\\" + ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur).strip())
    return cells


def md_table_to_latex(rows):
    header = split_row(rows[0])
    body = [split_row(r) for r in rows[2:]]
    ncol = len(header)
    body = [r + [""] * (ncol - len(r)) for r in body]
    width = [max([len(header[c])] + [len(r[c]) for r in body]) for c in range(ncol)]
    spec = ["X" if w > 45 else "l" for w in width]
    # если фиксированные колонки в сумме шире полосы, самые широкие переводятся в X
    while sum(w for w, s in zip(width, spec) if s == "l") > 80 and "l" in spec:
        widest = max((w for w, s in zip(width, spec) if s == "l"))
        spec[width.index(widest)] = "X"
    colspec = "".join(spec)
    wide = "X" in colspec
    env = ("tabularx", r"{\textwidth}") if wide else ("tabular", "")
    lines = [r"\begin{center}", r"\small",
             r"\begin{%s}%s{%s}" % (env[0], env[1], colspec), r"\toprule"]
    lines.append(" & ".join(inline(c) for c in header) + r" \\")
    lines.append(r"\midrule")
    for r in body:
        lines.append(" & ".join(inline(c) for c in r) + r" \\")
    lines += [r"\bottomrule", r"\end{%s}" % env[0], r"\end{center}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- разбор md

def parse_blocks(lines):
    """Разбивает markdown на блоки: heading / table / quote / list / fence /
    anchor / rule / paragraph."""
    blocks, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("#"):
            m = re.match(r"(#+) (.*)", line)
            blocks.append(("heading", len(m.group(1)), m.group(2), i))
            i += 1
        elif ANCHOR_RE.match(line.strip()):
            blocks.append(("anchor", *ANCHOR_RE.match(line.strip()).groups(), i))
            i += 1
        elif line.strip() == "---":
            blocks.append(("rule", i))
            i += 1
        elif line.strip().startswith("```"):
            j = i + 1
            while j < n and not lines[j].strip().startswith("```"):
                j += 1
            blocks.append(("fence", lines[i + 1:j], i))
            i = j + 1
        elif line.strip().startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            blocks.append(("table", lines[i:j], i, j))
            i = j
        elif line.startswith(">"):
            j = i
            while j < n and lines[j].startswith(">"):
                j += 1
            blocks.append(("quote", [re.sub(r"^> ?", "", x) for x in lines[i:j]], i))
            i = j
        elif re.match(r"^- ", line):
            j = i
            items, cur = [], None
            while j < n and (re.match(r"^- ", lines[j]) or (lines[j].startswith("  ") and lines[j].strip())):
                if re.match(r"^- ", lines[j]):
                    if cur is not None:
                        items.append(cur)
                    cur = lines[j][2:]
                else:
                    cur += " " + lines[j].strip()
                j += 1
            items.append(cur)
            blocks.append(("ulist", items, i))
            i = j
        elif re.match(r"^\d+\. ", line):
            j = i
            items, cur, num = [], None, None
            while j < n and (re.match(r"^\d+\. ", lines[j]) or (lines[j].strip() and not lines[j].startswith(("#", "|", ">", "- ", "<!--")) and cur is not None and not re.match(r"^\d+\. ", lines[j]) and lines[j - 1].strip())):
                m = re.match(r"^(\d+)\. (.*)", lines[j])
                if m:
                    if cur is not None:
                        items.append((num, cur))
                    num, cur = m.group(1), m.group(2)
                else:
                    cur += "\n" + lines[j]
                j += 1
            items.append((num, cur))
            blocks.append(("olist", items, i))
            i = j
        else:
            j = i
            par = []
            while j < n and lines[j].strip() and not lines[j].startswith(("#", "|", ">", "```")) \
                    and not ANCHOR_RE.match(lines[j].strip()) and lines[j].strip() != "---" \
                    and not re.match(r"^- ", lines[j]) and not re.match(r"^\d+\. ", lines[j]):
                par.append(lines[j])
                j += 1
            blocks.append(("para", "\n".join(par), i, j))
            i = j
    return blocks


# ---------------------------------------------------------------- конверсия

def convert(md_text):
    lines = md_text.split("\n")
    blocks = parse_blocks(lines)
    out = []
    dropped_spans = []      # диапазоны строк md, выброшенные как копии таблиц
    seen_tables = set()
    anchors_found = []
    current_section = None
    title = None
    bib_placeholder_set = False

    k = 0
    while k < len(blocks):
        b = blocks[k]
        kind = b[0]
        if kind == "heading":
            level, text = b[1], b[2]
            if level == 1 and title is None:
                title = text
                dropped_spans.append((b[3], b[3] + 1))
                k += 1
                continue
            if level == 1:
                current_section = text
                if text.startswith("Appendix A") and not bib_placeholder_set:
                    out.append("%%BIBLIOGRAPHY%%")
                    bib_placeholder_set = True
                if text == "Supplementary Material":
                    out.append("\\clearpage")
                    out.append("\\renewcommand{\\thetable}{S\\arabic{table}}")
                    out.append("\\setcounter{table}{0}")
                out.append("\\section{%s}" % inline(text))
            elif level == 3:
                out.append("\\subsection{%s}" % inline(text))
            elif level == 4:
                out.append("\\subsubsection{%s}" % inline(text))
            else:
                out.append("\\subsection{%s}" % inline(text))
            out.append("")
            k += 1
        elif kind == "anchor":
            num, fname = b[1], b[2]
            anchors_found.append((num, fname))
            # markdown-копию сразу после якоря выбрасываем
            if k + 1 < len(blocks) and blocks[k + 1][0] == "table":
                t = blocks[k + 1]
                dropped_spans.append((t[2], t[3]))
                k += 1
            if fname not in seen_tables:
                seen_tables.add(fname)
                if not num.startswith("S"):
                    out.append("\\setcounter{table}{%d}" % (int(num) - 1))
                out.append("\\input{tables/%s}" % fname)
                out.append("")
            k += 1
        elif kind == "table":
            out.append(md_table_to_latex(b[1]))
            out.append("")
            k += 1
        elif kind == "quote":
            out.append("\\begin{quote}")
            out.append(inline("\n".join(b[1])))
            out.append("\\end{quote}")
            out.append("")
            k += 1
        elif kind == "fence":
            out.append("\\begin{verbatim}")
            out.extend(b[1])
            out.append("\\end{verbatim}")
            out.append("")
            k += 1
        elif kind == "ulist":
            out.append("\\begin{itemize}")
            for it in b[1]:
                out.append("\\item " + inline(it))
            out.append("\\end{itemize}")
            out.append("")
            k += 1
        elif kind == "olist":
            out.append("\\begin{enumerate}")
            for num, it in b[1]:
                out.append("\\item[%s.] " % num + inline(it))
            out.append("\\end{enumerate}")
            out.append("")
            k += 1
        elif kind == "rule":
            dropped_spans.append((b[1], b[1] + 1))
            k += 1
        elif kind == "para":
            text = b[1]
            if re.match(r"(?:Сборка|Assembled) 20", text):
                out.append("{\\footnotesize " + inline(text) + "\\par}")
                out.append("")
                k += 1
                continue
            m = re.match(r"\*\*(?:Рисунок|Figure) (\d)\. ", text)
            if m and current_section and current_section.startswith("Appendix B"):
                fig = FIGURE_FILES[int(m.group(1))]
                out.append("\\begin{figure}[p]")
                out.append("\\centering")
                out.append("\\includegraphics[width=\\linewidth]{figures/%s}" % fig)
                out.append("\\par\\vspace{6pt}")
                out.append("{\\small " + inline(text) + "\\par}")
                out.append("\\end{figure}")
            else:
                out.append(inline(text))
            out.append("")
            k += 1
        else:
            raise RuntimeError(f"неизвестный блок {kind}")
    return "\n".join(out), title, anchors_found, dropped_spans


# ---------------------------------------------------------------- библиография

def parse_references():
    text = REFS.read_text(encoding="utf-8")
    paras = [p.strip().replace("\n", " ") for p in text.split("\n\n") if p.strip()]
    entries = []
    for p in paras:
        m = re.match(r"^([^()]{0,70}?, \d{4}[a-z]?)\. (.*)$", p)
        if m and not p.startswith("#"):
            label, body = m.group(1), m.group(2)
            if body.endswith("**"):
                # известный дефект копипасты в источнике (Venkatraman et al., 2024)
                print(f"[warn] запись «{label}»: срезан хвост '**' из источника")
                body = body.rstrip("*")
            entries.append((label, body))
    return entries


TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
            "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
            "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
            "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
            "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ё": "yo"}


def bib_key(label):
    m = re.match(r"^(.*?), (\d{4}[a-z]?)$", label)
    name, year = m.group(1), m.group(2)
    first = re.split(r" et al| и ", name)[0].strip()
    first = re.sub(r"[^\w]", "", first.split()[0]).lower()
    first = "".join(TRANSLIT.get(c, c) for c in first)
    first = unicodedata.normalize("NFKD", first)
    first = "".join(c for c in first if c.isascii() and c.isalnum())
    return f"{first}{year}"


def parse_authors(authors_str):
    tokens = [t.strip() for t in authors_str.split(",")]
    people, cur = [], None
    for t in tokens:
        if not t:
            continue
        if re.match(r"^(и др|et al)\.?$", t):
            if cur:
                people.append(cur)
                cur = None
            people.append("others")
        elif re.match(r"^[A-ZА-ЯЁ]\.($| ?-?[A-ZА-ЯЁ]?\.?( III)?$)|^[A-ZА-ЯЁ]\. [A-ZА-ЯЁ]\.( [A-ZА-ЯЁ]\.)?$", t):
            if cur is None:
                cur = t
            else:
                people.append(cur + ", " + t)
                cur = None
        else:
            if cur is not None:
                people.append(cur)
            cur = t
    if cur:
        people.append(cur)
    return " and ".join(people)


def make_bib(entries):
    out, keys = [], set()
    for label, body in entries:
        key = bib_key(label)
        assert key not in keys, f"дубль ключа {key}"
        keys.add(key)
        year = re.search(r"\((\d{4})\)", body)
        m = re.match(r"^(.*?) \((\d{4})\)\. (.*)$", body)
        if not m:
            out.append("@misc{%s,\n  note = {%s},\n}" % (key, body))
            continue
        authors_str, year, rest = m.groups()
        authors_str = re.sub(r" и др$", ", и др", authors_str)
        author = parse_authors(authors_str)
        tm = re.match(r"^(.+?[.?!])\s+(.*)$", rest)
        title, tail = (tm.group(1), tm.group(2)) if tm else (rest, "")
        title = title.rstrip(".")
        um = re.search(r"(https?://\S+?)\.?\*{0,2}$", tail)
        url = um.group(1) if um else ""
        venue = tail[: um.start()].strip().rstrip(".") if um else tail.strip().rstrip(".")
        if "aclanthology.org" in url or re.search(r"Proceedings|Workshop|Conference|ACL|EMNLP|NAACL|COLING|ICML|NeurIPS|ICLR|CHI|IJCAI|LREC|EACL|SemEval|Dialogue", venue):
            etype, vfield = "inproceedings", "booktitle"
        elif re.search(r"Journal|journal|PNAS|PLOS|Patterns|iScience|Science|Frontiers|Behavior|Expert Systems|Information|Cell Reports|Transactions|Доклады", venue):
            etype, vfield = "article", "journal"
        else:
            etype, vfield = "misc", "howpublished"
        fields = [f"  author = {{{author}}}", f"  title = {{{title}}}", f"  year = {{{year}}}"]
        if venue:
            fields.append(f"  {vfield} = {{{venue}}}")
        if url:
            fields.append(f"  url = {{{url}}}")
        out.append("@%s{%s,\n%s,\n}" % (etype, key, ",\n".join(fields)))
    header = ("% Машинно-сгенерировано из 08-paper/references-section.md скриптом\n"
              "% 09-tools/build_latex_paper.py. Черновик для этапа журнала: типы записей\n"
              "% и разбор полей эвристические, перед подачей в журнал требуется вычитка.\n\n")
    return header + "\n\n".join(out) + "\n", len(entries)


URL_RE = re.compile(r"https?://[^\s]+")


def inline_with_urls(s):
    """Как inline(), но URL оборачиваются в \\url{} и переносятся пакетом xurl."""
    parts, last = [], 0
    for m in URL_RE.finditer(s):
        url = m.group(0)
        trail = ""
        while url and url[-1] in ").,;":
            trail = url[-1] + trail
            url = url[:-1]
        parts.append(inline(s[last:m.start()]))
        parts.append("\\url{" + url + "}" + inline(trail))
        last = m.end()
    parts.append(inline(s[last:]))
    return "".join(parts)


def en_entry(label, body):
    """Приведение записи к английской вёрстке: союзы в метке, «и др» → et al.,
    транслитерация кириллической метки. Тело записи не переводится."""
    label = label.replace(" и ", " and ").replace("Грицай", "Gritsay")
    body = body.replace(" и др", " et al.")
    return label, body


def render_bibliography(entries, intro_para):
    section = "Литература" if LANG == "ru" else "References"
    lines = ["\\section{%s}" % section, ""]
    if intro_para:
        lines.append(inline(intro_para))
        lines.append("")
    for label, body in entries:
        if LANG == "en":
            label, body = en_entry(label, body)
        lines.append("\\noindent\\hangindent=1.5em " + inline_with_urls(f"{label}. {body}") + "\\par\\smallskip")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- нормализация

WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def tokens_from_md(md_text, dropped_spans):
    lines = md_text.split("\n")
    drop = set()
    for a, b in dropped_spans:
        drop.update(range(a, b))
    kept = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        if ANCHOR_RE.match(line.strip()):
            continue
        kept.append(line)
    text = "\n".join(kept)
    text = re.sub(r"```", " ", text)
    for ch, base in ACCENT_FOLD.items():
        text = text.replace(ch, base)
    return WORD.findall(text)


def tokens_from_tex(tex_body):
    lines = []
    for line in tex_body.split("\n"):
        s = line.strip()
        if s.startswith(("\\input{", "\\includegraphics", "\\begin{", "\\end{",
                         "\\setcounter", "\\renewcommand", "\\clearpage",
                         "%%BIBLIOGRAPHY%%", "\\toprule", "\\midrule",
                         "\\bottomrule", "\\centering", "\\par\\vspace")):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\\foreignlanguage\{russian\}", " ", text)
    text = re.sub(r"\$[^$]*\$", " ", text)          # math — с обеих сторон не токены
    text = re.sub(r"\\item\[([^\]]*)\]", r" \1 ", text)
    # акцентные команды сводятся к базовой букве до общего снятия команд
    text = re.sub(r"\\['\"`^~=.]\{?([a-zA-Z])\}?", r"\1", text)
    text = re.sub(r"\\v\{([a-zA-Z])\}", r"\1", text)
    text = re.sub(r"=\d+(\.\d+)?em", " ", text)     # \hangindent=1.5em
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)     # команды
    text = re.sub(r"[{}\[\]&]", " ", text)
    return WORD.findall(text)


def compare_tokens(a, b, what):
    if a == b:
        gate(f"G4 токены {what}", True, f"{len(a)} токенов совпали")
        return
    n = min(len(a), len(b))
    pos = next((i for i in range(n) if a[i] != b[i]), n)
    ctx_a = " ".join(a[max(0, pos - 8): pos + 8])
    ctx_b = " ".join(b[max(0, pos - 8): pos + 8])
    gate(f"G4 токены {what}", False,
         f"расхождение на позиции {pos} из {len(a)}/{len(b)}:\n  md : …{ctx_a}…\n  tex: …{ctx_b}…")


# ---------------------------------------------------------------- преамбула

PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[T2A,T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[english,main=russian]{babel}
\usepackage[margin=2.5cm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{textcomp}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{longtable}
\usepackage{rotating}
\usepackage{xurl}
\usepackage{ragged2e}
\setlength{\tabcolsep}{4pt}
\setlength{\LTcapwidth}{\textwidth}
% allow line breaks after underscores in identifiers
\renewcommand{\_}{\textunderscore\allowbreak}
\usepackage{graphicx}
\usepackage{microtype}
\usepackage[hidelinks,unicode]{hyperref}
\emergencystretch=2em
\sloppy
\setcounter{secnumdepth}{-1}
% Unicode characters used by the canonical table files (tab-*.tex are not edited)
\DeclareUnicodeCharacter{00B7}{\ensuremath{\cdot}}
\DeclareUnicodeCharacter{00D7}{\ensuremath{\times}}
\DeclareUnicodeCharacter{0394}{\ensuremath{\Delta}}
\DeclareUnicodeCharacter{03B1}{\ensuremath{\alpha}}
\DeclareUnicodeCharacter{2074}{\ensuremath{^{4}}}
\DeclareUnicodeCharacter{2076}{\ensuremath{^{6}}}
\DeclareUnicodeCharacter{2077}{\ensuremath{^{7}}}
\DeclareUnicodeCharacter{207B}{\ensuremath{^{-}}}
\DeclareUnicodeCharacter{2212}{\ensuremath{-}}
% allow line breaks after en-dashes in numeric ranges
\DeclareUnicodeCharacter{2013}{\textendash\allowbreak}
"""


def main():
    md_text = MANUSCRIPT.read_text(encoding="utf-8")
    body, title, anchors, dropped = convert(md_text)

    # G1: якоря
    uniq = {f for _, f in anchors}
    missing = [f for f in uniq if not (TABLES_DIR / f"{f}.tex").exists()]
    gate("G1 якоря таблиц", len(anchors) == 25 and len(uniq) == 24 and not missing,
         f"якорей {len(anchors)}, уникальных {len(uniq)}, отсутствуют файлы: {missing}")

    # библиография
    entries = parse_references()
    refs_intro = None
    if LANG == "en":
        refs_intro = REFS_INTRO_EN.read_text(encoding="utf-8").strip().replace("\n", " ")
    else:
        for p in REFS.read_text(encoding="utf-8").split("\n\n"):
            p = p.strip()
            if p and not p.startswith("#") and not re.match(r"^\S[^.]*?, \d{4}[a-z]?\.", p):
                refs_intro = p.replace("\n", " ")
                break
    bib_text, n_bib = make_bib(entries)
    bib_section = render_bibliography(entries, refs_intro)
    gate("G5 записи библиографии", n_bib == len(entries) and len(entries) == 68,
         f"записей: источник {len(entries)}, bib {n_bib} (ожидалось 68)")

    pre_body = body
    body = body.replace("%%BIBLIOGRAPHY%%", bib_section)

    # заголовок, аннотация, ключевые слова
    abstract_md = ABSTRACT.read_text(encoding="utf-8")
    abstract_body = "\n\n".join(
        p.strip() for p in abstract_md.split("\n\n")
        if p.strip() and not p.strip().startswith("#"))
    abstract_tex = "\n\n".join(inline(p) for p in abstract_body.split("\n\n"))

    preamble = PREAMBLE if LANG == "ru" else PREAMBLE.replace(
        "[english,main=russian]", "[russian,main=english]")
    doc = [preamble]
    doc.append(r"\title{%s}" % inline(title))
    doc.append(r"""\author{Stanislav Kirichenko\\
\small Independent Researcher\\
\small \texttt{staurus86@gmail.com}\\
\small ORCID: \href{https://orcid.org/0009-0001-2914-9541}{0009-0001-2914-9541}}
\date{}""")
    doc.append(r"\begin{document}")
    doc.append(r"\maketitle")
    doc.append(r"\begin{abstract}")
    doc.append(abstract_tex)
    doc.append(r"\end{abstract}")
    kw_label = "Ключевые слова" if LANG == "ru" else "Keywords"
    doc.append(r"\noindent\textbf{%s:} " % kw_label + "; ".join(KEYWORDS) + r".\par\bigskip")
    doc.append(body)
    doc.append(r"\end{document}")
    main_tex = "\n".join(doc) + "\n"

    # G2: markdown-остатки
    remnants = []
    for pat in ["**", "|---", "<!--", "### ", "\n# "]:
        if pat in main_tex.replace("\\#", ""):
            remnants.append(pat)
    gate("G2 markdown-остатки", not remnants, str(remnants))

    # G3: белый список символов
    bad = sorted({ch for ch in main_tex
                  if ord(ch) > 127 and not (0x0400 <= ord(ch) <= 0x04FF)
                  and ch not in ALLOWED_LITERAL})
    gate("G3 белый список символов", not bad,
         " ".join(f"U+{ord(c):04X}({c})" for c in bad))

    # G4: токены тела (без библиографии — она сверяется отдельно)
    md_tokens = tokens_from_md(md_text, dropped)
    tex_tokens = tokens_from_tex(pre_body)
    compare_tokens(md_tokens, tex_tokens, "рукописи")
    if LANG == "en":
        pairs = [en_entry(l, b) for l, b in entries]
        section_word = "References"
    else:
        pairs = entries
        section_word = "Литература"
    ref_src = refs_intro + " " + " ".join(f"{l}. {b}" for l, b in pairs)
    for ch, base in ACCENT_FOLD.items():
        ref_src = ref_src.replace(ch, base)
    ref_src_tokens = [section_word] + WORD.findall(ref_src)
    ref_tex_tokens = tokens_from_tex(bib_section)
    compare_tokens(ref_src_tokens, ref_tex_tokens, "библиографии")

    if failures:
        print(f"\nСборка остановлена: {len(failures)} провалов шлюзов")
        sys.exit(1)

    # запись выхода
    OUT.mkdir(exist_ok=True)
    (OUT / "tables").mkdir(exist_ok=True)
    (OUT / "figures").mkdir(exist_ok=True)
    for f in sorted(TABLES_DIR.glob("tab-*.tex")):
        text = f.read_text(encoding="utf-8")
        for rule in TABLE_ADAPTATIONS.get(f.name, []):
            old, new, count = rule if len(rule) == 3 else (*rule, -1)
            assert old in text, f"{f.name}: не найден фрагмент для адаптации: {old}"
            text = text.replace(old, new, count)
        # \raggedright запрещает переносы слов в p-ячейках, \RaggedRight разрешает
        # \RaggedRight возвращает переносы слов; \hspace{0pt} разрешает
        # перенос первого слова ячейки
        text = text.replace(r">{\raggedright\arraybackslash}",
                            r">{\RaggedRight\hspace{0pt}\arraybackslash}")
        text = rescale_p_columns(text)
        (OUT / "tables" / f.name).write_text(text, encoding="utf-8", newline="\n")
        # G6: адаптация не изменила словесные токены таблицы
        if text != f.read_text(encoding="utf-8"):
            same = tokens_from_tex(f.read_text(encoding="utf-8")) == tokens_from_tex(text)
            gate(f"G6 адаптация {f.name}", same, "токены копии разошлись с оригиналом")
            if not same:
                sys.exit(1)
    for f in sorted(FIGURES_DIR.glob("fig-*.pdf")):
        shutil.copy2(f, OUT / "figures" / f.name)
    (OUT / "main.tex").write_text(main_tex, encoding="utf-8", newline="\n")
    (OUT / "references.bib").write_text(bib_text, encoding="utf-8", newline="\n")

    def sha(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "09-tools/build_latex_paper.py",
        "generator_sha256": sha(__file__),
        "inputs": {
            "manuscript-final.md": sha(MANUSCRIPT),
            "references-section.md": sha(REFS),
            "abstract.md": sha(ABSTRACT),
            "declarations.md": sha(DECLARATIONS),
            "title-and-keywords.md": sha(TITLE_KEYWORDS),
            "tables": {f.name: sha(f) for f in sorted(TABLES_DIR.glob("tab-*.tex"))},
            "figures": {f.name: sha(f) for f in sorted(FIGURES_DIR.glob("fig-*.pdf"))},
        },
        "outputs": {
            "main.tex": sha(OUT / "main.tex"),
            "references.bib": sha(OUT / "references.bib"),
            "tables_copies": {f.name: sha(f) for f in sorted((OUT / "tables").glob("tab-*.tex"))},
        },
        "notes": [
            "Цитаты в тексте — дословный текст замороженной рукописи, не \\cite.",
            "references.bib — машиночитаемый параллельный артефакт, в компиляцию не входит.",
            "Нумерация таблиц задаётся \\setcounter перед каждым \\input: таблица 8 стоит в тексте раньше таблицы 2.",
            "Копии таблиц в latex/tables адаптированы к ширине полосы (окружение, "
            "спецификация колонок, переносы); содержимое ячеек не менялось — шлюз G6 "
            "сверяет словесные токены каждой изменённой копии с каноническим файлом.",
            "Запись Venkatraman et al., 2024: из источника срезан хвост '**'; "
            "URL записи требует проверки PI — см. status-2026-08-02.md.",
        ],
    }
    (OUT / "latex-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"\nГотово: {OUT / 'main.tex'} ({len(main_tex)} байт), "
          f"references.bib ({n_bib} записей), таблиц {len(list((OUT / 'tables').glob('*.tex')))}, "
          f"рисунков {len(list((OUT / 'figures').glob('*.pdf')))}")


if __name__ == "__main__":
    main()
