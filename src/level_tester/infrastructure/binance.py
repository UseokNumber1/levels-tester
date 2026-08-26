from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from level_tester.domain.models import Candle


class BinanceError(RuntimeError):
    pass


def tls_verification() -> Any:
    """Broken SSL_CERT_FILE environments must not crash every request."""
    cert_file = os.environ.get("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).is_file():
        import certifi

        return certifi.where()
    return True


class BinanceFuturesClient:
    """Public klines adapter; pagination is explicit and safe for replay ranges."""

    def __init__(self, base_url: str = "https://fapi.binance.com", timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url, timeout=self.timeout, verify=tls_verification()
        )

    def klines(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, limit: int = 1500
    ) -> list[Candle]:
        if limit < 1 or limit > 1500:
            raise ValueError("Binance kline limit must be between 1 and 1500")
        start_ms = _millis(start)
        end_ms = _millis(end)
        output: list[Candle] = []
        while start_ms < end_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": timeframe,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": limit,
            }
            try:
                response = self._client.get("/fapi/v1/klines", params=params)
                response.raise_for_status()
                rows = response.json()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                raise BinanceError("failed to fetch Binance klines") from exc
            if not isinstance(rows, list):
                raise BinanceError("Binance returned an invalid kline payload")
            page = [
                candle
                for candle in (_candle_from_binance(row, timeframe) for row in rows)
                if candle.close_time <= datetime.now(UTC)
            ]
            output.extend(page)
            if not page:
                break
            next_start = _millis(page[-1].open_time) + 1
            if next_start <= start_ms:
                raise BinanceError("Binance pagination did not advance")
            start_ms = next_start
            if len(page) < limit:
                break
        return _deduplicate(output)

    def exchange_info(self) -> list[dict[str, Any]]:
        payload = self._get("/fapi/v1/exchangeInfo")
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list):
            raise BinanceError("Binance returned invalid exchange info")
        return [item for item in symbols if isinstance(item, dict)]

    def ticker_24h(self) -> list[dict[str, Any]]:
        payload = self._get("/fapi/v1/ticker/24hr")
        if not isinstance(payload, list):
            raise BinanceError("Binance returned invalid 24h ticker payload")
        return [item for item in payload if isinstance(item, dict)]

    def ping(self) -> bool:
        try:
            self._get("/fapi/v1/ping", timeout=3.0)
        except BinanceError:
            return False
        return True

    def _get(
        self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        try:
            response = self._client.get(path, params=params, timeout=timeout or self.timeout)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise BinanceError(f"failed to fetch Binance {path}") from exc


def _millis(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Binance range must use timezone-aware datetimes")
    return int(value.astimezone(UTC).timestamp() * 1000)


def _candle_from_binance(row: list[Any], timeframe: str) -> Candle:
    if len(row) < 7:
        raise BinanceError("Binance kline row is incomplete")
    opened = datetime.fromtimestamp(int(row[0]) / 1000, UTC)
    closed = datetime.fromtimestamp(int(row[6]) / 1000, UTC)
    return Candle(
        opened,
        closed,
        Decimal(row[1]),
        Decimal(row[2]),
        Decimal(row[3]),
        Decimal(row[4]),
        Decimal(row[5]),
        timeframe,
    )


def _deduplicate(candles: list[Candle]) -> list[Candle]:
    unique = {(candle.timeframe, candle.open_time): candle for candle in candles}
    return sorted(unique.values(), key=lambda candle: candle.open_time)
