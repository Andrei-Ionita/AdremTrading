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


@dataclass(frozen=True)
class CollectionResult:
    readings: tuple[PowerReading, ...]
    errors: dict[str, str]


@dataclass
class _AssetProcess:
    process: multiprocessing.Process
    connection: object
    started_at: float


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
            asset = pending.pop(0)
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

    while not STOP_EVENT.is_set():
        cycle_started = time.monotonic()
        try:
            run_cycle(assets, max_workers, asset_timeout_seconds)
        except Exception:
            LOGGER.exception("Power collection cycle failed before completion")
        if run_once:
            return
        elapsed = time.monotonic() - cycle_started
        STOP_EVENT.wait(max(1.0, interval_seconds - elapsed))


def _configured_assets() -> list[str]:
    raw = (os.getenv("POWER_READING_ASSETS") or "").strip()
    assets = [part.strip().lower().replace(" ", "_") for part in raw.split(",") if part.strip()]
    if not assets:
        return list(available_assets())
    unknown = sorted(set(assets) - set(available_assets()))
    if unknown:
        raise ValueError(f"Unknown POWER_READING_ASSETS: {', '.join(unknown)}")
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
