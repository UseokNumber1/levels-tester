# Confirmation Waiting Trade Execution Flow

Документация алгоритма отработки сделки в режиме ожидания подтверждения (entry_method=confirmation).

---

## 📁 Core Files

| File | Purpose |
|------|---------|
| `backend/app/core/execution/confirmation_loop.py` | **Main algorithm** — `ConfirmationLoop.run()` encapsulates the entire confirmation lifecycle: bounce pre-check → HTTP polling (candles) → level touch detection → consecutive bars counting → global timeout → fallback (HTTP / ground-truth) → market order execution. |
| `backend/app/core/execution/engine.py` | Orchestrates entry: `_execute_signal()` routes `confirmation` → `_execute_confirmation_entry()` which **instantiates & awaits `ConfirmationLoop`** (lines 1536-1554). Also manages `_confirmation_tasks` dict to prevent race conditions. |
| `backend/app/services/trading_service.py` | High-level `execute_signal()` entry point called by API / recovery; calls `engine.execute_signal()`. |
| `backend/app/api/routes/signals.py` | REST endpoints: `POST /signals/{id}/confirm-entry` → sets `entry_confirmed` + spawns background execution; `POST /signals/manual/confirm` → `add_manually → entry_waiting_confirmation`. |

---

## 🔄 Key State Transitions (FSM + Confirmation)

| From Status | Trigger | To Status | Where |
|-------------|---------|-----------|-------|
| `accepted` / `add_manually` | User clicks "Execute" or `/confirm` | `entry_waiting_confirmation` | `signals.py:274` or `engine.py:1370` |
| `entry_waiting_confirmation` | Candle touches level (close) | `entry_waiting_confirmation` (but `confirmation_level_touched=true`, `confirmation_started_at=now`) | `confirmation_loop.py:185-189` |
| `entry_waiting_confirmation` | Required consecutive bars met | `entry_confirmed` (polling loop exits, places market order) | `confirmation_loop.py:728-748` |
| `entry_waiting_confirmation` | Global timeout (`max_wait_bars`) | `entry_confirmation_timeout` → `cancelled` | `confirmation_loop.py:205, 914` |
| `entry_confirmed` | Background task runs `execute_signal` | `entry_placed` → `entry_filled` → `protection_placed` → `in_position` | `engine.py:_execute_market_order` |

---

## 📊 DB Columns (Schema in `database.py`)

```python
# Lines 82-83, 636-645
confirmation_waiting_started_at TEXT  -- execute() timestamp (global timeout start)
confirmation_started_at TEXT          -- level-touch timestamp (per-bar timeout)
confirmation_level_touched BOOLEAN
confirmation_bars_after_touch INTEGER
confirmation_consecutive_bars INTEGER
confirmation_max_wait_bars INTEGER
confirmation_timeframe TEXT
confirmation_price_probed BOOLEAN
```

---

## ⚡ Key Code Paths

```
signals.py:POST /signals/{id}/execute
    → trading_service.execute_signal(signal)
        → engine.execute_signal()
            → entry_method == 'confirmation'
                → _execute_confirmation_entry()
                    → ConfirmationLoop(engine, signal, ...).run()
                        1. _bounce_precheck()  — was last closed candle already touching?
                        2. while True:
                           a. poll Binance klines (HTTP, run_in_executor)
                           b. skip duplicate open_time
                           c. evaluate closed candle:
                              • touch? → set level_touched, start confirmation_start_time
                              • bounce mode? count consecutive closes on "good" side
                              • else normal: count bars_waited
                           d. persist intermediate state to DB each candle
                           e. check global_timeout / per-bar timeout
                           f. if confirmed → engine._execute_market_order()
                           g. else fallback → engine._confirmation_http_fallback / ground_truth_fallback
```

---

## 🔔 Entry Points (How a signal enters this flow)

| Path | File | Line |
|------|------|------|
| User clicks **Execute** on `new`/`accepted` | `signals.py:723` | `_execute_signal()` |
| User clicks **Confirm** on `add_manually` | `signals.py:274` | `confirm_manual_signal` |
| User clicks **Confirm Entry** on `entry_waiting_confirmation` | `signals.py:1443` | `confirm_entry` → sets `entry_confirmed` → background `_confirm_entry_background` |
| Recovery engine finds position but signal stuck | `recovery/engine.py:317` | preserves `entry_waiting_confirmation` |

---

## 🎯 Summary Diagram

```
┌──────────────┐
│  Signal in   │
│  new/accepted│
└──────┬───────┘
       ▼
┌──────────────┐    entry_method=confirmation
│ execute_signal│ ─────────────────────────►
└──────┬───────┘                          │
       ▼                                  ▼
┌───────────────────────┐        ┌──────────────────────┐
│ _execute_confirmation │        │  ConfirmationLoop    │
│ _entry()              │        │  .run()              │
│  - cancel prior task  │        │  - bounce pre-check  │
│  - create Loop        │───────►│  - poll klines HTTP  │
│  - await run()        │        │  - touch → bars wait │
└───────────────────────┘        │  - persist state     │
                                 │  - timeout checks    │
                                 │  - fallback paths    │
                                 │  - _execute_market_  │
                                 │    order() on confirm│
                                 └──────────┬───────────┘
                                            ▼
                                 ┌────────────────────┐
                                 │ entry_filled →     │
                                 │ protection_placed  │
                                 │ → in_position      │
                                 └────────────────────┘
```

---

Все код находится в **`backend/app/core/execution/`** + **`backend/app/api/routes/signals.py`** + **`backend/app/services/trading_service.py`**. Никакие другие файлы не содержат логику ожидания подтверждения.