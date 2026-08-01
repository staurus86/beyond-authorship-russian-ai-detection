# Хеши кода и входов ревизии r8 стресс-теста

Зафиксировано **до запуска четвёртой попытки P2**. Основания —
`02-preregistration/amendment-stress-r7-p2-features.md` и решение об откате
`07-analysis/rollback-decision-2026-08-01-d04-d05.json`.

Отметка UTC `2026-07-31T21:32:26+00:00`, по Москве `2026-08-01T00:32:26+03:00`.

Ревизии r6 и r7 отменены вместе с матрицей v5-r2: они вели вход P2 на артефакт,
собранный пересчётом по профилям prep-v4. Ревизия r8 работает на действующей
матрице `feature-matrix-v5.csv` и сохраняет досчёт пяти признаков, которые
прежде приходили пропусками. P1, P3 и P4 остаются в r5.

| Файл | Что это | sha256 |
|---|---|---|
| `07-analysis/rollback-decision-2026-08-01-d04-d05.json` | решение об откате: действует матрица v5 | `78bd2a6629f2093d1766e521f14a6e251827ffaebfd9cc09d81199f73793884e` |
| `02-preregistration/amendment-stress-r7-p2-features.md` | амендмент: досчёт D04, D05, F04, F05 и перенос F06 | `3a076c9979d64a335fb5a893ad64e319b22bc464009ab69e3d8feca116b3e47e` |
| `02-preregistration/amendment-stress-r5-t14-not-executable.md` | амендмент r5: t14 в not executable, знаменатель 10 | `9c2071b85e3ee335452b17bfa083e4f08e53346c3c5ae4c1d17598e5465e44fd` |
| `02-preregistration/amendment-stress-r5-attempt-provenance.md` | дополнение r5: попытка как единица происхождения | `8421b88c80b3dcb3709c6f1e90ef19c543d1907cd2411f60ed1017b74eb2ba0b` |
| `02-preregistration/amendment-stress-r5-p2-input-contract.md` | дополнение r5: квантование входа до %.6g | `057640a16e423dad4a5eb1dfa3e445565e26eb1c95866d5feaf83630541af29b` |
| `02-preregistration/amendment-stress-r4-invariant-t14.md` | амендмент r4: инвариант входа | `a969e832ca6dbf20983493d991ad3234093177f32a8b15f7fbb459f941e2f671` |
| `02-preregistration/amendment-p2-stress-units.md` | единицы анализа P2 и пороги | `3717cd12987bdb2461397a72bc677a9e09ae6e8f45bfe6b3bf60e496074dec78` |
| `06-features/feature-matrix-v5.csv` | вход P2: действующая матрица серии v2 | `421fc634e69ed20fa9ffac1b965905cb2b2a675d93fb214f1e995cd53706636d` |
| `07-analysis/stress-panel-v1.csv` | панель 60 документов | `39d96e19c6cbf291d4a8940f00a9915e55be5a0dee2ddc979fbcc676b231efce` |
| `07-analysis/splits-v5/p2a-inner-folds-valid.json` | схема inner CV | `7d49f357f1306842f446529e6503d6d22bbb04581fde10e6ec9bfed82506180f` |
| `09-tools/stress_paths.py` | входы, ревизия процедуры, каталог попытки | `74bb25ffd27b3aa1659357d572cdb690fffd028a3057548cc0cc1c87046d5da8` |
| `09-tools/stress_run_p2.py` | расчётный код P2 | `57ab409c4d066258af05c962c66144b0feb30fd671ca9b9a4cad30900c18df53` |
| `09-tools/stress_transforms.py` | десять выполнимых преобразований | `d62f65605583e74eb74a65263a34def50f663be62b0907ab40eefea5665ffb90` |
| `09-tools/stress_run_p1.py` | источник ключей кешей и панельных счётчиков | `d87138a57751046f8fb64e5c205f63731ad437c008f6705ad39c6bfecbaf0bc8` |
| `09-tools/extract_discourse.py` | disc-v1: источник D04 и D05 | `b9145ad50d48055ab86438a1f4a67d9ea4f6aaf015ed1712cfa5e98f0cf02726` |
| `09-tools/extract_artifacts.py` | art-v2: источник F04 и F05 | `d5a5e22fa32ca39599cde91e0fc39f7bb97a9c04919335147525b110b31a6b0e` |
| `09-tools/test_stress_gates_synth.py` | синтетический тест шлюзов | `a744f26f5ca6d92817c92ca097da2da3f566767b26cf2c3333bb1c24708090d9` |

Синтетический тест перед запуском: 96 проверок, провалов нет.
