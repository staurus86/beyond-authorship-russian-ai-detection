# Схема B: двухклассовые inner fold-ы подбора C в P2a

Собрано 2026-07-29T13:19:36+00:00 скриптом `09-tools/p2a_inner_valid.py`. Правило зафиксировано амендментом `amendment-clf-v2-inner-cv.md` до построения.

Перестроено разбиений: 7 из 18. У остальных перенесённое из clf-v1 разбиение уже двухклассово и не менялось.

| Holdout | Перестроено | Fold-ов | Причина |
|---|---|---|---|
| `holdout_author` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_genre_analytics` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_genre_commercial` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_genre_news` | да | 3 | три fold-а возможны |
| `holdout_genre_prose` | да | 3 | три fold-а возможны |
| `holdout_genre_science` | да | 3 | три fold-а возможны |
| `holdout_genre_seo` | да | 3 | три fold-а возможны |
| `holdout_genre_translation` | да | 3 | три fold-а возможны |
| `holdout_model_Anthropic` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_model_DeepSeek` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_model_NVIDIA` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_model_OpenAI` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_prompt_P1` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_prompt_P2` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_prompt_P3` | нет | 3 | перенесённое разбиение двухклассово |
| `holdout_source` | да | 2 | групп A 2, групп H 16: больше 2 двухклассовых fold-ов не построить |
| `holdout_time` | да | 3 | три fold-а возможны |
| `holdout_topic` | нет | 3 | перенесённое разбиение двухклассово |

## Состав validation у перестроенных разбиений

| Holdout | Fold | Документов A | Документов H | Групп A | Групп H |
|---|---|---|---|---|---|
| `holdout_genre_news` | 0 | 539 | 150 | 2 | 7 |
| `holdout_genre_news` | 1 | 270 | 138 | 1 | 7 |
| `holdout_genre_news` | 2 | 270 | 123 | 1 | 7 |
| `holdout_genre_prose` | 0 | 539 | 90 | 2 | 6 |
| `holdout_genre_prose` | 1 | 270 | 78 | 1 | 6 |
| `holdout_genre_prose` | 2 | 270 | 63 | 1 | 6 |
| `holdout_genre_science` | 0 | 539 | 150 | 2 | 7 |
| `holdout_genre_science` | 1 | 270 | 138 | 1 | 7 |
| `holdout_genre_science` | 2 | 270 | 123 | 1 | 7 |
| `holdout_genre_seo` | 0 | 359 | 60 | 2 | 1 |
| `holdout_genre_seo` | 1 | 180 | 60 | 1 | 1 |
| `holdout_genre_seo` | 2 | 180 | 60 | 1 | 1 |
| `holdout_genre_translation` | 0 | 539 | 150 | 2 | 7 |
| `holdout_genre_translation` | 1 | 270 | 138 | 1 | 7 |
| `holdout_genre_translation` | 2 | 270 | 123 | 1 | 7 |
| `holdout_source` | 0 | 270 | 194 | 1 | 8 |
| `holdout_source` | 1 | 270 | 145 | 1 | 8 |
| `holdout_time` | 0 | 539 | 125 | 2 | 7 |
| `holdout_time` | 1 | 270 | 119 | 1 | 6 |
| `holdout_time` | 2 | 270 | 101 | 1 | 6 |

Ни один validation не остался одноклассовым. Состав посчитан до расчёта метрик: правило смотрит на класс и размер группы, а не на результат модели.
