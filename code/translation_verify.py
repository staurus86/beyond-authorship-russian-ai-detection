# -*- coding: utf-8 -*-
"""Шлюзы перевода: сверка каждого EN-чанка с русским исходником.

T1 — мультимножество чисел совпадает (потеря/искажение числа = брак чанка);
T2 — HTML-якоря таблиц идентичны и в том же порядке;
T3 — структура: число заголовков по уровням, markdown-таблиц, блоков цитат;
T4 — запрещённая AI-slop-лексика отсутствует;
T5 — код-спаны: мультимножество содержимого `...` совпадает;
T6 — в EN-чанке не осталось кириллицы вне код-спанов (кроме белого списка).

Запуск: python translation_verify.py [--assemble]
"""
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TDIR = ROOT / "08-paper" / "translation-en"
SRC, EN = TDIR / "src", TDIR / "en"

NUM = re.compile(r"\d+(?:[.,]\d+)?(?:e[-+]?\d+)?", re.I)
ANCHOR = re.compile(r"<!-- ТАБЛИЦА [^>]+ -->")
CODE = re.compile(r"`[^`]+`")
FENCE = re.compile(r"```.*?```", re.S)
BANNED = [
    "delve", "tapestry", "vibrant", "pivotal", "paramount", "nuanced",
    "moreover", "furthermore", "embark", "testament", "realm", "beacon",
    "cornerstone", "seamless", "transformative", "unprecedented", "crucially",
    "it is important to note", "in today's",
]
# кириллица, допустимая в EN-тексте: якоря обрабатываются отдельно;
# слова строки-примера из корпуса в Results §7 — это данные, а не текст статьи
CYRILLIC_OK = {"Грицай", "ТАБЛИЦА", "Как", "перевести", "сеошные", "отчеты", "деньги"}

# управляемый перевод код-спанов с дизайн-нотацией: только эти четыре;
# пример переноса `за- ключение` — данные корпуса и не переводится
SPAN_MAP = {
    "`коррекция извлечения correction-v5.0`": "`extraction correction correction-v5.0`",
    "`45 заданий × 4 канала генерации × 3 режима задания × 2 повтора`":
        "`45 tasks × 4 generation channels × 3 task modes × 2 replicates`",
    "`задание × модель × повтор`": "`task × model × replicate`",
    "`документ × преобразование`": "`document × transformation`",
}

failures = []


def gate(chunk, name, ok, detail=""):
    if not ok:
        failures.append(f"{chunk} {name}: {detail}")
        print(f"[FAIL] {chunk} {name}: {detail}")


def numbers(text):
    # код-спаны и якоря не считаем: имена файлов вида prep-v5 дают шум,
    # но числа в них обязаны сохраниться и так — они внутри неизменяемых спанов
    t = CODE.sub(" ", text)
    t = ANCHOR.sub(" ", t)
    return collections.Counter(n.replace(",", ".") for n in NUM.findall(t))


def structure(text):
    lines = text.split("\n")
    return {
        "h1": sum(1 for l in lines if re.match(r"^# ", l)),
        "h3": sum(1 for l in lines if re.match(r"^### ", l)),
        "h4": sum(1 for l in lines if re.match(r"^#### ", l)),
        "table_rows": sum(1 for l in lines if l.strip().startswith("|")),
        "quotes": sum(1 for l in lines if l.startswith(">")),
        "fences": text.count("```") // 2,
    }


def main():
    src_files = sorted(SRC.glob("chunk-*.md"))
    pairs = []
    for sf in src_files:
        n = sf.name.split("-")[1]
        matches = list(EN.glob(f"chunk-{n}-*.md"))
        if not matches:
            failures.append(f"chunk-{n}: перевод отсутствует")
            print(f"[FAIL] chunk-{n}: перевод отсутствует")
            continue
        pairs.append((sf, matches[0]))

    for sf, ef in pairs:
        cid = sf.name.split("-")[1]
        s = sf.read_text(encoding="utf-8")
        e = ef.read_text(encoding="utf-8")
        if cid == "01":
            continue  # титульный чанк: новое название и provenance, сверка не применима

        sn, en_ = numbers(s), numbers(e)
        if sn != en_:
            miss = sn - en_
            extra = en_ - sn
            gate(cid, "T1 числа", False,
                 f"потеряно {dict(miss)}, лишнее {dict(extra)}")

        sa, ea = ANCHOR.findall(s), ANCHOR.findall(e)
        gate(cid, "T2 якоря", sa == ea, f"{len(sa)} vs {len(ea)}")

        ss, es = structure(s), structure(e)
        for k in ss:
            if ss[k] != es[k]:
                gate(cid, f"T3 структура {k}", False, f"{ss[k]} vs {es[k]}")

        low = CODE.sub(" ", e).lower()
        hits = [b for b in BANNED if b in low]
        # 'delve' может встретиться в цитировании 'LLM-DetectAIve'? нет; проверка по границам слова
        hits = [b for b in hits if re.search(r"(?<![\w-])" + re.escape(b) + r"(?![\w-])", low)]
        gate(cid, "T4 slop-лексика", not hits, str(hits))

        # T7: fenced-блоки не переводятся — сравниваются как есть, затем снимаются
        sf_, ef_ = FENCE.findall(s), FENCE.findall(e)
        gate(cid, "T7 fenced-блоки", collections.Counter(sf_) == collections.Counter(ef_),
             f"{len(sf_)} vs {len(ef_)}")
        s_nf, e_nf = FENCE.sub(" ", s), FENCE.sub(" ", e)

        def spans(t, mapped=False):
            out = []
            for m in CODE.findall(t):
                m = re.sub(r"\s+", " ", m)
                if mapped:
                    m = SPAN_MAP.get(m, m)
                out.append(m)
            return collections.Counter(out)

        sc, ec = spans(s_nf, mapped=True), spans(e_nf)
        if sc != ec:
            gate(cid, "T5 код-спаны", False,
                 f"потеряно {list((sc - ec).keys())[:5]}, лишнее {list((ec - sc).keys())[:5]}")

        no_code = CODE.sub(" ", e_nf)
        no_anchor = ANCHOR.sub(" ", no_code)
        cyr = collections.Counter(re.findall(r"[А-Яа-яЁё]{2,}", no_anchor))
        cyr = {w: c for w, c in cyr.items() if w not in CYRILLIC_OK}
        gate(cid, "T6 кириллица", not cyr, str(dict(list(cyr.items())[:8])))

    if failures:
        print(f"\nПровалов: {len(failures)}")
        sys.exit(1)
    print(f"[ok] все шлюзы: {len(pairs)} чанков чистые")

    if "--assemble" in sys.argv:
        parts = []
        for sf, ef in pairs:
            parts.append(ef.read_text(encoding="utf-8").rstrip())
        out = ROOT / "08-paper" / "manuscript-final-en.md"
        out.write_text("\n\n".join(parts) + "\n", encoding="utf-8", newline="\n")
        import hashlib
        h = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"[ok] собран {out.name}, sha256 {h}")


if __name__ == "__main__":
    main()
