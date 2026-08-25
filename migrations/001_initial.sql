CREATE TABLE instruments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    exchange VARCHAR(32) NOT NULL,
    market_type VARCHAR(32) NOT NULL,
    symbol VARCHAR(30) NOT NULL UNIQUE,
    quote_asset VARCHAR(12) NOT NULL,
    base_asset VARCHAR(20) NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'TRADING',
    daily_volume DECIMAL(40, 16) NOT NULL DEFAULT 0,
    last_synced_at DATETIME(6) NULL,
    INDEX ix_instrument_status_volume (status, daily_volume)
);

CREATE TABLE candles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    instrument_id BIGINT NOT NULL,
    timeframe VARCHAR(8) NOT NULL,
    open_time DATETIME(6) NOT NULL,
    close_time DATETIME(6) NOT NULL,
    open DECIMAL(32, 16) NOT NULL,
    high DECIMAL(32, 16) NOT NULL,
    low DECIMAL(32, 16) NOT NULL,
    close DECIMAL(32, 16) NOT NULL,
    volume DECIMAL(40, 16) NOT NULL,
    UNIQUE KEY uq_candle (instrument_id, timeframe, open_time),
    CONSTRAINT fk_candle_instrument FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE runs (
    id CHAR(36) PRIMARY KEY,
    symbol VARCHAR(30) NOT NULL,
    display_from DATETIME(6) NOT NULL,
    calculation_from DATETIME(6) NOT NULL,
    effective_to DATETIME(6) NOT NULL,
    cursor_time DATETIME(6) NULL,
    status VARCHAR(24) NOT NULL,
    config_hash CHAR(64) NOT NULL,
    data_set_id CHAR(64) NOT NULL,
    progress INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    settings_json LONGTEXT NOT NULL
);

CREATE TABLE replay_checkpoints (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    run_id CHAR(36) NOT NULL,
    sequence INT NOT NULL,
    cursor_time DATETIME(6) NOT NULL,
    state_json LONGTEXT NOT NULL,
    INDEX ix_checkpoint_run (run_id, sequence)
);

CREATE TABLE levels (
    id CHAR(36) PRIMARY KEY,
    run_id CHAR(36) NOT NULL,
    side VARCHAR(16) NOT NULL,
    price DECIMAL(32, 16) NOT NULL,
    zone_low DECIMAL(32, 16) NOT NULL,
    zone_high DECIMAL(32, 16) NOT NULL,
    state VARCHAR(32) NOT NULL,
    created_time DATETIME(6) NOT NULL,
    confirmed_time DATETIME(6) NOT NULL,
    broken_time DATETIME(6) NULL,
    expired_time DATETIME(6) NULL,
    INDEX ix_level_run (run_id)
);

CREATE TABLE level_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    run_id CHAR(36) NOT NULL,
    level_id CHAR(36) NOT NULL,
    sequence INT NOT NULL,
    event_time DATETIME(6) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    reason VARCHAR(255) NOT NULL,
    payload_json LONGTEXT NOT NULL,
    INDEX ix_event_run_sequence (run_id, sequence)
);

CREATE TABLE level_touches (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    level_id CHAR(36) NOT NULL,
    touch_time DATETIME(6) NOT NULL,
    sequence INT NOT NULL
);

CREATE TABLE outcome_profiles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(64) NOT NULL UNIQUE,
    settings_json LONGTEXT NOT NULL
);

CREATE TABLE outcomes (
    id CHAR(36) PRIMARY KEY,
    run_id CHAR(36) NOT NULL,
    profile_name VARCHAR(64) NOT NULL,
    level_id CHAR(36) NOT NULL,
    status VARCHAR(16) NOT NULL,
    touch_time DATETIME(6) NOT NULL,
    resolved_time DATETIME(6) NULL,
    reason VARCHAR(255) NULL,
    INDEX ix_outcome_run (run_id),
    INDEX ix_outcome_status (status)
);
