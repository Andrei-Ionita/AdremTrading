from __future__ import annotations

import os
from contextlib import closing
from datetime import datetime
from typing import Iterable

from .service import PowerReading


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS power_readings (
    id BIGSERIAL PRIMARY KEY,
    asset VARCHAR(64) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pv_mw DOUBLE PRECISION,
    load_mw DOUBLE PRECISION,
    grid_mw DOUBLE PRECISION,
    source VARCHAR(128) NOT NULL,
    raw_excerpt TEXT NOT NULL DEFAULT '',
    CONSTRAINT power_readings_asset_observed_unique UNIQUE (asset, observed_at)
);

CREATE INDEX IF NOT EXISTS power_readings_asset_observed_idx
    ON power_readings (asset, observed_at DESC);

CREATE TABLE IF NOT EXISTS power_reading_errors (
    id BIGSERIAL PRIMARY KEY,
    asset VARCHAR(64) NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS power_reading_errors_asset_collected_idx
    ON power_reading_errors (asset, collected_at DESC);
"""

UPSERT_SQL = """
INSERT INTO power_readings (
    asset, observed_at, collected_at, pv_mw, load_mw, grid_mw, source, raw_excerpt
) VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s)
ON CONFLICT (asset, observed_at) DO UPDATE SET
    collected_at = EXCLUDED.collected_at,
    pv_mw = EXCLUDED.pv_mw,
    load_mw = EXCLUDED.load_mw,
    grid_mw = EXCLUDED.grid_mw,
    source = EXCLUDED.source,
    raw_excerpt = EXCLUDED.raw_excerpt;
"""


def get_database_url() -> str:
    url = (
        os.getenv("POWER_READING_DATABASE_URL")
        or os.getenv("pulseai-db-url")
        or os.getenv("pulseai_db_url")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()
    if not url:
        raise RuntimeError(
            "PostgreSQL is not configured. Set DATABASE_URL or POWER_READING_DATABASE_URL."
        )
    return url


def ensure_schema(*, connection=None) -> None:
    with _connection_scope(connection) as conn:
        with conn.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        conn.commit()


def store_readings(readings: Iterable[PowerReading], *, connection=None) -> int:
    rows = list(readings)
    if not rows:
        return 0
    with _connection_scope(connection) as conn:
        with conn.cursor() as cursor:
            for reading in rows:
                cursor.execute(
                    UPSERT_SQL,
                    (
                        reading.asset,
                        reading.timestamp_utc,
                        reading.pv_mw,
                        reading.load_mw,
                        reading.grid_mw,
                        reading.source,
                        reading.raw_excerpt,
                    ),
                )
        conn.commit()
    return len(rows)


def store_errors(errors: dict[str, str], *, connection=None) -> int:
    if not errors:
        return 0
    with _connection_scope(connection) as conn:
        with conn.cursor() as cursor:
            for asset, error in errors.items():
                cursor.execute(
                    "INSERT INTO power_reading_errors (asset, error) VALUES (%s, %s)",
                    (asset, error[:8000]),
                )
        conn.commit()
    return len(errors)


def get_latest_reading(asset: str, *, before: datetime | None = None, connection=None) -> PowerReading | None:
    query = """
        SELECT asset, observed_at, pv_mw, load_mw, grid_mw, source, raw_excerpt
        FROM power_readings
        WHERE asset = %s
    """
    params: list[object] = [asset]
    if before is not None:
        query += " AND observed_at < %s"
        params.append(before)
    query += " ORDER BY observed_at DESC LIMIT 1"

    with _connection_scope(connection) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
    return _row_to_reading(row)


def get_interval_readings(
    asset: str,
    *,
    start: datetime,
    end: datetime,
    connection=None,
) -> list[PowerReading]:
    if start >= end:
        raise ValueError("start must be earlier than end")

    with _connection_scope(connection) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                (
                    SELECT asset, observed_at, pv_mw, load_mw, grid_mw, source, raw_excerpt
                    FROM power_readings
                    WHERE asset = %s AND observed_at <= %s
                    ORDER BY observed_at DESC
                    LIMIT 1
                )
                UNION ALL
                (
                    SELECT asset, observed_at, pv_mw, load_mw, grid_mw, source, raw_excerpt
                    FROM power_readings
                    WHERE asset = %s AND observed_at > %s AND observed_at <= %s
                    ORDER BY observed_at ASC
                )
                ORDER BY observed_at ASC
                """,
                (asset, start, asset, start, end),
            )
            rows = cursor.fetchall()
    return [_row_to_reading(row) for row in rows]


def get_recent_readings(asset: str, *, limit: int = 96, connection=None) -> list[PowerReading]:
    safe_limit = max(1, min(int(limit), 10_000))
    with _connection_scope(connection) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT asset, observed_at, pv_mw, load_mw, grid_mw, source, raw_excerpt
                FROM power_readings
                WHERE asset = %s
                ORDER BY observed_at DESC
                LIMIT %s
                """,
                (asset, safe_limit),
            )
            rows = cursor.fetchall()
    return [_row_to_reading(row) for row in reversed(rows)]


def _row_to_reading(row) -> PowerReading | None:
    if row is None:
        return None
    observed_at = row[1]
    timestamp = observed_at.isoformat() if hasattr(observed_at, "isoformat") else str(observed_at)
    return PowerReading(
        asset=row[0],
        timestamp_utc=timestamp,
        pv_mw=row[2],
        load_mw=row[3],
        grid_mw=row[4],
        source=row[5],
        raw_excerpt=row[6] or "",
    )


class _connection_scope:
    def __init__(self, connection=None) -> None:
        self.connection = connection
        self.owns_connection = connection is None

    def __enter__(self):
        if self.connection is None:
            self.connection = _connect()
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            self.connection.rollback()
        if self.owns_connection:
            self.connection.close()


def _connect():
    import psycopg2

    sslmode = (os.getenv("POWER_READING_DB_SSLMODE") or "prefer").strip()
    return psycopg2.connect(get_database_url(), sslmode=sslmode, connect_timeout=15)
