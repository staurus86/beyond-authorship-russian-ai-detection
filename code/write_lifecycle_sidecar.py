#!/usr/bin/env python3
"""Запись sidecar-файла со статусом жизненного цикла прогона.

    python 09-tools/write_lifecycle_sidecar.py --manifest stress-p4-r4-manifest.json \
        --lifecycle superseded --superseded-by stress-p4-r5-manifest.json \
        --reason "..." --scope "..."

Сам манифест прогона не редактируется: его хеш уже входит в чужие манифесты, и
правка задним числом сломала бы их воспроизводимость. Статус жизненного цикла
живёт рядом, в отдельном файле, и ссылается на манифест по хешу.

Основание — `02-preregistration/amendment-stress-r5-lifecycle-status.md`.

Sidecar неизменяем: повторная запись поверх существующего файла запрещена, для
исправления нужна новая запись с новым именем и объяснением.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "07-analysis"
MSK = timezone(timedelta(hours=3))

LIFECYCLE = ("current", "superseded", "invalidated")

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True,
                        help="имя манифеста в 07-analysis")
    parser.add_argument("--lifecycle", required=True, choices=LIFECYCLE)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--scope", default="",
                        help="область пригодности, обязательна для superseded")
    parser.add_argument("--superseded-by", default="",
                        help="имя манифеста-преемника в 07-analysis")
    parser.add_argument("--basis",
                        default="02-preregistration/"
                                "amendment-stress-r5-lifecycle-status.md")
    args = parser.parse_args()

    manifest = ANALYSIS / args.manifest
    if not manifest.exists():
        print(f"ОТКАЗ: нет манифеста {args.manifest}")
        return 2

    out = ANALYSIS / args.manifest.replace("-manifest.json", "-lifecycle.json")
    if out.exists():
        print(f"ОТКАЗ: {out.name} уже существует и неизменяем")
        return 2

    if args.lifecycle == "superseded" and not (args.scope and args.superseded_by):
        print("ОТКАЗ: для superseded обязательны --scope и --superseded-by")
        return 2

    try:
        execution = json.loads(manifest.read_text(encoding="utf-8")).get("status")
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ОТКАЗ: манифест не читается — {exc}")
        return 2

    now = datetime.now(timezone.utc)
    record = {
        "manifest": args.manifest,
        "manifest_sha256": sha256_file(manifest),
        "execution_status": execution,
        "lifecycle_status": args.lifecycle,
        "reason": args.reason,
        "reusable_scope": args.scope,
        "basis": args.basis,
        "basis_sha256": sha256_file(ROOT / args.basis),
        "recorded_at_utc": now.isoformat(timespec="seconds"),
        "recorded_at_moscow": now.astimezone(MSK).isoformat(timespec="seconds"),
    }
    if args.superseded_by:
        successor = ANALYSIS / args.superseded_by
        record["superseded_by"] = args.superseded_by
        record["superseded_by_sha256"] = (sha256_file(successor)
                                          if successor.exists() else "")

    out.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"записано: {out.name}")
    for key in ("execution_status", "lifecycle_status", "reusable_scope",
                "superseded_by"):
        if record.get(key):
            print(f"  {key}: {record[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
