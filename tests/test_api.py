from datetime import datetime, timedelta, timezone
from time import sleep

from fastapi.testclient import TestClient

from level_tester.api.app import app


def test_run_commands_and_snapshot() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
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
