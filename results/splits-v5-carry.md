# Шлюз 2: перенос разбиений на prep-v5

Собрано 2026-07-29T10:07:25+00:00 скриптом `09-tools/splits_v5_carry.py`.

Новые разбиения не строились. Взято прежнее соответствие `document_id → fold` от 2026-07-25, из него вычтены исключённые после коррекции ID; состав источников не перераспределялся. Прежние манифесты не перезаписаны, новые лежат в `07-analysis/splits-v5/`.

| Holdout | Train | Test | Убрано из train | Убрано из test | Train A/H | Test A/H |
|---|---|---|---|---|---|---|
| holdout_author | 1118 | 764 | 6 | 28 | 810/308 | 269/495 |
| holdout_genre_analytics | 1131 | 751 | 6 | 28 | 720/411 | 359/392 |
| holdout_genre_commercial | 1130 | 752 | 6 | 28 | 719/411 | 360/392 |
| holdout_genre_news | 1490 | 392 | 6 | 28 | 1079/411 | 0/392 |
| holdout_genre_prose | 1310 | 572 | 6 | 28 | 1079/231 | 0/572 |
| holdout_genre_science | 1490 | 392 | 6 | 28 | 1079/411 | 0/392 |
| holdout_genre_seo | 899 | 983 | 0 | 34 | 719/180 | 360/623 |
| holdout_genre_translation | 1490 | 392 | 6 | 28 | 1079/411 | 0/392 |
| holdout_model_Anthropic | 1220 | 662 | 6 | 28 | 809/411 | 270/392 |
| holdout_model_DeepSeek | 1220 | 662 | 6 | 28 | 809/411 | 270/392 |
| holdout_model_NVIDIA | 1221 | 661 | 6 | 28 | 810/411 | 269/392 |
| holdout_model_OpenAI | 1220 | 662 | 6 | 28 | 809/411 | 270/392 |
| holdout_prompt_P1 | 1130 | 752 | 6 | 28 | 719/411 | 360/392 |
| holdout_prompt_P2 | 1130 | 752 | 6 | 28 | 719/411 | 360/392 |
| holdout_prompt_P3 | 1131 | 751 | 6 | 28 | 720/411 | 359/392 |
| holdout_source | 879 | 1003 | 5 | 29 | 540/339 | 539/464 |
| holdout_time | 1424 | 458 | 1 | 33 | 1079/345 | 0/458 |
| holdout_topic | 1131 | 751 | 6 | 28 | 720/411 | 359/392 |

## Проверки

- обучающая часть с одним классом: нет;
- тестовая часть с одним классом: 5 — holdout_genre_news, holdout_genre_prose, holdout_genre_science, holdout_genre_translation, holdout_time. Как и в прежнем прогоне, на таких срезах определим только FPR;
- ни один оставшийся документ не сменил сторону разбиения: проверено включением множеств;
- список удалённых ID по каждому holdout: `splits-v5-dropped-ids.csv`, строк 612.
