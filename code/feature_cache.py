#!/usr/bin/env python3
"""Ключ кеша разборов: годность записи определяется входом, а не версией папки.

    python 09-tools/feature_cache.py --bootstrap        # проставить ключи
    python 09-tools/feature_cache.py --report           # что в индексе сейчас

Кэши `06-features/cache/*` до сих пор адресовались одним `document_id`. При
коррекции prep-v5 это молчаливая ошибка: у 68 документов текст изменился, а имя
файла осталось прежним, и разбор четырёхдневной давности выдался бы за новый.

**Ключ — `document_id × input_sha256 × cache_version × model_revision`.**
`prep_version` записывается как происхождение, но в проверку годности не входит.
Причина: байт-идентичный вход даёт байт-идентичный разбор, и требование
совпадения версии препроцессинга заставило бы пересчитать 1814 неизменённых
документов без единого изменения результата. Так кэш и работал раньше по факту:
из 1974 записей 1181 построена под `prep-v1`, и у всех 1916 документов
`prose_sha256` версии разбора совпал с `prose_sha256` в `prep-v4` — проверено
2026-07-29, расхождений ноль.

Индекс лежит рядом с записями — `keys.json` в самой папке кеша.
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

DERIVED = ROOT / "04-corpus" / "derived"
CACHE_ROOT = ROOT / "06-features" / "cache"
INDEX_NAME = "keys.json"

# Кэш → профиль препроцессинга, из которого приходит вход.
CACHE_PROFILE = {"stanza-v1": "prose", "ner-v1": "full", "embed-v1": "prose"}
CACHE_SUFFIX = {"stanza-v1": ".json.gz", "ner-v1": ".json.gz", "embed-v1": ".npz"}


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(prep_version):
    path = DERIVED / prep_version / "manifest.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r["document_id"]: r for r in csv.DictReader(fh)}


def input_path(prep_version, profile, doc_id):
    return DERIVED / prep_version / profile / f"{doc_id}.txt"


CORRECTED_ROOT = {"prep-v5": DERIVED / "raw-v5"}


def source_file(registry_row, prep_version):
    """Исходный файл документа для версии препроцессинга.

    Правило то же, что в `prep.py`: у prep-v5 берётся скорректированный текст из
    `derived/raw-v5/<источник>/<id>.txt`, если он есть. Слой артефактов читает
    исходник, а не профиль, поэтому без этого правила он считал бы F04–F08 по
    тексту с дефектом извлечения — тому самому, ради которого сделан prep-v5.
    """
    path = ROOT / registry_row["file_path"]
    root = CORRECTED_ROOT.get(prep_version)
    if root:
        source = (registry_row["source_platform"]
                  or registry_row["generation_channel"] or "unknown")
        fixed = root / source / f"{registry_row['document_id']}.txt"
        if fixed.exists():
            return fixed
    return path


def matrix_path(prep_version, default):
    """Куда пишет слой признаков.

    prep-v4 — прежняя `feature-matrix.csv` со всеми колонками, включая жанровый
    перцентиль. Остальные версии — `features-normalized-<версия>.csv` без
    перцентиля: он корпус-зависим и считается отдельным этапом.
    """
    if prep_version == "prep-v4":
        return Path(default)
    return ROOT / "06-features" / f"features-normalized-{prep_version}.csv"


def percentiles_inline(prep_version):
    """Считает ли слой перцентиль сам. С prep-v5 — нет, этап вынесен."""
    return prep_version == "prep-v4"


def load_index(cache_dir):
    path = Path(cache_dir) / INDEX_NAME
    if not path.exists():
        return {"cache": Path(cache_dir).name, "entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(cache_dir, index):
    index["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (Path(cache_dir) / INDEX_NAME).write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


def sha_for(prep_version, profile, doc_id):
    """sha256 входного файла нужной версии препроцессинга; None, если файла нет."""
    path = input_path(prep_version, profile, doc_id)
    return sha256_file(path) if path.exists() else None


def lookup(cache_dir, index, doc_id, input_sha, revision):
    """Путь к годной записи или None.

    Записи разных входов одного документа лежат рядом и адресуются хешем: после
    коррекции prep-v5 разбор prep-v4 остаётся доступен, и прогон под старой
    версией берёт именно его, а не свежий.
    """
    entry = index.get("entries", {}).get(doc_id, {}).get(input_sha)
    if not entry or entry.get("model_revision") != revision:
        return None
    path = Path(cache_dir) / entry["file"]
    return path if path.exists() else None


def new_name(doc_id, input_sha, suffix):
    """Имя новой записи содержит хеш входа: прежняя запись не затирается.

    Разбор prep-v4 остаётся на диске и после пересчёта под prep-v5 — иначе
    воспроизвести замороженную серию v1 можно было бы только повторным разбором.
    """
    return f"{doc_id}__{input_sha[:12]}{suffix}"


def stamp(index, doc_id, input_sha, prep_version, revision, filename):
    """Записать ключ. Версии препроцессинга, давшие тот же вход, копятся списком."""
    entries = index.setdefault("entries", {}).setdefault(doc_id, {})
    known = entries.get(input_sha, {})
    versions = sorted(set(known.get("prep_versions", [])) | {prep_version})
    entries[input_sha] = {
        "file": filename,
        "prep_versions": versions,
        "model_revision": revision,
        "stamped_at": known.get("stamped_at")
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def bootstrap(cache_name, revision, versions=("prep-v4", "prep-v5")):
    """Проставить ключи уже лежащим записям по манифестам версий.

    Записи, названной одним `document_id`, приписывается вход prep-v4: под этой
    версией считалась матрица v1, и у всех 1916 документов `prose_sha256`
    версии разбора совпал с prep-v4 — проверено 2026-07-29. Записи с хешем в
    имени опознаются по нему. Документ, для которого файла нет, ключа не
    получает: считать его негодным безопаснее, чем угадать файл.
    """
    cache_dir = CACHE_ROOT / cache_name
    profile = CACHE_PROFILE[cache_name]
    suffix = CACHE_SUFFIX[cache_name]
    index = load_index(cache_dir)
    index["cache"] = cache_name
    index["profile"] = profile
    index["bootstrapped_from"] = [f"{v}/manifest.csv" for v in versions]
    index["key"] = ("document_id × input_sha256 × cache_version × model_revision; "
                    "prep_version записывается как происхождение и в проверку "
                    "годности не входит")
    legacy_sha = {d: r[f"{profile}_sha256"] for d, r in manifest("prep-v4").items()}
    stamped, unresolved = 0, 0
    for version in versions:
        for doc_id, row in manifest(version).items():
            sha = row[f"{profile}_sha256"]
            named = cache_dir / new_name(doc_id, sha, suffix)
            legacy = cache_dir / f"{doc_id}{suffix}"
            if named.exists():
                path = named
            elif legacy.exists() and legacy_sha.get(doc_id) == sha:
                path = legacy
            else:
                unresolved += 1
                continue
            stamp(index, doc_id, sha, version, revision, path.name)
            stamped += 1
    save_index(cache_dir, index)
    return {"cache": cache_name, "stamped": stamped, "unresolved": unresolved,
            "revision": revision, "profile": profile}


def report():
    out = []
    for name in sorted(CACHE_PROFILE):
        cache_dir = CACHE_ROOT / name
        if not cache_dir.exists():
            continue
        index = load_index(cache_dir)
        files = [p for p in cache_dir.iterdir() if p.name != INDEX_NAME]
        entries = index.get("entries", {})
        keys = sum(len(v) for v in entries.values())
        revisions = {e["model_revision"] for v in entries.values() for e in v.values()}
        out.append({"cache": name, "files": len(files), "documents": len(entries),
                    "keys": keys, "revision": next(iter(revisions), None)})
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bootstrap", action="store_true",
                        help="проставить ключи существующим записям")
    parser.add_argument("--versions", nargs="+", default=["prep-v4", "prep-v5"],
                        help="манифесты, по которым берутся sha входов")
    # Ревизии проверены запросом к установленным пакетам 2026-07-29, а не взяты
    # из спецификации: запись о среде консервирует ошибку так же надёжно, как факт.
    parser.add_argument("--stanza-revision", default="stanza 1.14.0/ru-syntagrus")
    parser.add_argument("--ner-revision",
                        default="natasha 1.6.0/slovnet 0.6.0/navec 0.10.0")
    parser.add_argument("--embed-revision",
                        default="BAAI/bge-m3@5617a9f61b02, sentence-transformers 5.5.1")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    if args.bootstrap:
        revisions = {"stanza-v1": args.stanza_revision,
                     "ner-v1": args.ner_revision,
                     "embed-v1": args.embed_revision}
        for name, revision in revisions.items():
            if not (CACHE_ROOT / name).exists():
                continue
            result = bootstrap(name, revision, tuple(args.versions))
            print(f"  {name}: ключей проставлено {result['stamped']}, "
                  f"без файла записи {result['unresolved']}, ревизия {revision}")
    if args.report or not args.bootstrap:
        for row in report():
            print(f"  {row['cache']}: файлов {row['files']}, документов "
                  f"{row['documents']}, ключей {row['keys']}, "
                  f"ревизия {row['revision']}")


if __name__ == "__main__":
    main()
