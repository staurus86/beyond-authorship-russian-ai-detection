# Процедура 2, clf-v1: результаты замороженного прогона

Запуск 2026-07-28T10:09:51+00:00, seed 20260727. Хеши входов, разбиений и кода — `clf-v1-manifest.json`.

**Статус — exploratory.** P2a — корпусный baseline, подверженный жанровому и источниковому конфаундингу. Формулировка «точность обнаружения машинного текста» к нему неприменима.

## Два решения, принятых после первого прогона

Первый прогон сохранён целиком в `_clf-v1-run1-before-posthoc/`. Оба изменения помечены post hoc и касаются протоколирования, а не выбора модели или признаков.

1. **Негативный контроль** считается как серия из 20 кластерных перестановок. Одна перестановка при четырёх машинных кластерах против 124 человеческих сигнал не снимала, а инвертировала: AUROC уходил к 0.01 вместо 0.5, и число читалось бы как сильный эффект наоборот.
2. **На одноклассовых тестах считается FPR.** Пять holdout из восемнадцати имеют целиком человеческую тестовую часть — жанры `news`, `prose`, `science`, `translation` машинных документов не содержат, а временной срез выносит период без генераций. AUROC, MCC и TPR там неопределимы; FPR определим и показывает цену ложного обвинения на невиданном жанре.

## P2a: основная модель против диагностических baseline

| Holdout | Модель | Estimand | AUROC | MCC | Balanced acc. | TPR@1%FPR | FPR | Статус |
|---|---|---|---|---|---|---|---|---|
| holdout_author | format-only | full | 0.993 | 0.961 | 0.980 | 0.974 | 0.013 | зарегистрирован до прогона |
| holdout_author | genre-only | full | 0.723 | -0.152 | 0.431 | 0.665 | 0.803 | зарегистрирован до прогона |
| holdout_author | length-only | full | 0.368 | -0.068 | 0.466 | 0.000 | 0.667 | зарегистрирован до прогона |
| holdout_author | main | full | 0.987 | 0.896 | 0.950 | 0.851 | 0.040 | зарегистрирован до прогона |
| holdout_author | main | net | 0.871 | 0.405 | 0.661 | 0.141 | 0.061 | зарегистрирован до прогона |
| holdout_author | main+M02 | full | 0.989 | 0.911 | 0.958 | 0.888 | 0.036 | зарегистрирован до прогона |
| holdout_author | negative-control | full | 0.256 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.003–0.996 |
| holdout_author | source-only | full | 0.526 | 0.135 | 0.526 | 0.000 | 0.948 | зарегистрирован до прогона |
| holdout_genre_analytics | format-only | full | 0.972 | 0.873 | 0.932 | 0.877 | 0.019 | зарегистрирован до прогона |
| holdout_genre_analytics | genre-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_analytics | length-only | full | 0.459 | -0.018 | 0.491 | 0.022 | 0.619 | зарегистрирован до прогона |
| holdout_genre_analytics | main | full | 0.995 | 0.939 | 0.971 | 0.914 | 0.048 | зарегистрирован до прогона |
| holdout_genre_analytics | main | net | 0.985 | 0.868 | 0.935 | 0.696 | 0.088 | зарегистрирован до прогона |
| holdout_genre_analytics | main+M02 | full | 0.994 | 0.934 | 0.968 | 0.889 | 0.055 | зарегистрирован до прогона |
| holdout_genre_analytics | negative-control | full | 0.392 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.002–0.946 |
| holdout_genre_analytics | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_commercial | format-only | full | 0.977 | 0.888 | 0.938 | 0.883 | 0.007 | зарегистрирован до прогона |
| holdout_genre_commercial | genre-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_commercial | length-only | full | 0.469 | 0.109 | 0.551 | 0.017 | 0.631 | зарегистрирован до прогона |
| holdout_genre_commercial | main | full | 0.996 | 0.907 | 0.955 | 0.961 | 0.076 | зарегистрирован до прогона |
| holdout_genre_commercial | main | net | 0.996 | 0.895 | 0.949 | 0.894 | 0.100 | зарегистрирован до прогона |
| holdout_genre_commercial | main+M02 | full | 0.997 | 0.922 | 0.962 | 0.975 | 0.062 | зарегистрирован до прогона |
| holdout_genre_commercial | negative-control | full | 0.363 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.959 |
| holdout_genre_commercial | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_news | format-only | full | — | — | — | — | 0.010 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | length-only | full | — | — | — | — | 0.640 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | main | full | — | — | — | — | 0.055 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | main | net | — | — | — | — | 0.086 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | main+M02 | full | — | — | — | — | 0.048 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_news | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | format-only | full | — | — | — | — | 0.007 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | length-only | full | — | — | — | — | 0.830 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | main | full | — | — | — | — | 0.085 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | main | net | — | — | — | — | 0.190 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | main+M02 | full | — | — | — | — | 0.102 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_prose | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | format-only | full | — | — | — | — | 0.010 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | length-only | full | — | — | — | — | 0.640 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | main | full | — | — | — | — | 0.055 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | main | net | — | — | — | — | 0.086 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | main+M02 | full | — | — | — | — | 0.048 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_science | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_seo | format-only | full | 0.976 | 0.885 | 0.928 | 0.878 | 0.008 | зарегистрирован до прогона |
| holdout_genre_seo | genre-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_seo | length-only | full | 0.613 | 0.125 | 0.558 | 0.000 | 0.686 | зарегистрирован до прогона |
| holdout_genre_seo | main | full | 0.959 | 0.790 | 0.911 | 0.022 | 0.154 | зарегистрирован до прогона |
| holdout_genre_seo | main | net | 0.859 | 0.542 | 0.779 | 0.000 | 0.393 | зарегистрирован до прогона |
| holdout_genre_seo | main+M02 | full | 0.961 | 0.804 | 0.917 | 0.031 | 0.142 | зарегистрирован до прогона |
| holdout_genre_seo | negative-control | full | 0.434 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.168–0.795 |
| holdout_genre_seo | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_genre_translation | format-only | full | — | — | — | — | 0.010 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | genre-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | length-only | full | — | — | — | — | 0.640 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | main | full | — | — | — | — | 0.055 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | main | net | — | — | — | — | 0.086 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | main+M02 | full | — | — | — | — | 0.048 | post hoc: тест одноклассовый, определим только FPR |
| holdout_genre_translation | source-only | full | — | — | — | — | 1.000 | post hoc: тест одноклассовый, определим только FPR |
| holdout_model_Anthropic | format-only | full | 1.000 | 0.982 | 0.993 | 1.000 | 0.014 | зарегистрирован до прогона |
| holdout_model_Anthropic | genre-only | full | 0.667 | -0.483 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_model_Anthropic | length-only | full | 0.425 | 0.009 | 0.504 | 0.004 | 0.636 | зарегистрирован до прогона |
| holdout_model_Anthropic | main | full | 1.000 | 0.922 | 0.968 | 0.996 | 0.064 | зарегистрирован до прогона |
| holdout_model_Anthropic | main | net | 0.974 | 0.850 | 0.931 | 0.515 | 0.090 | зарегистрирован до прогона |
| holdout_model_Anthropic | main+M02 | full | 0.996 | 0.936 | 0.974 | 0.981 | 0.052 | зарегистрирован до прогона |
| holdout_model_Anthropic | negative-control | full | 0.297 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.002–0.972 |
| holdout_model_Anthropic | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_model_DeepSeek | format-only | full | 0.911 | 0.817 | 0.892 | 0.796 | 0.019 | зарегистрирован до прогона |
| holdout_model_DeepSeek | genre-only | full | 0.667 | -0.483 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_model_DeepSeek | length-only | full | 0.471 | -0.014 | 0.493 | 0.033 | 0.621 | зарегистрирован до прогона |
| holdout_model_DeepSeek | main | full | 0.994 | 0.922 | 0.963 | 0.915 | 0.040 | зарегистрирован до прогона |
| holdout_model_DeepSeek | main | net | 0.992 | 0.884 | 0.950 | 0.811 | 0.093 | зарегистрирован до прогона |
| holdout_model_DeepSeek | main+M02 | full | 0.995 | 0.930 | 0.967 | 0.915 | 0.033 | зарегистрирован до прогона |
| holdout_model_DeepSeek | negative-control | full | 0.415 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.004–0.929 |
| holdout_model_DeepSeek | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_model_NVIDIA | format-only | full | 0.993 | 0.957 | 0.979 | 0.974 | 0.017 | зарегистрирован до прогона |
| holdout_model_NVIDIA | genre-only | full | 0.665 | -0.484 | 0.333 | 0.665 | 1.000 | зарегистрирован до прогона |
| holdout_model_NVIDIA | length-only | full | 0.277 | -0.412 | 0.289 | 0.000 | 0.693 | зарегистрирован до прогона |
| holdout_model_NVIDIA | main | full | 0.985 | 0.888 | 0.947 | 0.859 | 0.055 | зарегистрирован до прогона |
| holdout_model_NVIDIA | main | net | 0.864 | 0.386 | 0.655 | 0.104 | 0.069 | зарегистрирован до прогона |
| holdout_model_NVIDIA | main+M02 | full | 0.988 | 0.897 | 0.951 | 0.859 | 0.050 | зарегистрирован до прогона |
| holdout_model_NVIDIA | negative-control | full | 0.132 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.003–0.971 |
| holdout_model_NVIDIA | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_model_OpenAI | format-only | full | 0.966 | 0.775 | 0.863 | 0.733 | 0.014 | зарегистрирован до прогона |
| holdout_model_OpenAI | genre-only | full | 0.667 | -0.483 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_model_OpenAI | length-only | full | 0.405 | -0.001 | 0.500 | 0.007 | 0.619 | зарегистрирован до прогона |
| holdout_model_OpenAI | main | full | 0.996 | 0.933 | 0.968 | 0.922 | 0.031 | зарегистрирован до прогона |
| holdout_model_OpenAI | main | net | 0.993 | 0.881 | 0.947 | 0.852 | 0.083 | зарегистрирован до прогона |
| holdout_model_OpenAI | main+M02 | full | 0.996 | 0.942 | 0.973 | 0.948 | 0.029 | зарегистрирован до прогона |
| holdout_model_OpenAI | negative-control | full | 0.415 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.052–0.939 |
| holdout_model_OpenAI | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P1 | format-only | full | 0.990 | 0.934 | 0.965 | 0.939 | 0.010 | зарегистрирован до прогона |
| holdout_prompt_P1 | genre-only | full | 0.667 | -0.461 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P1 | length-only | full | 0.407 | -0.179 | 0.410 | 0.006 | 0.607 | зарегистрирован до прогона |
| holdout_prompt_P1 | main | full | 0.998 | 0.933 | 0.968 | 0.972 | 0.062 | зарегистрирован до прогона |
| holdout_prompt_P1 | main | net | 0.975 | 0.841 | 0.921 | 0.589 | 0.086 | зарегистрирован до прогона |
| holdout_prompt_P1 | main+M02 | full | 0.997 | 0.950 | 0.976 | 0.983 | 0.045 | зарегистрирован до прогона |
| holdout_prompt_P1 | negative-control | full | 0.362 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.002–0.879 |
| holdout_prompt_P1 | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P2 | format-only | full | 0.990 | 0.974 | 0.987 | 0.986 | 0.012 | зарегистрирован до прогона |
| holdout_prompt_P2 | genre-only | full | 0.667 | -0.461 | 0.333 | 0.667 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P2 | length-only | full | 0.394 | 0.334 | 0.623 | 0.000 | 0.726 | зарегистрирован до прогона |
| holdout_prompt_P2 | main | full | 0.999 | 0.931 | 0.967 | 1.000 | 0.067 | зарегистрирован до прогона |
| holdout_prompt_P2 | main | net | 0.985 | 0.868 | 0.935 | 0.692 | 0.107 | зарегистрирован до прогона |
| holdout_prompt_P2 | main+M02 | full | 0.999 | 0.935 | 0.969 | 0.983 | 0.062 | зарегистрирован до прогона |
| holdout_prompt_P2 | negative-control | full | 0.349 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.000–0.999 |
| holdout_prompt_P2 | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P3 | format-only | full | 0.923 | 0.735 | 0.848 | 0.705 | 0.014 | зарегистрирован до прогона |
| holdout_prompt_P3 | genre-only | full | 0.666 | -0.462 | 0.333 | 0.666 | 1.000 | зарегистрирован до прогона |
| holdout_prompt_P3 | length-only | full | 0.401 | -0.177 | 0.411 | 0.014 | 0.610 | зарегистрирован до прогона |
| holdout_prompt_P3 | main | full | 0.967 | 0.817 | 0.907 | 0.529 | 0.064 | зарегистрирован до прогона |
| holdout_prompt_P3 | main | net | 0.980 | 0.846 | 0.924 | 0.699 | 0.086 | зарегистрирован до прогона |
| holdout_prompt_P3 | main+M02 | full | 0.974 | 0.837 | 0.918 | 0.540 | 0.062 | зарегистрирован до прогона |
| holdout_prompt_P3 | negative-control | full | 0.422 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.006–0.964 |
| holdout_prompt_P3 | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |
| holdout_source | format-only | full | 0.998 | 0.981 | 0.990 | 0.987 | 0.006 | зарегистрирован до прогона |
| holdout_source | genre-only | full | 0.691 | -0.216 | 0.407 | 0.666 | 0.852 | зарегистрирован до прогона |
| holdout_source | length-only | full | 0.417 | 0.080 | 0.537 | 0.002 | 0.661 | зарегистрирован до прогона |
| holdout_source | main | full | 0.994 | 0.932 | 0.966 | 0.948 | 0.045 | зарегистрирован до прогона |
| holdout_source | main | net | 0.903 | 0.559 | 0.768 | 0.102 | 0.075 | зарегистрирован до прогона |
| holdout_source | main+M02 | full | 0.995 | 0.930 | 0.965 | 0.939 | 0.045 | зарегистрирован до прогона |
| holdout_source | negative-control | full | 0.230 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.001–0.998 |
| holdout_source | source-only | full | 0.500 | 0.000 | 0.500 | 0.000 | 1.000 | зарегистрирован до прогона |
| holdout_time | format-only | full | — | — | — | — | 0.018 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | genre-only | full | — | — | — | — | 0.855 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | length-only | full | — | — | — | — | 0.591 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | main | full | — | — | — | — | 0.051 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | main | net | — | — | — | — | 0.088 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | main+M02 | full | — | — | — | — | 0.051 | post hoc: тест одноклассовый, определим только FPR |
| holdout_time | source-only | full | — | — | — | — | 0.890 | post hoc: тест одноклассовый, определим только FPR |
| holdout_topic | format-only | full | 0.962 | 0.880 | 0.934 | 0.880 | 0.007 | зарегистрирован до прогона |
| holdout_topic | genre-only | full | 0.599 | -0.515 | 0.299 | 0.599 | 1.000 | зарегистрирован до прогона |
| holdout_topic | length-only | full | 0.462 | 0.128 | 0.559 | 0.014 | 0.643 | зарегистрирован до прогона |
| holdout_topic | main | full | 0.995 | 0.924 | 0.963 | 0.933 | 0.062 | зарегистрирован до прогона |
| holdout_topic | main | net | 0.981 | 0.851 | 0.927 | 0.649 | 0.086 | зарегистрирован до прогона |
| holdout_topic | main+M02 | full | 0.993 | 0.925 | 0.964 | 0.833 | 0.064 | зарегистрирован до прогона |
| holdout_topic | negative-control | full | 0.388 | — | — | — | — | post hoc: серия кластерных перестановок вместо одной; 20 перестановок, разброс 0.003–0.953 |
| holdout_topic | source-only | full | 1.000 | 0.000 | 0.500 | 1.000 | 1.000 | зарегистрирован до прогона |

**Как читается разрыв с baseline.** Если genre-only или source-only приближается к основной модели, вывод о происхождении не делается: результат публикуется как разделение жанров и источников.

## P2b: четыре внешних fold-а

| Estimand | Fold | Удержанный канал | AUROC | MCC | TPR@1%FPR | N теста |
|---|---|---|---|---|---|---|
| full | outer_fold_0 | gpt | 0.998 | 0.839 | 0.933 | 150 |
| full | outer_fold_1 | real_claude | 1.000 | 0.891 | 1.000 | 149 |
| full | outer_fold_2 | deepseek_pro | 0.997 | 0.932 | 0.944 | 150 |
| full | outer_fold_3 | nemotron | 0.998 | 0.929 | 0.978 | 148 |
| full | pooled_out_of_fold | все четыре | 0.990 | 0.892 | 0.814 | 597 |
| net | outer_fold_0 | gpt | 0.986 | 0.862 | 0.744 | 150 |
| net | outer_fold_1 | real_claude | 0.958 | 0.777 | 0.589 | 149 |
| net | outer_fold_2 | deepseek_pro | 0.994 | 0.917 | 0.833 | 150 |
| net | outer_fold_3 | nemotron | 0.919 | 0.553 | 0.311 | 148 |
| net | pooled_out_of_fold | все четыре | 0.945 | 0.765 | 0.411 | 597 |

**Ограничение публикуется всегда:** четыре машинных source-кластера дают слабую оценку межканальной обобщаемости.

## P2b: контрасты O1 внутри SEO

| Estimand | Контраст | Пар | Кластеров | Средняя разница вероятности | 95% CI | p wild cluster | p sign-flip |
|---|---|---|---|---|---|---|---|
| full | P3-P1 | 120 | 15 | -0.0765 | [-0.1237; -0.0354] | 0.0030 | 0.0030 |
| full | P2-P1 | 120 | 15 | 0.0806 | [0.0574; 0.1066] | 0.0002 | 0.0002 |
| net | P3-P1 | 120 | 15 | 0.0161 | [-0.0187; 0.0498] | 0.3786 | 0.3786 |
| net | P2-P1 | 120 | 15 | 0.0834 | [0.0462; 0.1202] | 0.0016 | 0.0016 |

**120 пар не равны 120 независимым наблюдениям:** это 15 кластеров-заданий. Сильные выводы только из обычного bootstrap не делаются.

Основная модель P2a посчитана на 18 holdout-разбиениях.
