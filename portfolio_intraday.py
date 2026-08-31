from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
LOCAL_TIMEZONE = "Europe/Bucharest"
MAX_SAMPLE_GAP = pd.Timedelta(minutes=7, seconds=30)
CORRECTION_INITIAL_WEIGHT = 1.0
CORRECTION_HALF_LIFE_MINUTES = 120.0
MIN_ACTUAL_TO_FORECAST_RATIO = 0.5


@dataclass(frozen=True)
class PortfolioIntradayConfig:
    asset_key: str
    display_name: str
    dam_results_path: Path
    weather_path: Path
    intraday_results_path: Path
    max_interval_energy_mwh: float | None = None
    min_actual_to_forecast_ratio: float | None = MIN_ACTUAL_TO_FORECAST_RATIO
    baseline_scale: float = 1.0


ASTRO_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="astro",
    display_name="Astro",
    dam_results_path=APP_ROOT / "Astro" / "Results_Production_Astro_xgb_15min.xlsx",
    weather_path=APP_ROOT / "Astro" / "Solcast" / "Luna_15min.csv",
    intraday_results_path=(
        APP_ROOT / "Astro" / "Results_Production_Astro_DAM_Corrected_Intraday_15min.xlsx"
    ),
)
IMPERIAL_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="imperial",
    display_name="Imperial",
    dam_results_path=APP_ROOT / "Imperial" / "Results_Production_Imperial_xgb_15min.xlsx",
    weather_path=APP_ROOT / "Imperial" / "Solcast" / "Jucu_15min.csv",
    intraday_results_path=(
        APP_ROOT / "Imperial" / "Results_Production_Imperial_DAM_Corrected_Intraday_15min.xlsx"
    ),
)
ANTO_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="anto",
    display_name="Anto",
    dam_results_path=APP_ROOT / "Anto" / "Results_Production_Anto_xgb_15min.xlsx",
    weather_path=APP_ROOT / "Anto" / "Solcast" / "Uileacu_15min.csv",
    intraday_results_path=(
        APP_ROOT / "Anto" / "Results_Production_Anto_DAM_Corrected_Intraday_15min.xlsx"
    ),
)
MOTIF_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="motif",
    display_name="Motif",
    dam_results_path=APP_ROOT / "Motif" / "Results_Production_Motif_xgb_15min.xlsx",
    weather_path=APP_ROOT / "Motif" / "Solcast" / "Varfu_Campului_15min.csv",
    intraday_results_path=(
        APP_ROOT / "Motif" / "Results_Production_Motif_DAM_Corrected_Intraday_15min.xlsx"
    ),
)
FERMA_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="ferma_frumusica",
    display_name="Ferma Frumusica",
    dam_results_path=APP_ROOT / "Ferma" / "Results_Production_Ferma_xgb_15min.xlsx",
    weather_path=APP_ROOT / "Ferma" / "Solcast" / "Axintele_15min.csv",
    intraday_results_path=(
        APP_ROOT / "Ferma" / "Results_Production_Ferma_DAM_Corrected_Intraday_15min.xlsx"
    ),
)
NECALUXAN_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="necaluxan",
    display_name="Necaluxan",
    dam_results_path=(
        APP_ROOT / "Necaluxan" / "Results_Production_Necaluxan_xgb_15min.xlsx"
    ),
    weather_path=APP_ROOT / "Necaluxan" / "Solcast" / "Salcioara_15min.csv",
    intraday_results_path=(
        APP_ROOT
        / "Necaluxan"
        / "Results_Production_Necaluxan_DAM_Corrected_Intraday_15min.xlsx"
    ),
)
ULMENI_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="ulmeni",
    display_name="Solar Energy Ulmeni",
    dam_results_path=(
        APP_ROOT
        / "Solar Energy Ulmeni"
        / "Results_Production_SolarEnergy_xgb_15min.xlsx"
    ),
    weather_path=(
        APP_ROOT / "Solar Energy Ulmeni" / "Solcast" / "Oltenita_15min.csv"
    ),
    intraday_results_path=(
        APP_ROOT
        / "Solar Energy Ulmeni"
        / "Results_Production_SolarEnergy_DAM_Corrected_Intraday_15min.xlsx"
    ),
    max_interval_energy_mwh=4.35 / 4,
)
START_FOTOVOLTAICE_SCALE = 0.996 / 4.44
START_FOTOVOLTAICE_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="start_fotovoltaice",
    display_name="Start Fotovoltaice",
    dam_results_path=(
        APP_ROOT
        / "Solar Energy Ulmeni"
        / "Results_Production_SolarEnergy_xgb_15min.xlsx"
    ),
    weather_path=(
        APP_ROOT / "Solar Energy Ulmeni" / "Solcast" / "Oltenita_15min.csv"
    ),
    intraday_results_path=(
        APP_ROOT
        / "Start Fotovoltaice"
        / "Results_Production_Start_Fotovoltaice_Derived_Corrected_Intraday_15min.xlsx"
    ),
    max_interval_energy_mwh=0.996 / 4,
    baseline_scale=START_FOTOVOLTAICE_SCALE,
)
ANASUN_INTRADAY_CONFIG = PortfolioIntradayConfig(
    asset_key="anasun",
    display_name="AnaSun",
    dam_results_path=(
        APP_ROOT / "AnaSun" / "Results_Production_AnaSun_xgb_15min.xlsx"
    ),
    weather_path=APP_ROOT / "AnaSun" / "Solcast" / "Ulmi_15min.csv",
    intraday_results_path=(
        APP_ROOT
        / "AnaSun"
        / "Results_Production_AnaSun_DAM_Corrected_Intraday_15min.xlsx"
    ),
    max_interval_energy_mwh=7.5 / 4,
)


class PortfolioIntradayError(RuntimeError):
    """Base error for a safe, user-facing portfolio correction failure."""


class PortfolioIntradayInputError(PortfolioIntradayError):
    """Raised when live production, DAM baseline, or weather is invalid."""


def calculate_interval_energy(
    config: PortfolioIntradayConfig,
    readings,
    interval_start: pd.Timestamp,
    interval_end: pd.Timestamp,
) -> float:
    start = _local_timestamp(interval_start)
    end = _local_timestamp(interval_end)
    if start >= end:
        raise PortfolioIntradayInputError(
            f"The {config.display_name} production interval is invalid."
        )

    samples = []
    for reading in readings:
        observed_raw = getattr(reading, "timestamp_utc", None)
        try:
            observed_at = pd.Timestamp(observed_raw)
        except Exception as exc:
            raise PortfolioIntradayInputError(
                f"A {config.display_name} production timestamp is invalid."
            ) from exc
        if observed_at.tzinfo is None:
            raise PortfolioIntradayInputError(
                f"A {config.display_name} production timestamp has no timezone."
            )
        observed_at = observed_at.tz_convert(LOCAL_TIMEZONE)
        power_mw = _finite_number(
            getattr(reading, "pv_mw", None),
            f"{config.display_name} power",
        )
        if power_mw < 0:
            raise PortfolioIntradayInputError(
                f"{config.display_name} production cannot be negative."
            )
        samples.append((observed_at, power_mw))

    if not samples:
        raise PortfolioIntradayInputError(
            f"No {config.display_name} power samples are available for the completed "
            f"interval {start} to {end}."
        )

    samples.sort(key=lambda sample: sample[0])
    timestamps = pd.DatetimeIndex(sample[0] for sample in samples)
    if timestamps.duplicated().any():
        raise PortfolioIntradayInputError(
            f"{config.display_name} production contains duplicate sample timestamps."
        )
    if timestamps[-1] > end:
        raise PortfolioIntradayInputError(
            f"{config.display_name} interval energy cannot use samples after the interval end."
        )

    interval_samples = [sample for sample in samples if start <= sample[0] <= end]
    if len(interval_samples) < 2:
        raise PortfolioIntradayInputError(
            f"{config.display_name} interval energy requires at least two power samples "
            "from the completed interval."
        )
    gaps = pd.Series([sample[0] for sample in interval_samples]).diff().dropna()
    if (gaps > MAX_SAMPLE_GAP).any():
        raise PortfolioIntradayInputError(
            f"{config.display_name} power samples contain a gap larger than 7.5 minutes."
        )

    points = list(interval_samples)
    if points[0][0] > start:
        points.insert(0, (start, points[0][1]))
    if points[-1][0] < end:
        points.append((end, points[-1][1]))

    energy_mwh = 0.0
    for (left_time, left_power), (right_time, right_power) in zip(points, points[1:]):
        duration_hours = (right_time - left_time).total_seconds() / 3600
        energy_mwh += (left_power + right_power) / 2 * duration_hours
    if not np.isfinite(energy_mwh) or energy_mwh < 0:
        raise PortfolioIntradayInputError(
            f"Calculated {config.display_name} interval energy is invalid."
        )
    return float(energy_mwh)


def get_latest_forecast_origin(
    config: PortfolioIntradayConfig,
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    )
    forecast_origin = _latest_completed_origin(current_time)
    interval_start = forecast_origin - pd.Timedelta(minutes=15)

    if readings_getter is None:
        from power_reading.service import read_interval_energy

        try:
            energy_mwh = read_interval_energy(
                config.asset_key,
                start=interval_start.tz_convert("UTC").to_pydatetime(),
                end=forecast_origin.tz_convert("UTC").to_pydatetime(),
            )
        except Exception as exc:
            raise PortfolioIntradayInputError(
                f"Could not retrieve {config.display_name} interval production: {exc}"
            ) from exc
        return forecast_origin, energy_mwh
    try:
        readings = readings_getter(
            config.asset_key,
            start=interval_start.tz_convert("UTC").to_pydatetime(),
            end=forecast_origin.tz_convert("UTC").to_pydatetime(),
        )
    except Exception as exc:
        raise PortfolioIntradayInputError(
            f"Could not retrieve {config.display_name} interval production: {exc}"
        ) from exc

    energy_mwh = calculate_interval_energy(config, readings, interval_start, forecast_origin)
    return forecast_origin, energy_mwh


def predict_portfolio_intraday(
    config: PortfolioIntradayConfig,
    dam_forecast: pd.DataFrame,
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_interval_energy_mwh: float,
    *,
    target_start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise PortfolioIntradayInputError(
            "Forecast_origin must be on a 15-minute boundary."
        )

    targets, baseline = _remaining_dam_baseline(
        config,
        dam_forecast,
        origin,
        target_start=target_start,
    )
    if baseline.empty:
        return _empty_result()

    radiation = _target_radiation(config, weather_data, targets)
    actual_energy = _finite_number(last_interval_energy_mwh, "Last_Productie")
    if actual_energy < 0:
        raise PortfolioIntradayInputError("Last_Productie cannot be negative.")

    dam_predictions = baseline["Prediction_DAM"].to_numpy(dtype=float)
    reference_prediction = float(dam_predictions[0])
    residual = actual_energy - reference_prediction
    forecast_horizons = ((targets - origin) / pd.Timedelta(minutes=1)).astype(int)
    correction_weights = _correction_weights(config, forecast_horizons)
    if _suppress_downward_correction(config, actual_energy, reference_prediction):
        correction_weights = np.zeros(len(targets), dtype=float)
        corrections = np.zeros(len(targets), dtype=float)
    else:
        corrections = correction_weights * residual
    predictions = np.maximum(dam_predictions + corrections, 0)
    if config.max_interval_energy_mwh is not None:
        predictions = np.minimum(predictions, config.max_interval_energy_mwh)
    dark_targets = radiation <= 0
    predictions[dark_targets] = 0
    corrections[dark_targets] = -dam_predictions[dark_targets]

    if not np.isfinite(predictions).all():
        raise PortfolioIntradayInputError(
            f"{config.display_name} corrected predictions contain NaN or infinite values."
        )

    return pd.DataFrame(
        {
            "Data": targets.tz_localize(None),
            "Interval": baseline["Interval"].to_numpy(dtype=int),
            "Prediction_DAM": np.round(dam_predictions, 3),
            "Correction": np.round(corrections, 3),
            "Prediction_ID": np.round(predictions, 3),
            "Forecast_origin": origin.tz_localize(None),
            "Last_Productie": actual_energy,
            "Reference_DAM_Prediction": round(reference_prediction, 3),
            "Actual_minus_DAM": round(residual, 3),
            "Correction_weight": np.round(correction_weights, 4),
            "Forecast_horizon_minutes": forecast_horizons,
            "Market": "Intraday",
        }
    )


def _correction_weights(
    config: PortfolioIntradayConfig,
    forecast_horizons: pd.Index,
) -> np.ndarray:
    threshold = config.min_actual_to_forecast_ratio
    if threshold is not None and (not np.isfinite(threshold) or not 0 <= threshold <= 1):
        raise PortfolioIntradayInputError(
            f"{config.display_name} minimum actual-to-forecast ratio must be between 0 and 1."
        )
    return CORRECTION_INITIAL_WEIGHT * np.exp(
        -np.log(2)
        * (forecast_horizons.to_numpy(dtype=float) - 15.0)
        / CORRECTION_HALF_LIFE_MINUTES
    )


def _suppress_downward_correction(
    config: PortfolioIntradayConfig,
    actual_energy: float,
    reference_prediction: float,
) -> bool:
    threshold = config.min_actual_to_forecast_ratio
    return bool(
        threshold is not None
        and reference_prediction > 0
        and actual_energy < threshold * reference_prediction
    )


def run_portfolio_intraday_forecast(
    config: PortfolioIntradayConfig,
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
) -> pd.DataFrame:
    run_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    )
    forecast_origin, last_interval_energy_mwh = get_latest_forecast_origin(
        config,
        now=run_time,
        readings_getter=readings_getter,
        latest_reading_getter=latest_reading_getter,
    )

    if not config.dam_results_path.is_file():
        raise PortfolioIntradayInputError(
            f"{config.display_name} 15-minute DAM forecast was not found: "
            f"{config.dam_results_path}"
        )
    try:
        dam_forecast = pd.read_excel(config.dam_results_path)
    except Exception as exc:
        raise PortfolioIntradayInputError(
            f"Could not read the {config.display_name} 15-minute DAM forecast: {exc}"
        ) from exc

    if not config.weather_path.is_file():
        raise PortfolioIntradayInputError(
            f"{config.display_name} target-weather file was not found: {config.weather_path}"
        )
    try:
        weather_data = pd.read_csv(config.weather_path)
    except Exception as exc:
        raise PortfolioIntradayInputError(
            f"Could not read {config.display_name} target weather: {exc}"
        ) from exc

    result = predict_portfolio_intraday(
        config,
        dam_forecast,
        weather_data,
        forecast_origin,
        last_interval_energy_mwh,
        target_start=run_time,
    )
    config.intraday_results_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.to_excel(
            config.intraday_results_path,
            index=False,
            sheet_name="Intraday_Predictions",
        )
    except Exception as exc:
        raise PortfolioIntradayError(
            f"Could not write the {config.display_name} intraday forecast: {exc}"
        ) from exc
    return result


def _remaining_dam_baseline(
    config: PortfolioIntradayConfig,
    dam_forecast: pd.DataFrame,
    origin: pd.Timestamp,
    *,
    target_start: pd.Timestamp | None,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    required = {"Data", "Interval", "Prediction"}
    missing_columns = sorted(required - set(dam_forecast.columns))
    if missing_columns:
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM forecast is missing columns: "
            + ", ".join(missing_columns)
        )

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    first_target = origin + pd.Timedelta(minutes=15)
    if target_start is not None:
        first_target = max(first_target, _local_timestamp(target_start).ceil("15min"))
    targets = pd.date_range(first_target, day_end, freq="15min", tz=LOCAL_TIMEZONE)
    if len(targets) == 0:
        return targets, pd.DataFrame(columns=["Interval", "Prediction_DAM"], index=targets)

    forecast = dam_forecast.loc[:, ["Data", "Interval", "Prediction"]].copy()
    forecast["Target_timestamp"] = _local_timestamp_series(
        forecast["Data"],
        f"{config.display_name} DAM forecast",
    )
    forecast = forecast[forecast["Target_timestamp"].isin(targets)].copy()
    duplicate_targets = forecast["Target_timestamp"].duplicated(keep=False)
    if duplicate_targets.any():
        duplicate = forecast.loc[duplicate_targets, "Target_timestamp"].iloc[0]
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM forecast has duplicate rows for {duplicate}."
        )

    forecast["_present"] = True
    forecast = forecast.set_index("Target_timestamp").reindex(targets)
    missing_rows = forecast.index[forecast["_present"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM forecast is missing {len(missing_rows)} required "
            f"intervals: {preview}{suffix}"
        )

    intervals = pd.to_numeric(forecast["Interval"], errors="coerce")
    predictions = pd.to_numeric(forecast["Prediction"], errors="coerce")
    if not np.isfinite(intervals.to_numpy(dtype=float)).all():
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM forecast contains an invalid Interval value."
        )
    if not np.isfinite(predictions.to_numpy(dtype=float)).all():
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM forecast contains NaN or infinite predictions."
        )
    if (predictions < 0).any():
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM forecast contains negative predictions."
        )

    expected_intervals = targets.hour * 4 + targets.minute // 15 + 1
    if not np.array_equal(intervals.to_numpy(dtype=int), expected_intervals):
        raise PortfolioIntradayInputError(
            f"{config.display_name} DAM Interval values do not match their timestamps."
        )

    if not np.isfinite(config.baseline_scale) or config.baseline_scale < 0:
        raise PortfolioIntradayInputError(
            f"{config.display_name} baseline scale must be finite and non-negative."
        )

    baseline = pd.DataFrame(index=targets)
    baseline["Interval"] = expected_intervals
    scaled_predictions = predictions.to_numpy(dtype=float) * config.baseline_scale
    if config.max_interval_energy_mwh is not None:
        scaled_predictions = np.minimum(
            scaled_predictions,
            config.max_interval_energy_mwh,
        )
    baseline["Prediction_DAM"] = scaled_predictions
    return targets, baseline


def _target_radiation(
    config: PortfolioIntradayConfig,
    weather_data: pd.DataFrame,
    targets: pd.DatetimeIndex,
) -> np.ndarray:
    required = {"period_end", "ghi"}
    missing_columns = sorted(required - set(weather_data.columns))
    if missing_columns:
        raise PortfolioIntradayInputError(
            f"{config.display_name} target weather is missing columns: "
            + ", ".join(missing_columns)
        )

    weather = weather_data.loc[:, ["period_end", "ghi"]].copy()
    parsed_timestamps = pd.to_datetime(
        weather["period_end"], errors="coerce", utc=True, format="mixed"
    )
    if parsed_timestamps.isna().any():
        raise PortfolioIntradayInputError(
            f"{config.display_name} target weather contains invalid period_end timestamps."
        )
    weather["Target_timestamp"] = parsed_timestamps.dt.tz_convert(LOCAL_TIMEZONE)
    weather = weather[weather["Target_timestamp"].isin(targets)].copy()
    duplicate_targets = weather["Target_timestamp"].duplicated(keep=False)
    if duplicate_targets.any():
        duplicate = weather.loc[duplicate_targets, "Target_timestamp"].iloc[0]
        raise PortfolioIntradayInputError(
            f"{config.display_name} target weather has duplicate rows for {duplicate}."
        )

    weather["_present"] = True
    weather = weather.set_index("Target_timestamp").reindex(targets)
    missing_rows = weather.index[weather["_present"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise PortfolioIntradayInputError(
            f"{config.display_name} target weather is missing {len(missing_rows)} required "
            f"intervals: {preview}{suffix}"
        )

    radiation = pd.to_numeric(weather["ghi"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(radiation).all():
        raise PortfolioIntradayInputError(
            f"{config.display_name} target weather contains invalid radiation values."
        )
    return radiation


def _local_timestamp_series(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    if parsed.isna().any():
        raise PortfolioIntradayInputError(f"{label} contains invalid Data timestamps.")
    if parsed.dt.tz is None:
        try:
            return parsed.dt.tz_localize(LOCAL_TIMEZONE)
        except Exception as exc:
            raise PortfolioIntradayInputError(
                f"{label} contains ambiguous or nonexistent local timestamps."
            ) from exc
    return parsed.dt.tz_convert(LOCAL_TIMEZONE)


def _local_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(LOCAL_TIMEZONE)
    return timestamp.tz_convert(LOCAL_TIMEZONE)


def _latest_completed_origin(current_time: pd.Timestamp) -> pd.Timestamp:
    return current_time.floor("15min")


def _finite_number(value, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioIntradayInputError(f"{label} is missing or invalid.") from exc
    if not np.isfinite(numeric):
        raise PortfolioIntradayInputError(f"{label} must be finite.")
    return numeric


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "Data",
            "Interval",
            "Prediction_DAM",
            "Correction",
            "Prediction_ID",
            "Forecast_origin",
            "Last_Productie",
            "Reference_DAM_Prediction",
            "Actual_minus_DAM",
            "Correction_weight",
            "Forecast_horizon_minutes",
            "Market",
        ]
    )
