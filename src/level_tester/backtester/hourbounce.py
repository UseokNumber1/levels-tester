"""HourBounce Review Lite: детерминированный реплей отскока от часового уровня.

Правила — из tz/deepseek_markdown_20260910_956c04.md п.3:
- touch: первая M5 после dt_place с low<=level (BUY) / high>=level (SELL)
- confirm: ТОЛЬКО ПОДРЯД (как PGv2 confirmation_loop.py:962-966): направленная
  свеча с close за уровнем строго после касания — LONG: close>open AND close>level,
  SHORT: close<open AND close<level; doji и любая неподтверждающая свеча
  сбрасывают счётчик в 0 (confirmation_loop.py:924-929, 968)
- T1: вход по level, при гэпе — по open; T2/T3: open свечи после 1-й/2-й подтверждающей
- T0: копия PGv2 req=0 (касание + направленное закрытие, вход по open следующей)
- T1L: лимитка уже стоит на уровне и заливается сразу в касание — момент
  заливки задаёт M1-минута касания, исполнение на свечах выбранного ТФ
  (при tf=5m host-M5 подменяется post-touch M1-диапазоном, при tf=1m хост
  и есть минута касания), вход всегда по level, touch-свеча учитывается
  только post-touch M1-диапазоном (без lookahead)
- T1M: маркет на касании — триггер первой M1 low<=level/high>=level, вход по принту
  касания (=level, при гэпе — по open минуты), поиск и исполнение полностью на M1
- SL: % от входа; TP: трейлинг activate +1% / distance 1%
- порядок в свече: сначала стоп, затем активация; подтяжка с N+1; коллизия -> STOP+ambiguous

Режим SIGNAL (сделка PGv2 1:1): SL/TP/BE/trailing/частичка берутся из
архивного сигнала (ExecParams; BE-fix маппится в partial по цене триггера БУ),
вход — по архивной цене (entry_price_override), иначе абсолютные SL/TP
архива висят на чужой базе. Перевёрнутый архивный TP (не с той стороны входа)
игнорируется; несвежий SL (sl_filled с выходом вдали от записанного SL при
пустых флагах) — config-фолбэк, как подвинутый.
порядок в свече v5 — гэп → стоп (предпочтительнее TP/БУ) → БУ → частичка
(фикс доли по триггеру, сделка продолжается) → активация трейлинга (строго
после БУ, если БУ задан) → подтяжка (порог) → стоп → TP.
Релокация БУ вступает в силу со следующей свечи (как подтяжка трейлинга);
лок вне рынка (за триггером) — выход по цене триггера: живой стоп-маркет
исполнился бы сразу по рынку. Стоп в зоне прибыли срабатывает только на
пуллбэке, гэп через стоп — сразу по open.
Ручные закрытия оператора (closed_manual) не моделируются: расхождение
модели с архивом по таким строкам ожидаемо, за ним не гонимся.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from level_tester.domain.models import Candle

if TYPE_CHECKING:
    from level_tester.backtester.signal_reader import SignalEntry

EntryCode = Literal["T0", "T1", "T2", "T3", "PG", "T1M", "T1L"]
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


def _load_grid_be_params() -> tuple[Decimal, Decimal]:
    """БУ-параметры унифицированного движка из config/default.yaml.

    Fallback — 0.8/0.35 (живой снапшот PGv2, TRADE_SETTINGS_2026-09-28).
    Требуется перезапуск сервера после правок файла.
    """
    import os
    from pathlib import Path

    fallback = (Decimal("0.8"), Decimal("0.35"))
    try:
        default_path = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
        cfg_path = Path(os.environ.get("LEVELS_TESTER_CONFIG", str(default_path)))
        if not cfg_path.is_file():
            return fallback
        import yaml

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        hb = raw.get("hourbounce", {}) or {}
        trigger = Decimal(str(hb.get("grid_be_trigger_pct", "0.8")))
        lock = Decimal(str(hb.get("grid_be_lock_pct", "0.35")))
        if trigger <= 0 or lock < 0:
            return fallback
        return trigger, lock
    except Exception:
        return fallback


GRID_BE_TRIGGER_PCT, GRID_BE_LOCK_PCT = _load_grid_be_params()


def _load_fee_params() -> tuple[Decimal, Decimal]:
    """Комиссии maker/taker из config/default.yaml (пользовательские %: 0.02 = 0.02%).

    Fallback — тарифы Binance USDT-M Futures для Regular/VIP0
    (maker 0.02%, taker 0.05%). Требуется перезапуск сервера после правок файла.
    """
    import os
    from pathlib import Path

    fallback = (Decimal("0.02"), Decimal("0.05"))
    try:
        default_path = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
        cfg_path = Path(os.environ.get("LEVELS_TESTER_CONFIG", str(default_path)))
        if not cfg_path.is_file():
            return fallback
        import yaml

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        hb = raw.get("hourbounce", {}) or {}
        maker = Decimal(str(hb.get("maker_fee_percent", "0.02")))
        taker = Decimal(str(hb.get("taker_fee_percent", "0.05")))
        if maker < 0 or taker < 0:
            return fallback
        return maker, taker
    except Exception:
        return fallback


MAKER_FEE_PCT, TAKER_FEE_PCT = _load_fee_params()

# Поддерживаемые ТФ сетки. Окна движка заданы в барах, но семантика —
# временная (база — M5): confirm_window_w=20 бар M5 = 100 мин.
# Для M1 количество баров масштабируется, чтобы покрывать то же время.
TF_STEPS_MIN = {"5m": 5, "1m": 1}
M5_WINDOW_MIN = 100  # confirm_window_w (20) * 5 мин

# Версия логики движка: входит в отпечаток кеша hb_reviews
# (config_fingerprint). Поднимать при любом изменении правил входа/исполнения,
# иначе старые ячейки будут отдаваться как свежие.
# v4: SIGNAL/PG маппит BE-fix частичку (взвешенный выход как в проде PGv2),
# tm1_exec порог трейлинга по умолчанию 0.1% (как trailing_update_threshold_pct).
# v5: политика v5 внутри свечи (стоп первый, релокация БУ со следующей свечи,
# пуллбэк-правило для профит-стопов, выход по триггеру при локе вне рынка).
# v6: эталон PG 1:1 — вход по архивной цене (entry_price_override), игнор
# перевёрнутого архивного TP, детектор несвежего SL (sl_filled с выходом
# вдали от записанного SL → config-фолбэк).
# v7: унифицированный live-движок — grid/grid_be собраны на tm1_exec
# с живыми дефолтами PGv2 (TP 5.0, trail 1.6/0.6 thr 0.1, BE 0.8/0.35,
# partial 0.8/50); base и +БУ различаются только флагом BE.
ENGINE_VERSION = 7


def validate_tf(tf: str) -> str:
    if tf not in TF_STEPS_MIN:
        raise ValueError(f"unsupported timeframe: {tf!r} (expected '5m' or '1m')")
    return tf


def step_for_tf(tf: str) -> int:
    """Шаг свечи в минутах."""
    return TF_STEPS_MIN[validate_tf(tf)]


def config_for_tf(tf: str) -> HourBounceConfig:
    """Конфиг движка под ТФ: окна — по времени, а не по барам.

    M5: confirm_window_w=20 (100 мин). M1: 100 бар = те же 100 мин.
    """
    import dataclasses

    validate_tf(tf)
    confirm_bars = M5_WINDOW_MIN // TF_STEPS_MIN[tf]
    if tf == "5m" and confirm_bars == DEFAULT_CONFIG.confirm_window_w:
        return DEFAULT_CONFIG
    return dataclasses.replace(DEFAULT_CONFIG, confirm_window_w=confirm_bars)


@dataclass(frozen=True, slots=True)
class ExecParams:
    """Параметры исполнения сделки.

    GRID (унифицированный live): sl_pct из сетки (0.5/1.0/1.5), TP 5.0,
    trail 1.6/0.6 thr 0.1, partial 0.8/50; grid без BE, grid_be с БУ 0.8/0.35.
    SIGNAL (1:1 PGv2): абсолютные SL/TP из архива + BE + трейлинг сигнала.
    TM1: кастомный набор (стоп/тейк/BE/трейлинг/частичная фиксация) на M1.
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
    sl_source: str = "grid"  # grid | archive | config | tm1 (откуда взят SL — для честности сверки)
    # Частичная фиксация (TM1): при достижении partial_trigger_pct от входа
    # закрывается partial_close_pct % позиции по цене триггера; остаток идёт
    # дальше с тем же SL/BE/трейлингом. Итоговый PnL — взвешенный.
    partial_trigger_pct: Decimal | None = None  # None = без частички
    partial_close_pct: Decimal | None = None  # % позиции, 0..100
    # Фикс-тейк TM1 в % от входа (конвертируется в цену после входа).
    # Отличие от fixed_tp (абсолютная цена из архива PGv2).
    tp_pct: Decimal | None = None  # None = без фикс-тейка

    @property
    def use_be(self) -> bool:
        return self.be_trigger_pct is not None and self.be_lock_pct is not None

    @property
    def use_trail(self) -> bool:
        return self.trail_activation_pct is not None and self.trail_distance_pct is not None

    @property
    def use_partial(self) -> bool:
        return (
            self.partial_trigger_pct is not None
            and self.partial_close_pct is not None
            and self.partial_trigger_pct > 0
            and self.partial_close_pct > 0
            and self.partial_close_pct < 100
        )


# Унифицированный live-набор (TRADE_SETTINGS_2026-09-28 + TM1-дефолты UI):
# SL задаёт колонка, остальное фиксировано. Базовые колонки — BE выкл,
# колонки +БУ — BE вкл; всё остальное (TP/трейлинг/частичка/порог/комиссии/
# порядок v5 в свече) идентично. Это один движок, а не два.
LIVE_TP_PCT = Decimal("5.0")
LIVE_TRAIL_ACTIVATION_PCT = Decimal("1.6")
LIVE_TRAIL_DISTANCE_PCT = Decimal("0.6")
LIVE_TRAIL_THRESHOLD_PCT = Decimal("0.1")
LIVE_PARTIAL_TRIGGER_PCT = Decimal("0.8")
LIVE_PARTIAL_CLOSE_PCT = Decimal("50.0")


def grid_exec(sl_index: int, config: HourBounceConfig = DEFAULT_CONFIG) -> ExecParams:
    """Базовая колонка унифицированного отчёта: live-набор без БУ.

    SL — из сетки колонки, TP 5.0 / trail 1.6/0.6 thr 0.1 / partial 0.8/50.
    От +БУ отличается только выключенным BE (входной параметр, не движок).
    """
    import dataclasses as _dc

    return _dc.replace(
        tm1_exec(
            sl_pct=config.sl_sizes[sl_index - 1],
            tp_pct=LIVE_TP_PCT,
            be_trigger_pct=None,
            be_lock_pct=GRID_BE_LOCK_PCT,
            trail_activation_pct=LIVE_TRAIL_ACTIVATION_PCT,
            trail_distance_pct=LIVE_TRAIL_DISTANCE_PCT,
            trail_threshold_pct=LIVE_TRAIL_THRESHOLD_PCT,
            partial_trigger_pct=LIVE_PARTIAL_TRIGGER_PCT,
            partial_close_pct=LIVE_PARTIAL_CLOSE_PCT,
        ),
        sl_source="grid",
    )


def grid_be_exec(sl_index: int, config: HourBounceConfig = DEFAULT_CONFIG) -> ExecParams:
    """Колонка +БУ унифицированного отчёта: live-набор с БУ из конфига.

    Трейлинг включается строго после БУ (гейт в движке, порядок как в
    проде PGv2) — см. review_signal: `(not ex.use_be or be_was)`.
    """
    import dataclasses as _dc

    return _dc.replace(
        tm1_exec(
            sl_pct=config.sl_sizes[sl_index - 1],
            tp_pct=LIVE_TP_PCT,
            be_trigger_pct=GRID_BE_TRIGGER_PCT,
            be_lock_pct=GRID_BE_LOCK_PCT,
            trail_activation_pct=LIVE_TRAIL_ACTIVATION_PCT,
            trail_distance_pct=LIVE_TRAIL_DISTANCE_PCT,
            trail_threshold_pct=LIVE_TRAIL_THRESHOLD_PCT,
            partial_trigger_pct=LIVE_PARTIAL_TRIGGER_PCT,
            partial_close_pct=LIVE_PARTIAL_CLOSE_PCT,
        ),
        sl_source="grid_be",
    )


def tm1_exec(
    *,
    sl_pct: Decimal | float | str,
    tp_pct: Decimal | float | str | None = None,
    be_trigger_pct: Decimal | float | str | None = None,
    be_lock_pct: Decimal | float | str | None = None,
    trail_activation_pct: Decimal | float | str | None = None,
    trail_distance_pct: Decimal | float | str | None = None,
    trail_threshold_pct: Decimal | float | str | None = Decimal("0.1"),
    partial_trigger_pct: Decimal | float | str | None = None,
    partial_close_pct: Decimal | float | str | None = None,
) -> ExecParams:
    """Кастомный набор TM1 (всё в % от входа, исполнение на M1).

    Пустые/нулевые значения = механизм выключен. Валидация диапазонов —
    на стороне API; здесь только нормализация в Decimal/None.
    Порог перестановки трейлинга по умолчанию 0.1% — как в проде PGv2
    (trailing_update_threshold_pct); 0/None = подтяжка на каждом тике.
    """
    def _pct(v: object) -> Decimal | None:
        d = _dec(v)
        if d is None or d <= 0:
            return None
        return d

    thr = _dec(trail_threshold_pct)
    if thr is None or thr < 0:
        thr = Decimal("0.1")
    return ExecParams(
        sl_pct=_pct(sl_pct),
        tp_pct=_pct(tp_pct),
        be_trigger_pct=_pct(be_trigger_pct),
        be_lock_pct=_dec(be_lock_pct),
        trail_activation_pct=_pct(trail_activation_pct),
        trail_distance_pct=_pct(trail_distance_pct),
        trail_threshold_pct=thr,
        partial_trigger_pct=_pct(partial_trigger_pct),
        partial_close_pct=_dec(partial_close_pct),
        sl_source="tm1",
    )


def _dec(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class PositionLevels:
    """Ценовые уровни позиции, посчитанные один раз от цены входа.

    be_trig/be_lock/act_price/tp_price/partial_trig — абсолютные цены;
    partial_frac — доля позиции 0..1. None = механизм выключен.
    """

    be_trig: Decimal | None = None
    be_lock: Decimal | None = None
    act_price: Decimal | None = None
    tp_price: Decimal | None = None
    partial_trig: Decimal | None = None
    partial_frac: Decimal | None = None


def _position_levels(entry_price: Decimal, is_long: bool, ex: ExecParams) -> PositionLevels:
    """Уровни позиции от цены входа. Чистая функция — одно место расчёта."""
    if is_long:
        be_trig = entry_price * (Decimal(1) + ex.be_trigger_pct / Decimal(100)) if (ex.use_be and ex.be_trigger_pct is not None) else None
        be_lock = entry_price * (Decimal(1) + ex.be_lock_pct / Decimal(100)) if (ex.use_be and ex.be_lock_pct is not None) else None
        act_price = entry_price * (Decimal(1) + ex.trail_activation_pct / Decimal(100)) if (ex.use_trail and ex.trail_activation_pct is not None) else None
        partial_trig = entry_price * (Decimal(1) + ex.partial_trigger_pct / Decimal(100)) if (ex.use_partial and ex.partial_trigger_pct is not None) else None
    else:
        be_trig = entry_price * (Decimal(1) - ex.be_trigger_pct / Decimal(100)) if (ex.use_be and ex.be_trigger_pct is not None) else None
        be_lock = entry_price * (Decimal(1) - ex.be_lock_pct / Decimal(100)) if (ex.use_be and ex.be_lock_pct is not None) else None
        act_price = entry_price * (Decimal(1) - ex.trail_activation_pct / Decimal(100)) if (ex.use_trail and ex.trail_activation_pct is not None) else None
        partial_trig = entry_price * (Decimal(1) - ex.partial_trigger_pct / Decimal(100)) if (ex.use_partial and ex.partial_trigger_pct is not None) else None
    # Фикс-тейк TM1 (% от входа) + архивный абсолютный TP (SIGNAL). TM1-приоритет — tp_pct.
    # Перевёрнутый TP (ниже входа для LONG) — не цель, а несвежие данные: игнор.
    tp_price = ex.fixed_tp
    if tp_price is not None:
        if (is_long and tp_price <= entry_price) or (not is_long and tp_price >= entry_price):
            tp_price = None
    if ex.tp_pct is not None and ex.tp_pct > 0:
        tp_price = entry_price * (Decimal(1) + ex.tp_pct / Decimal(100)) if is_long else entry_price * (Decimal(1) - ex.tp_pct / Decimal(100))
    partial_frac = (
        ex.partial_close_pct / Decimal(100)
        if ex.use_partial and ex.partial_close_pct is not None
        else None
    )
    return PositionLevels(
        be_trig=be_trig, be_lock=be_lock, act_price=act_price,
        tp_price=tp_price, partial_trig=partial_trig, partial_frac=partial_frac,
    )


def _touched(is_long: bool, level: Decimal, c: Candle) -> bool:
    """Уровень задет в благоприятную сторону: LONG — high, SHORT — low.

    Единый предикат для триггера БУ, активации трейлинга и триггера частички.
    """
    return (c.high >= level) if is_long else (c.low <= level)


def _gapped(is_long: bool, level: Decimal, c: Candle) -> bool:
    """Гэп через уровень на open: LONG — open ниже/с're уровня, SHORT — выше/на нём.

    Стоп в зоне прибыли при гэпе вниз тоже исполняется сразу по open:
    живой стоп-маркет выше рынка маркетабелен.
    """
    return (c.open <= level) if is_long else (c.open >= level)


def _stop_hit(is_long: bool, eff: Decimal, entry_price: Decimal, c: Candle) -> bool:
    """Пробитие эффективного стопа внутри свечи (стоп предпочтительнее TP/БУ).

    Стоп в зоне убытка — достаточно low/high в неблагоприятную сторону.
    Стоп в зоне прибыли (БУ-лок/трейлинг выше входа для LONG) — только пуллбэк:
    свеча открылась ВЫШЕ стопа и спустилась к нему. Продолжение движения
    сквозь лок выходом не считается (цена такого стопа могла не печатать).
    """
    if is_long:
        if eff > entry_price:
            return c.open > eff and c.low <= eff
        return c.low <= eff
    if eff < entry_price:
        return c.open < eff and c.high >= eff
    return c.high >= eff


def _tp_hit(is_long: bool, tp_price: Decimal, c: Candle) -> bool:
    """Фикс-тейк напечатан: цена инструмента дошла до уровня."""
    return (c.high >= tp_price) if is_long else (c.low <= tp_price)


def _lock_beyond_market(is_long: bool, be_lock: Decimal, be_trig: Decimal) -> bool:
    """Лок БУ вне рынка относительно триггера: живой стоп-маркет исполнился бы
    сразу по рынку (цена лока могла не печататься)."""
    return (be_lock > be_trig) if is_long else (be_lock < be_trig)


# SL PGv2 по умолчанию (config.yaml: stop_loss_method=percent, stop_loss_pct=1.0).
# Нужен, когда архивный SL уже подвинут сделкой (BE/трейлинг) и начальный
# по нему не восстановить.
PGV2_CONFIG_SL_PCT = Decimal("1.0")


def exec_from_signal(sig: SignalEntry, close_reason: str | None = None,
                     exit_price: Decimal | float | str | None = None) -> ExecParams | None:
    """Параметры сделки PGv2 1:1 из архивного сигнала. None — нет usable SL.

    Важно: у закрытых сделок stop_loss в архиве уже может быть подвинут
    (sl_moved_to_breakeven / trailing_activated) — тогда это НЕ начальный SL,
    и начальный восстанавливаем по правилу конфига (stop_loss_pct).
    Флаги архива ненадёжны: детектор несвежего SL — закрытие `sl_filled` при
    выходе далеко от записанного SL (напр. INJUSDT: выход 6.121 против SL 6.241
    при флагах 0/0 — стоп двигали трейлингом, но флаг не выставили).
    TP фиксируется при создании и в архиве всегда начальный.
    Частичная фиксация BE (breakeven_fix_enabled/pct, engine.py:4802-4909):
    TP_BE-ордер на breakeven_fix_pct% позиции стоит с момента входа по цене
    триггера БУ — маппим в partial_trigger/partial_close, взвешенный выход
    движка повторяет механику продa (0.5×триггер + 0.5×остаток при fix 50%).
    """
    sl: Decimal | None = None
    sl_how = ""
    mutated = sig.trailing_activated is True or sig.sl_moved_to_breakeven is True
    if not mutated:
        sl = _dec(sig.stop_loss)
        if sl is not None:
            sl_how = "archive"
            if close_reason == "sl_filled" and exit_price is not None and sig.entry_price:
                # Выход по стопу обязан совпасть с записанным SL (плюс проскальзывание).
                # Расхождение >0.5% — записанный SL несвежий (двигали без флага).
                ex_p = _dec(exit_price)
                en_p = _dec(sig.entry_price)
                if (ex_p is not None and en_p is not None and en_p > 0
                        and abs(ex_p - sl) / en_p > Decimal("0.005")):
                    sl = None
                    sl_how = ""
                    mutated = True
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
    fix_pct = _dec(sig.breakeven_fix_pct)
    fix_on = (
        be
        and sig.breakeven_fix_enabled is not False
        and fix_pct is not None
        and fix_pct > 0
        and fix_pct < 100
    )
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
        partial_trigger_pct=be_t if fix_on else None,
        partial_close_pct=fix_pct if fix_on else None,
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
    gross_pnl_pct: Decimal | None = None  # PnL% без комиссий, со знаком стороны
    fee_pct: Decimal | None = None  # комиссии входа+выхода в % от входа
    net_pnl_pct: Decimal | None = None  # gross минус комиссии
    max_profit_pct: Decimal | None = None
    mae_pct: Decimal | None = None
    mfe_pct: Decimal | None = None
    bars_in_trade: int = 0
    events: list[HbEvent] = field(default_factory=list)
    trail_path: list[dict] = field(default_factory=list)  # [{dt, trail}] iso dt
    partial_dt: datetime | None = None
    partial_price: Decimal | None = None


def _is_long(side: str) -> bool:
    return side.upper() in ("LONG", "BUY")


def _entry_fee_pct(entry_code: EntryCode, entry_by_level: bool = False) -> Decimal:
    """Комиссия входа (пользовательские %): лимитка -> maker, маркет -> taker.

    T1L — заранее выставленная лимитка (заливка по level) -> maker.
    T1 без гэпа (вход по level) -> maker, T1 с гэпом (вход по open) -> taker.
    Остальные входы (T1M/T2/T3/PG/T0 — по open/принту) -> taker.
    """
    if entry_code == "T1L":
        return MAKER_FEE_PCT
    if entry_code == "T1" and entry_by_level:
        return MAKER_FEE_PCT
    return TAKER_FEE_PCT


def _exit_fee_pct(exit_kind: str | None) -> Decimal:
    """Комиссия выхода: TP (лимит) -> maker, SL/trail (стоп-маркет) -> taker."""
    if exit_kind == "tp":
        return MAKER_FEE_PCT
    return TAKER_FEE_PCT


def _pnl_net(
    *,
    is_long: bool,
    entry_price: Decimal,
    exit_price: Decimal,
    entry_fee_pct: Decimal,
    exit_fee_pct: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Возвращает (gross_pct, fee_pct, net_pct) со знаком стороны.

    Комиссии всегда уменьшают PnL: fee берётся от номинала каждой ноги
    (entry·fee_entry + exit·fee_exit) и нормируется на вход.
    """
    if is_long:
        gross = (exit_price - entry_price) / entry_price * Decimal(100)
    else:
        gross = (entry_price - exit_price) / entry_price * Decimal(100)
    fee = (entry_price * entry_fee_pct / Decimal(100)
           + exit_price * exit_fee_pct / Decimal(100)) / entry_price * Decimal(100)
    return gross, fee, gross - fee


def _refix_entry_code(
    res: HourBounceResult, new_code: EntryCode, is_long: bool,
) -> HourBounceResult:
    """Пересчитать комиссии под итоговый код входа после делегирования ветке T1.

    T1M — маркет на касании (вход всегда taker); T1L — лимитка по level
    (вход всегда maker, вкл. гэп-кейсы: заливка по принту касания).
    """
    res.entry_code = new_code  # type: ignore[assignment]
    if res.entry_price and res.exit_price and res.entry_price > 0:
        gross, fee, net = _pnl_net(
            is_long=is_long, entry_price=res.entry_price, exit_price=res.exit_price,
            entry_fee_pct=_entry_fee_pct(new_code, new_code == "T1L"),
            exit_fee_pct=_exit_fee_pct(res.exit_kind),
        )
        res.gross_pnl_pct, res.fee_pct, res.net_pnl_pct = gross, fee, net
    return res


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
    m1_candles: list[Candle] | None = None,  # минутные свечи того же периода; обязательны для T1M/T1L
    slippage_pct: Decimal | float | str | int = 0,  # проскальзывание входа против трейдера, %
    tf: str = "5m",  # ТФ основного ряда candles; T1L исполняется на нём
    entry_price_override: Decimal | float | str | None = None,  # эталон 1:1 — архивная цена входа
) -> HourBounceResult:
    # PG-режим (entry_code "PG", как в confirmation_loop.py:875-989):
    # свеча касания сразу проверяется на in_direction и идёт в счётчик.
    # T-сетка (ТЗ): касание подтверждением не считается.
    pg_mode = entry_code == "PG"
    if pg_mode and pg_required is None:
        raise ValueError("PG mode requires pg_required")
    validate_tf(tf)
    level = Decimal(str(level_price))
    if level <= 0:
        # Битый уровень из архива (напр. entry_price=0.0): считать нечего.
        # Без этого T1L подменял open на 0 через _clip_post_touch и падал
        # с DivisionUndefined, роняя всю матрицу/строку отчёта.
        return HourBounceResult(entry_code, sl_index, "NO_ENTRY", reason="bad_level")
    is_long = _is_long(side)
    ex = exec_params if exec_params is not None else grid_exec(sl_index, config)
    slip = _slip_pct(slippage_pct)

    # только свечи от signal_time; life_window_t=None = вся история
    window = [c for c in candles if c.open_time >= signal_time]
    if config.life_window_t:
        window = window[: config.life_window_t]
    if not window:
        return HourBounceResult(entry_code, sl_index, "NO_ENTRY", reason="no_data")

    # T1M/T1L — отдельные ветки (T1 при этом не меняется)
    if entry_code in ("T1M", "T1L"):
        return _review_t1x(
            entry_code=entry_code, sl_index=sl_index, side=side, level=level,
            is_long=is_long, ex=ex, slip=slip, signal_time=signal_time,
            window=window, m1_candles=m1_candles, config=config,
            window_full=candles, tf=tf,
        )

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
    # T0 обрабатывается ниже отдельным PG-0 блоком (qual-поиск с touch_pos).
    # Строго по PGv2: подтверждающие идут ТОЛЬКО ПОДРЯД — направленная свеча
    # (LONG: close>open, SHORT: close<open) с close за уровнем; doji и любая
    # неподтверждающая свеча сбрасывают счётчик (окно confirm_window_w от касания).
    need = {"T0": 0, "T1": 0, "T2": 1, "T3": 2, "T1M": 0, "T1L": 0, "PG": pg_required if pg_required is not None else 0}[entry_code]
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
    entry_by_level = False  # True = вход по level (лимитка, maker), иначе по open/принту (taker)
    if entry_code == "T1":
        # гэп: свеча касания открылась ЗА уровнем (цена прошла уровень между
        # свечами) — войти по уровню уже нельзя, вход по open:
        # LONG: open < level (открылись под поддержкой), SHORT: open > level.
        # Иначе маркет в момент касания ≈ уровень.
        gap = (touch.open < level) if is_long else (touch.open > level)
        entry_price = touch.open if gap else level
        entry_by_level = not gap
        if slip:
            entry_price = _with_slip(entry_price, is_long, slip)
        entry_pos = touch_pos
        entry_dt = touch.open_time
    elif (pg_mode and need == 0) or entry_code == "T0":
        # PG market-on-touch (required=0) / T0: касание + закрытие в направлении,
        # иначе первая такая свеча в окне; вход по open следующей.
        # T0 — абсолютная копия PG req=0, но с сеточным SL (для PnL-отчёта).
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
    if pg_mode and entry_price_override is not None:
        # Эталон 1:1: живой бот вошёл по архивной цене — используем её вместо
        # open следующей свечи. Иначе абсолютные SL/TP архива висят на чужой
        # базе входа и риск-дистанция не совпадает с живой (напр. XRP: вход
        # реплея +1.11% хуже архивного → SL −2.28% вместо −1.1%).
        try:
            _ov = Decimal(str(entry_price_override))
        except Exception:
            _ov = None
        if _ov is not None and _ov > 0:
            entry_price = _ov
    if ex.sl_price is not None:
        sl_price = ex.sl_price
    elif ex.sl_pct is not None:
        sl_price = entry_price * (Decimal(1) - ex.sl_pct / Decimal(100)) if is_long else entry_price * (Decimal(1) + ex.sl_pct / Decimal(100))
    else:  # pragma: no cover - grid_exec/signal_exec всегда дают SL
        raise ValueError("ExecParams has no stop")
    events.append(HbEvent(3, "entry", entry_dt, entry_price))

    # 4. исполнение (политика v5, единый порядок в свече — см. _stop_hit):
    # гэп → стоп (предпочтительнее) → БУ → частичка → активация трейлинга
    # (строго после БУ, если БУ задан) → подтяжка (порог) → стоп → TP.
    # Релокация БУ вступает в силу со следующей свечи (как подтяжка трейлинга).
    # Лок вне рынка (lock за триггером) — выход по цене триггера: живой
    # стоп-маркет исполнился бы сразу по рынку.
    lv = _position_levels(entry_price, is_long, ex)
    be_trig, be_lock = lv.be_trig, lv.be_lock
    act_price, tp_price = lv.act_price, lv.tp_price
    partial_price_trig, partial_frac = lv.partial_trig, lv.partial_frac
    threshold = ex.trail_threshold_pct if ex.trail_threshold_pct is not None else Decimal("0.5")
    partial_done = False
    partial_dt = None
    partial_px: Decimal | None = None

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

        # гэп через стоп/трейлинг на open (консервативное дополнение реплея).
        if _gapped(is_long, sl, c):
            exit_outcome, exit_kind, exit_dt, exit_price = "STOP", "sl", c.open_time, c.open
            events.append(HbEvent(4, "stop", c.open_time, c.open))
            break
        if activated and trail is not None and _gapped(is_long, trail, c):
            exit_outcome, exit_kind, exit_dt, exit_price = "TAKE", "trail", c.open_time, c.open
            events.append(HbEvent(4, "take", c.open_time, c.open))
            break

        be_was = be_active
        be_just_hit = False
        check_tp = tp_price is not None and not (ex.trail_tp_only and activated)
        tp_hit = check_tp and tp_price is not None and _tp_hit(is_long, tp_price, c)
        # 1. безубыток
        if ex.use_be and not be_active and be_trig is not None and be_lock is not None:
            if _touched(is_long, be_trig, c):
                # Стоп предпочтительнее БУ: та же свеча задела и старый стоп —
                # выходим по стопу, релокации нет.
                if _stop_hit(is_long, sl, entry_price, c):
                    if tp_hit:
                        ambiguous = True
                    exit_outcome, exit_kind, exit_dt, exit_price = "STOP", "sl", c.close_time, sl
                    events.append(HbEvent(4, "stop", c.close_time, sl))
                    break
                relocated = (is_long and be_lock > sl) or (not is_long and be_lock < sl)
                if relocated:
                    sl = be_lock
                    be_price = sl
                be_active = True
                be_just_hit = True
                events.append(HbEvent(4, "breakeven", c.close_time, sl))
                if relocated and _lock_beyond_market(is_long, be_lock, be_trig):
                    if tp_hit:
                        ambiguous = True
                    exit_outcome, exit_kind, exit_dt, exit_price = "STOP", "sl", c.close_time, be_trig
                    events.append(HbEvent(4, "stop", c.close_time, be_trig))
                    break
        # 1b. частичная фиксация TM1 (только фиксация доли, сделка продолжается)
        if ex.use_partial and not partial_done and partial_price_trig is not None:
            if _touched(is_long, partial_price_trig, c):
                partial_done = True
                partial_dt = c.close_time
                partial_px = partial_price_trig
                events.append(HbEvent(4, "partial", c.close_time, partial_price_trig))
        # 2. активация трейлинга (строго после BE, если BE задан)
        if ex.use_trail and not activated and act_price is not None and (not ex.use_be or be_was):
            if _touched(is_long, act_price, c):
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
        # 4. эффективный стоп: если BE только что сработал — используем СТАРЫЙ стоп на этой свече
        #    (новая релокация вступает в силу со следующей, как и подтяжка трейлинга)
        eff, from_trail = sl, False
        if be_just_hit:
            eff = sl_price  # исходный SL
        elif activated and trail is not None and ((is_long and trail > eff) or (not is_long and trail < eff)):
            eff, from_trail = trail, True
        # 5. проверка стопа (приоритет над TP; стоп предпочтительнее).
        # TP пересчитываем здесь: trail_tp_only зависит от активации трейлинга
        # на этой же свече (ранний tp_hit выше — только для блока БУ).
        check_tp = tp_price is not None and not (ex.trail_tp_only and activated)
        tp_hit = check_tp and tp_price is not None and _tp_hit(is_long, tp_price, c)
        if _stop_hit(is_long, eff, entry_price, c):
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
        if tp_hit and tp_price is not None:
            exit_outcome, exit_kind, exit_dt, exit_price = "TAKE", "tp", c.close_time, tp_price
            events.append(HbEvent(4, "tp", c.close_time, tp_price))
            break

    if exit_outcome == "EXPIRED":
        events.append(HbEvent(4, "expired", exit_dt, exit_price))

    # Взвешенный выход с учётом частички: доля closed — по цене триггера,
    # остаток — по фактической цене выхода. Стоп/трейлинг/TP остатка не меняются.
    if partial_done and partial_px is not None and partial_frac is not None:
        exit_price = partial_frac * partial_px + (Decimal(1) - partial_frac) * exit_price

    risk = abs(entry_price - sl_price)
    r_mult = ((exit_price - entry_price) / risk) if (is_long and risk) else ((entry_price - exit_price) / risk) if risk else Decimal(0)

    entry_fee = _entry_fee_pct(entry_code, entry_by_level)
    gross_pct, fee_pct, net_pct = _pnl_net(
        is_long=is_long, entry_price=entry_price, exit_price=exit_price,
        entry_fee_pct=entry_fee, exit_fee_pct=_exit_fee_pct(exit_kind),
    )

    return HourBounceResult(
        entry_code=entry_code, sl_index=sl_index, outcome=exit_outcome,
        reason=None if exit_outcome in ("TAKE", "STOP") else "expired",
        ambiguous=ambiguous,
        touch_dt=touch.open_time, touch_idx=touch_pos, confirm_idx=confirms,
        confirm_dt=[window[i].open_time for i in confirms],
        entry_dt=entry_dt, entry_price=entry_price, sl_price=sl_price,
        be_price=be_price,
        exit_dt=exit_dt, exit_price=exit_price, exit_kind=exit_kind, r_multiple=r_mult,
        gross_pnl_pct=gross_pct, fee_pct=fee_pct, net_pnl_pct=net_pct,
        max_profit_pct=max_fav, mae_pct=-max_adv, mfe_pct=max_fav,
        bars_in_trade=max(0, len(window) - entry_pos),
        events=events, trail_path=trail_path,
        partial_dt=partial_dt, partial_price=partial_px,
    )


def _slip_pct(v: Decimal | float | str | int) -> Decimal:
    """Нормализация проскальзывания (%). Неположительное/мусор -> 0."""
    try:
        d = Decimal(str(v))
    except Exception:
        return Decimal(0)
    return d if d > 0 else Decimal(0)


def _with_slip(base: Decimal, is_long: bool, slip: Decimal) -> Decimal:
    """Цена входа с проскальзыванием против трейдера."""
    if slip <= 0:
        return base
    return base * (Decimal(1) + slip / Decimal(100)) if is_long else base * (Decimal(1) - slip / Decimal(100))


def _clip_post_touch(
    host: Candle, post: list[Candle], level: Decimal, is_long: bool,
    touch_open: Decimal | None = None,
) -> Candle | None:
    """M5-свеча касания, обрезанная post-touch M1-диапазоном (без lookahead).

    `post` — M1-минуты от минуты касания до конца host-свечи (минута касания
    целиком: внутриминутный порядок без тиков неразличим — остаточный допуск
    <= 1 минуты, раскрыт в отчёте). Условие: лимитка уже стоит на уровне и
    заливается сразу в касание, поэтому вход всегда по level:
    - касание до открытия минуты (гэп между минутами через уровень) — заливка
      по принту касания, open подменяется на level;
    - гэп M5 (host открылась за уровнем, возврат внутри свечи) — заливка по
      level при возврате, open подменяется на level.
    Возвращает None, если обрезка невозможна (нет post-диапазона).
    """
    if not post:
        return None
    ph = max(c.high for c in post)
    pl = min(c.low for c in post)
    if touch_open is not None and ((touch_open < level) if is_long else (touch_open > level)):
        no = level  # касание до открытия минуты — заливка по принту
    elif (host.open < level) if is_long else (host.open > level):
        no = level  # гэп M5 — заливка по level при возврате
    elif is_long:
        no = min(max(host.open, level), ph)
        if no < pl or no > ph:
            return None
    else:
        no = max(min(host.open, level), pl)
        if no < pl or no > ph:
            return None
    nc = min(max(host.close, pl), ph)
    return replace(host, open=no, high=max(ph, no, nc), low=min(pl, no, nc), close=nc)


def _review_t1x(
    *,
    entry_code: EntryCode,
    sl_index: int,
    side: str,
    level: Decimal,
    is_long: bool,
    ex: ExecParams,
    slip: Decimal,
    signal_time: datetime,
    window: list[Candle],
    m1_candles: list[Candle] | None,
    config: HourBounceConfig,
    window_full: list[Candle] | None = None,  # полный ряд основного ТФ (включая свечи до signal_time)
    tf: str = "5m",  # ТФ основного ряда: исполнение T1L идёт на нём
) -> HourBounceResult:
    """T1M/T1L через делегирование проверенной ветке T1 (сам T1 не меняется).

    T1M: поиск триггера и всё исполнение на M1 — первая минута low<=level /
    high>=level, вход по принту касания (=level, при гэпе — по open минуты).
    T1L: лимитка уже стоит на уровне и заливается сразу в касание — момент
    заливки задаёт M1-минута касания, исполнение идёт на свечах выбранного ТФ
    (tf): host-свеча основного ТФ, содержащая минуту касания, подменяется
    post-touch M1-диапазоном от минуты касания (при tf=1m хост и есть минута
    касания, клиппинг вырожденный), вход всегда по level; свечи основного ТФ
    до касания из исполнения исключаются (позиции тогда не было).
    """
    validate_tf(tf)
    new_code = "T1M" if entry_code == "T1M" else "T1L"
    if not m1_candles:
        return HourBounceResult(new_code, sl_index, "NO_ENTRY", reason="no_m1")
    m1w = [c for c in m1_candles if c.open_time >= signal_time]
    if not m1w:
        return HourBounceResult(new_code, sl_index, "NO_ENTRY", reason="no_m1")

    if entry_code == "T1M":
        cfg = config
        if config.life_window_t:
            cfg = replace(config, life_window_t=config.life_window_t * 5)
        res = review_signal(
            side=side, level_price=level, signal_time=signal_time, candles=m1w,
            entry_code="T1", sl_index=sl_index, config=cfg, exec_params=ex,
            slippage_pct=slip, tf="1m",
        )
        return _refix_entry_code(res, new_code, is_long)

    # T1L: лимитка стоит и заливается в касание — M1-минута касания задаёт
    # момент заливки; host ищется в свечах основного ТФ (при tf=5m это M5-свеча,
    # которая может открыться до signal_time — якорь watch_start режет окно,
    # но не факт касания; при tf=1m хост совпадает с минутой касания).
    # M1 нужна только для минуты касания и клиппинга.
    touch_min = next(
        (c for c in m1w if (c.low <= level if is_long else c.high >= level)), None)
    if touch_min is None:
        wintouch = next(
            (c for c in window if (c.low <= level if is_long else c.high >= level)), None)
        return HourBounceResult(
            new_code, sl_index, "NO_ENTRY",
            reason="no_m1_touch" if wintouch is not None else "no_touch",
            touch_dt=wintouch.open_time if wintouch else None,
            events=[HbEvent(1, "no_entry", None, None)],
        )
    winsrc = window_full if window_full else window
    host_idx = next(
        (i for i, c in enumerate(winsrc)
         if c.open_time <= touch_min.open_time < c.close_time), None)
    if host_idx is None:
        return HourBounceResult(new_code, sl_index, "NO_ENTRY", reason="no_host")
    host = winsrc[host_idx]
    post = [c for c in m1w if touch_min.open_time <= c.open_time < host.close_time]
    clipped = _clip_post_touch(host, post, level, is_long, touch_min.open)
    if clipped is None:
        return HourBounceResult(
            new_code, sl_index, "NO_ENTRY", reason="no_host",
            touch_dt=host.open_time,
            events=[HbEvent(1, "touch", host.open_time, level)],
        )
    # свечи до касания — до позиции, из исполнения исключаются
    exec_window = [clipped, *winsrc[host_idx + 1:]]
    res = review_signal(
        side=side, level_price=level,
        signal_time=min(signal_time, host.open_time), candles=exec_window,
        entry_code="T1", sl_index=sl_index, config=config, exec_params=ex,
        slippage_pct=slip, tf=tf,
    )
    return _refix_entry_code(res, new_code, is_long)


ExecMode = Literal["grid", "grid_be"]


def review_matrix(
    *,
    side: str,
    level_price: Decimal | float | str,
    signal_time: datetime,
    candles: list[Candle],
    config: HourBounceConfig = DEFAULT_CONFIG,
    exec_mode: ExecMode = "grid",
    m1_candles: list[Candle] | None = None,
    tf: str = "5m",  # ТФ основного ряда candles; T1L исполняется на нём
) -> list[HourBounceResult]:
    """Матрица 12 ячеек: T1M/T1L/T2/T3 × SL1/SL2/SL3 (унифицированный live-движок).

    T1 (вход по level на M5 с lookahead) из матрицы убран — вместо него T1M
    (маркет на касании, триггер и исполнение на M1) и T1L (заранее
    выставленная лимитка: заливка в касание по level, исполнение на свечах
    выбранного ТФ, без lookahead).
    Все ячейки — один движок review_signal (порядок v5) + live-набор
    (TP 5.0, trail 1.6/0.6 thr 0.1, partial 0.8/50); exec_mode различает
    только BE: grid = без БУ, grid_be = с БУ 0.8/0.35. Меняются лишь
    входные данные (entry/SL/BE-флаг) и ТФ.
    Без m1_candles ячейки T1M/T1L дают NO_ENTRY/no_m1.
    """
    validate_tf(tf)
    out: list[HourBounceResult] = []
    for code in ("T1M", "T1L", "T2", "T3"):
        for sl in (1, 2, 3):
            ex = grid_be_exec(sl, config) if exec_mode == "grid_be" else grid_exec(sl, config)
            out.append(review_signal(
                side=side, level_price=level_price, signal_time=signal_time,
                candles=candles, entry_code=code, sl_index=sl, config=config,  # type: ignore[arg-type]
                exec_params=ex, m1_candles=m1_candles, tf=tf,
            ))
    return out
