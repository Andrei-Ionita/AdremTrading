from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from power_reading.database import get_database_url, get_latest_reading, store_readings
from power_reading.service import PowerReading
from power_reading.worker import collect_once


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_instance = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def reading(asset="incuba", value=0.42):
    return PowerReading(asset, "2026-07-26T10:00:00+00:00", value, None, None, "test")


class DatabaseTests(unittest.TestCase):
    def test_database_url_uses_existing_railway_variable(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://example"}, clear=True):
            self.assertEqual(get_database_url(), "postgresql://example")

    def test_store_readings_upserts_and_commits(self):
        connection = FakeConnection()
        self.assertEqual(store_readings([reading()], connection=connection), 1)
        self.assertEqual(connection.commits, 1)
        self.assertIn("ON CONFLICT", connection.cursor_instance.executions[0][0])

    def test_latest_reading_is_mapped_for_forecast_input(self):
        observed = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
        connection = FakeConnection([("incuba", observed, 0.42, None, None, "test", "")])
        result = get_latest_reading("incuba", connection=connection)
        self.assertEqual(result.pv_mw, 0.42)
        self.assertEqual(result.timestamp_utc, observed.isoformat())


class WorkerTests(unittest.TestCase):
    def test_collect_once_keeps_successes_when_an_asset_fails(self):
        def fake_reader(asset, *, headless):
            if asset == "hng":
                raise RuntimeError("offline")
            return reading(asset)

        result = collect_once(["incuba", "hng"], max_workers=2, reader=fake_reader)
        self.assertEqual([item.asset for item in result.readings], ["incuba"])
        self.assertIn("hng", result.errors)


if __name__ == "__main__":
    unittest.main()
