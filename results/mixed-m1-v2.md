# Модель M1 на серии v2: контрасты режима задания

Собрано 2026-07-29T18:07:52+00:00 скриптом `09-tools/mixed_m1_fit.py`. Подгонка разрешена выводом `mixed-identifiability.md`; порядок задан `analysis-closure.md` §6.4.

Отклик — операционализированный индекс стиля серии v2. Наблюдений 1079, заданий 45, метод REML, сходимость: **не достигнута**.

> **Оптимизатор не сошёлся. Числа ниже не являются оценками модели и в выводы не идут.** По правилу `analysis-closure.md` §1 отказ сходимости фиксируется как отказ: модель не упрощается, спецификация по результату не подбирается. Таблица приведена как часть диагностики.

| Терм | Оценка | 95% CI | p |
|---|---|---|---|
| `C(prompt)[T.P2]` | +6.7598 | [+4.1513; +9.3683] | 0.0000 |
| `C(prompt)[T.P3]` | -4.1572 | [-6.8178; -1.4965] | 0.0022 |

| Оптимизатор | Сходимость |
|---|---|
| bfgs | нет |
| lbfgs | нет |
| cg | нет |

Цепочка BFGS → L-BFGS → CG зафиксирована PI 2026-07-29 как единственный разрешённый численный fallback. Принимается первый сошедшийся результат, а не лучший по коэффициентам.

Дисперсия свободного члена задания — 5.6478, остаточная — 52.4365.

**`wrapper_version` закодирован вложенно в канал.** Вне `gpt` версия обёртки определена каналом: у `deepseek_pro` и `nemotron` всегда `none`, у `real_claude` всегда `unknown`. Поэтому фактор давал ровно одну лишнюю степень свободы при любом выборе базового уровня. Внутри `gpt` базовым взят алфавитно первый уровень; столбцовое пространство то же, что после удаления одного вырожденного индикатора, наблюдения не выбрасываются.

Модель не упрощалась и по результату не подбиралась. Спецификация взята из `preregistration.md` §11 без изменений.

## Диагностика оптимизатора

- bfgs: ConvergenceWarning: Gradient optimization failed, |grad| = 35.258107;
- bfgs: ConvergenceWarning: Maximum Likelihood optimization failed to converge. Check mle_retvals;
- bfgs: ConvergenceWarning: MixedLM optimization failed, trying a different optimizer may help.;
- bfgs: ConvergenceWarning: The Hessian matrix at the estimated parameter values is not positive definite.;
- bfgs: ConvergenceWarning: The MLE may be on the boundary of the parameter space.;
- lbfgs: ConvergenceWarning: Gradient optimization failed, |grad| = 15.623280;
- lbfgs: ConvergenceWarning: Maximum Likelihood optimization failed to converge. Check mle_retvals;
- lbfgs: ConvergenceWarning: MixedLM optimization failed, trying a different optimizer may help.;
- cg: ConvergenceWarning: Gradient optimization failed, |grad| = 4.472461;
- cg: ConvergenceWarning: Maximum Likelihood optimization failed to converge. Check mle_retvals;
- cg: ConvergenceWarning: MixedLM optimization failed, trying a different optimizer may help.;
