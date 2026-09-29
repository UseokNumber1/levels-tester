# Задача: полный grid-search TM1 по проторгованным сигналам сентября, топ-3 настройки

## 1. Контекст
- Проект `levels-tester`, отчёт TM1: `web/report_tm1.html` + `src/level_tester/api/app.py:2422-3093`.
- Движок: `src/level_tester/backtester/hourbounce.py:review_signal(entry_code="T1M", tf="1m")` + `tm1_exec()`. Вход — маркет на касании по M1, исполнение строго на M1. Комиссии из `config/default.yaml:hourbounce` (maker 0.02 / taker 0.05). Порядок в свече: БУ → частичка → активация трейлинга (строго после БУ) → подтяжка (порог 0.1%) → стоп → фикс-TP.
- Дефолты = живой PGv2 (`TRADE_SETTINGS_2026-09-28.md`): SL 1.0, TP 5.0, trail 1.6/0.6, БУ 0.8/0.35, частичка 0.8/50.

## 2. Выборка (фиксированная)
- Только проторгованные из архива за сентябрь 2026: `dt_place 2026-09-01..2026-09-30 UTC`, `traded_only=true` (`arch_status closed_*`, `_hb_archive_trades`).
- Взять через `GET /api/hourbounce/signals?traded_only=true&date_from=2026-09-01&date_to=2026-09-30&limit=5000`.
- M1-окна один раз: `POST /api/hourbounce/report-tm1/prepare {signal_ids, lookforward:10000}` → `session_id`. Все переборы только через `recalc` / `_tm1_build_rows` / `heatmap` по кешу сессии. Зафиксируй `signal_ids, count, min/max dt_place`.

## 3. Сетка перебора (строго по ползункам TM1, `report_tm1.html:162-171 PSPEC`)
Перебирать все 8 параметров с шагом `0.1` в полных пределах ползунков:
- `sl_pct`: 0.05..3.0 (старт 0.1, далее 0.2..3.0)
- `tp_pct`: 0.1..5.0 + отдельное значение `None` (группа `tp` выкл)
- `trail_activation_pct`: 0.1..5.0 (группа `trail`; пара вкл/выкл целиком)
- `trail_distance_pct`: 0.1..5.0 (группа `trail`)
- `be_trigger_pct`: 0.1..5.0 (группа `be`)
- `be_lock_pct`: 0.0..1.0 (0.0..0.9 с шагом 0.1 + 1.0)
- `partial_trigger_pct`: 0.1..5.0 (группа `part`; пара вкл/выкл целиком)
- `partial_close_pct`: 1.0..90.0 с шагом 0.1 (ползунок UI идёт шагом 1, но API `HbTm1RecalcRequest` принимает любые float — считать с шагом 0.1)
Выключенные группы (`tp/trail/be/part` = `None`) тоже протестировать как отдельные точки. Нормализация пар как в `_tm1_normalize_params`.

## 4. Как считать (полный декарт невозможен — разбей)
Полный декарт (~30×50×50×50×50×10×50×890) не считается. Лимит `heatmap` — 400 ячеек на запрос. Делай так:
1. `prepare` один раз, дальше только `recalc`.
2. Этап A — 1D-sweep каждого параметра (остальные = база), шаг 0.1.
3. Этап B — 2D-heatmap связок через `/api/hourbounce/report-tm1/heatmap`: `(be_trigger × be_lock)`, `(trail_activation × trail_distance)`, `(sl × tp)`, `(partial_trigger × partial_close)`, шаг 0.1 (дробить на под-сетки ≤400 ячеек).
4. Этап C — координатный спуск / перебор топ-кандидатов этапов A+B между собой до сходимости.
5. Этап D — финальный `recalc` топ-3 на том же `session_id`.

## 5. Ранжирование
- Основная метрика: `kpi.total` (Σ net-PnL%, `_tm1_kpi`).
- Для каждого топ-кандидата показать: `total, count/decided/wins/losses, winrate, avg/avg_win/avg_loss, expectancy, pf, max_dd, outcomes/exits, n_partial/n_be`, equity-кривую.
- Проверка устойчивости: соседи ±0.1 по каждому параметру не должны ронять результат.

## 6. Результат
1. Таблица ТОП-3: `место | Σ PnL | WR/PF/maxDD/EV | sl/tp/trail_act/trail_dist/be_trig/be_lock/part_trig/part_close (с пометками выкл)`.
2. Готовые JSON `params` для `recalc` (проверены повторным прогоном).
3. CSV всех просчитанных комбинаций: `params..., total, winrate, pf, max_dd, expectancy` + список `signal_ids` выборки.
4. Вывод: что дало прирост vs база, какой механизм ключевой.

## 7. Запреты и проверка
- Не менять движок, комиссии, порядок исполнения. Детерминизм на закрытых M1, без заглядывания вправо.
- `pytest -q`, `ruff check src tests`. Топ-1 воспроизвести вручную через UI TM1 и подтвердить совпадение `total`.
