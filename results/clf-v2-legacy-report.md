# Процедура 2, clf-v2-legacy: результаты замороженного прогона

Запуск 2026-07-29T14:52:16+00:00, seed 20260727. Хеши входов, разбиений и кода — `clf-v2-legacy-manifest.json`.

**Статус — exploratory.** P2a — корпусный baseline, подверженный жанровому и источниковому конфаундингу. Формулировка «точность обнаружения машинного текста» к нему неприменима.

## Два решения, принятых после первого прогона

Первый прогон сохранён целиком в `_clf-v1-run1-before-posthoc/`. Оба изменения помечены post hoc и касаются протоколирования, а не выбора модели или признаков.

1. **Негативный контроль** считается как серия из 20 кластерных перестановок. Одна перестановка при четырёх машинных кластерах против 124 человеческих сигнал не снимала, а инвертировала: AUROC уходил к 0.01 вместо 0.5, и число читалось бы как сильный эффект наоборот.
2. **На одноклассовых тестах считается FPR.** Пять holdout из восемнадцати имеют целиком человеческую тестовую часть — жанры `news`, `prose`, `science`, `translation` машинных документов не содержат, а временной срез выносит период без генераций. AUROC, MCC и TPR там неопределимы; FPR определим и показывает цену ложного обвинения на невиданном жанре.

## P2a: основная модель против диагностических baseline

| Holdout | Модель | Estimand | AUROC | MCC | Balanced acc. | TPR@1%FPR | FPR | Статус |
|---|---|---|---|---|---|---|---|---|
| holdout_author | format-only | full | 0.993 | 0.960 | 0.980 | 0.974 | 0.014 | зарегистрирован до прогона |
| holdout_author | genre-only | full | 0.726 | -0.139 | 0.437 | 0.665 | 0.792 | зарегистрирован до прогона |
| holdout_author | length-only | full | 0.379 | -0.060 | 0.470 | 0.000 | 0.663 | зарегистрирован до прогона |
| holdout_author | main | full | 0.991 | 0.908 | 0.952 | 0.885 | 0.028 | зарегистрирован до прогона |
| holdout_author | main | net | 0.887 | 0.452 | 0.682 | 0.190 | 0.053 | зарегистрирован до прогона |
| holdout_author | main+M02 | full | 0.993 | 0.928 | 0.964 | 0.903 | 0.024 | зарегистрирован до прогона |
| holdout_author | negative-control | full | 0.266 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.003–0.996 |
| holdout_author | source-only | full | 0.527 | 0.141 | 0.527 | 0.000 | 0.945 | зарегистрирован до прогона |
| holdout_genre_analytics | format-only | full | 0.973 | 0.870 | 0.931 | 0.869 | 0.020 | зарегистрирован до прогона |
| holdout_genre_analytics | genre-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_analytics | length-only | full | 0.471 | 0.017 | 0.508 | 0.019 | 0.605 | зарегистрирован до прогона |
| holdout_genre_analytics | main | full | 0.999 | 0.963 | 0.982 | 0.986 | 0.026 | зарегистрирован до прогона |
| holdout_genre_analytics | main | net | 0.987 | 0.878 | 0.940 | 0.760 | 0.079 | зарегистрирован до прогона |
| holdout_genre_analytics | main+M02 | full | 0.999 | 0.963 | 0.982 | 0.986 | 0.028 | зарегистрирован до прогона |
| holdout_genre_analytics | negative-control | full | 0.389 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.935 |
| holdout_genre_analytics | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_commercial | format-only | full | 0.977 | 0.884 | 0.938 | 0.883 | 0.008 | зарегистрирован до прогона |
| holdout_genre_commercial | genre-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_commercial | length-only | full | 0.483 | 0.140 | 0.565 | 0.014 | 0.620 | зарегистрирован до прогона |
| holdout_genre_commercial | main | full | 0.997 | 0.926 | 0.964 | 0.975 | 0.056 | зарегистрирован до прогона |
| holdout_genre_commercial | main | net | 0.997 | 0.908 | 0.954 | 0.906 | 0.087 | зарегистрирован до прогона |
| holdout_genre_commercial | main+M02 | full | 0.999 | 0.947 | 0.974 | 0.981 | 0.041 | зарегистрирован до прогона |
| holdout_genre_commercial | negative-control | full | 0.361 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.951 |
| holdout_genre_commercial | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_news | format-only | full | — | — | — | — | 0.010 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | length-only | full | — | — | — | — | 0.628 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | main | full | — | — | — | — | 0.041 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | main | net | — | — | — | — | 0.079 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | main+M02 | full | — | — | — | — | 0.033 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | format-only | full | — | — | — | — | 0.007 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | length-only | full | — | — | — | — | 0.839 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | main | full | — | — | — | — | 0.082 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | main | net | — | — | — | — | 0.185 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | main+M02 | full | — | — | — | — | 0.082 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | format-only | full | — | — | — | — | 0.010 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | length-only | full | — | — | — | — | 0.628 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | main | full | — | — | — | — | 0.041 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | main | net | — | — | — | — | 0.079 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | main+M02 | full | — | — | — | — | 0.033 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_seo | format-only | full | 0.978 | 0.883 | 0.928 | 0.878 | 0.008 | зарегистрирован до прогона |
| holdout_genre_seo | genre-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_seo | length-only | full | 0.614 | 0.137 | 0.564 | 0.000 | 0.676 | зарегистрирован до прогона |
| holdout_genre_seo | main | full | 0.993 | 0.883 | 0.950 | 0.900 | 0.075 | зарегистрирован до прогона |
| holdout_genre_seo | main | net | 0.899 | 0.588 | 0.802 | 0.069 | 0.345 | зарегистрирован до прогона |
| holdout_genre_seo | main+M02 | full | 0.994 | 0.891 | 0.953 | 0.914 | 0.069 | зарегистрирован до прогона |
| holdout_genre_seo | negative-control | full | 0.431 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.170–0.770 |
| holdout_genre_seo | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_translation | format-only | full | — | — | — | — | 0.010 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | length-only | full | — | — | — | — | 0.628 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | main | full | — | — | — | — | 0.041 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | main | net | — | — | — | — | 0.079 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | main+M02 | full | — | — | — | — | 0.033 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_model_Anthropic | format-only | full | 1.000 | 0.981 | 0.992 | 1.000 | 0.015 | зарегистрирован до прогона |
| holdout_model_Anthropic | genre-only | full | 0.667 | -0.478 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_model_Anthropic | length-only | full | 0.440 | 0.025 | 0.512 | 0.004 | 0.620 | зарегистрирован до прогона |
| holdout_model_Anthropic | main | full | 1.000 | 0.963 | 0.985 | 1.000 | 0.031 | зарегистрирован до прогона |
| holdout_model_Anthropic | main | net | 0.978 | 0.868 | 0.937 | 0.519 | 0.074 | зарегистрирован до прогона |
| holdout_model_Anthropic | main+M02 | full | 1.000 | 0.960 | 0.983 | 0.993 | 0.033 | зарегистрирован до прогона |
| holdout_model_Anthropic | negative-control | full | 0.286 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.970 |
| holdout_model_Anthropic | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_model_DeepSeek | format-only | full | 0.915 | 0.813 | 0.892 | 0.793 | 0.020 | зарегистрирован до прогона |
| holdout_model_DeepSeek | genre-only | full | 0.667 | -0.478 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_model_DeepSeek | length-only | full | 0.484 | 0.032 | 0.516 | 0.019 | 0.602 | зарегистрирован до прогона |
| holdout_model_DeepSeek | main | full | 0.996 | 0.928 | 0.965 | 0.922 | 0.036 | зарегистрирован до прогона |
| holdout_model_DeepSeek | main | net | 0.991 | 0.881 | 0.946 | 0.796 | 0.089 | зарегистрирован до прогона |
| holdout_model_DeepSeek | main+M02 | full | 0.997 | 0.944 | 0.972 | 0.937 | 0.023 | зарегистрирован до прогона |
| holdout_model_DeepSeek | negative-control | full | 0.383 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.002–0.926 |
| holdout_model_DeepSeek | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_model_NVIDIA | format-only | full | 0.993 | 0.956 | 0.978 | 0.974 | 0.018 | зарегистрирован до прогона |
| holdout_model_NVIDIA | genre-only | full | 0.665 | -0.479 | 0.333 | 0.665 | 1.000 | зарегистрирован до прогона |
| holdout_model_NVIDIA | length-only | full | 0.296 | -0.363 | 0.316 | 0.000 | 0.673 | зарегистрирован до прогона |
| holdout_model_NVIDIA | main | full | 0.990 | 0.915 | 0.958 | 0.885 | 0.036 | зарегистрирован до прогона |
| holdout_model_NVIDIA | main | net | 0.881 | 0.429 | 0.672 | 0.149 | 0.051 | зарегистрирован до прогона |
| holdout_model_NVIDIA | main+M02 | full | 0.993 | 0.925 | 0.962 | 0.907 | 0.028 | зарегистрирован до прогона |
| holdout_model_NVIDIA | negative-control | full | 0.128 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.005–0.974 |
| holdout_model_NVIDIA | source-only | full | 0.502 | 0.000 | 0.500 | 0.004 | 1.000 | зарегистрирован до прогона |
| holdout_model_OpenAI | format-only | full | 0.966 | 0.770 | 0.863 | 0.711 | 0.015 | зарегистрирован до прогона |
| holdout_model_OpenAI | genre-only | full | 0.667 | -0.478 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_model_OpenAI | length-only | full | 0.425 | 0.037 | 0.518 | 0.007 | 0.605 | зарегистрирован до прогона |
| holdout_model_OpenAI | main | full | 0.997 | 0.950 | 0.976 | 0.952 | 0.026 | зарегистрирован до прогона |
| holdout_model_OpenAI | main | net | 0.994 | 0.897 | 0.953 | 0.848 | 0.071 | зарегистрирован до прогона |
| holdout_model_OpenAI | main+M02 | full | 0.998 | 0.956 | 0.978 | 0.974 | 0.018 | зарегистрирован до прогона |
| holdout_model_OpenAI | negative-control | full | 0.405 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.010–0.968 |
| holdout_model_OpenAI | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P1 | format-only | full | 0.990 | 0.932 | 0.964 | 0.936 | 0.010 | зарегистрирован до прогона |
| holdout_prompt_P1 | genre-only | full | 0.667 | -0.455 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P1 | length-only | full | 0.425 | -0.145 | 0.427 | 0.006 | 0.582 | зарегистрирован до прогона |
| holdout_prompt_P1 | main | full | 0.999 | 0.963 | 0.982 | 0.986 | 0.033 | зарегистрирован до прогона |
| holdout_prompt_P1 | main | net | 0.977 | 0.846 | 0.923 | 0.625 | 0.082 | зарегистрирован до прогона |
| holdout_prompt_P1 | main+M02 | full | 0.999 | 0.960 | 0.981 | 0.981 | 0.033 | зарегистрирован до прогона |
| holdout_prompt_P1 | negative-control | full | 0.357 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.000–0.871 |
| holdout_prompt_P1 | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P2 | format-only | full | 0.990 | 0.973 | 0.987 | 0.986 | 0.013 | зарегистрирован до прогона |
| holdout_prompt_P2 | genre-only | full | 0.667 | -0.455 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P2 | length-only | full | 0.405 | 0.301 | 0.612 | 0.000 | 0.730 | зарегистрирован до прогона |
| holdout_prompt_P2 | main | full | 1.000 | 0.966 | 0.983 | 1.000 | 0.033 | зарегистрирован до прогона |
| holdout_prompt_P2 | main | net | 0.986 | 0.882 | 0.941 | 0.717 | 0.089 | зарегистрирован до прогона |
| holdout_prompt_P2 | main+M02 | full | 1.000 | 0.958 | 0.980 | 1.000 | 0.041 | зарегистрирован до прогона |
| holdout_prompt_P2 | negative-control | full | 0.357 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.000–0.998 |
| holdout_prompt_P2 | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P3 | format-only | full | 0.924 | 0.729 | 0.848 | 0.688 | 0.015 | зарегистрирован до прогона |
| holdout_prompt_P3 | genre-only | full | 0.666 | -0.456 | 0.333 | 0.666 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P3 | length-only | full | 0.422 | -0.130 | 0.435 | 0.011 | 0.589 | зарегистрирован до прогона |
| holdout_prompt_P3 | main | full | 0.978 | 0.849 | 0.922 | 0.652 | 0.041 | зарегистрирован до прогона |
| holdout_prompt_P3 | main | net | 0.982 | 0.859 | 0.930 | 0.760 | 0.074 | зарегистрирован до прогона |
| holdout_prompt_P3 | main+M02 | full | 0.984 | 0.850 | 0.922 | 0.691 | 0.038 | зарегистрирован до прогона |
| holdout_prompt_P3 | negative-control | full | 0.417 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.003–0.937 |
| holdout_prompt_P3 | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_source | format-only | full | 0.998 | 0.970 | 0.985 | 0.987 | 0.019 | зарегистрирован до прогона |
| holdout_source | genre-only | full | 0.692 | -0.206 | 0.411 | 0.666 | 0.845 | зарегистрирован до прогона |
| holdout_source | length-only | full | 0.436 | 0.114 | 0.552 | 0.002 | 0.647 | зарегистрирован до прогона |
| holdout_source | main | full | 0.996 | 0.952 | 0.976 | 0.963 | 0.026 | зарегистрирован до прогона |
| holdout_source | main | net | 0.906 | 0.554 | 0.757 | 0.425 | 0.034 | зарегистрирован до прогона |
| holdout_source | main+M02 | full | 0.997 | 0.950 | 0.975 | 0.967 | 0.024 | зарегистрирован до прогона |
| holdout_source | negative-control | full | 0.242 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.999 |
| holdout_source | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_time | format-only | full | — | — | — | — | 0.022 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | genre-only | full | — | — | — | — | 0.856 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | length-only | full | — | — | — | — | 0.592 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | main | full | — | — | — | — | 0.039 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | main | net | — | — | — | — | 0.092 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | main+M02 | full | — | — | — | — | 0.028 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | source-only | full | — | — | — | — | 0.893 | post hoc: тест одноклассовый, определим только FPR |
| holdout_topic | format-only | full | 0.965 | 0.865 | 0.929 | 0.869 | 0.020 | зарегистрирован до прогона |
| holdout_topic | genre-only | full | 0.599 | -0.509 | 0.299 | 0.599 | 1.000 | зарегистрирован до прогона |
| holdout_topic | length-only | full | 0.476 | 0.151 | 0.569 | 0.011 | 0.630 | зарегистрирован до прогона |
| holdout_topic | main | full | 0.998 | 0.947 | 0.974 | 0.969 | 0.041 | зарегистрирован до прогона |
| holdout_topic | main | net | 0.982 | 0.869 | 0.935 | 0.710 | 0.069 | зарегистрирован до прогона |
| holdout_topic | main+M02 | full | 0.999 | 0.958 | 0.979 | 0.983 | 0.033 | зарегистрирован до прогона |
| holdout_topic | negative-control | full | 0.384 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.940 |
| holdout_topic | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |

### Сверка подбора C: перенос против пересборки

Разбиение подбора регуляризации перенесено из clf-v1 (`p2a-inner-carry.md`). Колонка `c_rebuilt` показывает значение, которое дало бы разбиение, построенное заново на составе prep-v5.

Сверено строк: 126; выбранный C отличался бы у **31**.

| Holdout | Модель | Estimand | C перенос | C пересборка |
|---|---|---|---|---|
| holdout_author | source-only | full | 1.0 | 0.01 |
| holdout_genre_analytics | main | full | 1.0 | 10.0 |
| holdout_genre_analytics | source-only | full | 0.1 | 0.01 |
| holdout_genre_commercial | format-only | full | 1.0 | 10.0 |
| holdout_genre_news | format-only | full | 1.0 | 10.0 |
| holdout_genre_prose | format-only | full | 1.0 | 10.0 |
| holdout_genre_prose | main | full | 1.0 | 0.1 |
| holdout_genre_prose | main+M02 | full | 10.0 | 0.01 |
| holdout_genre_science | format-only | full | 1.0 | 10.0 |
| holdout_genre_translation | format-only | full | 1.0 | 10.0 |
| holdout_model_Anthropic | main | full | 10.0 | 0.1 |
| holdout_model_Anthropic | main+M02 | full | 0.1 | 10.0 |
| holdout_model_Anthropic | source-only | full | 1.0 | 10.0 |
| holdout_model_DeepSeek | main | net | 0.1 | 0.01 |
| holdout_model_DeepSeek | main+M02 | full | 0.1 | 0.01 |
| holdout_model_OpenAI | source-only | full | 0.01 | 1.0 |
| holdout_prompt_P1 | format-only | full | 1.0 | 10.0 |
| holdout_prompt_P1 | main | full | 10.0 | 1.0 |
| holdout_prompt_P1 | main+M02 | full | 10.0 | 0.1 |
| holdout_prompt_P2 | format-only | full | 1.0 | 10.0 |
| holdout_prompt_P2 | main | full | 10.0 | 0.1 |
| holdout_prompt_P2 | main+M02 | full | 10.0 | 0.1 |
| holdout_prompt_P3 | main | net | 0.01 | 0.1 |
| holdout_prompt_P3 | source-only | full | 0.1 | 0.01 |
| holdout_source | format-only | full | 1.0 | 0.1 |
| holdout_source | main | full | 1.0 | 0.1 |
| holdout_source | main | net | 10.0 | 1.0 |
| holdout_source | source-only | full | 0.1 | 0.01 |
| holdout_topic | main | net | 0.01 | 0.1 |
| holdout_topic | main+M02 | full | 0.1 | 1.0 |
| holdout_topic | source-only | full | 0.1 | 0.01 |

**Как читается разрыв с baseline.** Если genre-only или source-only приближается к основной модели, вывод о происхождении не делается: результат публикуется как разделение жанров и источников.

## P2b: четыре внешних fold-а

| Estimand | Fold | Удержанный канал | AUROC | MCC | TPR@1%FPR | N теста |
|---|---|---|---|---|---|---|
| full | outer_fold_0 | gpt | 0.995 | 0.830 | 0.889 | 133 |
| full | outer_fold_1 | real_claude | 1.000 | 1.000 | 1.000 | 150 |
| full | outer_fold_2 | deepseek_pro | 0.991 | 0.863 | 0.944 | 142 |
| full | outer_fold_3 | nemotron | 0.999 | 0.940 | 0.922 | 166 |
| full | pooled_out_of_fold | все четыре | 0.996 | 0.912 | 0.931 | 591 |
| net | outer_fold_0 | gpt | 0.984 | 0.764 | 0.733 | 133 |
| net | outer_fold_1 | real_claude | 0.986 | 0.889 | 0.722 | 150 |
| net | outer_fold_2 | deepseek_pro | 0.971 | 0.838 | 0.656 | 142 |
| net | outer_fold_3 | nemotron | 0.955 | 0.595 | 0.244 | 166 |
| net | pooled_out_of_fold | все четыре | 0.948 | 0.750 | 0.225 | 591 |

**Ограничение публикуется всегда:** четыре машинных source-кластера дают слабую оценку межканальной обобщаемости.

## P2b: контрасты O1 внутри SEO

| Estimand | Контраст | Пар | Кластеров | Средняя разница вероятности | 95% CI | p wild cluster | p sign-flip |
|---|---|---|---|---|---|---|---|
| full | P3-P1 | 120 | 15 | -0.0941 | [-0.1473; -0.0463] | 0.0028 | 0.0028 |
| full | P2-P1 | 120 | 15 | 0.0523 | [0.0325; 0.0738] | 0.0002 | 0.0002 |
| net | P3-P1 | 120 | 15 | 0.0169 | [-0.0134; 0.0496] | 0.3168 | 0.3168 |
| net | P2-P1 | 120 | 15 | 0.0722 | [0.0395; 0.1036] | 0.0014 | 0.0014 |

**120 пар не равны 120 независимым наблюдениям:** это 15 кластеров-заданий. Сильные выводы только из обычного bootstrap не делаются.

Основная модель P2a посчитана на 18 holdout-разбиениях.
