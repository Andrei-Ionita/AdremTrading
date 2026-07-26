from __future__ import annotations

import json
import logging
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


def collect_once(
    assets: Iterable[str],
    *,
    max_workers: int = 4,
    reader: Callable[..., PowerReading] = read_asset,
) -> CollectionResult:
    asset_list = list(dict.fromkeys(assets))
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
            except Exception as exc:  # Each portal is isolated from the others.
                errors[asset] = f"{type(exc).__name__}: {exc}"

    readings.sort(key=lambda item: asset_list.index(item.asset))
    return CollectionResult(tuple(readings), errors)


def run_cycle(assets: list[str], max_workers: int) -> CollectionResult:
    started = time.monotonic()
    result = collect_once(assets, max_workers=max_workers)
    stored = store_readings(result.readings)
    store_errors(result.errors)
    LOGGER.info(
        json.dumps(
            {
                "event": "power_collection_complete",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "stored": stored,
                "failed": len(result.errors),
                "errors": result.errors,
                "duration_seconds": round(time.monotonic() - started, 2),
            },
            ensure_ascii=True,
        )
    )
    return result


def main() -> None:
    logging.basicConfig(
        level=(os.getenv("POWER_READING_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    assets = _configured_assets()
    interval_seconds = _positive_int("POWER_READING_INTERVAL_SECONDS", 180)
    max_workers = _positive_int("POWER_READING_MAX_WORKERS", 4)
    run_once = _bool_env("POWER_READING_RUN_ONCE", False)

    _install_signal_handlers()
    ensure_schema()
    LOGGER.info(
        "Power reader started assets=%s interval_seconds=%s max_workers=%s",
        ",".join(assets),
        interval_seconds,
        max_workers,
    )

    while not STOP_EVENT.is_set():
        cycle_started = time.monotonic()
        try:
            run_cycle(assets, max_workers)
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
