# Перенос inner-разбиений подбора C в P2a

Собрано 2026-07-29T12:16:29+00:00 скриптом `09-tools/p2a_inner_carry.py`.

`GroupKFold` раскладывает группы по размеру, поэтому исключение 34 документов могло сдвинуть источники между inner fold-ами подбора регуляризации. Прежнее соответствие зафиксировано и переносится в clf-v2; своё разбиение в серии v2 не строится.

| Holdout | Групп в train | Сменили inner fold | Исчезли из train | Документов ушло |
|---|---|---|---|---|
| holdout_author | 21 | 7 | 0 | 6 |
| holdout_genre_analytics | 25 | 10 | 0 | 6 |
| holdout_genre_commercial | 25 | 11 | 0 | 6 |
| holdout_genre_news | 25 | 8 | 0 | 6 |
| holdout_genre_prose | 22 | 6 | 0 | 6 |
| holdout_genre_science | 25 | 8 | 0 | 6 |
| holdout_genre_seo | 7 | 0 | 0 | 0 |
| holdout_genre_translation | 25 | 8 | 0 | 6 |
| holdout_model_Anthropic | 24 | 5 | 0 | 6 |
| holdout_model_DeepSeek | 24 | 5 | 0 | 6 |
| holdout_model_NVIDIA | 24 | 5 | 0 | 6 |
| holdout_model_OpenAI | 24 | 5 | 0 | 6 |
| holdout_prompt_P1 | 25 | 11 | 0 | 6 |
| holdout_prompt_P2 | 25 | 11 | 0 | 6 |
| holdout_prompt_P3 | 25 | 10 | 0 | 6 |
| holdout_source | 18 | 6 | 0 | 5 |
| holdout_time | 23 | 5 | 0 | 1 |
| holdout_topic | 25 | 10 | 0 | 6 |

## Группы, сменившие inner fold при пересборке

**holdout_author:** `convertmonster`, `madcats_ru`, `seoprofy_ua`, `seopulses_ru`, `sosnovskij_ru`, `author_archive`, `texterra_ru`;
**holdout_genre_analytics:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_genre_commercial:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`, `seopulses`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_genre_news:** `drmax_su`, `labrika`, `optimizatorsha_ru`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_genre_prose:** `labrika`, `madcats_ru`, `optimizatorsha_ru`, `seopulses`, `seopulses_ru`, `shakin_ru`;
**holdout_genre_science:** `drmax_su`, `labrika`, `optimizatorsha_ru`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_genre_translation:** `drmax_su`, `labrika`, `optimizatorsha_ru`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_model_Anthropic:** `drmax_su`, `shakin`, `sosnovskij_ru`, `author_archive`, `texterra_ru`;
**holdout_model_DeepSeek:** `drmax_su`, `shakin`, `sosnovskij_ru`, `author_archive`, `texterra_ru`;
**holdout_model_NVIDIA:** `drmax_su`, `shakin`, `sosnovskij_ru`, `author_archive`, `texterra_ru`;
**holdout_model_OpenAI:** `drmax_su`, `shakin`, `sosnovskij_ru`, `author_archive`, `texterra_ru`;
**holdout_prompt_P1:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`, `seopulses`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_prompt_P2:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`, `seopulses`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_prompt_P3:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;
**holdout_source:** `convertmonster`, `labrika`, `seoprofy_ua`, `shakin_ru`, `sosnovskij_ru`, `texterra_ru`;
**holdout_time:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`;
**holdout_topic:** `convertmonster`, `drmax_su`, `labrika`, `madcats_ru`, `seoprofy_ua`, `seopulses_ru`, `shakin`, `shakin_ru`, `sosnovskij_ru`, `author_archive`;

**Пересборка сдвинула бы 131 назначений — перенос обязателен.**

Файл читает `clf_run --series clf-v2`. Диагностика «сменился ли выбранный C» считается в самом прогоне: рядом с рабочим значением записывается `c_rebuilt` — значение, которое дало бы разбиение, построенное заново.
