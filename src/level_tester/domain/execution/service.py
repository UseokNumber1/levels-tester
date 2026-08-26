from __future__ import annotations

from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from level_tester.domain.confirmation.models import TradeSetup, TradeSetupStatus
from level_tester.domain.execution.models import ExecutionConfig, Trade, TradeStatus
from level_tester.domain.models import Candle, Level, LevelSide


class TradeExecution:
    """Open confirmed setups and close them using closed detail candles."""

    def __init__(self, run_id: str, config: ExecutionConfig | None = None) -> None:
        self.run_id = run_id
        self.config = config or ExecutionConfig()
        self.trades: list[Trade] = []

    def evaluate(
        self, setups: list[TradeSetup], levels: list[Level], candles: list[Candle]
    ) -> list[Trade]:
        for setup in setups:
            if setup.status != TradeSetupStatus.ENTRY_CONFIRMED:
                continue
            if any(trade.setup_id == setup.id for trade in self.trades):
                continue
            level = next((item for item in levels if item.id == setup.level_id), None)
            if level is None or setup.entry_time is None or setup.entry_price is None:
                continue
            entry = self._apply_slippage(setup.entry_price, level.side, opening=True)
            stop, take = self._brackets(entry, level.side)
            trade = Trade(
                id=str(uuid5(NAMESPACE_URL, f"{self.run_id}:{setup.id}")),
                setup_id=setup.id,
                side=level.side,
                entry_time=setup.entry_time,
                entry_price=entry,
                stop_price=stop,
                take_price=take,
            )
            self.trades.append(trade)

        for trade in self.trades:
            if trade.status != TradeStatus.OPEN:
                continue
            for candle in candles:
                if candle.close_time <= trade.entry_time:
                    continue
                exit_reason = self._exit_reason(trade, candle)
                if exit_reason:
                    trade.status = TradeStatus.CLOSED
                    trade.exit_time = candle.close_time
                    trade.exit_reason = exit_reason
                    trade.exit_price = self._exit_price(trade, candle, exit_reason)
                    trade.pnl = self._pnl(trade)
                    break
        return list(self.trades)

    def _brackets(self, entry: Decimal, side: LevelSide) -> tuple[Decimal, Decimal]:
        buffer = self.config.stop_buffer_percent
        risk = entry * buffer
        if side == LevelSide.SUPPORT:
            return entry - risk, entry + risk * self.config.risk_reward
        return entry + risk, entry - risk * self.config.risk_reward

    @staticmethod
    def _exit_reason(trade: Trade, candle: Candle) -> str | None:
        if trade.side == LevelSide.SUPPORT:
            if candle.low <= trade.stop_price:
                return "stop_loss"
            if candle.high >= trade.take_price:
                return "take_profit"
        elif candle.high >= trade.stop_price:
            return "stop_loss"
        elif candle.low <= trade.take_price:
            return "take_profit"
        return None

    @staticmethod
    def _exit_price(trade: Trade, candle: Candle, reason: str) -> Decimal:
        if reason == "stop_loss":
            return trade.stop_price
        return trade.take_price

    def _apply_slippage(self, price: Decimal, side: LevelSide, opening: bool) -> Decimal:
        direction = Decimal(1) if side == LevelSide.SUPPORT else Decimal(-1)
        if not opening:
            direction *= Decimal(-1)
        return price * (Decimal(1) + direction * self.config.slippage_percent)

    def _pnl(self, trade: Trade) -> Decimal:
        assert trade.exit_price is not None
        multiplier = Decimal(1) if trade.side == LevelSide.SUPPORT else Decimal(-1)
        gross = (trade.exit_price - trade.entry_price) * multiplier
        fees = (trade.entry_price + trade.exit_price) * self.config.fee_percent
        return gross - fees
