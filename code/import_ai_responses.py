#!/usr/bin/env python3
"""Импорт ответов моделей в корпус.

Разбирает имена файлов вида <model>_<brief>_<condition>_r<N>.txt, сверяет их
с реестром промптов и журналом прогона, считает объём сам и пишет документы
в реестр корпуса.

Длина машинных текстов не фильтруется: у моделей она сама зависит от режима
задания, поэтому короткий ответ — это результат, а не брак. Документы короче
порога помечаются в поле abstention_reason.

    python 09-tools/import_ai_responses.py --src 04-corpus/raw-ai/deepseek --dry-run
    python 09-tools/import_ai_responses.py --src 04-corpus/raw-ai/deepseek
"""

import argparse
import csv
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY = ROOT / "04-corpus" / "documents-registry.csv"
HASHES = ROOT / "04-corpus" / "hashes.csv"
PROMPTS = ROOT / "03-briefs" / "prompt-registry.csv"
GEN_LOG = ROOT / "04-corpus" / "generation-log.csv"
RAW_AI = ROOT / "04-corpus" / "raw-ai"

MIN_WORDS = 700
NAME = re.compile(r"^(?P<model>[a-z0-9_]+?)_(?P<brief>b\d{3})_(?P<condition>P[123])_r(?P<repeat>\d+)$", re.I)

GENRE_BY_PREFIX = {"b0": "seo", "b1": "seo", "b2": "analytics", "b3": "commercial", "b4": "commercial"}

# Паспорт канала генерации. Введён 2026-07-24 по рецензии: два канала из
# четырёх работают через CLI-оболочку с несъёмным системным промптом, поэтому
# сравниваются не модели, а каналы — модель плюс оболочка плюс её версия плюс
# параметры инференса. «Claude Opus 4.8 показывает паттерн» писать нельзя:
# показывает канал.
CHANNELS = {
    "real_claude": {
        "model_family": "Anthropic", "provider": "anthropic",
        "access_method": "nested_cli", "wrapper_name": "claude-cli",
        "wrapper_version": "unknown", "system_prompt_visibility": "hidden",
        "raw_payload_available": "yes", "sampling_parameters_known": "no",
    },
    "gpt": {
        "model_family": "OpenAI", "provider": "openai",
        "access_method": "codex_cli", "wrapper_name": "codex-cli",
        "wrapper_version": "unknown", "system_prompt_visibility": "hidden",
        "raw_payload_available": "yes", "sampling_parameters_known": "no",
    },
    "deepseek_pro": {
        "model_family": "DeepSeek", "provider": "deepseek",
        "access_method": "https_api", "wrapper_name": "none",
        "wrapper_version": "none", "system_prompt_visibility": "none",
        "raw_payload_available": "no", "sampling_parameters_known": "yes",
    },
    "nemotron": {
        "model_family": "NVIDIA", "provider": "nvidia",
        "access_method": "https_api_gateway", "wrapper_name": "opencode-zen-gateway",
        "wrapper_version": "none", "system_prompt_visibility": "none",
        "raw_payload_available": "no", "sampling_parameters_known": "yes",
    },
}


def channel_fields(model_key, doc_id):
    """Поля канала, версия оболочки — поячеечно из карты, если она есть."""
    spec = dict(CHANNELS.get(model_key, {}))
    if not spec:
        return {}
    versions = RAW_AI / model_key / "_channel-versions.csv"
    if versions.exists():
        with versions.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["document_id"] == doc_id:
                    v = row.get("codex_cli_version", "")
                    spec["wrapper_version"] = v if v and "не сохранён" not in v else "unknown"
                    break
    spec["generation_channel"] = model_key
    return spec


def abstention_for(words, entry):
    """Почему документ помечен как неполноценный.

    `finish_reason=length` означает обрыв по потолку max_tokens: текст
    кончается на середине мысли и лишён вывода. Дискурсивные признаки —
    рамка, концовка, связки — входят в первичный набор, поэтому такой
    документ отличается от целого систематически, а не случайно.
    """
    reasons = []
    if words < MIN_WORDS:
        reasons.append("короче 700 слов")
    if (entry or {}).get("finish_reason") == "length":
        reasons.append("обрыв по max_tokens, текст без концовки")
    return "; ".join(reasons)


def genre_for(brief_id):
    number = int(brief_id[1:])
    if number <= 15:
        return "seo"
    if number <= 30:
        return "analytics"
    return "commercial"


def load_prompts():
    if not PROMPTS.exists():
        return {}
    with PROMPTS.open(encoding="utf-8-sig", newline="") as fh:
        return {row["prompt_id"]: row for row in csv.DictReader(fh)}


def load_gen_log():
    if not GEN_LOG.exists():
        return {}
    with GEN_LOG.open(encoding="utf-8-sig", newline="") as fh:
        return {row["attempt_id"]: row for row in csv.DictReader(fh) if row.get("attempt_id")}


def load_channel_log(src):
    """Журнал канала рядом с ответами.

    Канал real_claude пишет не в общий generation-log.csv, а в собственный
    _run-log.csv, и attempt_id там свой (rc-0001), не совпадающий с именем
    файла. Ключом служит resulting_document_id: без этой сверки в реестр
    ушли бы пустые model_exact_id и generation_date.
    """
    path = src / "_run-log.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {
            row["resulting_document_id"]: row
            for row in csv.DictReader(fh)
            if row.get("resulting_document_id")
        }


def read_registry_fields():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh))


def existing_ids():
    with REGISTRY.open(encoding="utf-8-sig", newline="") as fh:
        return {row.get("document_id") for row in csv.DictReader(fh)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="папка с ответами одной модели")
    parser.add_argument("--model-key", help="ключ модели, по умолчанию берётся из имени папки")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    src = ROOT / args.src if not Path(args.src).is_absolute() else Path(args.src)
    if not src.exists():
        raise SystemExit(f"нет папки {src}")

    model_key = args.model_key or src.name
    prompts = load_prompts()
    gen_log = load_gen_log()
    channel_log = load_channel_log(src)
    if channel_log:
        print(f"Журнал канала: {len(channel_log)} записей в {src.name}/_run-log.csv")
    taken = existing_ids()
    fields = read_registry_fields()
    stamp = datetime.now().strftime("%Y-%m-%d")

    files = sorted(src.glob("*.txt"))
    print(f"Файлов: {len(files)}, модель: {model_key}\n")

    registry_rows, hash_rows = [], []
    problems = {"имя не по схеме": [], "нет в реестре промптов": [], "нет в журнале": [],
                "объём в журнале расходится": [], "короче порога": [], "уже в реестре": []}

    for path in files:
        stem = path.stem
        match = NAME.match(stem)
        if not match:
            problems["имя не по схеме"].append(stem)
            continue

        brief_id = match.group("brief").lower()
        condition = match.group("condition").upper()
        repeat = int(match.group("repeat"))
        prompt_id = f"{brief_id}_{condition}"

        if prompt_id not in prompts:
            problems["нет в реестре промптов"].append(stem)
            continue

        text = path.read_text(encoding="utf-8", errors="replace").strip()
        words = len(text.split())

        entry = gen_log.get(stem) or channel_log.get(stem)
        if entry is None:
            problems["нет в журнале"].append(stem)
        else:
            try:
                logged = int(entry.get("output_word_count") or 0)
                if logged and abs(logged - words) > 20:
                    problems["объём в журнале расходится"].append(f"{stem}: журнал {logged}, файл {words}")
            except ValueError:
                pass

        if words < MIN_WORDS:
            problems["короче порога"].append(f"{stem}: {words}")

        doc_id = stem
        if doc_id in taken:
            problems["уже в реестре"].append(stem)
            continue

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row = {field: "" for field in fields}
        row.update(
            {
                "document_id": doc_id,
                "file_path": path.relative_to(ROOT).as_posix(),
                "sha256": digest,
                "language": "ru",
                "genre": genre_for(brief_id),
                "brief_id": brief_id,
                "topic_id": brief_id,
                "origin_class": "A",
                "author_or_model_id": model_key,
                "model_provider": (entry or {}).get("model_provider", model_key),
                "model_exact_id": (entry or {}).get("model_exact_id", ""),
                # то, что сообщил журнал: у первых ячеек gpt это
                # gpt-5-codex-current-chat, тогда как логи оболочки дают gpt-5.5
                "model_id_reported": (entry or {}).get("model_exact_id", ""),
                **channel_fields(model_key, doc_id),
                "generation_date": ((entry or {}).get("timestamp") or "")[:10],
                "source_platform": model_key,
                "prompt_id": prompt_id,
                "prompt_condition": condition,
                "user_prompt_hash": prompts[prompt_id].get("user_prompt_hash", ""),
                "repeat_index": repeat,
                "word_count": words,
                "char_count": len(text),
                "preprocessing_profile": "prose",
                "license_status": "машинная генерация",
                "consent_or_public_basis": "сгенерировано в рамках исследования",
                "leakage_group": f"{brief_id}",
                "revision_family_id": doc_id,
                "dedup_cluster_id": hashlib.sha256(re.sub(r"\s+", " ", text.lower()).encode("utf-8")).hexdigest()[:16],
                "split_group_author": model_key,
                "split_group_source": model_key,
                "split_group_topic": brief_id,
                "abstention_reason": abstention_for(words, entry),
                "status": "collected",
                "notes": f"prompt_condition={condition}; repeat={repeat}",
            }
        )
        registry_rows.append(row)
        hash_rows.append(
            {
                "document_id": doc_id,
                "file_path": row["file_path"],
                "sha256": digest,
                "bytes": path.stat().st_size,
                "recorded_at": stamp,
            }
        )

    for reason, items in problems.items():
        if items:
            print(f"{reason}: {len(items)}")
            for item in items[:5]:
                print(f"    {item}")
            if len(items) > 5:
                print(f"    … ещё {len(items) - 5}")

    print(f"\nК записи: {len(registry_rows)}")

    if args.dry_run:
        print("Сухой прогон: ничего не записано")
        return
    if not registry_rows:
        return

    with REGISTRY.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fields).writerows(registry_rows)
    with HASHES.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(
            fh, fieldnames=["document_id", "file_path", "sha256", "bytes", "recorded_at"]
        ).writerows(hash_rows)

    print(f"Записано в реестр: {len(registry_rows)}")
    print("Дальше: python 09-tools/validate_registry.py")


if __name__ == "__main__":
    main()
