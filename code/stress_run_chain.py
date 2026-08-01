#!/usr/bin/env python3
"""Последовательный прогон стресс-теста с механической проверкой шлюзов.

    python 09-tools/stress_run_chain.py            # с текущего незавершённого этапа
    python 09-tools/stress_run_chain.py --dry-run   # показать план, ничего не запускать

Порядок этапов зафиксирован до прогона. После каждого шага проверяются
код возврата и `status` в манифесте процедуры. Правила остановки взяты из
`stress-run-manifest.json`:

- `blocked` — зависимый этап не запускается;
- `incomplete` — зависимый этап не запускается;
- ненулевой код возврата — цепочка останавливается.

Сработавший шлюз считается результатом QA. Скрипт не пытается его обойти,
не меняет панель, преобразования, пороги и правило агрегации.

Каждый шаг дописывает результат в `stress-run-manifest.json`, stdout каждого
этапа сохраняется в `07-analysis/run-logs/`.
"""

import argparse
import csv
import os
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "09-tools"
sys.path.insert(0, str(TOOLS))
import stress_transforms as st  # noqa: E402
import stress_paths as sp  # noqa: E402

ANALYSIS = ROOT / "07-analysis"
LOGS = ANALYSIS / "run-logs"
PARENT = ANALYSIS / "stress-run-manifest.json"
# У каждой ревизии своя таблица хешей. Отметки прежних ревизий остаются в своих
# файлах и не переписываются — они относятся к инвалидированным прогонам.
HASHFILE = ANALYSIS / f"stress-{sp.REVISION}-code.sha256.md"

MSK = timezone(timedelta(hours=3))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# Этап: (номер, аргументы, файл-манифест для проверки статуса, файл-признак
# завершённости для пропуска уже сделанного)
def steps():
    """Шаги цепочки. Пути считаются лениво: обращение к каталогу попытки
    создаёт его, и делать это при импорте нельзя — dry-run плодил бы пустые
    каталоги."""
    return [
    (1, ["stress_run_p1.py", "--stage", "texts"], None, sp.MANIFEST),
    # У этапов parse, embed и ner признака завершённости нет. Число записей в
    # кэше его не даёт: кэш переживает смену ревизии, и после правки t14 в нём
    # лежат годные записи, часть которых относится к прежним текстам. Проверка
    # по числу записей приняла бы это за готовый разбор. Все три этапа
    # идемпотентны: запись ищется по хешу входа, досчитывается недостающее.
    (2, ["stress_run_p1.py", "--stage", "parse"], None, None),
    (3, ["stress_run_p1.py", "--stage", "embed"], None, None),
    (4, ["stress_run_p1.py", "--stage", "ner"], None, None),
    (5, ["stress_run_p1.py", "--stage", "score"],
     sp.analysis("p1", "manifest.json"), None),
    (6, ["stress_run_p3.py"], sp.analysis("p3", "manifest.json"), None),
    # Шаг 7 — деривация, а не прогон судьи. Судья вызывался в r4; тексты десяти
    # преобразований не менялись, поэтому r5 получается детерминированным
    # преобразованием журнала r4 с четырьмя шлюзами. Решение PI 2026-07-31,
    # амендмент r5. Сам stress_run_p4.py в этой ревизии стартовать откажется.
    # Деривация P4 писала в 07-analysis до введения попыток и имеет замкнутую
    # цепочку происхождения: её файлы остаются на месте, attempt=False.
    (7, ["derive_p4_r5.py"], sp.analysis("p4", "manifest.json", attempt=False), None),
    (8, ["stress_run_p2.py", "--stage", "features"], None,
     sp.analysis("p2a", "features.csv")),
    (9, ["stress_run_p2.py", "--stage", "score"],
     sp.analysis("p2a", "manifest.json"), None),
]

# Метка ревизии входит в имя лога: без неё прогон затёр бы логи прежней
# ревизии, а они — протокол инвалидированного прогона и переписываться не
# должны.
REVISION = sp.REVISION

# Ячеек в панели: 60 документов × число выполнимых преобразований. Значение
# берётся из stress_transforms, а не пишется числом: после перевода t14 в
# not executable ручная константа разошлась бы с составом прогона.
PANEL_DOCUMENTS = 60
EXPECTED_CELLS = PANEL_DOCUMENTS * len(st.TRANSFORMS)
# Признак завершённости для CSV — число строк, а не существование файла:
# частичный прогон оставляет файл на месте, и проверка «файл есть» приняла бы
# его за готовый результат.
EXPECTED_CACHE_ENTRIES = EXPECTED_CELLS


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamps():
    now = datetime.now(timezone.utc)
    return (now.isoformat(timespec="seconds"),
            now.astimezone(MSK).isoformat(timespec="seconds"))


def verify_hashes():
    """Сверка зафиксированной таблицы хешей. Расхождение запрещает прогон."""
    import re
    text = HASHFILE.read_text(encoding="utf-8")
    rows = re.findall(r"\|\s*.([^`]+).\s*\|[^|]+\|\s*.([0-9a-f]{64}).\s*\|", text)
    bad = []
    for path, recorded in rows:
        target = ROOT / path
        if not target.exists() or sha256_file(target) != recorded:
            bad.append(path)
    return len(rows), bad


def cache_entries(index_path):
    if not Path(index_path).exists():
        return 0
    try:
        data = json.loads(Path(index_path).read_text(encoding="utf-8"))
        return len(data.get("entries", {}))
    except (json.JSONDecodeError, OSError):
        return 0


def csv_rows(path):
    if not Path(path).exists():
        return 0
    try:
        with Path(path).open(encoding="utf-8", newline="") as fh:
            return sum(1 for _ in csv.DictReader(fh))
    except OSError:
        return 0


def already_done(step, sentinel, manifest_path):
    """Этап пропускается, только если он завершён полностью и со статусом."""
    if sentinel is not None:
        if "keys.json" in str(sentinel):
            return cache_entries(sentinel) >= EXPECTED_CACHE_ENTRIES
        if str(sentinel).endswith(".csv"):
            return csv_rows(sentinel) >= EXPECTED_CELLS
        return Path(sentinel).exists()
    if manifest_path is not None and Path(manifest_path).exists():
        try:
            status = json.loads(
                Path(manifest_path).read_text(encoding="utf-8")).get("status")
            return status == "completed"
        except (json.JSONDecodeError, OSError):
            return False
    return False


def read_status(manifest_path):
    if manifest_path is None or not Path(manifest_path).exists():
        return None, None
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return "unreadable", str(exc)
    return data.get("status"), data.get("gate_detail") or data.get("reason")


def record(entry):
    """Дописывает результат шага в родительский манифест."""
    data = json.loads(PARENT.read_text(encoding="utf-8"))
    data.setdefault("executed_steps", []).append(entry)
    utc, msk = stamps()
    data["last_updated_utc"] = utc
    data["last_updated_moscow"] = msk
    for item in data.get("run_order", []):
        if item["step"] == entry["step"]:
            item["status"] = entry["outcome"]
    PARENT.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def run_step(number, args, manifest_path, dry_run=False):
    label = " ".join(args)
    started_utc, started_msk = stamps()
    print(f"\n=== шаг {number}: {label}")
    print(f"    старт UTC {started_utc} / MSK {started_msk}", flush=True)
    if dry_run:
        return "dry-run", None

    log = sp.log(number, args[0].replace(".py", ""))
    # Идентификатор попытки уходит дочернему процессу: все шаги одной попытки
    # обязаны писать в один каталог (амендмент r5 о происхождении попытки).
    env = dict(os.environ, **{sp.ATTEMPT_ENV: sp.attempt_id()})
    proc = subprocess.run([sys.executable, str(TOOLS / args[0])] + args[1:],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ROOT),
                          env=env)
    finished_utc, finished_msk = stamps()
    log.write_text(
        f"# {label}\n# старт UTC {started_utc} / MSK {started_msk}\n"
        f"# финиш UTC {finished_utc}\n# код возврата {proc.returncode}\n\n"
        + proc.stdout
        + (("\n--- stderr ---\n" + proc.stderr) if proc.stderr.strip() else ""),
        encoding="utf-8")

    tail = [l for l in proc.stdout.strip().split("\n") if l.strip()][-6:]
    for line in tail:
        print(f"    {line}")
    status, detail = read_status(manifest_path)
    print(f"    код возврата {proc.returncode}, статус манифеста {status!r}")

    entry = {
        "step": number, "command": label,
        "started_utc": started_utc, "started_moscow": started_msk,
        "finished_utc": finished_utc, "finished_moscow": finished_msk,
        "duration_seconds": round(
            (datetime.fromisoformat(finished_utc)
             - datetime.fromisoformat(started_utc)).total_seconds(), 1),
        "return_code": proc.returncode,
        "manifest_status": status, "gate_detail": detail,
        "attempt_id": sp.attempt_id(),
        "log": str(log.relative_to(ROOT)).replace("\\", "/"),
        "log_sha256": sha256_file(log),
    }

    # Шлюз: ненулевой код, blocked или incomplete останавливают цепочку
    if proc.returncode != 0:
        entry["outcome"] = "failed"
        entry["stop_reason"] = f"код возврата {proc.returncode}"
    elif status in ("blocked", "incomplete"):
        entry["outcome"] = status
        entry["stop_reason"] = f"статус манифеста {status}"
    else:
        entry["outcome"] = "ok"
    record(entry)
    return entry["outcome"], entry.get("stop_reason")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-step", type=int, default=1)
    parser.add_argument("--to-step", type=int, default=99,
                        help="остановиться после этого шага")
    parser.add_argument("--attempt", default="",
                        help="продолжить существующую попытку по её attempt_id")
    args = parser.parse_args()

    utc, msk = stamps()
    print(f"Цепочка стресс-теста, старт UTC {utc} / MSK {msk}")

    n_hashes, bad = verify_hashes()
    print(f"хеши: сверено {n_hashes}, расхождений {len(bad)}")
    if bad:
        print("ЗАПУСК ЗАПРЕЩЁН: расхождение хешей — " + ", ".join(bad))
        return 2

    # Попытка заводится один раз и не перезаписывается: её каталог хранит выходы,
    # логи и манифесты всех шагов (амендмент r5 о происхождении попытки).
    # Продолжение прерванной серии идёт под её же идентификатором: разрывать
    # попытку на несколько каталогов нельзя, иначе происхождение снова размоется.
    if args.attempt:
        os.environ[sp.ATTEMPT_ENV] = args.attempt
        path = sp.ATTEMPTS / args.attempt
        if not path.exists():
            print(f"ЗАПУСК ЗАПРЕЩЁН: попытки {args.attempt} нет")
            return 2
        print(f"попытка: {args.attempt} (продолжение)")
    elif args.dry_run:
        print(f"попытка: {sp.attempt_id()} (dry-run, каталог не создаётся)")
    else:
        attempt_path = sp.start_attempt()
        print(f"попытка: {sp.attempt_id()}")
        print(f"каталог: {attempt_path.relative_to(ROOT)}")

    for number, step_args, manifest_path, sentinel in steps():
        if number < args.from_step:
            print(f"шаг {number} пропущен по --from-step")
            continue
        if number > args.to_step:
            print(f"остановка после шага {args.to_step} по --to-step")
            break
        if already_done(number, sentinel, manifest_path):
            print(f"шаг {number} ({' '.join(step_args)}): уже выполнен, пропуск")
            continue
        outcome, stop = run_step(number, step_args, manifest_path, args.dry_run)
        if outcome in ("failed", "blocked", "incomplete"):
            print(f"\nЦЕПОЧКА ОСТАНОВЛЕНА на шаге {number}: {stop}")
            print("Это результат QA. Зависимые этапы не запускаются, "
                  "проверка не обходится.")
            return 1

    print("\nвсе шаги пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
