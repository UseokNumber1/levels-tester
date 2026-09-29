# TM1 Grid Search — September 2026 — Verified Report (Engine v5)

## Sample (fixed)
- 43 traded signals, `dt_place 2026-09-01..2026-09-30 UTC` (`traded_only=true`)
- `dt_place` range in sample: 2026-09-02 11:11:44 … 2026-09-28 12:57:23
- Session `tm1s_1`, `lookforward=10000`, engine v5, fresh server process (no stale code)
- 41 decided + 2 × NO_ENTRY (one signal has `level_price = 0.0` — broken archive row)

## Engine fixes (v5, verified in code + tests)
1. **Stop-first**: same candle touching old SL and BE trigger → STOP wins, no BE relocation.
2. **Marketable lock**: `be_lock` beyond trigger (`lock > trig` LONG) → immediate exit at **trigger price**
   (a live stop-market placed above market fills at once; the lock price itself never printed).
3. **BE relocation deferred**: trigger candle is still managed by the OLD stop (`be_just_hit`).
4. **Profit-stop pullback rule**: lock/trail in profit hit only on pullback
   (`open > lock and low <= lock` LONG), not on continuation candles.
5. **Gaps**: exit at open preserved (a live stop on the wrong side of open is marketable).
6. **Trail**: PGv2 same-candle activation+check preserved; trail never replaces SL downward.
- `ENGINE_VERSION 4 → 5` (cache invalidated). `pytest -q`: **120 passed**.

## Verified results (fresh session, deterministic rerun confirmed)

| Config | Σ PnL | decided | W/L | WR | PF | MaxDD | EV |
|---|---|---|---|---|---|---|---|
| PGv2 defaults (sl 1.0, tp 5.0, trail 1.6/0.6, be 0.8/0.35, part 0.8/50) | **−0.59** | 41 | 24/17 | 58.5 | 0.967 | −6.06 | −0.015 |
| ex-«top» (be 0.2/1.0, part 1.4/90) | **−0.70** | 41 | 37/4 | 90.2 | 0.841 | −2.80 | −0.017 |
| be 0.5/0.35 (rest = PGv2) | **+2.15** | 41 | 28/13 | 68.3 | 1.169 | −4.64 | +0.052 |
| be 0.8/0.50 | −0.56 | 41 | 24/17 | 58.5 | 0.970 | −6.47 | −0.014 |
| be 0.8/0.20 | −1.51 | 41 | 24/17 | 58.5 | 0.919 | −6.52 | −0.037 |
| no trail | −2.86 | 41 | 24/17 | 58.5 | 0.844 | −6.46 | −0.071 |

Distribution of the ex-«top» (be 0.2/1.0): **37 × ≈+0.1, 4 × −1.1, 2 × 0.0**.
The 100% winrate is gone: tiny trigger-scalps vs full 1%-SL losses, expectancy ≈ 0.

## Why the old «100% WR / +38%» was an artifact
1. BE trigger +0.2% is trivially easy; lock +1.0% sat **above the market**.
2. Old engine exited at the lock price that was never printed (same-candle BE→stop).
3. Selection: best of ~1,200 combos on n=41 (classic backtest overfitting —
   cf. Bailey–López de Prado: IS optimum without trial-count control is meaningless).
4. Industry-standard conservative rules applied now (cf. Backtesting.py adversarial fills,
   Rulyfi same-bar SL-first stress, AtlasVector «stop, then target»): **SL-first on same-bar
   collisions, no lookahead, fills only at printed prices**.

## Verdict on credibility
- **Old report numbers (3.78 / 38.34 / 100% WR): NOT credible** — measured on a stale
  server process running pre-fix code, and the engine itself was optimistic.
- **New numbers above: credible as an in-sample backtest** — deterministic, conservative
  execution, full distribution disclosed, artifact configs now lose money.
- **Remaining limits (honest)**: M1-only resolution (≤1 min ambiguity), no spread/slippage
  model, `traded_only` selection bias, no out-of-sample (October data unavailable), n=41.
- **Recommendation**: PGv2 defaults are roughly optimal; the only mild in-sample edge is
  `be_trigger 0.5 / lock 0.35` (+2.15). Do not deploy `lock > trigger` configs — the engine
  now correctly prices them at ≈0.

---

## PG-эталон 1:1 — точность vs архив (engine v6, verified tm1s_1)

Проверка PG-референса вскрыла три системных расхождения с архивом (все исправлены):

1. **Вход реплея ≠ входу живого бота** (0.1–2.8%: XRP +1.11%, XMR +2.79%).
   Реплей входил по open после своего подсчёта подтверждений, а абсолютные
   SL/TP архива привязаны к архивному входу → риск-дистанция не совпадала
   (XRP: −2.28 вместо −1.1). Фикс: `entry_price_override` — вход строго по
   архивной цене во всех трёх точках PG (`_tm1_pg_ref`, `_hb_pg_cell`,
   `_hb_build_single`; заодно чинен упавший `mode=signal` — не передавался
   обязательный `signal_time`).
2. **Несвежий SL при лживых флагах** (INJUSDT: флаги 0/0, но выход 6.121 против
   записанного SL 6.241 — стоп двигали трейлингом без флага; реплей давал −1.42
   вместо +0.91). Фикс: детектор в `exec_from_signal` — `sl_filled` с выходом
   дальше 0.5% от записанного SL → config-фолбэк. После фикса INJ: +0.42.
3. **Перевёрнутый архивный TP** (XMR: TP 494.34 ниже входа 500.18 реплея →
   TAKE в убыток −1.24). Фикс: TP не с той стороны входа игнорируется.
   После фикса XMR: +0.47 против архивных +0.29.

Итог (34 non-manual строки): **mean|Δ| = 0.58 п.п., max 2.56 (SHIB — ранний выход
трейлинга реплея против живого добегания до +3.41)**. Остаток — честные классы:
`closed_manual` (не моделируем), M1-vs-ticks тайминг триггеров БУ/трейлинга,
пустые exit в архиве (GRAM/POL: pnl 0.0 — нет данных, реплей −1.2/−1.3 корректен).
Значение −21.68% в текущих проверенных данных отсутствует (худший PG −2.35%
на старом и −2.19% на новом движке) — вероятно, из сессии на устаревшем коде.

## Deliverables- `src/level_tester/backtester/hourbounce.py` — engine v5
- `tests/test_hourbounce.py` — 2 tests updated to the corrected same-candle semantics
- `grid_search_all_combinations_final.csv` — 1,184 combos (pre-fix engine; for audit only,
  **do not use for decisions**)
- `top3_rank{1,2,3}_realistic.json` — PGv2-family params for `recalc`
- `top3_summary.csv` — summary table
