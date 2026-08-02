# Гайд перевода рукописи на английский

Перевод замороженной рукописи `manuscript-final.md` для arXiv (cs.CL) и
Cambridge Natural Language Processing. Русский оригинал — единственный источник
истины; перевод — новый артефакт со своими проверками.

## Жёсткие правила (нарушение = брак перевода)

1. **Числа, даты, хеши, URL, идентификаторы — байт в байт.** Каждое число
   оригинала обязано появиться в переводе ровно один раз в том же контексте.
   Проверяется скриптом; потеря или искажение числа бракует чанк целиком.
2. **Ничего не добавлять и не выбрасывать.** Ни поясняющих вставок, ни
   «улучшений» аргументации, ни новых хеджей, ни переходных фраз, которых нет
   в оригинале. Исследовательские утверждения не редактируются.
3. **Структура markdown сохраняется точно:** те же уровни заголовков, те же
   позиции таблиц, цитат `>`, списков, жирного. HTML-якоря
   `<!-- ТАБЛИЦА N: tab-... -->` копируются без изменений, включая слово
   ТАБЛИЦА — это машинные метки, не текст.
4. **Не переводить:** содержимое `код-спанов` и ``` блоков, имена файлов, пути,
   идентификаторы признаков (M01, R06, F01…), имена преобразований (t01…t16),
   имена серий (clf-v2-valid), профили prose/full, имена holdout
   (genre_seo…), названия моделей, prep-v4/prep-v5.
5. **Готовые английские формулировки копировать дословно.** Блоки-цитаты с
   «Публикуемая формулировка: …» содержат зафиксированный PI английский текст —
   он переносится без единой правки (вводные слова «Публикуемая формулировка»
   переводятся как «Published wording»). Верстальный блок ``` с титульным
   блоком копируется как есть.
6. **Цитирования:** «(Wang et al., 2024a)» — без изменений; «и» между двумя
   авторами меняется на «and»: (Borile и Abrate, 2025) → (Borile and Abrate,
   2025). (Грицай et al., 2023) → (Gritsay et al., 2023).

## Глоссарий (терминология единая по всей рукописи)

| Русский | English |
|---|---|
| условия производства текста | text production conditions |
| машинность | machine-likeness |
| оценка машинности | machine-likeness score / assessment of machine-likeness |
| операционализация | operationalisation |
| конфаундер | confounder |
| происхождение (текста) | origin |
| уровень регламентированности источника | source regimentation level |
| страта | stratum (мн. strata) |
| групповой holdout | group holdout |
| разбиение | split |
| признак | feature |
| матрица признаков | feature matrix |
| прогон | run |
| замороженный прогон | frozen run |
| шлюз | gate |
| допуск | tolerance |
| амендмент | amendment |
| предрегистрация | preregistration |
| знаменатель (стресс-теста) | denominator |
| преобразование | transformation |
| смена решения | decision flip |
| нестабильность | instability |
| доля ложных срабатываний | false positive rate (FPR) |
| модель-судья | judge model (процедура: LLM-as-judge) |
| индекс стиля | style index |
| вложенная валидация / вложенный CV | nested cross-validation |
| разбор ошибок | error analysis |
| markdown-разметка / вёрстка | markup / formatting |
| разметка (аннотация уровня) | annotation |
| препроцессинг | preprocessing |
| коррекция извлечения | extraction correction |
| ячейка | cell |
| пара «документ × допустимая модель» | document × eligible-model pair |
| режим задания (P1–P3) | task mode |
| задание | task |
| повтор (генерации) | replicate |
| канал генерации | generation channel |
| оболочка доступа | access wrapper |
| дублирование предложений | verbatim sentence duplication |
| разброс сходства | dispersion of similarity |
| переводная проза | translated prose |
| формальный регистр | formal register |
| ограничение N | limitation N |
| исключение (документов) | exclusion |
| реестр | registry |
| двойник (жанр как двойник класса) | proxy |
| цена ошибки | cost of error |
| обвинительный сценарий | accusatory scenario |

## Стиль

- **Британская орфография**, единообразно: operationalisation, normalised,
  labelled, behaviour, modelling, centre.
- **Голос оригинала сохраняется.** Русский текст прямой и утвердительный, без
  канцелярита. Перевод — meaning-for-meaning, не word-for-word: английский
  синтаксис естественный, но регистр тот же — сухой, точный, без украшений.
- **Запрещённая лексика** (AI-slop): delve, tapestry, vibrant, pivotal,
  paramount, nuanced, moreover, furthermore, embark, leverage (глагол),
  underscore (глагол), testament, realm, beacon, cornerstone, seamless,
  transformative, unprecedented, crucially, «It is important to note»,
  «In today's …». Слово robust допустимо только как статистический термин
  (robustness analysis, robust to X), не как украшение.
- **Не добавлять:** симметричные хеджи («on the one hand…»), трёхчленные
  перечисления ради ритма, педагогические подпорки, вежливые поклоны, вводные
  «Note that», «Importantly». Если в оригинале утверждение прямое — в переводе
  оно прямое.
- Ритм предложений — как в оригинале: короткие и длинные чередуются. Не
  выравнивать.
- Термины сообщества: machine-generated text detection, AI-generated text
  detection, LLM-as-judge, zero-shot NLL, holdout, cross-validation.

## Формат работы

Вход: файл `src/chunk-NN-<имя>.md`. Выход: файл `en/chunk-NN-<имя>.md` — тот же
markdown, переведённый по правилам выше. Никакого сопроводительного текста в
выходном файле: только перевод.
