#!/usr/bin/env python3
"""Прогон промптов через API, совместимый с OpenAI (DeepSeek и другие).

Каждый запрос — отдельная чистая сессия: истории нет, системного промпта нет.
Ответ сохраняется как есть, строка пишется в журнал прогона со всеми параметрами
генерации. Уже существующие файлы пропускаются, поэтому прогон можно прерывать
и продолжать.

Ключ берётся из переменной окружения (по умолчанию DEEPSEEK_API_KEY).

Ячейки независимы, поэтому прогон можно вести в несколько потоков: --workers N
держит N запросов в полёте. Запись в журнал и в консоль идёт под замком.
По умолчанию поток один — прежнее поведение сохраняется.

    python 09-tools/run_api.py --list-models
    python 09-tools/run_api.py --model deepseek-chat --limit 3 --dry-run
    python 09-tools/run_api.py --model deepseek-chat --repeat 1
    python 09-tools/run_api.py --model deepseek-chat --repeat 1 --workers 3
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PROMPTS_DIR = ROOT / "03-briefs" / "prompts"
RAW_AI = ROOT / "04-corpus" / "raw-ai"
GEN_LOG = ROOT / "04-corpus" / "generation-log.csv"

# Ключи лежат вне публикуемой части корпуса.
ENV_FILE = ROOT / "00-admin" / "private" / "api-keys.env"


def key_from_env_file(name):
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        variable, value = line.split("=", 1)
        if variable.strip() == name:
            return value.strip().strip('"').strip("'")
    return None

LOG_FIELDS = [
    "attempt_id", "timestamp", "brief_id", "prompt_id", "prompt_condition",
    "model_provider", "model_exact_id", "repeat_index", "http_status", "latency_ms",
    "finish_reason", "refusal", "system_prompt_leak", "output_word_count",
    "retry_reason", "resulting_document_id", "notes",
]

REFUSAL_MARKERS = [
    "не могу выполнить", "не могу помочь", "как языковая модель",
    "i cannot", "i can't help", "не имею возможности",
]

# Порог ТЕХНИЧЕСКОЙ валидности, а не соблюдения объёма. Правлено 2026-07-24
# по рецензии: прежние 480 слов смешивали две разные вещи и выбросили валидный
# ответ на 405 слов с finish_reason=stop. Режим P3 (anti-slop) даёт самые
# короткие тексты, поэтому перегенерация коротких систематически завышала бы
# P3 и смещала первичный исход O1.
#
# Ниже порога — только обрыв канала и пустое тело ответа: строка «API Error»
# укладывается в 11 слов, пустой ответ шлюза даёт 0. Всё, что длиннее,
# считается валидным и размечается переменной length_instruction_compliance.
MIN_VALID_WORDS = 50

# проверка утечки оболочки: раньше поле проставлялось константой "no", то есть
# было утверждением, а не проверкой. Скан 2026-07-24 нашёл документ, где модель
# рассуждала о своём инструментарии до начала статьи
LEAK_PATTERNS = (
    r"<system-reminder>", r"<user_instructions>", r"CLAUDE\.md", r"AGENTS\.md",
    r"You are Claude Code", r"OpenAI Codex v", r"^workdir:", r"^sandbox:",
    r"релевантн\w+ навык\w*[^.\n]{0,40}нет", r"пишу текст напрямую",
    r"инструменты (?:запрещены|недоступны|не нужны)",
)


def leak_flag(text):
    return "yes" if any(re.search(p, text, re.I | re.M) for p in LEAK_PATTERNS) else "no"



def api_request(url, key, payload=None, timeout=300):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # без явного User-Agent шлюз opencode.ai отдаёт Cloudflare 1010
            # на строку Python-urllib и запрос до модели не доходит
            "User-Agent": "ai-human-style-lab/0.2 (research corpus generation)",
        },
        method="POST" if data else "GET",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, json.loads(body), int((time.time() - started) * 1000)


def list_models(base, key):
    status, payload, _ = api_request(base.rstrip("/") + "/models", key)
    names = [item.get("id") for item in payload.get("data", [])]
    print(f"HTTP {status}, моделей: {len(names)}")
    for name in names:
        print("  ", name)


def load_prompts(only_brief=None, conditions=("P1", "P2", "P3"), briefs=None):
    items = []
    allowed = set(briefs) if briefs else None
    for path in sorted(PROMPTS_DIR.glob("b*_P*.txt")):
        match = re.match(r"^(b\d{3})_(P[123])$", path.stem)
        if not match:
            continue
        brief, condition = match.groups()
        if only_brief and brief != only_brief:
            continue
        if allowed and brief not in allowed:
            continue
        if condition not in conditions:
            continue
        items.append({"brief": brief, "condition": condition, "path": path})
    return items


def log_rows_existing():
    if not GEN_LOG.exists():
        return set()
    with GEN_LOG.open(encoding="utf-8-sig", newline="") as fh:
        return {row.get("attempt_id") for row in csv.DictReader(fh) if row.get("attempt_id")}


# журнал и консоль общие на все потоки, поэтому запись под замком:
# без него строки CSV режут друг друга на середине
LOG_LOCK = threading.Lock()
PRINT_LOCK = threading.Lock()


def append_log(row):
    with LOG_LOCK:
        exists = GEN_LOG.exists()
        with GEN_LOG.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)


def say(message):
    with PRINT_LOCK:
        print(message, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--model-key", help="ключ папки и префикс файлов, по умолчанию из --provider")
    parser.add_argument("--repeat", type=int, default=1, help="номер повтора для этой серии")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=8000, help="потолок ответа, чтобы текст не обрезался")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument(
        "--workers", type=int, default=1,
        help="сколько ячеек держать в полёте одновременно; 1 — прежнее последовательное поведение",
    )
    parser.add_argument("--limit", type=int, default=0, help="сколько промптов прогнать, 0 — все")
    parser.add_argument("--brief", help="один бриф, например b001")
    parser.add_argument(
        "--briefs", nargs="+", default=None,
        help="несколько брифов; нужен bridge experiment, где берётся подмножество заданий",
    )
    parser.add_argument("--conditions", default="P1,P2,P3")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    key = os.environ.get(args.key_env) or key_from_env_file(args.key_env)
    if not key:
        raise SystemExit(
            f"нет ключа: ни в переменной {args.key_env}, ни в {ENV_FILE.relative_to(ROOT)}\n"
            f"PowerShell:  $env:{args.key_env} = 'sk-...'\n"
            f"или строкой в файле: {args.key_env}=sk-..."
        )

    if args.list_models:
        list_models(args.base_url, key)
        return

    model_key = args.model_key or args.provider
    out_dir = RAW_AI / model_key
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts = load_prompts(args.brief, tuple(args.conditions.split(",")), args.briefs)
    if args.limit:
        prompts = prompts[: args.limit]

    already = log_rows_existing()
    print(f"Промптов к прогону: {len(prompts)}, модель {args.model}, повтор r{args.repeat}, потоков {args.workers}")
    print(f"Ответы: {out_dir.relative_to(ROOT)}\n", flush=True)

    counters = {"done": 0, "skipped": 0, "failed": 0}
    counter_lock = threading.Lock()

    def bump(name):
        with counter_lock:
            counters[name] += 1

    def run_cell(item):
        attempt_id = f"{model_key}_{item['brief']}_{item['condition']}_r{args.repeat}"
        target = out_dir / f"{attempt_id}.txt"

        if target.exists():
            bump("skipped")
            return

        prompt_text = item["path"].read_text(encoding="utf-8")
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_tokens": args.max_tokens,
            "stream": False,
        }

        if args.dry_run:
            say(f"  [сухой прогон] {attempt_id}, символов в промпте: {len(prompt_text)}")
            bump("done")
            return

        try:
            status, response, latency = api_request(args.base_url.rstrip("/") + "/chat/completions", key, payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            say(f"  ОШИБКА {attempt_id}: HTTP {exc.code} {detail}")
            bump("failed")
            append_log({
                "attempt_id": attempt_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                "brief_id": item["brief"], "prompt_id": f"{item['brief']}_{item['condition']}",
                "prompt_condition": item["condition"], "model_provider": args.provider,
                "model_exact_id": args.model, "repeat_index": args.repeat,
                "http_status": exc.code, "retry_reason": f"HTTP {exc.code}",
                "notes": detail,
            })
            time.sleep(args.delay * 3)
            return
        except Exception as exc:  # noqa: BLE001 — сеть капризна, прогон не должен падать
            say(f"  ОШИБКА {attempt_id}: {type(exc).__name__} {exc}")
            bump("failed")
            time.sleep(args.delay * 3)
            return

        choice = (response.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        usage = response.get("usage") or {}
        words = len(text.split())
        refusal = any(marker in text.lower()[:400] for marker in REFUSAL_MARKERS)

        # критерий валидного ответа, preregistration §5.1: не менее 40% нижней
        # границы объёма. Короткий ответ файлом не становится, иначе прогон
        # сочтёт ячейку сделанной и подборщик её не переделает
        if not refusal and words < MIN_VALID_WORDS:
            say(f"  БРАК {attempt_id}: {words} слов при пороге {MIN_VALID_WORDS}, ячейка оставлена на перегенерацию")
            bump("failed")
            append_log({
                "attempt_id": attempt_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                "brief_id": item["brief"], "prompt_id": f"{item['brief']}_{item['condition']}",
                "prompt_condition": item["condition"], "model_provider": args.provider,
                "model_exact_id": response.get("model") or args.model, "repeat_index": args.repeat,
                "http_status": status, "latency_ms": latency, "finish_reason": finish,
                "output_word_count": words, "retry_reason": "ответ короче порога валидности",
                "notes": f"порог {MIN_VALID_WORDS} слов; tokens_out={usage.get('completion_tokens')}",
            })
            time.sleep(args.delay)
            return

        target.write_text(text.strip() + "\n", encoding="utf-8")

        append_log({
            "attempt_id": attempt_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "brief_id": item["brief"],
            "prompt_id": f"{item['brief']}_{item['condition']}",
            "prompt_condition": item["condition"],
            "model_provider": args.provider,
            "model_exact_id": response.get("model") or args.model,
            "repeat_index": args.repeat,
            "http_status": status,
            "latency_ms": latency,
            "finish_reason": finish,
            "refusal": "yes" if refusal else "no",
            "system_prompt_leak": leak_flag(text),
            "output_word_count": words,
            "retry_reason": "",
            "resulting_document_id": attempt_id,
            "notes": (
                f"temperature={args.temperature}; top_p={args.top_p}; max_tokens={args.max_tokens}; "
                f"tokens_out={usage.get('completion_tokens')}"
            ),
        })

        flag = ""
        if finish == "length":
            flag = "  ⚠ упёрся в max_tokens"
        elif words < 700:
            flag = "  ⚠ короче 700 слов"
        say(f"  {attempt_id}: {words} слов, {latency} мс, finish={finish}{flag}")
        bump("done")
        time.sleep(args.delay)

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(run_cell, prompts))
    else:
        for item in prompts:
            run_cell(item)

    done, skipped, failed = counters["done"], counters["skipped"], counters["failed"]
    print(f"\nСделано: {done}, пропущено (уже есть): {skipped}, ошибок: {failed}")
    if not args.dry_run and done:
        print("Дальше: python 09-tools/import_ai_responses.py --src 04-corpus/raw-ai/" + model_key)


if __name__ == "__main__":
    main()
