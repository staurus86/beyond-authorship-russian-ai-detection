# Коррекция извлечения: что и почему изменено

Собрано 2026-07-29T09:53:40+00:00 скриптом `09-tools/correct_extraction.py`, правило `correction-v5.0`.

Правила зарегистрированы до расчёта — `02-preregistration/amendment-prep-v5-data-quality.md`. Исходные файлы не перезаписаны: корректированные тексты лежат в `04-corpus/derived/raw-v5/`.

Просмотрено документов корпуса: 1916. Кандидатов с долей повторов выше 0.1: 113. Скорректировано: 103.

> **Снимок состояния на момент коррекции.** Число 1916 — это состав корпуса до исключений, выполненных по её итогам. После коррекции 34 человеческих документа вышли из корпуса, потому что исправленный текст оказался короче порога в 700 слов (`news` 28, `seo` 6); решения записаны в `00-admin/exclusion-log.csv` стадией `коррекция извлечения correction-v5.0`. Аналитический корпус — 1882 документа, 1079 машинных и 803 человеческих; действующий источник численности — `04-corpus/documents-registry.csv`, поток данных — `08-paper/methods.md` §1.0. Отчёт не переписывается: он описывает саму коррекцию, а не итоговый состав.

## Вердикты происхождения

| Вердикт | Документов | Что сделано |
|---|---|---|
| `extraction-defect` | 75 | повторная экстракция исправленным парсером |
| `intermediary-defect` | 28 | детерминированное удаление точных каскадов |
| `unresolved` | 9 | **не редактируется**: происхождение не установлено |
| `source-property` | 1 | **не редактируется**: повтор есть в самом источнике |

## По источникам

| Источник | Кандидатов | Вердикты | Медиана удалённых слов |
|---|---|---|---|
| alaev | 3 | extraction-defect 3 | 196 |
| buriy_2014 | 5 | unresolved 5 | — |
| convertmonster | 1 | source-property 1 | — |
| devaka_ru | 15 | extraction-defect 15 | 338 |
| drmax | 21 | extraction-defect 21 | 838 |
| drmax_su | 6 | extraction-defect 6 | 696 |
| labrika | 2 | extraction-defect 2 | 570 |
| lenta | 28 | intermediary-defect 28 | 550 |
| madcats | 1 | extraction-defect 1 | 0 |
| madcats_ru | 1 | extraction-defect 1 | 108 |
| seoprofy_ua | 1 | extraction-defect 1 | 1505 |
| sosnovskij | 18 | extraction-defect 18 | 976 |
| sosnovskij_ru | 2 | extraction-defect 2 | 860 |
| spbgu | 3 | unresolved 3 | — |
| texterra_ru | 5 | extraction-defect 5 | 980 |
| urfu | 1 | unresolved 1 | — |

## Документы ниже порога после коррекции

Порог сбора — 700 слов. Ниже него оказались 34 документов; по §6 амендмента они подлежат исключению, решение фиксируется отдельно.

| Документ | Источник | Слов до | Слов после |
|---|---|---|---|
| `human_news_lenta_0025` | lenta | 761 | 245 |
| `human_news_lenta_0060` | lenta | 798 | 262 |
| `human_news_lenta_0026` | lenta | 735 | 263 |
| `human_news_lenta_0043` | lenta | 712 | 275 |
| `human_news_lenta_0033` | lenta | 770 | 282 |
| `human_news_lenta_0056` | lenta | 775 | 298 |
| `human_news_lenta_0032` | lenta | 818 | 301 |
| `human_news_lenta_0005` | lenta | 762 | 302 |
| `human_seo_texterra_ru_0016` | texterra_ru | 1196 | 306 |
| `human_news_lenta_0058` | lenta | 885 | 310 |
| `human_seo_texterra_ru_0013` | texterra_ru | 1186 | 317 |
| `human_news_lenta_0006` | lenta | 909 | 322 |
| `human_news_lenta_0028` | lenta | 945 | 322 |
| `human_news_lenta_0019` | lenta | 710 | 324 |
| `human_seo_texterra_ru_0017` | texterra_ru | 1307 | 327 |
| `human_news_lenta_0059` | lenta | 1001 | 338 |
| `human_news_lenta_0052` | lenta | 1004 | 343 |
| `human_news_lenta_0038` | lenta | 804 | 345 |
| `human_news_lenta_0050` | lenta | 912 | 349 |
| `human_news_lenta_0037` | lenta | 1048 | 357 |
| `human_news_lenta_0035` | lenta | 928 | 365 |
| `human_news_lenta_0020` | lenta | 912 | 394 |
| `human_news_lenta_0048` | lenta | 1293 | 394 |
| `human_news_lenta_0011` | lenta | 768 | 411 |
| `human_news_lenta_0047` | lenta | 1202 | 412 |
| `human_news_lenta_0008` | lenta | 859 | 414 |
| `human_news_lenta_0057` | lenta | 1150 | 420 |
| `human_seo_texterra_ru_0014` | texterra_ru | 1977 | 433 |
| `human_news_lenta_0054` | lenta | 1114 | 436 |
| `human_news_lenta_0031` | lenta | 1028 | 446 |
| `human_news_lenta_0030` | lenta | 724 | 489 |
| `human_seo_drmax_0008` | drmax | 877 | 510 |
| `human_seo_texterra_ru_0015` | texterra_ru | 3752 | 593 |
| `human_news_lenta_0003` | lenta | 1871 | 606 |
