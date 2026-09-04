from __future__ import annotations

import os
import math
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False


load_dotenv()

DEFAULT_FUSIONSOLAR_URL = "https://eu5.fusionsolar.huawei.com/unisso/login.action"
DEFAULT_AURORA_URL = "https://auroravision.net/ums/v1/loginPage"


@dataclass(frozen=True)
class PowerReading:
    asset: str
    timestamp_utc: str
    pv_mw: float | None
    load_mw: float | None
    grid_mw: float | None
    source: str
    raw_excerpt: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AssetSpec:
    asset_type: str
    env_prefix: str
    default_url: str
    default_plant_name: str


_ASSETS = {
    "elnet": AssetSpec("elnet", "ELNET", DEFAULT_FUSIONSOLAR_URL, "Elnet Biomasa.GR"),
    "anto": AssetSpec("anto", "ANTO", "https://adc-monitoring.ro/", "CEF Anto"),
    "incuba": AssetSpec(
        "incuba_adc",
        "INCUBA_ADC",
        "https://adc-monitoring.ro/",
        "CEF Incuba Reproduction",
    ),
    "motif": AssetSpec("motif", "MOTIF", DEFAULT_FUSIONSOLAR_URL, "Cai de vis"),
    "ferma_frumusica": AssetSpec(
        "ferma_frumusica", "FERMA_FRUMUSICA", "https://adc-monitoring.ro/", "CEF Ferma Frumusica"
    ),
    "start_fotovoltaice": AssetSpec(
        "start_fotovoltaice",
        "START_FOTOVOLTAICE",
        "https://adc-monitoring.ro/",
        "CEF Borcea",
    ),
    "snk": AssetSpec("snk", "SNK", "https://10.99.219.47/ehmi/hmiapp.html", "SNK"),
    "astro": AssetSpec(
        "astro_aurora",
        "ASTRO_AURORA",
        DEFAULT_AURORA_URL,
        "PV Luna de Jos",
    ),
    "hng": AssetSpec("hng", "HNG", "https://app.veltol-ems.ro/locations/3068", "HNG"),
    "pcsun": AssetSpec("pcsun", "PCSUN", "https://oltenita2.epgr.ro/~ViewOfThings/index.html", "PCSun"),
    "ulmeni": AssetSpec(
        "ulmeni",
        "ULMENI",
        "https://oltenita1.epgr.ro/~ViewOfThings/index.html",
        "Solar Energy Ulmeni",
    ),
    "imperial": AssetSpec("imperial", "IMPERIAL", DEFAULT_AURORA_URL, "PV Jucu"),
    "horeco": AssetSpec("horeco", "HORECO", DEFAULT_FUSIONSOLAR_URL, "CEF HORECO Costesti"),
    "necaluxan": AssetSpec(
        "necaluxan",
        "NECALUXAN",
        "https://www1.meteocontrol.de/vcom/default/login/index/",
        "RO-Slatioara 31.6MWp",
    ),
    "mm_mv": AssetSpec(
        "isolarcloud",
        "MM_MV",
        "https://web3.isolarcloud.eu/#/login",
        "Reghin",
    ),
    "anasun": AssetSpec(
        "anasun_isolarcloud",
        "ANASUN",
        "https://web3.isolarcloud.eu/#/login",
        "AnaSun Ulmi",
    ),
}

_INTERVAL_CACHE: dict[tuple[str, str, str], tuple[float, float]] = {}
_INTERVAL_CACHE_LOCK = threading.Lock()
_INTERVAL_CACHE_SECONDS = 15 * 60.0
_MAX_SOURCE_INTERVAL_LOOKBACK = 4


def available_assets() -> tuple[str, ...]:
    return tuple(_ASSETS)


def read_asset(asset: str, *, headless: bool = True) -> PowerReading:
    """Fetch one live reading and normalize all power fields to MW."""
    key = _normalize_asset(asset)
    spec = _ASSETS[key]
    scraper = _build_scraper(spec, headless=headless)
    snapshot = scraper.scrape_once()
    return PowerReading(
        asset=key,
        timestamp_utc=snapshot.timestamp_utc,
        pv_mw=_to_mw(snapshot.pv_kw, key),
        load_mw=_to_mw(snapshot.load_kw, key),
        grid_mw=_to_mw(snapshot.grid_kw, key),
        source=snapshot.source,
        raw_excerpt=snapshot.raw_excerpt,
    )


def read_interval_energy(
    asset: str,
    *,
    start: datetime,
    end: datetime,
    headless: bool = True,
) -> float:
    """Fetch one completed interval from its source and return production in MWh."""
    key = _normalize_asset(asset)
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Interval boundaries must be timezone-aware.")
    if start >= end:
        raise ValueError("Interval start must be earlier than interval end.")
    if (end - start).total_seconds() != 15 * 60:
        raise ValueError("Power correction requires exactly one 15-minute interval.")

    cache_key = (key, start.isoformat(), end.isoformat())
    now = time.monotonic()
    with _INTERVAL_CACHE_LOCK:
        cached = _INTERVAL_CACHE.get(cache_key)
        if cached is not None and now - cached[0] <= _INTERVAL_CACHE_SECONDS:
            return cached[1]

    scraper = _build_scraper(_ASSETS[key], headless=headless)
    reader = getattr(scraper, "read_interval_energy", None)
    if not callable(reader):
        raise RuntimeError(
            f"{key} does not yet expose verified historical interval production."
        )
    energy_mwh = float(reader(start=start, end=end))
    if not math.isfinite(energy_mwh) or energy_mwh < 0:
        raise RuntimeError(f"{key} returned invalid interval production.")
    with _INTERVAL_CACHE_LOCK:
        _INTERVAL_CACHE[cache_key] = (now, energy_mwh)
    return energy_mwh


def read_latest_interval_energy(
    asset: str,
    *,
    end: datetime,
    headless: bool = True,
) -> tuple[datetime, float]:
    """Return the most recent source-published 15-minute production interval.

    Some portals publish a completed quarter several minutes late.  We only
    step back when their response explicitly identifies an unpublished
    interval; authentication and data-quality failures remain visible.
    """
    if end.tzinfo is None:
        raise ValueError("Interval end must be timezone-aware.")
    if end != end.replace(second=0, microsecond=0) or end.minute % 15:
        raise ValueError("Interval end must be on a 15-minute boundary.")

    last_unpublished_error: Exception | None = None
    for offset in range(_MAX_SOURCE_INTERVAL_LOOKBACK):
        candidate_end = end - timedelta(minutes=15 * offset)
        candidate_start = candidate_end - timedelta(minutes=15)
        try:
            return candidate_end, read_interval_energy(
                asset,
                start=candidate_start,
                end=candidate_end,
                headless=headless,
            )
        except Exception as exc:
            if not _is_unpublished_interval_error(exc):
                raise
            last_unpublished_error = exc

    key = _normalize_asset(asset)
    raise RuntimeError(
        f"{key} has no source-published completed interval within the last "
        f"{_MAX_SOURCE_INTERVAL_LOOKBACK * 15} minutes: {last_unpublished_error}"
    ) from last_unpublished_error


def clear_interval_cache() -> None:
    with _INTERVAL_CACHE_LOCK:
        _INTERVAL_CACHE.clear()


def _is_unpublished_interval_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "missing a boundary for the completed interval",
            "missing a completed-interval boundary",
            "did not return all boundaries for the completed interval",
            "missing power value in the completed interval",
            "power history covers only",
        )
    )


def read_all_assets(
    assets: Iterable[str] | None = None, *, headless: bool = True
) -> tuple[list[PowerReading], dict[str, str]]:
    """Fetch assets independently, returning successful readings and per-asset errors."""
    readings: list[PowerReading] = []
    errors: dict[str, str] = {}
    for asset in assets or available_assets():
        key = _normalize_asset(asset)
        try:
            readings.append(read_asset(key, headless=headless))
        except Exception as exc:  # One portal failure must not discard other readings.
            errors[key] = f"{type(exc).__name__}: {exc}"
    return readings, errors


def _build_scraper(spec: AssetSpec, *, headless: bool):
    username, password = _credentials(spec)
    url = _url(spec)
    plant_name = _env(f"{spec.env_prefix}_PLANT_NAME") or spec.default_plant_name
    if spec.asset_type in {
        "anto",
        "incuba_adc",
        "ferma_frumusica",
        "start_fotovoltaice",
    }:
        profile_key = "adc_monitoring"
    elif spec.asset_type in {"imperial", "astro_aurora"}:
        profile_key = "aurora"
    else:
        profile_key = spec.asset_type
    profile_dir = _profile_dir(profile_key)

    if spec.asset_type in {
        "anto",
        "incuba_adc",
        "ferma_frumusica",
        "start_fotovoltaice",
    }:
        from .scrapers.adc_monitoring_scraper import ADCMonitoringScraper

        return ADCMonitoringScraper(
            target_url=url,
            username=username,
            password=password,
            plant_name=plant_name,
            user_data_dir=str(profile_dir),
            headless=headless,
        )

    if spec.asset_type == "hng":
        from .scrapers.veltol_scraper import VeltolScraper

        return VeltolScraper(
            target_url=url,
            username=username,
            password=password,
            user_data_dir=str(profile_dir),
            headless=headless,
        )

    if spec.asset_type in {"pcsun", "ulmeni"}:
        from .scrapers.pcsun_scraper import PCSunScraper

        prefix = spec.env_prefix
        return PCSunScraper(
            target_url=url,
            username=username,
            password=password,
            http_username=_env(f"{prefix}_HTTP_USERNAME"),
            http_password=_env(f"{prefix}_HTTP_PASSWORD"),
            active_power_tag=_env(f"{prefix}_ACTIVE_POWER_TAG"),
            timeout_sec=_int_env(f"{prefix}_TIMEOUT_SEC", 60),
            source_name=spec.asset_type,
        )

    if spec.asset_type in {"imperial", "astro_aurora"}:
        from .scrapers.imperial_scraper import ImperialScraper

        return ImperialScraper(
            target_url=url,
            username=username,
            password=password,
            plant_name=plant_name,
            user_data_dir=str(profile_dir),
            headless=headless,
            force_relogin_each_run=_bool_env("IMPERIAL_FORCE_RELOGIN", bool(os.getenv("RAILWAY_ENVIRONMENT"))),
            secondary_plant_name=(
                None if spec.asset_type == "astro_aurora" else "Imperial 2"
            ),
            source_prefix="astro-aurora" if spec.asset_type == "astro_aurora" else "imperial",
        )

    if spec.asset_type == "snk":
        from .scrapers.snk_scraper import SNKScraper

        return SNKScraper(
            target_url=url,
            username=username,
            password=password,
            user_data_dir=str(profile_dir),
            headless=True,
            post_login_wait_ms=_int_env("SNK_POST_LOGIN_WAIT_MS", 6000),
            value_wait_attempts=_int_env("SNK_VALUE_WAIT_ATTEMPTS", 90),
            value_wait_sleep_ms=_int_env("SNK_VALUE_WAIT_SLEEP_MS", 2500),
            session_attempts=_int_env("SNK_SESSION_ATTEMPTS", 3),
            debug_artifact_dir=_env("SNK_DEBUG_ARTIFACT_DIR"),
            window_mode=_env("SNK_WINDOW_MODE") or "offscreen",
        )

    if spec.asset_type == "necaluxan":
        from .scrapers.necaluxan_scraper import NecaluxanScraper

        return NecaluxanScraper(
            target_url=url,
            username=username,
            password=password,
            master_username=_env("NECALUXAN_MASTER_USERNAME"),
            master_password=_env("NECALUXAN_MASTER_PASSWORD"),
            plant_name=plant_name,
            user_data_dir=str(profile_dir),
            headless=headless,
            browser_timeout_ms=_int_env("NECALUXAN_BROWSER_TIMEOUT_MS", 60_000),
        )

    if spec.asset_type in {"isolarcloud", "anasun_isolarcloud"}:
        from .scrapers.isolarcloud_scraper import ISolarCloudScraper

        return ISolarCloudScraper(
            target_url=url,
            username=username,
            password=password,
            plant_name=plant_name,
            user_data_dir=str(profile_dir),
            headless=headless,
            browser_timeout_ms=_int_env(
                f"{spec.env_prefix}_BROWSER_TIMEOUT_MS", 60_000
            ),
            credential_env_prefix=spec.env_prefix,
        )

    from .scrapers.fusionsolar_scraper import FusionSolarScraper

    region = _env(f"{spec.env_prefix}_REGION_NAME")
    if spec.asset_type == "horeco":
        region = region or "region004"
    return FusionSolarScraper(
        target_url=url,
        username=username,
        password=password,
        plant_name=plant_name,
        region_name=region,
        user_data_dir=str(profile_dir),
        headless=headless,
    )


def _credentials(spec: AssetSpec) -> tuple[str | None, str | None]:
    username = _env(f"{spec.env_prefix}_USERNAME")
    password = _env(f"{spec.env_prefix}_PASSWORD")
    if spec.asset_type == "astro_aurora":
        return username or _env("IMPERIAL_USERNAME"), password or _env("IMPERIAL_PASSWORD")
    if spec.asset_type == "elnet":
        return username or _env("FUSIONSOLAR_USERNAME"), password or _env("FUSIONSOLAR_PASSWORD")
    if spec.asset_type == "horeco":
        return (
            username or _env("INCUBA_USERNAME") or _env("FUSIONSOLAR_USERNAME"),
            password or _env("INCUBA_PASSWORD") or _env("FUSIONSOLAR_PASSWORD"),
        )
    if spec.asset_type in {"incuba_adc", "ferma_frumusica", "start_fotovoltaice"}:
        return username or _env("ANTO_USERNAME"), password or _env("ANTO_PASSWORD")
    return username, password


def _url(spec: AssetSpec) -> str:
    url = _env(f"{spec.env_prefix}_URL")
    if spec.asset_type == "astro_aurora":
        url = url or _env("IMPERIAL_URL")
    if spec.asset_type in {"elnet", "motif", "horeco"}:
        url = url or _env("FUSIONSOLAR_PORTAL_URL") or _env("FUSIONSOLAR_URL")
    return url or spec.default_url


def _profile_dir(asset: str) -> Path:
    root = Path(_env("POWER_READING_PROFILE_DIR") or ".playwright_profiles")
    path = root / asset
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_mw(value: float | None, asset: str) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if asset in {"imperial", "astro"}:
        return numeric
    if asset == "elnet" and numeric > 10_000:
        return numeric / 1_000_000.0
    return numeric / 1000.0


def _normalize_asset(asset: str) -> str:
    key = str(asset).strip().lower().replace(" ", "_")
    if key == "fusionsolar":
        key = "elnet"
    if key not in _ASSETS:
        raise ValueError(f"Unknown asset {asset!r}. Expected one of: {', '.join(_ASSETS)}")
    return key


def _env(name: str) -> str | None:
    return (os.getenv(name) or "").strip() or None


def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw is not None else default


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
