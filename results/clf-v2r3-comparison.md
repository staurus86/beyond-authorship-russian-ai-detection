# P2a: два источника изменения, разведённые по прогонам

Собрано скриптом `09-tools/clf_v2_compare.py`. Основание — `02-preregistration/amendment-clf-v2-inner-cv.md`.

Сравнивается основная модель `main/full`. Ни один вариант не выбирается по результату: `clf-v2-valid` назначен основным до расчёта, `clf-v2-legacy` остаётся sensitivity.

## Коррекция корпуса: clf-v1 → clf-v2-legacy

Алгоритм тот же, что в замороженном прогоне, включая пропуск семи одноклассовых validation. Различие даёт только очистка корпуса: исключены 34 документа, у 68 изменился текст.

Holdout сопоставлено: 18. Выбранный C сменился у **3**: `holdout_genre_prose`, `holdout_prompt_P1`, `holdout_prompt_P2`.

| Метрика | Средний сдвиг по модулю | Максимальный сдвиг | Где максимум |
|---|---|---|---|
| AUROC | 0.005 | +0.034 | `holdout_genre_seo` |
| MCC | 0.028 | +0.091 | `holdout_genre_seo` |
| Balanced acc. | 0.012 | +0.038 | `holdout_genre_seo` |
| TPR@1%FPR | 0.093 | +0.878 | `holdout_genre_seo` |
| FPR | 0.020 | -0.077 | `holdout_genre_seo` |
| FPR hard-human | 0.020 | -0.061 | `holdout_genre_seo` |

### По каждому holdout

| Holdout | C | ΔAUROC | ΔMCC | ΔFPR | ΔFPR hard-human |
|---|---|---|---|---|---|
| `holdout_author` | 0.1 | +0.004 | +0.012 | -0.012 | -0.014 |
| `holdout_genre_analytics` | 1.0 | +0.004 | +0.024 | -0.022 | -0.022 |
| `holdout_genre_commercial` | 1.0 | +0.001 | +0.017 | -0.018 | -0.018 |
| `holdout_genre_news` | 0.1 | — | — | -0.014 | -0.014 |
| `holdout_genre_prose` | **10.0 → 1.0** | — | — | -0.003 | -0.004 |
| `holdout_genre_science` | 0.1 | — | — | -0.014 | -0.014 |
| `holdout_genre_seo` | 0.01 | +0.034 | +0.091 | -0.077 | -0.061 |
| `holdout_genre_translation` | 0.1 | — | — | -0.014 | -0.014 |
| `holdout_model_Anthropic` | 10.0 | +0.000 | +0.038 | -0.031 | -0.031 |
| `holdout_model_DeepSeek` | 0.01 | +0.002 | +0.007 | -0.005 | -0.005 |
| `holdout_model_NVIDIA` | 0.1 | +0.005 | +0.027 | -0.019 | -0.019 |
| `holdout_model_OpenAI` | 0.1 | +0.002 | +0.017 | -0.005 | -0.005 |
| `holdout_prompt_P1` | **1.0 → 10.0** | +0.002 | +0.031 | -0.029 | -0.029 |
| `holdout_prompt_P2` | **1.0 → 10.0** | +0.001 | +0.033 | -0.031 | -0.031 |
| `holdout_prompt_P3` | 10.0 | +0.009 | +0.025 | -0.021 | -0.021 |
| `holdout_source` | 1.0 | +0.002 | +0.020 | -0.019 | -0.022 |
| `holdout_time` | 0.1 | — | — | -0.012 | -0.014 |
| `holdout_topic` | 1.0 | +0.003 | +0.020 | -0.019 | -0.019 |

## Исправление вложенного CV: clf-v2-legacy → clf-v2-valid

Корпус тот же. Различие даёт только схема inner-разбиения: у семи holdout validation стал двухклассовым, и подбор C идёт по всем заявленным fold-ам.

Holdout сопоставлено: 18. Выбранный C сменился у **2**: `holdout_genre_prose`, `holdout_genre_seo`.

| Метрика | Средний сдвиг по модулю | Максимальный сдвиг | Где максимум |
|---|---|---|---|
| AUROC | 0.004 | -0.050 | `holdout_genre_seo` |
| MCC | 0.018 | -0.229 | `holdout_genre_seo` |
| Balanced acc. | 0.009 | -0.113 | `holdout_genre_seo` |
| TPR@1%FPR | 0.067 | -0.869 | `holdout_genre_seo` |
| FPR | 0.013 | +0.238 | `holdout_genre_seo` |
| FPR hard-human | 0.010 | +0.181 | `holdout_genre_seo` |

### По каждому holdout

| Holdout | C | ΔAUROC | ΔMCC | ΔFPR | ΔFPR hard-human |
|---|---|---|---|---|---|
| `holdout_author` | 0.1 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_genre_analytics` | 1.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_genre_commercial` | 1.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_genre_news` | 0.1 | — | — | +0.000 | +0.000 |
| `holdout_genre_prose` | **1.0 → 0.01** | — | — | +0.005 | -0.005 |
| `holdout_genre_science` | 0.1 | — | — | +0.000 | +0.000 |
| `holdout_genre_seo` | **0.01 → 10.0** | -0.050 | -0.229 | +0.238 | +0.181 |
| `holdout_genre_translation` | 0.1 | — | — | +0.000 | +0.000 |
| `holdout_model_Anthropic` | 10.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_model_DeepSeek` | 0.01 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_model_NVIDIA` | 0.1 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_model_OpenAI` | 0.1 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_prompt_P1` | 10.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_prompt_P2` | 10.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_prompt_P3` | 10.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_source` | 1.0 | +0.000 | +0.000 | +0.000 | +0.000 |
| `holdout_time` | 0.1 | — | — | +0.000 | +0.000 |
| `holdout_topic` | 1.0 | +0.000 | +0.000 | +0.000 | +0.000 |

## Пропущенные inner fold-ы в схеме A

| Holdout | Заявлено | Использовано | Номера пропущенных |
|---|---|---|---|
| `holdout_genre_news` | 3 | 2 | 0 |
| `holdout_genre_prose` | 3 | 2 | 0 |
| `holdout_genre_science` | 3 | 2 | 0 |
| `holdout_genre_seo` | 3 | 2 | 0 |
| `holdout_genre_translation` | 3 | 2 | 0 |
| `holdout_source` | 3 | 2 | 2 |
| `holdout_time` | 3 | 2 | 0 |

В clf-v1 это же происходило молча: ни метрики, ни манифест, ни отчёт числа использованных fold-ов не содержали.

## Итог

Коррекция корпуса сменила выбранный C у 3 holdout, исправление inner CV — у 2.
