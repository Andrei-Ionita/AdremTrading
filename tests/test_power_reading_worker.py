from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from power_reading import worker
from power_reading.service import PowerReading


def reading(asset, value=0.42):
    return PowerReading(asset, "2026-11-18T10:00:00+00:00", value, None, None, "test")


def spawned_reader(asset, connection):
    if asset == "slow":
        time.sleep(60)
    try:
        connection.send(("ok", reading(asset)))
    finally:
        connection.close()


class Clock:
    def __init__(self, until):
        self.now = 0.0
        self.until = until
        self.waits = 0

    def monotonic(self):
        return self.now

    def is_set(self):
        return self.now >= self.until

    def wait(self, seconds):
        self.waits += 1
        self.now = round(min(self.until, self.now + seconds), 6)
        return self.is_set()


class Connection:
    def __init__(self, context):
        self.context = context
        self.process = None
        self.closed = False
        self.received = False

    def poll(self):
        process = self.process
        return (
            not self.received
            and not process.options.get("crash")
            and self.context.clock.now >= process.started_at + process.duration
        )

    def recv(self):
        self.received = True
        options = self.process.options
        if "error" in options:
            return "error", options["error"]
        return "ok", reading(self.process.asset, options.get("value", 0.42))

    def close(self):
        self.closed = True


class Process:
    def __init__(self, context, asset, connection):
        self.context = context
        self.asset = asset
        self.options = context.options.get(asset, {})
        self.duration = self.options.get("duration", 0.2)
        self.started_at = context.clock.now
        self.pid = None
        self.exitcode = None
        self.stopped = False
        self.closed = False
        self.terminations = []
        self.kills = []
        connection.process = self

    def start(self):
        if self.options.get("start_error"):
            raise OSError("cannot spawn")
        self.pid = len(self.context.processes)
        live = [p for p in self.context.processes if p is not self and p.is_alive()]
        assert all(worker._collection_group(p.asset) != worker._collection_group(self.asset) for p in live)
        self.context.peak = max(self.context.peak, len(live) + 1)
        self.context.starts.append((self.asset, self.started_at))

    def is_alive(self):
        if self.pid is None or self.stopped:
            return False
        if not self.options.get("linger") and self.context.clock.now >= self.started_at + self.duration:
            self.exitcode = 7 if self.options.get("crash") else 0
            return False
        return True

    def join(self, timeout):
        self.context.joins.append(timeout)

    def terminate(self):
        self.terminations.append(self.context.clock.now)
        if not self.options.get("ignore_terminate"):
            self.stopped = True

    def kill(self):
        self.kills.append(self.context.clock.now)
        self.stopped = True

    def close(self):
        assert not self.is_alive()
        self.closed = True


class Context:
    def __init__(self, clock, options):
        self.clock = clock
        self.options = options
        self.processes = []
        self.connections = []
        self.starts = []
        self.joins = []
        self.peak = 0

    def Pipe(self, *, duplex):
        assert duplex is False
        receive, send = Connection(self), Connection(self)
        send.receive = receive
        self.connections.extend((receive, send))
        return receive, send

    def Process(self, *, target, args, name, daemon):
        assert target is worker._read_asset_child
        assert daemon is True
        asset, send = args
        process = Process(self, asset, send.receive)
        self.processes.append(process)
        return process


class ContinuousWorkerTests(unittest.TestCase):
    def simulate(self, assets, *, until=20, interval=3, timeout=15, workers=3, options=None,
                 store_failure=None, error_store_failure=None):
        clock = Clock(until)
        context = Context(clock, options or {})
        saved = []
        errors = []

        def store(rows):
            saved.append((clock.now, rows))
            if store_failure:
                raise store_failure
            return len(rows)

        def store_errors(values):
            errors.append((clock.now, values))
            if error_store_failure:
                raise error_store_failure

        with ExitStack() as stack:
            stack.enter_context(patch.object(worker, "STOP_EVENT", clock))
            stack.enter_context(patch.object(worker.time, "monotonic", clock.monotonic))
            get_context = stack.enter_context(patch.object(worker.multiprocessing, "get_context", return_value=context))
            stack.enter_context(patch.object(worker, "store_readings", side_effect=store))
            stack.enter_context(patch.object(worker, "store_errors", side_effect=store_errors))
            stack.enter_context(patch.object(worker, "LOGGER"))
            worker.run_continuous(assets, workers, timeout, interval)
        if assets:
            get_context.assert_called_once_with("spawn")
        self.assertTrue(all(p.closed for p in context.processes))
        self.assertTrue(all(c.closed for c in context.connections))
        self.assertLessEqual(context.peak, max(1, workers))
        self.assertLessEqual(clock.waits, until * 10 + 1)
        return context, saved, errors

    def test_healthy_assets_repeat_during_five_serialized_adc_timeouts(self):
        adc = ["anto", "incuba", "ferma_frumusica", "start_fotovoltaice", "renewable_energy_holding"]
        durations = {"hng": 40, "astro": 80}
        context, saved, errors = self.simulate(
            adc + list(durations), until=1170, interval=180, timeout=180, workers=3,
            options={
                **{asset: {"duration": 10000} for asset in adc},
                **{asset: {"duration": duration} for asset, duration in durations.items()},
            },
        )
        for asset, duration in durations.items():
            starts = [at for name, at in context.starts if name == asset]
            self.assertEqual(starts, [0, 180, 360, 540, 720, 900, 1080])
            persisted = [at for at, rows in saved if rows[0].asset == asset]
            self.assertEqual(persisted, [at + duration for at in starts])
        adc_starts = [(asset, at) for asset, at in context.starts if asset in adc]
        self.assertEqual([asset for asset, _ in adc_starts[:5]], adc)
        self.assertGreater(adc_starts[4][1], 600)
        self.assertTrue(all(any(asset in values for _, values in errors) for asset in adc))

    def test_cadence_is_per_asset_start_not_completion_or_batch(self):
        context, saved, _ = self.simulate(
            ["hng", "elnet"], until=12, interval=3,
            options={"hng": {"duration": 2}, "elnet": {"duration": 0.4}},
        )
        for asset in ("hng", "elnet"):
            self.assertEqual([at for name, at in context.starts if name == asset], [0, 3, 6, 9])
        self.assertEqual(saved[0][0], 0.4)

    def test_oldest_due_is_fair_and_does_not_build_catch_up_queue(self):
        assets = ["hng", "elnet", "motif"]
        context, _, _ = self.simulate(
            assets, until=25, interval=1, timeout=20, workers=1,
            options={asset: {"duration": 4} for asset in assets},
        )
        self.assertEqual([asset for asset, _ in context.starts], assets * 2 + ["hng"])
        self.assertEqual([at for _, at in context.starts], [0, 4, 8, 12, 16, 20, 24])

    def test_both_portal_groups_remain_serialized_across_repeated_polls(self):
        assets = ["anto", "incuba", "astro", "imperial", "hng"]
        context, _, _ = self.simulate(assets, until=20, interval=1, workers=5)
        for asset in assets:
            self.assertGreater(sum(name == asset for name, _ in context.starts), 2)
        self.assertLessEqual(context.peak, 3)

    def test_timeout_kill_escalation_keeps_group_locked_without_blocking_healthy(self):
        context, _, errors = self.simulate(
            ["anto", "incuba", "hng"], until=12, interval=1, timeout=2,
            options={"anto": {"duration": 100, "ignore_terminate": True}},
        )
        first = context.processes[0]
        self.assertEqual(first.terminations, [3])
        self.assertEqual(first.kills, [8])
        self.assertEqual(next(at for name, at in context.starts if name == "incuba"), 8.1)
        self.assertEqual([at for name, at in context.starts if name == "hng"], list(range(12)))
        self.assertIn((2, {"anto": "Asset read exceeded 2 seconds."}), errors)

    def test_result_is_persisted_once_while_process_is_still_exiting(self):
        context, saved, errors = self.simulate(
            ["anto", "incuba", "hng"], until=8, interval=20, timeout=3,
            options={"anto": {"linger": True, "ignore_terminate": True}},
        )
        self.assertEqual([at for at, rows in saved if rows[0].asset == "anto"], [0.2])
        self.assertEqual(next(at for name, at in context.starts if name == "incuba"), 6.3)
        self.assertEqual(errors, [])

    def test_start_crash_null_and_reader_failures_are_isolated_and_retried(self):
        options = {
            "spawn": {"start_error": True},
            "crash": {"crash": True},
            "null": {"value": None},
            "offline": {"error": "COMM FAILURE"},
        }
        context, saved, errors = self.simulate(list(options) + ["hng"], until=7, options=options, workers=5)
        self.assertEqual([at for at, _ in saved], [0.2, 3.2, 6.2])
        for asset in options:
            self.assertEqual(sum(asset in values for _, values in errors), 3)
        self.assertTrue(any("exit code 7" in values.get("crash", "") for _, values in errors))
        self.assertTrue(all(p.closed for p in context.processes if p.asset == "spawn"))

    def test_database_failures_do_not_stop_subsequent_polls(self):
        context, saved, errors = self.simulate(
            ["hng"], until=7, store_failure=RuntimeError("DB unavailable"),
            error_store_failure=RuntimeError("DB unavailable"),
        )
        self.assertEqual(len(context.starts), 3)
        self.assertEqual(len(saved), 3)
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("Could not store reading" in values["hng"] for _, values in errors))

    def test_new_logs_hide_error_payloads_but_database_keeps_details(self):
        secret = "private-portal-token"
        for result in (("error", f"RuntimeError: {secret}"), ("ok", reading("hng"))):
            with (
                self.subTest(status=result[0]),
                patch.object(worker, "store_readings", side_effect=RuntimeError(secret)),
                patch.object(worker, "store_errors", side_effect=RuntimeError(secret)) as errors,
                patch.object(worker, "LOGGER") as logger,
            ):
                worker._persist_asset_result("hng", result, time.monotonic())
            self.assertIn(secret, errors.call_args.args[0]["hng"])
            self.assertNotIn(secret, str(logger.mock_calls))
            logger.exception.assert_not_called()

    def test_shutdown_kills_active_processes_closes_pipes_and_never_starts_pending(self):
        context, saved, errors = self.simulate(
            ["anto", "incuba", "hng"], until=0.5, workers=2,
            options={"anto": {"duration": 100, "ignore_terminate": True}, "hng": {"duration": 100}},
        )
        self.assertEqual(context.starts, [("anto", 0), ("hng", 0)])
        self.assertEqual(saved, [])
        self.assertEqual(errors, [(0.5, {"anto": "Worker is stopping."}), (0.5, {"hng": "Worker is stopping."})])
        self.assertEqual(context.processes[0].kills, [0.5])

    def test_duplicate_assets_empty_assets_and_already_stopped(self):
        context, _, _ = self.simulate(["hng", "hng"], until=7)
        self.assertEqual(context.starts, [("hng", 0), ("hng", 3), ("hng", 6)])
        context, _, _ = self.simulate([], until=1)
        self.assertEqual(context.starts, [])
        context, _, _ = self.simulate(["hng"], until=0)
        self.assertEqual(context.starts, [])

    def test_main_selects_continuous_scheduler_with_configured_limits(self):
        with (
            patch.dict(os.environ, {
                "POWER_READING_INTERVAL_SECONDS": "180",
                "POWER_READING_MAX_WORKERS": "3",
                "POWER_READING_ASSET_TIMEOUT_SECONDS": "180",
            }, clear=True),
            patch.object(worker, "_configured_assets", return_value=["hng"]),
            patch.object(worker, "_install_signal_handlers"),
            patch.object(worker, "ensure_schema"),
            patch.object(worker, "run_continuous") as continuous,
            patch.object(worker, "run_cycle") as cycle,
            patch.object(worker.STOP_EVENT, "is_set", return_value=False),
        ):
            worker.main()
        continuous.assert_called_once_with(["hng"], 3, 180, 180)
        cycle.assert_not_called()


    def test_unexpected_scheduler_failure_cleans_up_every_active_process(self):
        clock = Clock(10)
        context = Context(clock, {asset: {"duration": 100} for asset in ("anto", "hng")})
        with (
            patch.object(worker, "STOP_EVENT", clock),
            patch.object(worker.time, "monotonic", clock.monotonic),
            patch.object(worker.multiprocessing, "get_context", return_value=context),
            patch.object(worker, "_receive_child_result", side_effect=RuntimeError("unexpected")),
            patch.object(worker, "LOGGER"),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                worker.run_continuous(["anto", "hng"], 2, 5, 1)
        self.assertEqual(len(context.processes), 2)
        self.assertTrue(all(p.closed for p in context.processes))
        self.assertTrue(all(c.closed for c in context.connections))

    def test_real_spawned_processes_repeat_healthy_polling_and_shutdown_cleanly(self):
        context = worker.multiprocessing.get_context("spawn")
        processes = []
        persisted = []
        stop = threading.Event()
        guard = threading.Timer(15, stop.set)

        class SpawnContext:
            Pipe = staticmethod(context.Pipe)

            @staticmethod
            def Process(**kwargs):
                kwargs["target"] = spawned_reader
                process = context.Process(**kwargs)
                processes.append(process)
                return process

        def persist(rows):
            self.assertEqual(rows[0].asset, "hng")
            self.assertTrue(processes[0].is_alive())
            persisted.append(time.monotonic())
            if len(persisted) == 3:
                stop.set()
            return 1

        guard.start()
        try:
            with (
                # Streamlit AppTest can leave __main__ pointing at its script.
                # Spawn only this inert test module, never that application.
                patch.object(sys.modules["__main__"], "__file__", __file__, create=True),
                patch.object(sys.modules["__main__"], "__spec__", None, create=True),
                patch.object(worker, "STOP_EVENT", stop),
                patch.object(worker.multiprocessing, "get_context", return_value=SpawnContext()),
                patch.object(worker, "store_readings", side_effect=persist),
                patch.object(worker, "store_errors") as errors,
                patch.object(worker, "LOGGER"),
            ):
                worker.run_continuous(["slow", "hng"], 2, 12, 1)
        finally:
            guard.cancel()
            guard.join()
        self.assertEqual(len(persisted), 3)
        self.assertGreaterEqual(persisted[-1] - persisted[0], 1.8)
        self.assertLess(persisted[-1] - persisted[0], 8)
        errors.assert_called_once_with({"slow": "Worker is stopping."})
        self.assertTrue(all(process._closed for process in processes))


if __name__ == "__main__":
    unittest.main()
