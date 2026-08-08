from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from power_reading.database import (
    get_database_url,
    get_interval_readings,
    get_latest_reading,
    store_readings,
)
from power_reading.service import PowerReading
from power_reading.scrapers.fusionsolar_scraper import (
    _extract_inverter_nominal_power_kw,
    _select_overview_pv_kw,
)
from power_reading.worker import CollectionResult, collect_once, run_cycle


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

    def fetchall(self):
        return self.rows


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

    def test_interval_readings_include_boundary_sample_and_interval_samples(self):
        start = datetime(2026, 7, 26, 9, 45, tzinfo=timezone.utc)
        end = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
        rows = [
            ("hng", start, 0.4, None, None, "test", ""),
            ("hng", end, 0.5, None, None, "test", ""),
        ]
        connection = FakeConnection(rows)
        result = get_interval_readings("hng", start=start, end=end, connection=connection)
        self.assertEqual([item.pv_mw for item in result], [0.4, 0.5])
        _, params = connection.cursor_instance.executions[0]
        self.assertEqual(params, ("hng", start, "hng", start, end))


class WorkerTests(unittest.TestCase):
    def test_collect_once_keeps_successes_when_an_asset_fails(self):
        def fake_reader(asset, *, headless):
            if asset == "hng":
                raise RuntimeError("offline")
            return reading(asset)

        result = collect_once(["incuba", "hng"], max_workers=2, reader=fake_reader)
        self.assertEqual([item.asset for item in result.readings], ["incuba"])
        self.assertIn("hng", result.errors)

    def test_collect_once_publishes_each_successful_reading(self):
        published = []

        result = collect_once(
            ["incuba", "hng"],
            max_workers=2,
            reader=lambda asset, *, headless: reading(asset),
            on_reading=published.append,
        )

        self.assertEqual({item.asset for item in published}, {"incuba", "hng"})
        self.assertEqual({item.asset for item in result.readings}, {"incuba", "hng"})

    def test_run_cycle_stores_readings_as_they_are_published(self):
        readings = (reading("incuba"), reading("hng"))

        def fake_collect(assets, *, max_workers, asset_timeout_seconds, on_reading):
            for item in readings:
                on_reading(item)
            return CollectionResult(readings, {"astro": "timeout"})

        with (
            patch("power_reading.worker.collect_once", side_effect=fake_collect),
            patch("power_reading.worker.store_readings", return_value=1) as store,
            patch("power_reading.worker.store_errors") as store_errors,
        ):
            result = run_cycle(["incuba", "hng", "astro"], 2, 120)

        self.assertEqual(store.call_count, 2)
        store.assert_any_call((readings[0],))
        store.assert_any_call((readings[1],))
        store_errors.assert_called_once_with({"astro": "timeout"})
        self.assertEqual(result.errors, {"astro": "timeout"})


class FusionSolarSelectionTests(unittest.TestCase):
    def test_explicit_plant_active_power_replaces_partial_ocr_flow_value(self):
        selected_kw, used_active_power = _select_overview_pv_kw(
            1_423.0,
            "Elnet Biomasa.GR\n2.057 MW Active power",
            2_700.0,
            None,
        )

        self.assertEqual(selected_kw, 2_057.0)
        self.assertTrue(used_active_power)

    def test_matching_ocr_flow_value_is_preserved(self):
        selected_kw, used_active_power = _select_overview_pv_kw(
            206.312,
            "Cai de vis\n206.312 kW Active power",
            2_275.0,
            None,
        )

        self.assertEqual(selected_kw, 206.312)
        self.assertFalse(used_active_power)

    def test_romanian_active_and_nominal_power_are_supported(self):
        text = (
            "Elnet Biomasa.GR\n2,371 MW\nPutere\nactiv\u0103\n"
            "Putere nominal\u0103 invertor 2.700,0 kW"
        )

        nominal_kw = _extract_inverter_nominal_power_kw(text)
        selected_kw, used_active_power = _select_overview_pv_kw(
            1_423.0,
            text,
            nominal_kw,
            None,
        )

        self.assertEqual(nominal_kw, 2_700.0)
        self.assertEqual(selected_kw, 2_371.0)
        self.assertTrue(used_active_power)


if __name__ == "__main__":
    unittest.main()
