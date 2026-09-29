# Текущие настройки сделки (снимок 2026-09-28)

Источник: `config/config.yaml:42-87` + `backend/app/core/execution/sl_tp_utils.py:24-91` + `engine.py:11723-12145`.
Режим `binance.mode=real`, `auto_trading_enabled=false`.

## 1. Вход, сайзинг, комиссии

* Метод входа: `entry_method=confirmation`, `fixed_sl_tp_at_creation=true` — SL/TP считаются один раз при создании сигнала и замораживаются, при исполнении не пересчитываются.
* Плечо: `leverage=2`, `margin_type=ISOLATED`.
* Размер: `position_sizing_method=fixed`, `position_size_usd=25.0`.
* Риск-ограничение: `max_risk_per_trade=0.7` (% на сделку, для расчёта qty).
* Комиссии для расчёта: `taker_fee_pct=0.05`, `maker_fee_pct=0.02`.
* Резерв маржи: `margin_reserve_pct=10.0`.
* Фильтры: `max_open_trades=42`, `min_volume_24h_m=40.0`.
* Лимитка: `limit_offset_pct=0.2`, `price_offset_pct=0.001`.
* `allow_min_notional_override=true`.

Подтверждение входа `entry_confirmation:`:
`activation_distance_pct=2.0`, `bounce_mode=true`, `timeframe=1m`, `confirmation_bars_required=0`, `max_price_deviation_pct=0.4`, `max_wait_bars=20`, `timeout_days=60`, `timeout_action=cancel`, `cancel_on_adverse_pct=3.0`, `cancel_on_sl_distance=true`, `cancel_on_sl_fallback_pct=3.5`, `unsubscribe_distance_pct=2.5`, `http_fallback_max_pct=5.0`, `price_sanity_threshold_pct=50`.

Логика: касание entry + отскок → `entry_confirmed` → если отклонение от entry ≤0.4% — MARKET сразу, иначе LIMIT по уровню. Таймаут — `cancelled`.

## 2. Стоп и тейк (базовые)

* `stop_loss_method=percent`, `stop_loss_pct=1.0`:
  `LONG: SL=entry*(1-0.01)`, `SHORT: SL=entry*(1+0.01)`.
* `take_profit_method=rr_ratio`, `rr_ratio=5.0`, `take_profit_pct=3.0` (запасное, при `rr_ratio` не используется):
  `TP% = stop_loss_pct * rr_ratio = 1.0*5.0 = 5.0%`.
  `LONG: TP=entry*1.05`, `SHORT: TP=entry*0.95`.
* Итого голый RR: риск 1.0% движения цены = 1R, тейк 5.0% = 5R.
* SL/TP ставятся как биржевые алго-ордера `STOP_MARKET / TAKE_PROFIT_MARKET` с `closePosition=true` (без qty/reduceOnly).

## 3. Промежуточная фиксация + БУ (работают в связке)

Снапшот параметров в строку сигнала делается в момент выставления входного ордера, дальше сделку ведёт снапшот (`engine.py:999-1026`).

* `breakeven_enabled=true`
* `breakeven_trigger_pct=0.8` — цена прошла +0.8% от входа в сторону позиции.
* `breakeven_profit_pct=0.35` — куда едет SL: `LONG: entry*1.0035`, `SHORT: entry*0.9965`.
* `breakeven_fix_enabled=true`, `breakeven_fix_pct=50.0`.

Механика `engine.py:4802-4909`:
Ордер частичного фикса `TP_BE` ставится СРАЗУ при входе по цене триггера (+0.8% от entry) на 50% позиции. Висит idle.
При достижении +0.8%:
1. закрывается 50% по +0.8% = `0.8R * 0.5 = 0.40R` зафиксировано;
2. SL остатка переставляется в +0.35%.
Итог выхода «по БУ» (если дальше разворот и задевает новый SL): `0.40R + 0.35R*0.5 = 0.40 + 0.175 = 0.575R` гросс, без комиссий. Статус закрытия `closed_be_filled`.

## 4. Трейлинг (строго после БУ)

* `trailing_stop_enabled=true`, `trailing_tp_only=false`
* `trailing_activation_pct=1.6`
* `trailing_stop_pct=0.6` (дистанция)
* `trailing_update_threshold_pct=0.1` (минимальный шаг цены для перестановки)

Логика `engine.py:11742-11757,12145-12187`:
Если БУ включён — трейлинг активируется только после `sl_moved_to_breakeven=true`. Если БУ выключен — независимо.
При `pct_from_entry >= 1.6%`: `trailing_activated=true`.
Новый SL: `LONG: price*(1-0.006)`, `SHORT: price*(1+0.006)`, только в сторону прибыли, только если цена ушла от последней перестановки ≥0.1%.
Минимальный замок на активации: `1.6 - 0.6 = 1.0% = 1.0R`, дальше растёт за ценой. Статус закрытия `closed_trailing_tp_filled`.

Порядок исходов по росту цены:
`0% вход → +0.8% BE-фикс 50% + SL→+0.35% → +1.6% трейлинг вкл (SL≈+1.0%) → +5.0% полный TP`.

Финальные статусы: `closed_tp`, `closed_sl`, `closed_be_filled`, `closed_trailing_tp_filled`, `closed_manual`, `cancelled / cancelled_by_competition`, `error / position_missing / execution_failed`.

## 5. Винрейт / факты (для матожидания)

Живая БД `data/db/trading.db` сейчас: только `blacklisted=97, low_quality=147, entry_waiting_confirmation=9` — открытых/закрытых сигналов в ней нет (всё в архиве).

Архив `data/db/signal_archive.db:signal_archive`:
`cancelled=271`, `cancelled_by_competition=1`, `new=112`, `error=20`, `position_missing=4`,
`closed_sl=27 (avg pnl% -0.38)`, `closed_sl_filled=13 (avg -1.62)`,
`closed_tp=9 (avg +0.10)`, `closed_tp_filled=2 (avg +2.86)`,
`closed_be_filled=23 (avg +0.19%, sum +0.74)`,
`closed_trailing_tp_filled=24 (avg +1.17%, sum +7.69)`,
`closed_manual=19 (avg +0.03%)`.

Разрешённых с позицией: 98 (`23+19+40+11+24`).
Потери: 40 (27+13). Победы TP+Trailing: 35 (11+24).
Если БУ считать победой: побед 58, винрейт `58/98 = 59.2%`.
Если БУ считать scratch (0.575R, не полный TP): чистый винрейт `35/98 = 35.7%`, scratch `23/98=23.5%`, лосс `40/98=40.8%`.
ML: `ml_daily_stats 2026-09-28 bayes_v1 n_train=98, winrate_base=NULL`, `ml_outcomes=0` — готового винрейта модели нет.

Для EV бери: `p_sl≈0.408, p_be≈0.235 (pay +0.575R), p_trail≈0.245 (pay ≥1.0R, факт avg +1.17% цены ≈ +1.17R), p_tp≈0.112 (pay 5R)`, минус `2*taker 0.05%` на круг + проскальзывание.
