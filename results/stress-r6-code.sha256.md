# Хеши кода и входов ревизии r6 стресс-теста

Зафиксировано **до запуска третьей попытки P2**. Основание —
`02-preregistration/amendment-stress-r6-p2-matrix.md`.

Отметка UTC `2026-07-31T20:42:28+00:00`, по Москве `2026-07-31T23:42:28+03:00`.

Ревизия r6 охватывает только процедуру 2. P1, P3 и P4 остаются в r5: P3 и P4
матрицу признаков не читают, а 22 признака индекса, которые читает P1,
совпадают в обеих версиях матрицы до последнего значения.

Предыдущие попытки P2 ревизии r5 помечены непригодными записью
`invalidation-decision-2026-07-31-p2-discourse-addendum-1.json`: они шли на
матрице до исправления.

| Файл | Что это | sha256 |
|---|---|---|
| `02-preregistration/amendment-stress-r6-p2-matrix.md` | амендмент r6: вход P2 переведён на матрицу v5-r2 | `fa7d5d70d46de057f438b64ec66f366d0c698a691dd62799ec8ca79096a22480` |
| `02-preregistration/amendment-stress-r5-t14-not-executable.md` | амендмент r5: t14 в not executable, знаменатель 10 | `9c2071b85e3ee335452b17bfa083e4f08e53346c3c5ae4c1d17598e5465e44fd` |
| `02-preregistration/amendment-stress-r5-attempt-provenance.md` | дополнение r5: попытка как единица происхождения | `8421b88c80b3dcb3709c6f1e90ef19c543d1907cd2411f60ed1017b74eb2ba0b` |
| `02-preregistration/amendment-stress-r5-p2-input-contract.md` | дополнение r5: входной контракт P2, квантование %.6g | `057640a16e423dad4a5eb1dfa3e445565e26eb1c95866d5feaf83630541af29b` |
| `02-preregistration/amendment-stress-r4-invariant-t14.md` | амендмент r4: инвариант входа, действует | `a969e832ca6dbf20983493d991ad3234093177f32a8b15f7fbb459f941e2f671` |
| `02-preregistration/amendment-p2-stress-units.md` | единицы анализа P2 и пороги | `3717cd12987bdb2461397a72bc677a9e09ae6e8f45bfe6b3bf60e496074dec78` |
| `02-preregistration/amendment-feature-matrix-v5-r2-discourse.md` | основание исправления матрицы | `ed4e0af5136c03d7d8164f23ce1491beaea389360a5d1a0d183ac911f6834feb` |
| `06-features/feature-matrix-v5-r2.csv` | вход P2: матрица с исправленными D04 и D05 | `ceaf9b6a18a8081ed68ea65ef259bc462e1752ba7ec21419a872ad5c8f01f592` |
| `07-analysis/stress-panel-v1.csv` | панель 60 документов, не меняется | `39d96e19c6cbf291d4a8940f00a9915e55be5a0dee2ddc979fbcc676b231efce` |
| `07-analysis/splits-v5/p2a-inner-folds-valid.json` | схема inner CV, переносится без изменений | `7d49f357f1306842f446529e6503d6d22bbb04581fde10e6ec9bfed82506180f` |
| `09-tools/stress_paths.py` | единая точка: входы, ревизия процедуры, каталог попытки | `e11784e3d3274a80bfdf294500169bb18b9b1eeae0c2c39b80df812213af76ab` |
| `09-tools/stress_run_p2.py` | расчётный код P2: реконструкция, инвариант, шлюзы | `8d959c79c6343f8b2f8f3774e7c8b675e1b9da4f6b28d6778bd6afd6d4617bce` |
| `09-tools/stress_transforms.py` | десять выполнимых преобразований, шесть нет | `d62f65605583e74eb74a65263a34def50f663be62b0907ab40eefea5665ffb90` |
| `09-tools/stress_run_p1.py` | P1: источник ключей кешей и панельных счётчиков | `d87138a57751046f8fb64e5c205f63731ad437c008f6705ad39c6bfecbaf0bc8` |
| `09-tools/test_stress_gates_synth.py` | синтетический тест шлюзов, дополнен проверками r6 | `df047ba4d84ff4ac27a28619d460a9e8a5b151a1119bb3983a3c517c32f54b13` |

Синтетический тест перед запуском: 86 проверок, провалов нет.
