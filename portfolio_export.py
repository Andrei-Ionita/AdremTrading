"""Assemble both portfolio downloads from the same timestamp-aligned quarters."""
from pathlib import Path

import numpy as np
import pandas as pd

import portfolio_intraday as intraday
from incuba_intraday import ADREM_TO_INCUBA_SCALE, INCUBA_INTRADAY_RESULTS_PATH


RENEWABLE_ENERGY_HOLDING_START = pd.Timestamp("2026-10-01")
RENEWABLE_ENERGY_HOLDING_SCALE = 2.37 / 2.7
RENEWABLE_ENERGY_HOLDING_RESULTS_PATH = (
    Path("Renewable Energy Holding") / "Results_Production_Renewable_Energy_Holding_xgb_15min.xlsx"
)


# Column suffix, directory, production filename suffix, Solcast location.
ASSET_FILES = (
    ("Astro", "Astro", "Astro", "Luna"),
    ("Imperial", "Imperial", "Imperial", "Jucu"),
    ("Kahraman", "Kahraman", "Kahraman", "Telesti"),
    ("SolEn_Ulmeni", "Solar Energy Ulmeni", "SolarEnergy", "Oltenita"),
    ("PCSunEn", "PC SunEnergy", "SunEnergy", "Oltenita"),
    ("Elnet", "Elnet", "Elnet", "Bucsani"),
    ("Horeco", "Horeco", "Horeco", "Buzau"),
    ("Dragosel", "Dragosel", "Dragosel", "Dragosel"),
    ("GESS", "GESS", "GESS", "Brezoaia"),
    ("NRG", "NRG", "NRG", "Mihaesti"),
    ("Sun_Grow_Lucia", "Sun_Grow_Lucia", "Sun_Grow_Lucia", "Vulcan"),
    ("Photovoltaic_Energy_Project", "Photovoltaic_Energy_Project", "Photovoltaic_Energy_Project", "Racari"),
    ("MM_MV", "MM_MV", "MM_MV", "Reghin"),
    ("Rosiori", "Rosiori", "Rosiori", "Rosiori"),
    ("Necaluxan", "Necaluxan", "Necaluxan", "Salcioara"),
    ("Adrem", "Adrem", "Adrem", "Apold"),
    ("Anto", "Anto", "Anto", "Uileacu"),
    ("Ferma", "Ferma", "Ferma", "Axintele"),
    ("HNG", "HNG", "HNG", "Mures"),
    ("AnaSun", "AnaSun", "AnaSun", "Ulmi"),
    ("GCSP", "GCSP", "GCSP", "Nusfalau"),
)

CORRECTIONS = {
    "astro": ("Astro", intraday.ASTRO_INTRADAY_CONFIG.intraday_results_path),
    "imperial": ("Imperial", intraday.IMPERIAL_INTRADAY_CONFIG.intraday_results_path),
    "mm_mv": ("MM_MV", intraday.MM_MV_INTRADAY_CONFIG.intraday_results_path),
    "elnet": ("Elnet", intraday.ELNET_INTRADAY_CONFIG.intraday_results_path),
    "horeco": ("Horeco", intraday.HORECO_INTRADAY_CONFIG.intraday_results_path),
    "hng": ("HNG", intraday.HNG_INTRADAY_CONFIG.intraday_results_path),
    "incuba": ("Incuba", INCUBA_INTRADAY_RESULTS_PATH),
    "anto": ("Anto", intraday.ANTO_INTRADAY_CONFIG.intraday_results_path),
    "ferma": ("Ferma", intraday.FERMA_INTRADAY_CONFIG.intraday_results_path),
    "necaluxan": ("Necaluxan", intraday.NECALUXAN_INTRADAY_CONFIG.intraday_results_path),
    "ulmeni": ("SolEn_Ulmeni", intraday.ULMENI_INTRADAY_CONFIG.intraday_results_path),
    "start_fotovoltaice": ("Start_Fotovoltaice", intraday.START_FOTOVOLTAICE_INTRADAY_CONFIG.intraday_results_path),
    "anasun": ("AnaSun", intraday.ANASUN_INTRADAY_CONFIG.intraday_results_path),
}


def _timestamps(values):
    parsed = pd.to_datetime(values, errors="raise", format="mixed")
    if parsed.dt.tz is not None:
        parsed = parsed.dt.tz_convert("Europe/Bucharest").dt.tz_localize(None)
    if parsed.isna().any() or (parsed != parsed.dt.floor("15min")).any():
        raise ValueError("Forecast timestamps must be valid 15-minute boundaries.")
    return pd.DatetimeIndex(parsed)


def _series(frame, value_column, label):
    stamps = _timestamps(frame["Data"])
    if stamps.duplicated().any():
        raise ValueError(f"{label} has duplicate forecast timestamps.")
    values = pd.to_numeric(frame[value_column], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"{label} has missing, negative or nonfinite predictions.")
    return pd.Series(values, index=stamps).sort_index()


def _align_baseline(series, targets, weather_path, label):
    aligned = series.reindex(targets)
    missing = aligned.index[aligned.isna()]
    if len(missing):
        # Some legacy base forecasts omit UTC midnight. Only confirmed darkness
        # permits filling a missing production row with zero.
        weather = pd.read_csv(weather_path)
        stamps = pd.to_datetime(weather["period_end"], utc=True, errors="raise", format="mixed")
        stamps = stamps.dt.tz_convert("Europe/Bucharest").dt.tz_localize(None)
        radiation = pd.Series(pd.to_numeric(weather["ghi"], errors="raise").to_numpy(), index=stamps)
        if radiation.index.duplicated().any():
            raise ValueError(f"{label} has duplicate weather timestamps.")
        dark = radiation.reindex(missing).eq(0)
        aligned.loc[missing[dark]] = 0.0
    missing = aligned.index[aligned.isna()]
    if len(missing):
        raise ValueError(f"{label} is missing forecast production for {missing[0]}.")
    return aligned


def build_quarter_hourly_portfolio(enabled_corrections):
    baselines = {}
    weather_paths = {}
    for suffix, directory, filename, location in ASSET_FILES:
        path = Path(directory) / f"Results_Production_{filename}_xgb_15min.xlsx"
        frame = pd.read_excel(path)
        series = _series(frame, "Prediction", suffix)
        if series.empty:
            raise ValueError(f"{suffix} has no base forecast rows.")
        baselines[suffix] = series
        weather_paths[suffix] = Path(directory) / "Solcast" / f"{location}_15min.csv"

    start = max(series.index.min() for series in baselines.values())
    end = min(series.index.max() for series in baselines.values())
    if start > end:
        raise ValueError("Asset forecasts have no common delivery horizon. Refresh the portfolio forecasts.")
    targets = pd.date_range(start, end, freq="15min")
    result = pd.DataFrame(index=targets)
    for suffix, series in baselines.items():
        result[f"Prediction_{suffix}"] = _align_baseline(series, targets, weather_paths[suffix], suffix)
    before_switch = targets < RENEWABLE_ENERGY_HOLDING_START
    if (~before_switch).any():
        result["Prediction_Renewable_Energy_Holding"] = (
            result["Prediction_Elnet"] * RENEWABLE_ENERGY_HOLDING_SCALE
        ).where(~before_switch, 0.0)
    result["Prediction_Incuba"] = result["Prediction_Adrem"] * ADREM_TO_INCUBA_SCALE
    result["Prediction_Start_Fotovoltaice"] = result["Prediction_SolEn_Ulmeni"] * intraday.START_FOTOVOLTAICE_SCALE

    for asset, (suffix, path) in CORRECTIONS.items():
        column = f"Prediction_{suffix}"
        if column not in result:
            continue
        if not enabled_corrections.get(asset, False) or not path.is_file():
            continue
        corrected = pd.read_excel(path)
        if corrected.empty:
            continue
        values = _series(corrected, "Prediction_ID", suffix).reindex(targets)
        result[column] = values.combine_first(result[column])

    column = "Prediction_Renewable_Energy_Holding"
    if column in result:
        values = result.pop(column)
        result.insert(result.columns.get_loc("Prediction_Anto") + 1, column, values)

    # Preserve the established order of derived/new assets before Lookup.
    for suffix in ("Incuba", "Start_Fotovoltaice", "AnaSun", "GCSP"):
        column = f"Prediction_{suffix}"
        result[column] = result.pop(column)
    result.insert(0, "Data", targets)
    result.insert(1, "Interval", targets.hour * 4 + targets.minute // 15 + 1)
    result["Lookup"] = result["Data"].dt.strftime("%d.%m.%Y") + result["Interval"].astype(str)
    return result.reset_index(drop=True)


def aggregate_hourly_portfolio(quarters):
    stamps = _timestamps(quarters["Data"])
    if stamps.duplicated().any():
        raise ValueError("Cannot aggregate duplicate forecast quarters.")
    columns = [column for column in quarters if column.startswith("Prediction_")]
    values = quarters[columns].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(values.to_numpy(dtype=float)).all() or (values < 0).any().any():
        raise ValueError("Cannot aggregate missing or invalid forecast quarters.")
    values.index = stamps
    groups = values.groupby(values.index.floor("h"))
    counts = groups.size()
    # A partial leading/trailing hour is not a complete hourly energy forecast.
    result = groups.sum(min_count=4).loc[counts == 4].copy()
    hours = result.index
    result.insert(0, "Data", hours.normalize())
    result.insert(1, "Interval", hours.hour + 1)
    result["Lookup"] = result["Data"].dt.strftime("%d.%m.%Y") + result["Interval"].astype(str)
    return result.reset_index(drop=True)
