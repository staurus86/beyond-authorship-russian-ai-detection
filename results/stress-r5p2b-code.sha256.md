# Хеши кода ревизии восстановления P2, попытка 2 (r5-p2-recovery-2)

Зафиксировано **до второй recovery-попытки P2**. Прежние таблицы не изменяются.

**supersedes:** `stress-r5p2-code.sha256.md`, sha256 `f22f5fbd22d9e4ed68c6a496f95e0e0c859f5f555c5780b381bfc8ade33e941f`
**область:** только процедура P2. P1, P3 и деривация P4 не пересчитываются.

Что изменилось против прежней таблицы:

- `stress_run_p2.py` — вход модели квантуется до шести значащих цифр перед
  импутацией и масштабированием; инвариант `input_unchanged` стал
  двухступенчатым: сначала точное совпадение квантизованного вектора со строкой
  матрицы, затем нулевая разность вероятностей;
- `test_stress_gates_synth.py` — четыре проверки квантизации, всего 75;
- добавлен амендмент о входном контракте.

Отметка UTC `2026-07-31T17:29:14+00:00`, по Москве `2026-07-31T20:29:14+03:00`.

| Файл | Что это | sha256 |
|---|---|---|
| `02-preregistration/amendment-stress-r5-t14-not-executable.md` | амендмент r5: t14 в not executable, знаменатель 10, пороги по процедурам | `9c2071b85e3ee335452b17bfa083e4f08e53346c3c5ae4c1d17598e5465e44fd` |
| `02-preregistration/amendment-stress-r5-lifecycle-status.md` | дополнение r5: статус жизненного цикла прогона | `0a218fd769cdfdc3c54970b1b01af9189d5fccb1aa493fccfa3de31de39f6ea1` |
| `02-preregistration/amendment-stress-r5-attempt-provenance.md` | дополнение r5: попытка как единица происхождения | `8421b88c80b3dcb3709c6f1e90ef19c543d1907cd2411f60ed1017b74eb2ba0b` |
| `02-preregistration/amendment-stress-r4-invariant-t14.md` | амендмент r4: инвариант входа, действует | `a969e832ca6dbf20983493d991ad3234093177f32a8b15f7fbb459f941e2f671` |
| `02-preregistration/amendment-stress-r3-pipeline.md` | амендмент r3: препроцессинг, форматные признаки, режим embed | `279c08bea4a5c0050231e82bbe42db653d14b2d56a1f62c6c51d33a06189330e` |
| `07-analysis/stress-panel-v1.csv` | панель 60 документов, не меняется | `39d96e19c6cbf291d4a8940f00a9915e55be5a0dee2ddc979fbcc676b231efce` |
| `09-tools/stress_paths.py` | единая точка: каталог входов и метка ревизии | `f46547dbe9ddb475513aef3734f3f1baa84b274080d89a5b58c7cc9fc1bc291a` |
| `09-tools/lifecycle_gate.py` | проверка статуса жизненного цикла прежней ревизии | `8cb7232a617f871b5885ba1124df05c26e3be2958ac89bd5308d6ead8be0212a` |
| `09-tools/stress_run_p1.py` | расчётный код P1: панель, кеши, baseline, шлюз | `d87138a57751046f8fb64e5c205f63731ad437c008f6705ad39c6bfecbaf0bc8` |
| `09-tools/stress_run_p2.py` | расчётный код P2: реконструкция, агрегация, шлюзы | `2d1c93efa7c802a0334d8cd2b5218fecec05200d375003b42ce8df1cbe3c4992` |
| `09-tools/stress_run_p3.py` | расчётный код P3 со шлюзом и сверкой хеша входа | `06523f24374ecffcdbdf484ac02fc5ca4f111af5d4842f9b6fdc62d0e0746da0` |
| `09-tools/stress_run_p4.py` | код P4: в r5 стартовать отказывается, результаты получены деривацией | `b6b8bc25f48dad7473c22ca4fc26ab2569fd2d22100546c3f9230d0096e1a9f1` |
| `09-tools/derive_p4_r5.py` | деривация P4 r5 из r4, четыре шлюза | `b005b2c4e6b446bfe74835f911996ee58e57c1d2892817c87734423081f9fa4d` |
| `09-tools/write_lifecycle_sidecar.py` | запись статуса жизненного цикла | `0806db05e888358243d4a4ec41dfa5174082d54e674772a90e6377b3c5f467ec` |
| `09-tools/stress_transforms.py` | десять выполнимых преобразований, шесть нет | `d62f65605583e74eb74a65263a34def50f663be62b0907ab40eefea5665ffb90` |
| `09-tools/stress_run_chain.py` | цепочка прогона и сверка хешей | `b74c6a8fe8223f12c50f9a24ec000dc56278b36fb282c448cd56fa09f2f9301c` |
| `09-tools/prep.py` | препроцессинг: профили и счётчики форматных признаков | `c3bf16d5935bb941c18777861c4aa7e07e100cc89bb2849c193e7e752a07ea6e` |
| `09-tools/extract_features.py` | экстрактор: читает счётчики для F01, R06, R07 | `ee9e4c89a02ba1677917a00710837449753e54ced2a057b5e94bac0d4d8549a3` |
| `09-tools/extract_semantic.py` | экстрактор M01, M02, M05 | `66084736bd00023d51849f3be2c4775483191d6cc8ac74a6a20c4a582d92e6ac` |
| `09-tools/score_style_index.py` | агрегация: терцили, веса, знаменатели | `4953acece66f82f28e9934e18fa2a89a3823b61024c355f88c8997afb7d19fc1` |
| `09-tools/test_stress_gates_synth.py` | синтетический тест шлюзов, 67 проверок | `5c9481c51ef364269f1ecce9358ec97dbe1410bb7a4f2836ee85ee99e3873956` |
| `07-analysis/analysis-closure.md` | закрытие анализа | `22170a49e1e841a007907b059128fe76bf1c88361452e290148357302bc0993f` |
| `07-analysis/splits-v5/p2a-inner-folds-valid.json` | схема B inner CV | `7d49f357f1306842f446529e6503d6d22bbb04581fde10e6ec9bfed82506180f` |
| `07-analysis/stress-p4-r5-manifest.json` | манифест деривации P4 r5 | `10ca6c8aa6c202dfd28164050ec1f0ee019e53f5eba6de431dbf41bf04c7af91` |
| `07-analysis/stress-p4-r4-lifecycle.json` | статус жизненного цикла P4 r4 | `b44e258fb52fa35a3067ab5bf4b4024a26207e1f5aadd910edb2a299c9a7f128` |
| `07-analysis/stress-p1-r4-lifecycle.json` | статус жизненного цикла P1 r4 | `0176b58a07062f5331ad476cb464565da16717660e79163399b9f1d1d27645ee` |
| `07-analysis/stress-p3-r4-lifecycle.json` | статус жизненного цикла P3 r4 | `515d15b90535f27a97903ab56e498751b2e91033d4bb14b2dae950db9a7c951b` |
| `02-preregistration/amendment-stress-r5-p2-input-contract.md` | дополнение r5: сериализация как входной контракт P2 | `057640a16e423dad4a5eb1dfa3e445565e26eb1c95866d5feaf83630541af29b` |
