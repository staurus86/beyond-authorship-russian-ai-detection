#!/usr/bin/env python3
"""Проверка статуса жизненного цикла прежней ревизии.

Вынесено отдельным модулем, потому что проверку выполняют несколько процедур, а
импортировать друг друга они не могут: `stress_run_p1` тянет torch и модели.

Правила — `02-preregistration/amendment-stress-r5-lifecycle-status.md`.

Прежняя ревизия допускает смену, только если рядом с её манифестом лежит
неизменяемая запись `*-lifecycle.json`, ссылающаяся на точный хеш этого
манифеста. Статус `completed` без такой записи означает, что две ревизии
одновременно считают себя действующими, и запуск блокируется.
"""
import hashlib
import json
from pathlib import Path

ACCEPTED = ("superseded", "invalidated")
REQUIRED_FOR_SUPERSEDED = ("superseded_by", "reason", "reusable_scope")


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lifecycle_path(manifest_path):
    """Путь sidecar со статусом жизненного цикла рядом с манифестом."""
    return Path(str(manifest_path).replace("-manifest.json", "-lifecycle.json"))


def check_previous_lifecycle(manifest_path):
    """Пригодна ли прежняя ревизия к смене: (принято: bool, пояснение: str)."""
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return False, "манифест отсутствует"
    sidecar = lifecycle_path(manifest_path)
    if not sidecar.exists():
        return False, f"нет записи жизненного цикла {sidecar.name}"
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"{sidecar.name} не читается: {exc}"

    actual = sha256_file(manifest_path)
    if record.get("manifest_sha256") != actual:
        return False, (f"{sidecar.name} записан для другого манифеста: хеш "
                       f"{record.get('manifest_sha256', '')[:12]} против "
                       f"фактического {actual[:12]}")

    status = record.get("lifecycle_status")
    if status not in ACCEPTED:
        return False, (f"lifecycle_status {status!r}: ревизия не объявлена "
                       f"заменённой или негодной")
    if status == "superseded":
        missing = [f for f in REQUIRED_FOR_SUPERSEDED if not record.get(f)]
        if missing:
            return False, f"superseded без обязательных полей: {', '.join(missing)}"
    return True, (f"{status}, execution_status "
                  f"{record.get('execution_status')!r}")
