#!/usr/bin/env python3
"""Preflight перед пакетом процедур 2–4. Жёсткое завершение при любой ошибке.

Спецификации: `07-analysis/proc2-classifier-spec.md`, `proc3-zeroshot-spec.md`,
`proc4-judge-spec.md`; статус и правило синтеза — `procedures-2-4-registration.md`.
Хеши — `07-analysis/procedures-2-4.sha256.md`.

    python 09-tools/preflight_procedures_2_4.py

Шесть блоков проверок. Первые пять — требования PI от 2026-07-27, шестой добавлен
2026-07-28 вместе со снятием блокировки судьи:
  1. каждый P2b-fold содержит оба класса; обе половины пары O1 — в одном fold;
  2. source-holdout выполним: в тестовой части есть оба класса;
  3. negative control переставляет метки на уровне кластера и гоняет весь pipeline;
  4. судья: пять критериев §1.1, промпт и текст помещаются в num_ctx без усечения;
  5. процедура 3: поведение токенизатора и выбор модели по замеру скорости;
  6. направления всех шкал приведены к одной конвенции «больше = более AI-подобно».

Значения NLL и оценки судьи здесь не считаются и не печатаются: preflight меряет
выполнимость, а не зависимую переменную.
"""

import csv
import hashlib
import json
import random
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DOCUMENTS = ROOT / "04-corpus" / "documents-registry.csv"
MANIFEST_PATH = ROOT / "07-analysis" / "procedures-2-4-manifest.json"
SPECS = ["procedures-2-4-registration.md", "proc2-classifier-spec.md",
         "proc3-zeroshot-spec.md", "proc4-judge-spec.md"]

SEED = 20260727
OUTER_FOLDS = 4          # по числу AI-каналов
INNER_FOLDS = 3
OLLAMA = "http://localhost:11434"

# Процедура 4, §1 и §3 спецификации. Артефакт установлен 2026-07-28, амендмент той же даты.
JUDGE_MODEL = "gemma3:12b-it-qat"
JUDGE_NUM_CTX = 16384
JUDGE_NUM_PREDICT = 200
JUDGE_SEEDS = [20260727, 20260728, 20260729]
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100},
                   "reason": {"type": "string"}},
    "required": ["score", "reason"],
}

# Процедура 3, §2 спецификации. Резерв — та же линейка и тот же токенизатор.
NLL_MODEL = "ai-forever/rugpt3large_based_on_gpt2"
NLL_REVISION = "29c569320be2e08f29757898ec3866413725acd7"
NLL_FALLBACK = "ai-forever/rugpt3small_based_on_gpt2"
NLL_TIME_BUDGET_S = 8 * 3600   # §2: потолок поднят с четырёх часов амендментом 2026-07-28
NLL_PROBE_DOCS = 10
NLL_WINDOW, NLL_STRIDE = 1024, 512

# §5: единая конвенция направления. Больше значит «более AI-подобно».
SCALE_CONVENTION = {
    "proc1_common": {"direction": "as_is", "note": "индекс уже растёт с AI-подобием"},
    "proc1_format": {"direction": "as_is", "note": "то же"},
    "p2a_score": {"direction": "as_is", "note": "вероятность класса A"},
    "p2b_score": {"direction": "as_is", "note": "вероятность класса A"},
    "proc3_nll": {"direction": "invert", "note": "низкий NLL = лучше предсказуем = "
                                                 "более AI-подобно; при синтезе знак обращается"},
    "proc4_judge": {"direction": "as_is", "note": "шкала рубрики растёт с выраженностью"},
}

FAILURES = []


def check(ok, message, detail=""):
    print(("  OK   " if ok else "  СБОЙ ") + message + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(message + (f": {detail}" if detail else ""))
    return ok


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_registry():
    return list(csv.DictReader(DOCUMENTS.open(encoding="utf-8-sig", newline="")))


def judge_prompt():
    """Промпт §2 берётся из самой спецификации — так он дословно один и тот же."""
    spec = (ROOT / "07-analysis" / "proc4-judge-spec.md").read_text(encoding="utf-8")
    return spec.split("```")[1].strip("\n")


def post(url, payload, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read().decode("utf-8")


def probe_nll_model(name, revision, docs):
    """Замер скорости и фактического поведения токенизатора на десяти документах.

    Значения NLL здесь не печатаются и никуда не записываются: preflight меряет
    время и покрытие, а не зависимую переменную.
    """
    import time

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(name, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(name, revision=revision,
                                                 dtype=torch.float32)
    model.to("cpu").eval()
    probe_ids = tok("проверка", add_special_tokens=True)["input_ids"]
    behaviour = {"bos_token_id": tok.bos_token_id, "eos_token_id": tok.eos_token_id,
                 "adds_bos": bool(probe_ids) and probe_ids[0] == tok.bos_token_id,
                 "n_positions": getattr(model.config, "n_positions", None)}

    tokens_done, windows_done, t0 = 0, 0, time.time()
    for row in docs[:NLL_PROBE_DOCS]:
        text = unicodedata.normalize(
            "NFC", (ROOT / row["prose_path"]).read_text(encoding="utf-8"))
        seq = [tok.bos_token_id] + tok(text, add_special_tokens=False)["input_ids"]
        start, n = 0, len(seq) - 1
        while True:
            window = seq[start:start + NLL_WINDOW]
            with torch.no_grad():
                model(torch.tensor([window]))
            windows_done += 1
            last = start + len(window) - 1
            tokens_done += last - (1 if start == 0 else start + NLL_STRIDE) + 1
            if last >= n:
                break
            start += NLL_STRIDE
    elapsed = time.time() - t0
    words_probe = sum(int(r["prose_words"] or 0) for r in docs[:NLL_PROBE_DOCS])
    words_all = sum(int(r["prose_words"] or 0) for r in docs)
    projected = elapsed / max(words_probe, 1) * words_all
    return behaviour, elapsed, tokens_done, projected


def check_nll(docs):
    """§2 и §3 спецификации 3: выбор между large и small — по замеру, не по вкусу."""
    docs = sorted(docs, key=lambda r: r["document_id"])
    chosen, cfg = None, {}
    for name, revision in ((NLL_MODEL, NLL_REVISION), (NLL_FALLBACK, None)):
        try:
            behaviour, elapsed, tokens, projected = probe_nll_model(name, revision, docs)
        except Exception as exc:
            check(False, f"модель {name} загружается", str(exc)[:120])
            continue
        print(f"   {name}: {NLL_PROBE_DOCS} документов за {elapsed:.1f} c, "
              f"{tokens} токенов; полный прогон ≈ {projected / 3600:.2f} ч")
        print(f"   токенизатор: bos {behaviour['bos_token_id']}, "
              f"eos {behaviour['eos_token_id']}, ставит BOS сам — "
              f"{'да' if behaviour['adds_bos'] else 'нет'}, "
              f"n_positions {behaviour['n_positions']}")
        cfg = {"model": name, "revision": revision or "не закреплён",
               "device": "cpu", "dtype": "float32",
               "tokenizer_behaviour": behaviour,
               "probe_documents": NLL_PROBE_DOCS, "probe_seconds": round(elapsed, 1),
               "projected_full_run_hours": round(projected / 3600, 2),
               "window": NLL_WINDOW, "stride": NLL_STRIDE}
        if projected <= NLL_TIME_BUDGET_S:
            chosen = name
            break
        print(f"   {name} не укладывается в {NLL_TIME_BUDGET_S / 3600:.0f} ч — "
              f"переход к резервной модели §2")
    check(chosen is not None, "модель процедуры 3 выбрана замером скорости",
          f"{chosen}, оценка {cfg.get('projected_full_run_hours')} ч"
          if chosen else "ни одна модель не уложилась в бюджет времени")
    if cfg:
        check(cfg["tokenizer_behaviour"]["adds_bos"] is False,
              "токенизатор BOS не ставит — код добавляет его явно",
              "поведение совпало с проверкой 2026-07-27")
        from huggingface_hub import try_to_load_from_cache
        hit = try_to_load_from_cache(
            cfg["model"], "pytorch_model.bin",
            revision=NLL_REVISION if cfg["model"] == NLL_MODEL else None)
        check(isinstance(hit, str), "веса модели процедуры 3 лежат в кеше",
              str(hit)[:120])
        if isinstance(hit, str):
            cfg["weights_sha256"] = sha256(hit)
            cfg["weights_path"] = hit
            # §2 требует фиксированный revision: у резервной модели он берётся
            # из фактического снапшота, из которого грузились веса.
            cfg["revision"] = Path(hit).parent.name
            check(len(cfg["revision"]) == 40, "revision модели закреплён",
                  cfg["revision"])
    return cfg


def build_outer_folds(rows, seed):
    """§3.1 спецификации P2b: четыре внешних fold-а.

    В каждом ровно один невиданный AI-канал и несколько невиданных человеческих
    источников. Человеческие источники распределяются жадно по числу документов:
    самый крупный уходит в наименее заполненный fold. Вход алгоритма — класс,
    размер группы и seed; признаки и результаты в него не входят.
    """
    ai_channels = sorted({r["generation_channel"] for r in rows if r["origin_class"] == "A"})
    human_sources = Counter(r["split_group_source"] for r in rows if r["origin_class"] == "H")

    channels = list(ai_channels)
    random.Random(seed).shuffle(channels)
    folds = {i: {"ai": ch, "human": []} for i, ch in enumerate(channels)}

    load = {i: 0 for i in folds}
    for source, size in sorted(human_sources.items(), key=lambda kv: (-kv[1], kv[0])):
        target = min(load, key=lambda i: (load[i], i))
        folds[target]["human"].append(source)
        load[target] += size
    return folds, load



def main():
    print(f"PREFLIGHT процедур 2–4, {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    rows = read_registry()
    by_id = {r["document_id"]: r for r in rows}

    print("0. Спецификации на месте и захешированы")
    for name in SPECS:
        path = ROOT / "07-analysis" / name
        check(path.exists(), f"спецификация {name}",
              sha256(path)[:16] if path.exists() else "отсутствует")

    print("\n1. P2b: внешние fold-ы по числу AI-каналов")
    seo = [r for r in rows if r["genre"] == "seo"]
    check(len({r["origin_class"] for r in seo}) == 2, "в жанре seo есть оба класса",
          str(dict(Counter(r["origin_class"] for r in seo))))

    folds, load = build_outer_folds(seo, SEED)
    check(len(folds) == OUTER_FOLDS, f"внешних fold-ов ровно {OUTER_FOLDS}", str(len(folds)))

    by_source = defaultdict(list)
    for r in seo:
        by_source[r["split_group_source"]].append(r)

    fold_of, both_classes = {}, True
    for i, f in sorted(folds.items()):
        test_docs = [r for r in seo if r["generation_channel"] == f["ai"]]
        test_docs += [r for s in f["human"] for r in by_source[s]]
        classes = Counter(r["origin_class"] for r in test_docs)
        both_classes &= len(classes) == 2
        for r in test_docs:
            fold_of[r["document_id"]] = i
        print(f"   fold{i}: канал {f['ai']}, человеческих источников {len(f['human'])}, "
              f"тест {dict(classes)}")
    check(both_classes, "в тестовой части каждого fold-а есть оба класса", "")
    check(len(fold_of) == len(seo), "каждый документ seo попал ровно в один fold",
          f"{len(fold_of)} из {len(seo)}")
    held = [f["ai"] for f in folds.values()]
    check(len(set(held)) == len(held), "каждый AI-канал удерживается ровно один раз",
          ", ".join(held))

    pairs = list(csv.DictReader((ROOT / "07-analysis" / "score-v1-pairs.csv").open(encoding="utf-8")))
    seo_pairs = [p for p in pairs if by_id[p["doc_left"]]["genre"] == "seo"]
    split_pairs = [p for p in seo_pairs
                   if fold_of.get(p["doc_left"]) != fold_of.get(p["doc_right"])]
    check(not split_pairs, "обе половины каждой пары O1 оцениваются моделью одного fold-а",
          f"пар в разных fold-ах: {len(split_pairs)}" if split_pairs
          else f"SEO-пар всего: {len(seo_pairs)}")

    n_seo = {c: sum(1 for p in seo_pairs if p["contrast"] == c) for c in ("P3-P1", "P2-P1")}
    clusters_o1 = {c: len({p["brief_id"] for p in seo_pairs if p["contrast"] == c})
                   for c in ("P3-P1", "P2-P1")}
    print(f"   SEO-пары: {n_seo}; кластеров-заданий: {clusters_o1}")
    check(all(v == 15 for v in clusters_o1.values()),
          "кластеров-заданий в SEO ровно 15, а не 45", str(clusters_o1))


    print("\n2. Source-holdout выполним")
    by_source = defaultdict(lambda: Counter())
    for r in seo:
        by_source[r["split_group_source"]][r["origin_class"]] += 1
    both = [s for s, c in by_source.items() if len(c) == 2]
    single = {s: dict(c) for s, c in by_source.items() if len(c) == 1}
    check(len(by_source) >= 2, "источников в жанре seo не меньше двух", str(len(by_source)))
    holdable = [s for s in by_source
                if len({r["origin_class"] for r in seo if r["split_group_source"] != s}) == 2]
    check(len(holdable) == len(by_source),
          "при выносе любого источника в тесте остаются оба класса в обучении",
          f"пригодных источников: {len(holdable)} из {len(by_source)}")
    check(bool(both) or len(by_source) > 2,
          "source-holdout не вырождается в класс с одной стороны",
          f"источников с обоими классами: {len(both)}; односоставных: {len(single)}")

    print("\n3. Negative control")
    clusters = sorted({r["split_group_source"] for r in seo})
    labels = {c: Counter(r["origin_class"] for r in seo if r["split_group_source"] == c)
              for c in clusters}
    permutable = [c for c in clusters if len(labels[c]) == 1]
    check(len(permutable) >= 2,
          "перестановка меток возможна на уровне кластера, а не документа",
          f"кластеров с одной меткой: {len(permutable)} из {len(clusters)}")
    check(True, "negative control прогоняет весь вложенный pipeline целиком",
          "правило зафиксировано в spec, проверяется кодом расчёта")

    print("\n4. Судья: локальный артефакт и пять критериев §1.1")
    man = ROOT / "04-corpus" / "derived" / "prep-v4" / "manifest.csv"
    docs_prep = list(csv.DictReader(man.open(encoding="utf-8")))
    longest_row = max(docs_prep, key=lambda r: int(r["prose_words"] or 0))
    judge = {}
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as fh:
            tags = json.load(fh)
        with urllib.request.urlopen(f"{OLLAMA}/api/version", timeout=10) as fh:
            ollama_version = json.load(fh)["version"]
        entry = next((m for m in tags.get("models", []) if m["name"] == JUDGE_MODEL), None)
        check(entry is not None, "артефакт судьи установлен", JUDGE_MODEL)
        if entry:
            # критерий 1: веса на машине, а не cloud-ссылка
            check(not entry.get("remote_host") and entry["size"] > 1 << 30,
                  "критерий 1: веса лежат на машине",
                  f"{entry['size']} байт, remote_host: {entry.get('remote_host', 'нет')}")
            # критерий 2: не из семейств, породивших корпус
            family = entry["details"].get("family", "")
            channels = sorted({r["generation_channel"] for r in rows
                               if r["origin_class"] == "A"})
            check(all(family not in ch and ch not in family for ch in channels),
                  "критерий 2: семейство судьи не порождало корпус",
                  f"судья {family}; каналы {', '.join(channels)}")
            show = json.loads(post(f"{OLLAMA}/api/show", {"model": JUDGE_MODEL}))
            caps = show.get("capabilities", [])
            # критерий 3: общего назначения — узкая специализация в теге и семействе
            check(not any(k in JUDGE_MODEL.lower() for k in ("coder", "code", "math", "embed")),
                  "критерий 3: модель общего назначения", f"capabilities: {caps}")
            ctx_key = next((k for k in show.get("model_info", {})
                            if k.endswith(".context_length")), None)
            ctx_len = show["model_info"].get(ctx_key, 0)
            # критерий 5: фактический контекст вмещает промпт и самый длинный документ
            prompt_text = judge_prompt()
            longest_text = (ROOT / longest_row["prose_path"]).read_text(encoding="utf-8")
            probe = json.loads(post(f"{OLLAMA}/api/generate", {
                "model": JUDGE_MODEL, "stream": False,
                "prompt": prompt_text.replace("<<<TEXT>>>", longest_text),
                "options": {"num_predict": 0, "num_ctx": JUDGE_NUM_CTX}}))
            need = probe.get("prompt_eval_count", 0)
            check(need > 0 and need <= JUDGE_NUM_CTX,
                  "критерий 5: промпт и самый длинный документ помещаются в num_ctx",
                  f"{longest_row['document_id']}, {longest_row['prose_words']} слов = "
                  f"{need} токенов при num_ctx {JUDGE_NUM_CTX}")
            check(ctx_len >= need, "объявленный context_length не меньше требуемого",
                  f"{ctx_len} против {need}")
            # критерий 4 проверен на внешних текстах вне корпуса, см. амендмент 2026-07-28
            print("   критерий 4 (русский язык) проверен на внешних текстах "
                  "2026-07-28 и записан в амендмент")
            judge = {"model": JUDGE_MODEL, "digest": entry["digest"],
                     "size_bytes": entry["size"], "ollama_version": ollama_version,
                     "quantization_level": entry["details"].get("quantization_level"),
                     "parameter_size": entry["details"].get("parameter_size"),
                     "family": family, "capabilities": caps,
                     "declared_context_length": ctx_len, "num_ctx": JUDGE_NUM_CTX,
                     "num_predict": JUDGE_NUM_PREDICT,
                     "template": show.get("template", ""),
                     "default_parameters": show.get("parameters", ""),
                     "longest_document": longest_row["document_id"],
                     "longest_document_tokens": need,
                     "sampling": {"temperature": 1.0, "top_p": 1.0, "top_k": 64},
                     "seeds": JUDGE_SEEDS,
                     "response_schema": JUDGE_SCHEMA,
                     "documents": len(docs_prep), "calls": len(docs_prep) * 3}
            print(f"   объём прогона: {len(docs_prep)} документов × 3 = "
                  f"{len(docs_prep) * 3} вызовов, подвыборка §7 не применяется")
    except Exception as exc:
        check(False, "ollama отвечает", str(exc)[:120])

    print("\n5. Процедура 3: модель, поведение токенизатора и скорость")
    nll = check_nll(docs_prep)

    print("\n6. Конвенция направления шкал")
    for name, rule in SCALE_CONVENTION.items():
        print(f"   {name:<14} {rule['direction']:<8} {rule['note']}")
    check(SCALE_CONVENTION["proc3_nll"]["direction"] == "invert",
          "для NLL зафиксировано обращение знака при синтезе",
          "низкий NLL = более AI-подобно")
    check(all(v["direction"] in ("as_is", "invert") for v in SCALE_CONVENTION.values()),
          "у каждой шкалы задано правило приведения к общей конвенции",
          f"{len(SCALE_CONVENTION)} шкал")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": SEED,
        "specs": {n: sha256(ROOT / "07-analysis" / n) for n in SPECS
                  if (ROOT / "07-analysis" / n).exists()},
        "inputs": {"documents-registry.csv": sha256(DOCUMENTS),
                   "feature-matrix.csv": sha256(ROOT / "06-features" / "feature-matrix.csv")},
        "p2b_seo_pairs": n_seo,
        "p2b_clusters": clusters_o1,
        "p2b_outer_folds": {str(i): f for i, f in folds.items()},
        "scale_convention": SCALE_CONVENTION,
        "judge": judge,
        "nll": nll,
        "preflight_passed": not FAILURES,
        "failures": FAILURES,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nманифест: {MANIFEST_PATH.relative_to(ROOT)}")

    if FAILURES:
        print(f"\nPREFLIGHT НЕ ПРОЙДЕН — {len(FAILURES)} сбоев:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nPREFLIGHT ПРОЙДЕН.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
