# Level Tester

Интерактивный исторический replay-тестер уровней для Binance USDT-M Futures.

Проект предназначен для причинного анализа работы алгоритма уровней на закрытых
свечах. H1 является основным таймфреймом поиска пивотов и уровней, M1/M5
используются для детализации касаний и исполнения.

## Статус

Рабочий срез: SQL-backed каталог Binance USDT-M инструментов с фильтрами по
поиску и суточному quote-volume в USDT, асинхронная проверка/дозагрузка H1,
causal pivot/levels, детерминированный replay, outcome-профили,
REST/WebSocket API и базовый web-интерфейс. Demo-режим удалён.

## Локальный запуск

Быстрый запуск в Windows:

```powershell
.\run.ps1
```

Также можно запустить `run.bat` двойным кликом.

Ручной запуск:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn level_tester.api.app:app --reload
```

Проверка:

```text
http://127.0.0.1:8080/health
http://127.0.0.1:8080/
```

## API

Создание запуска:

```powershell
curl.exe -X POST http://127.0.0.1:8080/api/runs `
  -H "Content-Type: application/json" `
  -d '{"symbol":"BTCUSDT","display_from":"2026-01-01T00:00:00+00:00","detail_timeframe":"1m"}'
```

Список инструментов синхронизируется через `GET /api/instruments?search=BTC`
с дополнительными параметрами `min_volume`, `max_volume`, `refresh`. Суточный
объём — `quoteVolume` Binance в USDT. Принудительная синхронизация: `POST
/api/instruments/sync`.

Создание запуска возвращает `202 Accepted` и статус `LOADING`. Прогресс можно
получать через `GET /api/runs/{run_id}`. После успешной проверки данных статус
меняется на `READY`; при ошибке Binance или качестве данных — `FAILED` с
`error_message`. H1 загружается при создании запуска, M1/M5 — лениво через
`GET /api/runs/{run_id}/detail?start=...&end=...`.

Состояние серверов доступно через `GET /api/status`: backend, SQL database и
Binance. Интерфейс показывает эти значения в верхней панели.

Команды имеют вид `POST /api/runs/{run_id}/play`, `pause`, `step`, `reset` и
`cancel`. Snapshot доступен через `GET /api/runs/{run_id}/snapshot`, поток
событий через `WS /api/runs/{run_id}/events?after_sequence=N`.

## Проверки

```powershell
pytest -q
ruff check src tests
```

MariaDB подключается переменной `DATABASE_URL`; схема находится в
`migrations/001_initial.sql`. База создаётся внутри каталогов сервера один раз:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\init_mariadb.ps1
```

Скрипт читает учётные данные из `.env`, запрашивает пароль root MariaDB
(только в памяти, нигде не сохраняет), создаёт базу `utf8mb4`, пользователя
приложения и применяет схему. Без SQL-сервера приложение всё равно стартует:
`/api/status` покажет `database: disconnected`.

При старте приложение проверяет соединение, таблицы, колонки и индексы,
устанавливает SQL-сессию в UTC и запускает синхронизацию `exchangeInfo` и
24-часовых quote-volume Binance в фоновом потоке. Если Binance временно
недоступна, сервер остаётся рабочим, но индикатор показывает ошибку, а
создание реального запуска завершается в `FAILED`.

## Принципы

- Историческое время отделено от wall-clock времени приложения.
- В расчёт допускаются только закрытые свечи.
- Replay является детерминированным и управляется cursor.
- Конфигурация запуска должна сохраняться вместе с результатом.
- Состояние доменного ядра не зависит от FastAPI и MariaDB.

Правила алгоритма подробно зафиксированы в `docs/SPECIFICATION.md`.
