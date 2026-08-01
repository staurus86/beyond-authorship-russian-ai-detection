# Хеши кода и входов ревизии r4 стресс-теста

Зафиксировано **до запуска цепочки ревизии r4**. Основание — `02-preregistration/amendment-stress-r4-invariant-t14.md`.

Отметка UTC `2026-07-30T15:23:51+00:00`, по Москве `2026-07-30T18:23:51+03:00`.

Ревизии r2 и r3 помечены `invalidated`; их хеши остаются в `p2-stress-units.sha256.md` и `stress-r3-code.sha256.md`.

| Файл | Что это | sha256 |
|---|---|---|
| `02-preregistration/amendment-stress-r4-invariant-t14.md` | амендмент r4: инвариант входа и исправление t14 | `a969e832ca6dbf20983493d991ad3234093177f32a8b15f7fbb459f941e2f671` |
| `02-preregistration/amendment-stress-r3-pipeline.md` | амендмент r3: препроцессинг, форматные признаки, режим embed | `279c08bea4a5c0050231e82bbe42db653d14b2d56a1f62c6c51d33a06189330e` |
| `07-analysis/stress-panel-v1.csv` | панель 60 документов, не меняется | `39d96e19c6cbf291d4a8940f00a9915e55be5a0dee2ddc979fbcc676b231efce` |
| `09-tools/stress_run_p1.py` | расчётный код P1: панель, кеши, baseline, шлюз | `a7083f42de67daab516c8ac1ca4ee84c22924302502eaef25de8a421f5782ca7` |
| `09-tools/stress_run_p2.py` | расчётный код P2: реконструкция, агрегация, шлюзы | `fd9fdbcf186ba323ba01019de4320fde79526fe554dad2be53a3d08c48ba8e8c` |
| `09-tools/stress_run_p3.py` | расчётный код P3 со шлюзом | `b36c4a12ff5144439c4eee56f00cb80bff0c0475f2bb92c3bbad7c8f91542d10` |
| `09-tools/stress_run_p4.py` | расчётный код P4 со шлюзом | `6d2f232cfe84af5479e08e0a930954af2e825cd5877f6235793225a86031d1c5` |
| `09-tools/stress_transforms.py` | одиннадцать преобразований, t14 исправлено | `8e5398d3d0b2a05e5ddc83700343e4ea925f0ab5be5a7171ae73a6b76e620378` |
| `09-tools/stress_run_chain.py` | цепочка прогона и сверка хешей | `e9901231c2685f83a117b337fb97222094c14e38a2e08f8ee7e3ec6462f0009d` |
| `09-tools/prep.py` | препроцессинг: профили и счётчики форматных признаков | `c3bf16d5935bb941c18777861c4aa7e07e100cc89bb2849c193e7e752a07ea6e` |
| `09-tools/extract_features.py` | экстрактор: читает счётчики для F01, R06, R07 | `ee9e4c89a02ba1677917a00710837449753e54ced2a057b5e94bac0d4d8549a3` |
| `09-tools/extract_semantic.py` | экстрактор M01, M02, M05 | `66084736bd00023d51849f3be2c4775483191d6cc8ac74a6a20c4a582d92e6ac` |
| `09-tools/score_style_index.py` | агрегация: терцили, веса, знаменатели | `4953acece66f82f28e9934e18fa2a89a3823b61024c355f88c8997afb7d19fc1` |
| `09-tools/test_stress_gates_synth.py` | синтетический тест шлюзов, 60 проверок | `947fd65e43023734857b750268c6660c71d13a09c29bdc1fa99b6a69c9f97693` |
| `07-analysis/analysis-closure.md` | закрытие анализа | `22170a49e1e841a007907b059128fe76bf1c88361452e290148357302bc0993f` |
| `07-analysis/splits-v5/p2a-inner-folds-valid.json` | схема B inner CV | `7d49f357f1306842f446529e6503d6d22bbb04581fde10e6ec9bfed82506180f` |

Изменение любого файла после этой отметки требует новой записи в амендменте и новой ревизии.

## Проверка кода до прогона

`test_stress_gates_synth.py`: **60 проверок**, провалов нет. Число получено подсчётом строк `[ok]` в выводе.

## Режим расчёта эмбеддингов

Канонический, введён ревизией r3 и не меняется: один поток BLAS и torch, `use_deterministic_algorithms`, `batch_size=1`. Строка ревизии кеша:

```
BAAI/bge-m3@5617a9f61b02, sentence-transformers 5.5.1, deterministic cpu/1thread/bs1
```

Воспроизводимость проверена на записях кеша r3: шесть входов пересчитаны в отдельном процессе, массивы совпали побитово, расхождений ноль.
