# ТЗ «HourBounce Replay» v2.0 — данные, API, движок

## 1. Схема БД `main` (DDL)

```sql
-- версии конфигурации
CREATE TABLE config_versions (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  comment     TEXT,
  payload     JSONB NOT NULL           -- полный снапшот настроек страницы Config
);

-- котировки M5 (кеш, on-demand)
CREATE TABLE candles_m5 (
  symbol   TEXT            NOT NULL,
  dt       TIMESTAMPTZ     NOT NULL,   -- open time, UTC
  open     NUMERIC(24,10)  NOT NULL,
  high     NUMERIC(24,10)  NOT NULL,
  low      NUMERIC(24,10)  NOT NULL,
  close    NUMERIC(24,10)  NOT NULL,
  volume   NUMERIC(30,10)  NOT NULL,
  PRIMARY KEY (symbol, dt)
);
CREATE INDEX candles_m5_symbol_dt_desc ON candles_m5 (symbol, dt DESC);

-- фоновые задачи пересчёта
CREATE TABLE replay_jobs (
  id                 BIGSERIAL PRIMARY KEY,
  config_version_id  BIGINT NOT NULL REFERENCES config_versions(id),
  status             TEXT NOT NULL,     -- queued | running | done | failed
  total              INT  NOT NULL DEFAULT 0,
  processed          INT  NOT NULL DEFAULT 0,
  error              TEXT,
  started_at         TIMESTAMPTZ,
  finished_at        TIMESTAMPTZ
);

-- результаты: одна строка = одна ячейка матрицы (signal × T × S)
CREATE TABLE replay_results (
  signal_id          BIGINT     NOT NULL,   -- id из архивной БД
  config_version_id  BIGINT     NOT NULL REFERENCES config_versions(id),
  entry_type         SMALLINT   NOT NULL CHECK (entry_type IN (1,2,3)),
  sl_index           SMALLINT   NOT NULL CHECK (sl_index   IN (1,2,3)),

  outcome            TEXT       NOT NULL CHECK (outcome IN
                                              ('TAKE','STOP','NO_ENTRY','EXPIRED')),
  reason             TEXT,                  -- no_touch | no_confirm | ...
  ambiguous          BOOLEAN    NOT NULL DEFAULT false,

  entry_dt           TIMESTAMPTZ,
  entry_price        NUMERIC(24,10),
  exit_dt            TIMESTAMPTZ,
  exit_price         NUMERIC(24,10),
  sl_price           NUMERIC(24,10),

  r_multiple         NUMERIC(12,4),
  max_profit_pct     NUMERIC(12,4),
  mae_pct            NUMERIC(12,4),
  mfe_pct            NUMERIC(12,4),

  events             JSONB NOT NULL DEFAULT '[]',   -- [{seq,type,dt,price}, ...]
  trail_path         JSONB NOT NULL DEFAULT '[]',   -- [{dt,trail,peak}, ...]

  PRIMARY KEY (signal_id, config_version_id, entry_type, sl_index)
);
CREATE INDEX replay_results_cv_outcome
  ON replay_results (config_version_id, outcome);