from __future__ import annotations

import json
import logging
import multiprocessing
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from .database import ensure_schema, store_errors, store_readings
from .service import PowerReading, available_assets, read_asset


LOGGER = logging.getLogger("power_reading.worker")
STOP_EVENT = threading.Event()
_SERIAL_COLLECTION_GROUPS = {
    "anto": "adc_monitoring",
    "incuba": "adc_monitoring",
    "ferma_frumusica": "adc_monitoring",
    "start_fotovoltaice": "adc_monitoring",
    "renewable_energy_holding": "adc_monitoring",
    "astro": "aurora",
    "imperial": "aurora",
}


@dataclass(frozen=True)
class CollectionResult:
    readings: tuple[PowerReading, ...]
    errors: dict[str, str]


@dataclass
class _AssetProcess:
    process: multiprocessing.Process
    connection: object
    started_at: float
    stopping_at: float | None = None
    terminated_at: float | None = None
    killed: bool = False


def collect_once(
    assets: Iterable[str],
    *,
    max_workers: int = 4,
    asset_timeout_seconds: int = 120,
    reader: Callable[..., PowerReading] = read_asset,
    on_reading: Callable[[PowerReading], None] | None = None,
) -> CollectionResult:
    asset_list = list(dict.fromkeys(assets))
    if not asset_list:
        return CollectionResult((), {})

    # Dependency-injected readers are used by unit tests and local callers.
    # Production portal reads use killable processes so a stuck browser cannot
    # permanently block every later collection cycle.
    if reader is not read_asset:
        return _collect_with_threads(
            asset_list,
            max_workers=max_workers,
            reader=reader,
            on_reading=on_reading,
        )
    return _collect_with_processes(
        asset_list,
        max_workers=max_workers,
        asset_timeout_seconds=asset_timeout_seconds,
        on_reading=on_reading,
    )


def _collect_with_threads(
    asset_list: list[str],
    *,
    max_workers: int,
    reader: Callable[..., PowerReading],
    on_reading: Callable[[PowerReading], None] | None,
) -> CollectionResult:
    readings: list[PowerReading] = []
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(asset_list)))) as executor:
        futures = {executor.submit(reader, asset, headless=True): asset for asset in asset_list}
        for future in as_completed(futures):
            asset = futures[future]
            try:
                reading = future.result()
                if reading.pv_mw is None and reading.load_mw is None and reading.grid_mw is None:
                    errors[asset] = "No numeric power values were detected."
                else:
                    readings.append(reading)
                    if on_reading is not None:
                        on_reading(reading)
            except Exception as exc:  # Each portal is isolated from the others.
                errors[asset] = f"{type(exc).__name__}: {exc}"

    readings.sort(key=lambda item: asset_list.index(item.asset))
    return CollectionResult(tuple(readings), errors)


def _collect_with_processes(
    asset_list: list[str],
    *,
    max_workers: int,
    asset_timeout_seconds: int,
    on_reading: Callable[[PowerReading], None] | None,
) -> CollectionResult:
    context = multiprocessing.get_context("spawn")
    worker_limit = max(1, min(max_workers, len(asset_list)))
    timeout = max(1, asset_timeout_seconds)
    pending = list(asset_list)
    active: dict[str, _AssetProcess] = {}
    readings: list[PowerReading] = []
    errors: dict[str, str] = {}

    while pending or active:
        while pending and len(active) < worker_limit and not STOP_EVENT.is_set():
            asset = _pop_next_pending_asset(pending, active)
            if asset is None:
                break
            receive_connection, send_connection = context.Pipe(duplex=False)
            process = context.Process(
                target=_read_asset_child,
                args=(asset, send_connection),
                name=f"power-reader-{asset}",
                daemon=True,
            )
            process.start()
            send_connection.close()
            active[asset] = _AssetProcess(process, receive_connection, time.monotonic())

        for asset, task in list(active.items()):
            result = _receive_child_result(task)
            if result is not None:
                _stop_process(task.process)
                task.connection.close()
                del active[asset]
                status, payload = result
                if status == "ok":
                    reading = payload
                    if reading.pv_mw is None and reading.load_mw is None and reading.grid_mw is None:
                        errors[asset] = "No numeric power values were detected."
                    else:
                        readings.append(reading)
                        if on_reading is not None:
                            on_reading(reading)
                else:
                    errors[asset] = str(payload)
                continue

            if not task.process.is_alive():
                exit_code = task.process.exitcode
                task.process.join(timeout=1)
                result = _receive_child_result(task)
                task.connection.close()
                del active[asset]
                if result is not None:
                    status, payload = result
                    if status == "ok":
                        reading = payload
                        if reading.pv_mw is None and reading.load_mw is None and reading.grid_mw is None:
                            errors[asset] = "No numeric power values were detected."
                        else:
                            readings.append(reading)
                            if on_reading is not None:
                                on_reading(reading)
                    else:
                        errors[asset] = str(payload)
                else:
                    errors[asset] = f"Reader process exited without a result (exit code {exit_code})."
                continue

            if STOP_EVENT.is_set() or time.monotonic() - task.started_at >= timeout:
                _stop_process(task.process)
                task.connection.close()
                del active[asset]
                reason = "Worker is stopping." if STOP_EVENT.is_set() else f"Asset read exceeded {timeout} seconds."
                errors[asset] = reason

        if active and not STOP_EVENT.is_set():
            STOP_EVENT.wait(0.1)
        elif STOP_EVENT.is_set():
            pending.clear()

    readings.sort(key=lambda item: asset_list.index(item.asset))
    return CollectionResult(tuple(readings), errors)


def _pop_next_pending_asset(
    pending: list[str], active: dict[str, _AssetProcess]
) -> str | None:
    active_groups = {_collection_group(asset) for asset in active}
    for index, asset in enumerate(pending):
        if _collection_group(asset) not in active_groups:
            return pending.pop(index)
    return None


def _collection_group(asset: str) -> str:
    return _SERIAL_COLLECTION_GROUPS.get(asset, asset)


def _read_asset_child(asset: str, connection) -> None:
    try:
        connection.send(("ok", read_asset(asset, headless=True)))
    except BaseException as exc:  # The parent records portal failures uniformly.
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _receive_child_result(task: _AssetProcess):
    try:
        if task.connection.poll():
            return task.connection.recv()
    except (EOFError, OSError):
        return None
    return None


def _stop_process(process: multiprocessing.Process) -> None:
    process.join(timeout=1)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


def run_cycle(assets: list[str], max_workers: int, asset_timeout_seconds: int) -> CollectionResult:
    started = time.monotonic()
    stored = 0
    storage_errors: dict[str, str] = {}

    def persist_reading(reading: PowerReading) -> None:
        nonlocal stored
        try:
            stored += store_readings((reading,))
        except Exception as exc:
            storage_errors[reading.asset] = f"Could not store reading: {type(exc).__name__}: {exc}"

    result = collect_once(
        assets,
        max_workers=max_workers,
        asset_timeout_seconds=asset_timeout_seconds,
        on_reading=persist_reading,
    )
    errors = {**result.errors, **storage_errors}
    store_errors(errors)
    LOGGER.info(
        json.dumps(
            {
                "event": "power_collection_complete",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "stored": stored,
                "failed": len(errors),
                "errors": errors,
                "duration_seconds": round(time.monotonic() - started, 2),
            },
            ensure_ascii=True,
        )
    )
    return CollectionResult(result.readings, errors)


def _start_asset_process(context, asset: str) -> _AssetProcess:
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = None
    try:
        process = context.Process(
            target=_read_asset_child,
            args=(asset, send_connection),
            name=f"power-reader-{asset}",
            daemon=True,
        )
        started_at = time.monotonic()
        process.start()
        return _AssetProcess(process, receive_connection, started_at)
    except BaseException:
        receive_connection.close()
        if process is not None:
            if process.pid is not None:
                _stop_process(process)
            process.close()
        raise
    finally:
        send_connection.close()


def _reap_asset_process(task: _AssetProcess, now: float) -> bool:
    # Keep the slot and portal lock until exit, without blocking other polls
    # during the normal-exit grace period or terminate/kill escalation.
    task.process.join(timeout=0)
    if not task.process.is_alive():
        task.connection.close()
        task.process.close()
        return True
    if task.terminated_at is None:
        if now - task.stopping_at >= 1:
            task.process.terminate()
            task.terminated_at = now
    elif not task.killed and now - task.terminated_at >= 5:
        task.process.kill()
        task.killed = True
    return False


def _persist_asset_result(asset: str, result: tuple, started_at: float) -> None:
    status, payload = result
    errors: dict[str, str] = {}
    failure_kind = "reader_error"
    stored = 0
    if status == "ok":
        if payload.pv_mw is None and payload.load_mw is None and payload.grid_mw is None:
            errors[asset] = "No numeric power values were detected."
            failure_kind = "no_numeric_power"
        else:
            try:
                stored = store_readings((payload,))
            except Exception as exc:
                errors[asset] = f"Could not store reading: {type(exc).__name__}: {exc}"
                failure_kind = f"storage_error: {type(exc).__name__}"
    else:
        errors[asset] = str(payload)
    if errors:
        try:
            store_errors(errors)
        except Exception as exc:
            LOGGER.error("Could not store power reading errors for %s (%s)", asset, type(exc).__name__)
    LOGGER.info(
        json.dumps(
            {
                "event": "power_collection_complete",
                "asset": asset,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "stored": stored,
                "failed": len(errors),
                # Retain detailed DB errors, but never log portal/DB payloads.
                "errors": {asset: failure_kind for asset in errors},
                "duration_seconds": round(time.monotonic() - started_at, 2),
            },
            ensure_ascii=True,
        )
    )


def run_continuous(
    assets: Iterable[str], max_workers: int, asset_timeout_seconds: int, interval_seconds: int
) -> None:
    """Poll independently, oldest due first, within worker and portal limits.

    Cadence is start-to-start. Overruns coalesce into one pending attempt per
    asset; missed intervals never create an unbounded catch-up queue.
    """
    next_due = dict.fromkeys(assets, time.monotonic())
    if not next_due:
        return
    context = multiprocessing.get_context("spawn")
    worker_limit = max(1, min(max_workers, len(next_due)))
    timeout = max(1, asset_timeout_seconds)
    interval = max(1, interval_seconds)
    active: dict[str, _AssetProcess] = {}
    try:
        while not STOP_EVENT.is_set():
            for asset, task in list(active.items()):
                if task.stopping_at is None:
                    result = _receive_child_result(task)
                    if result is None and not task.process.is_alive():
                        # A final pipe message can race with process exit.
                        result = _receive_child_result(task)
                        if result is None:
                            result = (
                                "error",
                                f"Reader process exited without a result (exit code {task.process.exitcode}).",
                            )
                    if result is None and time.monotonic() - task.started_at >= timeout:
                        result = ("error", f"Asset read exceeded {timeout} seconds.")
                    if result is not None:
                        task.stopping_at = time.monotonic()
                        _persist_asset_result(asset, result, task.started_at)
                if task.stopping_at is not None and _reap_asset_process(task, time.monotonic()):
                    del active[asset]

            # Sorting one deadline per asset is bounded and prevents a fast
            # asset from overtaking older due work under sustained saturation.
            now = time.monotonic()
            pending = sorted(
                (asset for asset, due in next_due.items() if due <= now and asset not in active),
                key=next_due.__getitem__,
            )
            while pending and len(active) < worker_limit and not STOP_EVENT.is_set():
                asset = _pop_next_pending_asset(pending, active)
                if asset is None:
                    break
                started_at = time.monotonic()
                next_due[asset] = started_at + interval
                try:
                    active[asset] = _start_asset_process(context, asset)
                    next_due[asset] = active[asset].started_at + interval
                except Exception as exc:
                    _persist_asset_result(
                        asset, ("error", f"Could not start reader: {type(exc).__name__}: {exc}"), started_at
                    )
            STOP_EVENT.wait(0.1)
    finally:
        for asset, task in active.items():
            try:
                try:
                    _stop_process(task.process)
                    if task.stopping_at is None:
                        result = _receive_child_result(task) or ("error", "Worker is stopping.")
                        _persist_asset_result(asset, result, task.started_at)
                finally:
                    task.connection.close()
                    if not task.process.is_alive():
                        task.process.close()
            except Exception as exc:
                LOGGER.error("Could not finish reader cleanup for %s (%s)", asset, type(exc).__name__)


def main() -> None:
    logging.basicConfig(
        level=(os.getenv("POWER_READING_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    assets = _configured_assets()
    interval_seconds = _positive_int("POWER_READING_INTERVAL_SECONDS", 180)
    max_workers = _positive_int("POWER_READING_MAX_WORKERS", 4)
    asset_timeout_seconds = _positive_int("POWER_READING_ASSET_TIMEOUT_SECONDS", 120)
    run_once = _bool_env("POWER_READING_RUN_ONCE", False)

    _install_signal_handlers()
    ensure_schema()
    LOGGER.info(
        "Power reader started assets=%s interval_seconds=%s max_workers=%s asset_timeout_seconds=%s",
        ",".join(assets),
        interval_seconds,
        max_workers,
        asset_timeout_seconds,
    )

    if STOP_EVENT.is_set():
        return
    if run_once:
        try:
            run_cycle(assets, max_workers, asset_timeout_seconds)
        except Exception:
            LOGGER.exception("Power collection cycle failed before completion")
        return
    run_continuous(assets, max_workers, asset_timeout_seconds, interval_seconds)


def _configured_assets() -> list[str]:
    raw = (os.getenv("POWER_READING_ASSETS") or "").strip()
    assets = [part.strip().lower().replace(" ", "_") for part in raw.split(",") if part.strip()]
    if not assets:
        assets = list(available_assets())
    unknown = sorted(set(assets) - set(available_assets()))
    if unknown:
        raise ValueError(f"Unknown POWER_READING_ASSETS: {', '.join(unknown)}")
    if not _bool_env("ULMENI_ENABLED", False):
        assets = [asset for asset in assets if asset != "ulmeni"]
    return list(dict.fromkeys(assets))


def _positive_int(name: str, default: int) -> int:
    value = int((os.getenv(name) or str(default)).strip())
    if value < 1:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _install_signal_handlers() -> None:
    def stop_worker(signum, frame) -> None:
        LOGGER.info("Power reader stopping after signal %s", signum)
        STOP_EVENT.set()

    for signal_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signal_name, None)
        if sig is not None:
            signal.signal(sig, stop_worker)


if __name__ == "__main__":
    main()
