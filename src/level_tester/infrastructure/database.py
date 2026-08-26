from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class InstrumentRow(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32))
    market_type: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    quote_asset: Mapped[str] = mapped_column(String(12))
    base_asset: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), index=True, default="TRADING")
    daily_volume: Mapped[Decimal] = mapped_column(Numeric(40, 16), default=0)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(32, 16), default=Decimal("0.01"))
    price_precision: Mapped[int] = mapped_column(Integer, default=8)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CandleRow(Base):
    __tablename__ = "candles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(Integer, index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    high: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    low: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    close: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    volume: Mapped[Decimal] = mapped_column(Numeric(40, 16))
    __table_args__ = (UniqueConstraint("instrument_id", "timeframe", "open_time"),)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    display_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calculation_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cursor_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    config_hash: Mapped[str] = mapped_column(String(64))
    data_set_id: Mapped[str] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings_json: Mapped[str] = mapped_column(Text)


class ReplayCheckpointRow(Base):
    __tablename__ = "replay_checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    cursor_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state_json: Mapped[str] = mapped_column(Text)


class LevelRow(Base):
    __tablename__ = "levels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    side: Mapped[str] = mapped_column(String(16))
    price: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    zone_low: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    zone_high: Mapped[Decimal] = mapped_column(Numeric(32, 16))
    state: Mapped[str] = mapped_column(String(32))
    created_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    broken_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LevelEventRow(Base):
    __tablename__ = "level_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    level_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[str] = mapped_column(Text)


class LevelTouchRow(Base):
    __tablename__ = "level_touches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level_id: Mapped[str] = mapped_column(String(36), index=True)
    touch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(Integer)


class OutcomeProfileRow(Base):
    __tablename__ = "outcome_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    settings_json: Mapped[str] = mapped_column(Text)


class OutcomeRow(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    profile_name: Mapped[str] = mapped_column(String(64), index=True)
    level_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    touch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


@dataclass(frozen=True, slots=True)
class DatabaseCheck:
    connected: bool
    schema_valid: bool
    timezone: str
    missing_tables: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()
    missing_indexes: tuple[str, ...] = ()
    error: str | None = None


def create_session_factory(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)

    if engine.dialect.name in {"mysql", "mariadb"}:

        @event.listens_for(engine, "connect")
        def set_utc_timezone(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET time_zone = '+00:00'")
            finally:
                cursor.close()

    # Tables are created by ensure_schema(); an unreachable SQL server must not
    # break application import.
    factory = sessionmaker(engine, expire_on_commit=False)
    factory.engine = engine
    return factory


def ensure_schema(session_factory) -> None:
    """Create missing tables and indexes; safe to call on every startup."""
    engine = session_factory.engine
    Base.metadata.create_all(engine)
    # SQLite does not add columns to existing tables via create_all; backfill
    # any columns the models gained after the schema was first created.
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, table in Base.metadata.tables.items():
            if table_name not in set(inspector.get_table_names()):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl_type = str(column.type.compile(dialect=engine.dialect))
                default_value = None
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    default_value = column.default.arg
                default_clause = ""
                if default_value is not None:
                    if isinstance(default_value, Decimal):
                        default_clause = f" DEFAULT '{default_value}'"
                    elif isinstance(default_value, str):
                        default_clause = f" DEFAULT '{default_value}'"
                    else:
                        default_clause = f" DEFAULT {default_value}"
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {ddl_type}{default_clause}")
                )


def check_database(session_factory) -> DatabaseCheck:
    try:
        engine = session_factory.engine
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            timezone_value = (
                "UTC (application)"
                if engine.dialect.name == "sqlite"
                else str(connection.execute(text("SELECT @@session.time_zone")).scalar_one())
            )
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names())
        required_tables = set(Base.metadata.tables)
        missing_tables = sorted(required_tables - actual_tables)
        missing_columns = []
        missing_indexes = []
        for table_name, table in Base.metadata.tables.items():
            if table_name not in actual_tables:
                continue
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            missing_columns.extend(
                f"{table_name}.{column.name}"
                for column in table.columns
                if column.name not in actual_columns
            )
            actual_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            required_indexes = {index.name for index in table.indexes if index.name}
            missing_indexes.extend(
                f"{table_name}.{index_name}" for index_name in required_indexes - actual_indexes
            )
        return DatabaseCheck(
            connected=True,
            schema_valid=not (missing_tables or missing_columns or missing_indexes),
            timezone=timezone_value,
            missing_tables=tuple(missing_tables),
            missing_columns=tuple(sorted(missing_columns)),
            missing_indexes=tuple(sorted(missing_indexes)),
        )
    except Exception as exc:  # noqa: BLE001 - database health must return a diagnostic result
        return DatabaseCheck(False, False, "unknown", error=str(exc))
