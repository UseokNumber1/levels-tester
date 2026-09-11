"""HourBounce Review Lite: детерминированный реплей отскока от часового уровня.

Правила — из tz/deepseek_markdown_20260910_956c04.md п.3:
- touch: первая M5 после dt_place с low<=level (BUY) / high>=level (SELL)
- confirm: ТОЛЬКО ПОДРЯД (как PGv2 confirmation_loop.py:962-966): направленная
  свеча с close за уровнем строго после касания — LONG: close>open AND close>level,
  SHORT: close<open AND close<level; doji и любая неподтверждающая свеча
  сбрасывают счётчик в 0 (confirmation_loop.py:924-929, 968)
- T1: вход по level, при гэпе — по open; T2/T3: open свечи после 1-й/2-й подтверждающей
- SL: % от входа; TP: трейлинг activate +1% / distance 1%
- порядок в свече: сначала стоп, затем активация; подтяжка с N+1; коллизия -> STOP+ambiguous

Режим SIGNAL (сделка PGv2 1:1): SL/TP/BE/trailing берутся из архивного сигнала
(ExecParams), порядок в свече — как в PGv2 trade_model.tick: BE → активация
трейлинга (строго после BE, если BE задан) → подтяжка (порог) → стоп → TP.
Фикс-парциал breakeven_fix_pct (размер позиции) в ценовом реплее не моделируется.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from level_tester.domain.models import Candle

if TYPE_CHECKING:
    from level_tester.backtester.signal_reader import SignalEntry

EntryCode = Literal["T1", "T2", "T3", "PG"]
Outcome = Literal["TAKE", "STOP", "NO_ENTRY", "EXPIRED"]


@dataclass(frozen=True, slots=True)
class HourBounceConfig:
    confirm_window_w: int = 20
    # None = без ограничения: ищем ближайшую фактическую отработку
    # после выставления уровня по всей загруженной истории
    life_window_t: int | None = None
    sl_sizes: tuple[Decimal, Decimal, Decimal] = (
        Decimal("0.5"),
        Decimal("1.0"),
        Decimal("1.5"),
    )
    tp_trail_activate_pct: Decimal = Decimal("1.0")
    tp_trail_distance_pct: Decimal = Decimal("1.0")
    pre_candles: int = 30
    post_candles: int = 30


DEFAULT_CONFIG = HourBounceConfig()


@dataclass(frozen=True, slots=True)
class ExecParams:
    """Параметры исполнения сделки.

    GRID: sl_pct из сетки (0.5/1.0/1.5), трейлинг 1%/1% без BE и фикс-TP.
    SIGNAL (1:1 PGv2): абсолютные SL/TP из архива + BE + трейлинг сигнала.
    """

    sl_price: Decimal | None = None
    sl_pct: Decimal | None = None
    fixed_tp: Decimal | None = None
    be_trigger_pct: Decimal | None = None  # None = без безубытка
    be_lock_pct: Decimal | None = None  # breakeven_profit_pct: стоп -> entry±lock
    trail_activation_pct: Decimal | None = None  # None = без трейлинга
    trail_distance_pct: Decimal | None = None
    trail_threshold_pct: Decimal | None = None  # None = 0.5 (дефолт PGv2 trade_model)
    trail_tp_only: bool = False
    sl_source: str = "grid"  # grid | archive | config (откуда взят SL — для честности сверки)

    @property
    def use_be(self) -> bool:
        return self.be_trigger_pct is not None and self.be_lock_pct is not None

    @property
    def use_trail(self) -> bool:
        return self.trail_activation_pct is not None and self.trail_distance_pct is not None


def grid_exec(sl_index: int, config: HourBounceConfig = DEFAULT_CONFIG) -> ExecParams:
    return ExecParams(
        sl_pct=config.sl_sizes[sl_index - 1],
        trail_activation_pct=config.tp_trail_activate_pct,
        trail_distance_pct=config.tp_trail_distance_pct,
        trail_threshold_pct=Decimal("0"),
    )


def _dec(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


# SL PGv2 по умолчанию (config.yaml: stop_loss_method=percent, stop_loss_pct=1.0).
# Нужен, когда архивный SL уже подвинут сделкой (BE/трейлинг) и начальный
# по нему не восстановить.
PGV2_CONFIG_SL_PCT = Decimal("1.0")


def exec_from_signal(sig: SignalEntry) -> ExecParams | None:
    """Параметры сделки PGv2 1:1 из архивного сигнала. None — нет usable SL.

    Важно: у закрытых сделок stop_loss в архиве уже может быть подвинут
    (sl_moved_to_breakeven / trailing_activated) — тогда это НЕ начальный SL,
    и начальный восстанавливаем по правилу конфига (stop_loss_pct).
    TP фиксируется при создании и в архиве всегда начальный.
    """
    sl: Decimal | None = None
    sl_how = ""
    mutated = sig.trailing_activated is True or sig.sl_moved_to_breakeven is True
    if not mutated:
        sl = _dec(sig.stop_loss)
        if sl is not None:
            sl_how = "archive"
    if sl is None:
        # SL в архиве уже подвинут сделкой (или отсутствует): начальный
        # восстанавливаем по правилу конфига PGv2 (stop_loss_method=percent).
        # Источник значения ('config'/'placed'/'signal') на начальный % не влияет.
        pct = _dec(sig.stop_loss_pct) or PGV2_CONFIG_SL_PCT
        if pct is not None and sig.entry_price:
            ep = Decimal(str(sig.entry_price))
            sl = ep * (Decimal(1) - pct / Decimal(100)) if _is_long(sig.side) else ep * (Decimal(1) + pct / Decimal(100))
            sl_how = "config"
    if sl is None:
        return None
    tp: Decimal | None = None
    if sig.take_profits:
        try:
            raw = _json.loads(sig.take_profits) if isinstance(sig.take_profits, str) else sig.take_profits
            if isinstance(raw, list) and raw:
                tp = _dec(raw[0])
        except Exception:
            tp = None
    be_t, be_p = _dec(sig.breakeven_trigger_pct), _dec(sig.breakeven_profit_pct)
    be = bool(sig.breakeven_enabled) and be_t is not None and be_p is not None
    tr_a, tr_d = _dec(sig.trailing_activation_pct), _dec(sig.trailing_stop_pct)
    tr_on = sig.trailing_stop_enabled is not False and tr_a is not None and tr_d is not None
    return ExecParams(
        sl_price=sl,
        fixed_tp=tp,
        be_trigger_pct=be_t if be else None,
        be_lock_pct=be_p if be else None,
        trail_activation_pct=tr_a if tr_on else None,
        trail_distance_pct=tr_d if tr_on else None,
        trail_threshold_pct=_dec(sig.trailing_update_threshold_pct),
        trail_tp_only=bool(sig.trailing_tp_only),
        sl_source=sl_how,
    )


@dataclass(slots=True)
class HbEvent:
    seq: int
    type: str  # touch | confirm1 | confirm2 | entry | breakeven | trail_on | take | tp | stop | expired | no_entry
    dt: datetime | None
    price: Decimal | None


@dataclass(slots=True)
class HourBounceResult:
    entry_code: EntryCode
    sl_index: int  # 1..3
    outcome: Outcome
    reason: str | None = None
    ambiguous: bool = False
    touch_dt: datetime | None = None
    touch_idx: int | None = None
    confirm_idx: list[int] = field(default_factory=list)
    confirm_dt: list[datetime] = field(default_factory=list)
    entry_dt: datetime | None = None
    entry_price: Decimal | None = None
    sl_price: Decimal | None = None
    be_price: Decimal | None = None  # стоп после переноса в БУ (для линии на графике)
    exit_dt: datetime | None = None
    exit_price: Decimal | None = None
    exit_kind: str | None = None  # sl | trail | tp
    r_multiple: Decimal | None = None
    max_profit_pct: Decimal | None = None
    mae_pct: Decimal | None = None
    mfe_pct: Decimal | None = None
    bars_in_trade: int = 0
    events: list[HbEvent] = field(default_factory=list)
    trail_path: list[dict] = field(default_factory=list)  # [{dt, trail}] iso dt


def _is_long(side: str) -> bool:
    return side.upper() in ("LONG", "BUY")


def review_signal(
    *,
    side: str,
    level_price: Decimal | float | str,
    signal_time: datetime,
    candles: list[Candle],
    entry_code: EntryCode = "T1",
    sl_index: int = 1,
    config: HourBounceConfig = DEFAULT_CONFIG,
    exec_params: ExecParams | None = None,  # None = сетка SL по sl_index
    pg_required: int | None = None,  # режим сделки PGv2: нужно consecutive, СЧИТАЯ свечу касания
) -> HourBounceResult:
    # PG-режим (entry_code "PG", как в confirmation_loop.py:875-989):
    # свеча касания сразу проверяется на in_direction и идёт в счётчик.
    # T-сетка (ТЗ): касание подтверждением не считается.
    pg_mode = entry_code == "PG"
    if pg_mode and pg_required is None:
        raise ValueError("PG mode requires pg_required")
    level = Decimal(str(level_price))
    is_long = _is_long(side)
    ex = exec_params if exec_params is not None else grid_exec(sl_index, config)

    # только свечи от signal_time; life_window_t=None = вся история
    window = [c for c in candles if c.open_time >= signal_time]
    if config.life_window_t:
        window = window[: config.life_window_t]
    if not window:
        return HourBounceResult(entry_code, sl_index, "NO_ENTRY", reason="no_data")

    # 1. touch
    touch_pos: int | None = None
    for i, c in enumerate(window):
        if is_long and c.low <= level:
            touch_pos = i
            break
        if not is_long and c.high >= level:
            touch_pos = i
            break
    if touch_pos is None:
        ev = [HbEvent(1, "no_entry", None, None)]
        return HourBounceResult(entry_code, sl_index, "NO_ENTRY", reason="no_touch", events=ev)

    touch = window[touch_pos]
    events: list[HbEvent] = [HbEvent(1, "touch", touch.open_time, level)]

    # 2. confirms: только для T2/T3, поиск со следующей свечи после касания.
    # Свеча касания подтверждением не считается. Для T1 поиск не ведётся вообще.
    # Строго по PGv2: подтверждающие идут ТОЛЬКО ПОДРЯД — направленная свеча
    # (LONG: close>open, SHORT: close<open) с close за уровнем; doji и любая
    # неподтверждающая свеча сбрасывают счётчик (окно confirm_window_w от касания).
    need = {"T1": 0, "T2": 1, "T3": 2, "PG": pg_required if pg_required is not None else 0}[entry_code]
    confirms: list[int] = []
    scan_from = touch_pos if pg_mode else touch_pos + 1
    if need > 0:
        run: list[int] = []
        for i in range(scan_from, min(len(window), touch_pos + 1 + config.confirm_window_w)):
            c = window[i]
            if is_long:
                ok = c.close > c.open and c.close > level
            else:
                ok = c.close < c.open and c.close < level
            if ok:
                run.append(i)
                if len(run) >= need:
                    confirms = run[-need:]
                    break
            else:
                run = []
        for k, idx in enumerate(confirms, 1):
            cc = window[idx]
            events.append(HbEvent(2, f"confirm{k}", cc.open_time, cc.close))
    if len(confirms) < need:
        for k in range(len(confirms) + 1, need + 1):
            events.append(HbEvent(2, f"confirm{k}_missing", None, None))
        return HourBounceResult(
            entry_code, sl_index, "NO_ENTRY", reason="no_confirm",
            touch_dt=touch.open_time, touch_idx=touch_pos,
            confirm_idx=confirms, confirm_dt=[window[i].open_time for i in confirms],
            events=events,
        )

    # 3. entry
    if entry_code == "T1":
        # гэп: свеча касания открылась ЗА уровнем (цена прошла уровень между
        # свечами) — войти по уровню уже нельзя, вход по open:
        # LONG: open < level (открылись под поддержкой), SHORT: open > level.
        # Иначе маркет в момент касания ≈ уровень.
        gap = (touch.open < level) if is_long else (touch.open > level)
        entry_price = touch.open if gap else level
        entry_pos = touch_pos
        entry_dt = touch.open_time
    elif pg_mode and need == 0:
        # PG market-on-touch (required=0): касание + закрытие в направлении,
        # иначе первая такая свеча в окне; вход по open следующей
        qual: int | None = None
        for i in range(touch_pos, min(len(window), touch_pos + 1 + config.confirm_window_w)):
            c = window[i]
            ok = (c.close > c.open and c.close > level) if is_long else (c.close < c.open and c.close < level)
            if ok:
                qual = i
                break
        if qual is None:
            return HourBounceResult(
                entry_code, sl_index, "NO_ENTRY", reason="no_confirm",
                touch_dt=touch.open_time, touch_idx=touch_pos,
                confirm_idx=confirms, confirm_dt=[window[i].open_time for i in confirms],
                events=events,
            )
        confirms = [qual]
        events.append(HbEvent(2, "confirm1", window[qual].open_time, window[qual].close))
        entry_pos = qual + 1
        if entry_pos >= len(window):
            return HourBounceResult(
                entry_code, sl_index, "NO_ENTRY", reason="no_next_candle",
                touch_dt=touch.open_time, touch_idx=touch_pos,
                confirm_idx=confirms, confirm_dt=[window[i].open_time for i in confirms],
                events=events,
            )
        entry_price = window[entry_pos].open
        entry_dt = window[entry_pos].open_time
    else:
        last_confirm = confirms[need - 1]
        entry_pos = last_confirm + 1
        if entry_pos >= len(window):
            return HourBounceResult(
                entry_code, sl_index, "NO_ENTRY", reason="no_next_candle",
                touch_dt=touch.open_time, touch_idx=touch_pos,
                confirm_idx=confirms, confirm_dt=[window[i].open_time for i in confirms],
                events=events,
            )
        entry_price = window[entry_pos].open
        entry_dt = window[entry_pos].open_time
    if ex.sl_price is not None:
        sl_price = ex.sl_price
    elif ex.sl_pct is not None:
        sl_price = entry_price * (Decimal(1) - ex.sl_pct / Decimal(100)) if is_long else entry_price * (Decimal(1) + ex.sl_pct / Decimal(100))
    else:  # pragma: no cover - grid_exec/signal_exec всегда дают SL
        raise ValueError("ExecParams has no stop")
    events.append(HbEvent(3, "entry", entry_dt, entry_price))

    # 4. исполнение 1:1 PGv2 (trade_model.tick): BE → активация трейлинга
    # (строго после BE, если BE задан) → подтяжка (порог) → стоп → TP.
    be_trig = entry_price * (Decimal(1) + ex.be_trigger_pct / Decimal(100)) if (ex.use_be and is_long and ex.be_trigger_pct is not None) else (
        entry_price * (Decimal(1) - ex.be_trigger_pct / Decimal(100)) if (ex.use_be and ex.be_trigger_pct is not None) else None)
    be_lock = entry_price * (Decimal(1) + ex.be_lock_pct / Decimal(100)) if (ex.use_be and is_long and ex.be_lock_pct is not None) else (
        entry_price * (Decimal(1) - ex.be_lock_pct / Decimal(100)) if (ex.use_be and ex.be_lock_pct is not None) else None)
    act_price = entry_price * (Decimal(1) + ex.trail_activation_pct / Decimal(100)) if (ex.use_trail and is_long and ex.trail_activation_pct is not None) else (
        entry_price * (Decimal(1) - ex.trail_activation_pct / Decimal(100)) if (ex.use_trail and ex.trail_activation_pct is not None) else None)
    threshold = ex.trail_threshold_pct if ex.trail_threshold_pct is not None else Decimal("0.5")

    sl = sl_price
    be_active = False
    be_price: Decimal | None = None
    trail: Decimal | None = None
    extreme: Decimal | None = None
    last_upd: Decimal | None = None
    activated = False
    trail_path: list[dict] = []
    max_fav = Decimal(0)
    max_adv = Decimal(0)
    exit_outcome: Outcome = "EXPIRED"
    exit_kind: str | None = None
    exit_dt = window[-1].close_time
    exit_price = window[-1].close
    ambiguous = False

    for i in range(entry_pos, len(window)):
        c = window[i]
        # экстремумы для метрик
        if is_long:
            fav = (c.high - entry_price) / entry_price * Decimal(100)
            adv = (entry_price - c.low) / entry_price * Decimal(100)
        else:
            fav = (entry_price - c.low) / entry_price * Decimal(100)
            adv = (entry_price - c.high) / entry_price * Decimal(100)
        max_fav = max(max_fav, fav)
        max_adv = max(max_adv, adv)

        # гэп через стоп/трейлинг на open (консервативное дополнение реплея)
        gap_stop = (c.open <= sl) if is_long else (c.open >= sl)
        if i > entry_pos and gap_stop:
            exit_outcome, exit_kind, exit_dt, exit_price = "STOP", "sl", c.open_time, c.open
            events.append(HbEvent(4, "stop", c.open_time, c.open))
            break
        if activated and trail is not None:
            gap_trail = (c.open <= trail) if is_long else (c.open >= trail)
            if i > entry_pos and gap_trail:
                exit_outcome, exit_kind, exit_dt, exit_price = "TAKE", "trail", c.open_time, c.open
                events.append(HbEvent(4, "take", c.open_time, c.open))
                break

        be_was = be_active
        # 1. безубыток
        if ex.use_be and not be_active and be_trig is not None and be_lock is not None:
            hit_be = (c.high >= be_trig) if is_long else (c.low <= be_trig)
            if hit_be:
                if (is_long and be_lock > sl) or (not is_long and be_lock < sl):
                    sl = be_lock
                    be_price = sl
                be_active = True
                events.append(HbEvent(4, "breakeven", c.close_time, sl))
        # 2. активация трейлинга (строго после BE, если BE задан)
        if ex.use_trail and not activated and act_price is not None and (not ex.use_be or be_was):
            hit_act = (c.high >= act_price) if is_long else (c.low <= act_price)
            if hit_act:
                activated = True
                extreme = c.high if is_long else c.low
                last_upd = extreme
                trail = extreme * (Decimal(1) - ex.trail_distance_pct / Decimal(100)) if (is_long and ex.trail_distance_pct is not None) else (
                    extreme * (Decimal(1) + ex.trail_distance_pct / Decimal(100)) if ex.trail_distance_pct is not None else None)
                if trail is not None:
                    trail_path.append({"dt": c.close_time.isoformat(), "trail": str(trail)})
                    events.append(HbEvent(4, "trail_on", c.close_time, trail))
        # 3. подтяжка трейлинга (порог от последнего обновления, как в PGv2)
        elif ex.use_trail and activated and extreme is not None:
            ref = c.high if is_long else c.low
            better = (ref > extreme) if is_long else (ref < extreme)
            if better and last_upd is not None and last_upd > 0:
                move = abs(ref - last_upd) / last_upd * Decimal(100)
                if move >= threshold and ex.trail_distance_pct is not None:
                    extreme = ref
                    new_trail = extreme * (Decimal(1) - ex.trail_distance_pct / Decimal(100)) if is_long else extreme * (Decimal(1) + ex.trail_distance_pct / Decimal(100))
                    if trail is None or ((is_long and new_trail > trail) or (not is_long and new_trail < trail)):
                        trail = new_trail
                        last_upd = ref
                        trail_path.append({"dt": c.close_time.isoformat(), "trail": str(trail)})
        # 4-5. эффективный стоп (трейлинг вытесняет базу только в свою сторону)
        eff, from_trail = sl, False
        if activated and trail is not None and ((is_long and trail > eff) or (not is_long and trail < eff)):
            eff, from_trail = trail, True
        hit_stop = (c.low <= eff) if is_long else (c.high >= eff)
        check_tp = ex.fixed_tp is not None and not (ex.trail_tp_only and activated)
        tp_hit = False
        if check_tp and ex.fixed_tp is not None:
            tp_hit = (c.high >= ex.fixed_tp) if is_long else (c.low <= ex.fixed_tp)
        if hit_stop:
            if tp_hit:
                ambiguous = True
            if from_trail:
                exit_outcome, exit_kind, exit_dt, exit_price = "TAKE", "trail", c.close_time, eff
                events.append(HbEvent(4, "take", c.close_time, eff))
            else:
                exit_outcome, exit_kind, exit_dt, exit_price = "STOP", "sl", c.close_time, eff
                events.append(HbEvent(4, "stop", c.close_time, eff))
            break
        # 6. фикс-TP (после стопа; при trail_tp_only пропускается)
        if tp_hit and ex.fixed_tp is not None:
            exit_outcome, exit_kind, exit_dt, exit_price = "TAKE", "tp", c.close_time, ex.fixed_tp
            events.append(HbEvent(4, "tp", c.close_time, ex.fixed_tp))
            break

    if exit_outcome == "EXPIRED":
        events.append(HbEvent(4, "expired", exit_dt, exit_price))

    risk = abs(entry_price - sl_price)
    r_mult = ((exit_price - entry_price) / risk) if (is_long and risk) else ((entry_price - exit_price) / risk) if risk else Decimal(0)

    return HourBounceResult(
        entry_code=entry_code, sl_index=sl_index, outcome=exit_outcome,
        reason=None if exit_outcome in ("TAKE", "STOP") else "expired",
        ambiguous=ambiguous,
        touch_dt=touch.open_time, touch_idx=touch_pos, confirm_idx=confirms,
        confirm_dt=[window[i].open_time for i in confirms],
        entry_dt=entry_dt, entry_price=entry_price, sl_price=sl_price,
        be_price=be_price,
        exit_dt=exit_dt, exit_price=exit_price, exit_kind=exit_kind, r_multiple=r_mult,
        max_profit_pct=max_fav, mae_pct=-max_adv, mfe_pct=max_fav,
        bars_in_trade=max(0, len(window) - entry_pos),
        events=events, trail_path=trail_path,
    )


def review_matrix(
    *,
    side: str,
    level_price: Decimal | float | str,
    signal_time: datetime,
    candles: list[Candle],
    config: HourBounceConfig = DEFAULT_CONFIG,
) -> list[HourBounceResult]:
    out: list[HourBounceResult] = []
    for code in ("T1", "T2", "T3"):
        for sl in (1, 2, 3):
            out.append(review_signal(
                side=side, level_price=level_price, signal_time=signal_time,
                candles=candles, entry_code=code, sl_index=sl, config=config,  # type: ignore[arg-type]
            ))
    return out
