from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import sleep

from fastapi.testclient import TestClient

from level_tester.api import app as app_module
from level_tester.api.app import _hb_price_spec, _hb_price_specs, app
from level_tester.infrastructure.database import (
    InstrumentRow,
    create_session_factory,
    ensure_schema,
)


def _catalog_with(symbol: str, tick_size: str, price_precision: int | None = None):
    factory = create_session_factory("sqlite://")
    ensure_schema(factory)
    with factory() as session:
        session.add(
            InstrumentRow(
                symbol=symbol,
                exchange="binance",
                market_type="usdt_m_futures",
                quote_asset="USDT",
                tick_size=Decimal(tick_size),
                price_precision=price_precision,
            )
        )
        session.commit()
    return factory


def test_hb_price_spec_uses_tick_size_not_exchange_precision() -> None:
    """TREEUSDT: Binance pricePrecision=7, tickSize=0.00001 -> отображаем 5 знаков.

    Иначе шкала рисуется с лишними нулями и сдвигается к 0.0000006 вместо 0.0476.
    """
    factory = _catalog_with("TREEUSDT", "0.0000100000000000", price_precision=7)
    original = app_module.session_factory
    app_module.session_factory = factory
    try:
        spec = _hb_price_spec("TREEUSDT", "0.0476")
    finally:
        app_module.session_factory = original
    assert spec == {"price_precision": 5, "tick_size": "0.0000100000000000"}


def test_hb_price_spec_falls_back_to_price_digits() -> None:
    factory = create_session_factory("sqlite://")
    ensure_schema(factory)
    original = app_module.session_factory
    app_module.session_factory = factory
    try:
        spec = _hb_price_spec("UNKNOWNUSDT", "0.0476")
    finally:
        app_module.session_factory = original
    assert spec["price_precision"] == 4
    assert spec["tick_size"] is None


def test_hb_price_specs_batch_one_query_and_fallback() -> None:
    factory = _catalog_with("TREEUSDT", "0.0000100000000000", price_precision=7)
    original = app_module.session_factory
    app_module.session_factory = factory
    try:
        specs = _hb_price_specs({"TREEUSDT", "UNKNOWNUSDT"}, {"UNKNOWNUSDT": "0.0476"})
    finally:
        app_module.session_factory = original
    assert specs["TREEUSDT"] == {"price_precision": 5, "tick_size": "0.0000100000000000"}
    assert specs["UNKNOWNUSDT"] == {"price_precision": 4, "tick_size": None}


def test_run_commands_and_snapshot() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(8):
        opened = start.replace(hour=12) - timedelta(hours=8 - index)
        candles.append(
            {
                "open_time": opened.isoformat(),
                "close_time": (opened + timedelta(hours=1)).isoformat(),
                "open": "100",
                "high": str(101 + index),
                "low": str(99 - index),
                "close": "100",
                "volume": "10",
            }
        )
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"symbol": "BTCUSDT", "display_from": start.isoformat(), "seed_candles": candles},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(100):
            if client.get(f"/api/runs/{run_id}").json()["status"] != "loading":
                break
            sleep(0.02)
        stepped = client.post(f"/api/runs/{run_id}/step")
        assert stepped.status_code == 200
        assert stepped.json()["cursor"]["bar_time"].endswith("+00:00")
        assert len(stepped.json()["master_candles"]) == 1
        assert client.post(f"/api/runs/{run_id}/reset").json()["cursor"] is None


def test_naive_api_timestamp_is_rejected() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/runs", json={"symbol": "BTCUSDT", "display_from": "2026-01-01T00:00:00"}
    )
    assert response.status_code == 422


def test_replay_config_is_available_to_frontend() -> None:
    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json()["replay"] == {
        "default_speed": 1.0,
        "detail_timeframes": ["1m", "5m"],
        "default_detail_timeframe": "1m",
        "confirmation_methods": [
            {"id": "touch", "number": 1, "label": "Touch"},
            {"id": "consecutive", "number": 3, "label": "N consecutive closes"},
            {"id": "bounce", "number": 5, "label": "Bounce"},
        ],
    }


def test_run_keeps_selected_confirmation_methods() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "symbol": "BTCUSDT",
                "display_from": "2026-01-01T00:00:00+00:00",
                "confirmation_methods": ["touch", "consecutive", "bounce"],
                "confirmation_required_bars": 3,
                "confirmation_max_wait_bars": 20,
                "seed_candles": [
                    {
                        "open_time": "2026-01-01T00:00:00+00:00",
                        "close_time": "2026-01-01T01:00:00+00:00",
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100",
                        "volume": "1",
                    }
                ],
            },
        )

    assert response.status_code == 202
    assert response.json()["confirmation_methods"] == ["touch", "consecutive", "bounce"]
    assert response.json()["confirmation_required_bars"] == 3
    assert response.json()["confirmation_max_wait_bars"] == 20


def test_run_rejects_unknown_confirmation_method() -> None:
    response = TestClient(app).post(
        "/api/runs",
        json={
            "symbol": "BTCUSDT",
            "display_from": "2026-01-01T00:00:00+00:00",
            "confirmation_methods": ["breakout"],
        },
    )

    assert response.status_code == 422
