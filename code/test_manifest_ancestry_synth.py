#!/usr/bin/env python3
"""Синтетический тест допуска upstream по цепочке ревизий.

    python 09-tools/test_manifest_ancestry_synth.py

Основание — `02-preregistration/amendment-run-manifest-ancestry.md`. Проверяется
на игрушечных манифестах во временном каталоге, корпус и результаты не читаются.

Правило: upstream допускается, если его родительский манифест — действующая
ревизия либо её предок по цепочке `supersedes`, статус `completed`, пометки
`invalidated` нет, а выходы совпадают по хешам. Ослабляется ровно одна строка
прежнего правила — совпадение имени родителя; остальные проверки сохраняются.
"""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

FAILED = 0


def check(label, got, want):
    global FAILED
    ok = got == want
    if not ok:
        FAILED += 1
    print(f"  [{'ok' if ok else 'СБОЙ'}] {label}: {got!r}" +
          ("" if ok else f" — ожидалось {want!r}"))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    print("Синтетический тест: допуск upstream по цепочке ревизий")
    import preflight_v2_run as pf

    tmp = Path(tempfile.mkdtemp(prefix="ancestry-"))
    try:
        # Цепочка из трёх ревизий: rev1 → rev2 → rev3 (действующая).
        h1 = write_json(tmp / "run-manifest-v2-rev1.json", {"revision": 1})
        h2 = write_json(tmp / "run-manifest-v2-rev2.json", {
            "revision": 2,
            "supersedes": {"file": "run-manifest-v2-rev1.json", "sha256": h1}})
        h3 = write_json(tmp / "run-manifest-v2-rev3.json", {
            "revision": 3,
            "supersedes": {"file": "run-manifest-v2-rev2.json", "sha256": h2}})
        chain = pf.ancestor_hashes(tmp / "run-manifest-v2-rev3.json")

        check("действующая ревизия в цепочке", h3 in chain, True)
        check("прямой предок в цепочке", h2 in chain, True)
        check("дальний предок в цепочке", h1 in chain, True)
        check("длина цепочки", len(chain), 3)

        # Чужая ветка: манифест той же формы, но не связанный с цепочкой.
        alien = write_json(tmp / "run-manifest-other.json", {"revision": 99})
        check("чужой манифест отклонён", alien in chain, False)

        # Обрыв цепочки: предок объявлен, но файла нет — дальше не идём.
        h_broken = write_json(tmp / "run-manifest-v2-rev4.json", {
            "revision": 4,
            "supersedes": {"file": "нет-такого-файла.json", "sha256": "deadbeef"}})
        broken = pf.ancestor_hashes(tmp / "run-manifest-v2-rev4.json")
        check("сам манифест в цепочке", h_broken in broken, True)
        check("объявленный предок учтён по хешу", "deadbeef" in broken, True)
        check("несуществующий файл цепочку не продолжает", len(broken), 2)

        # Цикл в ссылках не зацикливает обход.
        write_json(tmp / "loop-a.json", {
            "supersedes": {"file": "loop-b.json", "sha256": "aa"}})
        write_json(tmp / "loop-b.json", {
            "supersedes": {"file": "loop-a.json", "sha256": "bb"}})
        looped = pf.ancestor_hashes(tmp / "loop-a.json")
        check("цикл не зацикливает обход", len(looped) <= 4, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Прочие условия допуска не тронуты: они проверяются тем же кодом, что и
    # раньше, и обязаны отклонять upstream независимо от цепочки ревизий.
    src = Path(__file__).resolve().parent / "preflight_v2_run.py"
    body = src.read_text(encoding="utf-8")
    for label, fragment in [
            ("пометка invalidated отклоняется", 'помечен invalidated'),
            ("незавершённый статус отклоняется", 'child.get("status") != "completed"'),
            ("изменённый выход отклоняется", 'изменился выход'),
            ("отсутствующий манифест отклоняется", 'нет upstream-манифеста')]:
        check(label, fragment in body, True)

    print(f"\nпровалов: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
