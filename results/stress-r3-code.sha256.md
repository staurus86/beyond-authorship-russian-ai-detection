# Хеши кода и входов ревизии r3 стресс-теста

Зафиксировано **до запуска цепочки ревизии r3**. Основание — `02-preregistration/amendment-stress-r3-pipeline.md`.

Отметка UTC `2026-07-30T09:45:24+00:00`, по Москве `2026-07-30T12:45:24+03:00`.

Ревизия r2 помечена `invalidated`; её хеши остаются в `p2-stress-units.sha256.md` и здесь не дублируются.

| Файл | Что это | sha256 |
|---|---|---|
| `02-preregistration/amendment-stress-r3-pipeline.md` | амендмент ревизии r3: препроцессинг, форматные признаки, режим embed | `279c08bea4a5c0050231e82bbe42db653d14b2d56a1f62c6c51d33a06189330e` |
| `07-analysis/stress-panel-v1.csv` | панель 60 документов, не меняется | `39d96e19c6cbf291d4a8940f00a9915e55be5a0dee2ddc979fbcc676b231efce` |
| `09-tools/stress_run_p1.py` | расчётный код P1: панель, кеши, baseline, шлюз | `b20a89d472fe0b3661d090dc55edd475a071deb389c4b5b1263b6e3f310bbb52` |
| `09-tools/stress_run_p2.py` | расчётный код P2: реконструкция, агрегация, шлюзы | `5b04b7fbe7cfef68b50b9a683889a25bb89275f87e0bbdc9ed3b2dd903d13976` |
| `09-tools/stress_run_p3.py` | расчётный код P3 со шлюзом | `f27191ebac50aa37f29e346a81adbc19262342de2cf5db16de3dfe4569a0418e` |
| `09-tools/stress_run_p4.py` | расчётный код P4 со шлюзом | `bd22ad3ca5771485439ff92dd63dfc07b88d7db9d327300ac7cfe772f609c4d9` |
| `09-tools/stress_transforms.py` | одиннадцать преобразований, не меняются | `42b98b9849e3c5ba6d064493ccdd2df5c0e1382c6b838970b7d6fd0721274f2a` |
| `09-tools/stress_run_chain.py` | цепочка прогона и сверка хешей | `918944dca86c4ff1725eb520f4fadf9cade3787d775c8a1a6c3f8dcdde19d3c4` |
| `09-tools/prep.py` | препроцессинг: профили и счётчики форматных признаков | `c3bf16d5935bb941c18777861c4aa7e07e100cc89bb2849c193e7e752a07ea6e` |
| `09-tools/extract_features.py` | экстрактор: читает счётчики для F01, R06, R07 | `ee9e4c89a02ba1677917a00710837449753e54ced2a057b5e94bac0d4d8549a3` |
| `09-tools/extract_semantic.py` | экстрактор M01, M02, M05 | `66084736bd00023d51849f3be2c4775483191d6cc8ac74a6a20c4a582d92e6ac` |
| `09-tools/score_style_index.py` | агрегация: терцили, веса, знаменатели | `4953acece66f82f28e9934e18fa2a89a3823b61024c355f88c8997afb7d19fc1` |
| `09-tools/test_stress_gates_synth.py` | синтетический тест шлюзов | `a6fce10743de87a5b9985a21eac3c9bc9073bfc24c0bc63a88b13e0d2317bf8b` |
| `07-analysis/analysis-closure.md` | закрытие анализа | `22170a49e1e841a007907b059128fe76bf1c88361452e290148357302bc0993f` |
| `07-analysis/splits-v5/p2a-inner-folds-valid.json` | схема B inner CV | `7d49f357f1306842f446529e6503d6d22bbb04581fde10e6ec9bfed82506180f` |

Изменение любого файла после этой отметки требует новой записи в амендменте и новой ревизии.

## Проверка кода до прогона

`test_stress_gates_synth.py`: **48 проверок**, провалов нет. Число получено подсчётом строк `[ok]` в выводе.

## Режим расчёта эмбеддингов

Канонический: один поток BLAS и torch, `use_deterministic_algorithms`, `batch_size=1`. Проверен двумя независимыми процессами — sha256 массива и значения M01, M02, M05 совпали побитово. Строка ревизии кеша:

```
BAAI/bge-m3@5617a9f61b02, sentence-transformers 5.5.1, deterministic cpu/1thread/bs1
```
