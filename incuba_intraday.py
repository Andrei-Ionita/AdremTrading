from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
ADREM_DAM_RESULTS_PATH = APP_ROOT / "Adrem" / "Results_Production_Adrem_xgb_15min.xlsx"
ADREM_WEATHER_PATH = APP_ROOT / "Adrem" / "Solcast" / "Apold_15min.csv"
INCUBA_INTRADAY_RESULTS_PATH = (
    APP_ROOT / "Incuba" / "Results_Production_Incuba_Derived_Corrected_Intraday_15min.xlsx"
)
INCUBA_TIMEZONE = "Europe/Bucharest"
ADREM_TO_INCUBA_SCALE = 0.998 / 1.4
INCUBA_MAX_INTERVAL_ENERGY_MWH = 0.998 * 0.25
MAX_SAMPLE_GAP = pd.Timedelta(minutes=7, seconds=30)
CORRECTION_INITIAL_WEIGHT = 1.0
CORRECTION_HALF_LIFE_MINUTES = 120.0
MIN_ACTUAL_TO_FORECAST_RATIO = 0.5


class IncubaIntradayError(RuntimeError):
    """Base error for a safe, user-facing Incuba intraday forecast failure."""


class IncubaIntradayInputError(IncubaIntradayError):
    """Raised when Incuba production or the derived baseline is invalid."""


def calculate_incuba_interval_energy(
    readings,
    interval_start: pd.Timestamp,
    interval_end: pd.Timestamp,
) -> float:
    start = _local_timestamp(interval_start)
    end = _local_timestamp(interval_end)
    if start >= end:
        raise IncubaIntradayInputError("The Incuba production interval is invalid.")

    samples = []
    for reading in readings:
        observed_raw = getattr(reading, "timestamp_utc", None)
        try:
            observed_at = pd.Timestamp(observed_raw)
        except Exception as exc:
            raise IncubaIntradayInputError("An Incuba production timestamp is invalid.") from exc
        if observed_at.tzinfo is None:
            raise IncubaIntradayInputError("An Incuba production timestamp has no timezone.")
        observed_at = observed_at.tz_convert(INCUBA_TIMEZONE)
        power_mw = _finite_number(getattr(reading, "pv_mw", None), "Incuba power")
        if power_mw < 0:
            raise IncubaIntradayInputError("Incuba production cannot be negative.")
        samples.append((observed_at, power_mw))

    if not samples:
        raise IncubaIntradayInputError(
            f"No Incuba power samples are available for the completed interval {start} to {end}."
        )

    samples.sort(key=lambda sample: sample[0])
    timestamps = pd.DatetimeIndex(sample[0] for sample in samples)
    if timestamps.duplicated().any():
        raise IncubaIntradayInputError("Incuba production contains duplicate sample timestamps.")
    if timestamps[-1] > end:
        raise IncubaIntradayInputError(
            "Incuba interval energy cannot use samples after the interval end."
        )

    interval_samples = [sample for sample in samples if start <= sample[0] <= end]
    if len(interval_samples) < 2:
        raise IncubaIntradayInputError(
            "Incuba interval energy requires at least two power samples from the completed interval."
        )
    gaps = pd.Series([sample[0] for sample in interval_samples]).diff().dropna()
    if (gaps > MAX_SAMPLE_GAP).any():
        raise IncubaIntradayInputError(
            "Incuba power samples contain a gap larger than 7.5 minutes."
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
        raise IncubaIntradayInputError("Calculated Incuba interval energy is invalid.")
    return float(energy_mwh)


def get_latest_incuba_forecast_origin(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=INCUBA_TIMEZONE)
    )

    forecast_origin = _latest_completed_origin(current_time)
    interval_start = forecast_origin - pd.Timedelta(minutes=15)

    if readings_getter is None:
        from power_reading.service import read_interval_energy

        try:
            energy_mwh = read_interval_energy(
                "incuba",
                start=interval_start.tz_convert("UTC").to_pydatetime(),
                end=forecast_origin.tz_convert("UTC").to_pydatetime(),
            )
        except Exception as exc:
            raise IncubaIntradayInputError(
                f"Could not retrieve Incuba interval production: {exc}"
            ) from exc
        return forecast_origin, energy_mwh

    try:
        readings = readings_getter(
            "incuba",
            start=interval_start.tz_convert("UTC").to_pydatetime(),
            end=forecast_origin.tz_convert("UTC").to_pydatetime(),
        )
    except Exception as exc:
        raise IncubaIntradayInputError(
            f"Could not retrieve Incuba interval production: {exc}"
        ) from exc

    energy_mwh = calculate_incuba_interval_energy(readings, interval_start, forecast_origin)
    return forecast_origin, energy_mwh


def predict_incuba_intraday(
    adrem_forecast: pd.DataFrame,
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_interval_energy_mwh: float,
    *,
    target_start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise IncubaIntradayInputError("Forecast_origin must be on a 15-minute boundary.")

    targets, baseline = _derived_incuba_baseline(
        adrem_forecast,
        origin,
        target_start=target_start,
    )
    if baseline.empty:
        return _empty_result()

    radiation = _target_radiation(weather_data, targets)
    actual_energy = _finite_number(last_interval_energy_mwh, "Last_Productie")
    if actual_energy < 0:
        raise IncubaIntradayInputError("Last_Productie cannot be negative.")

    dam_predictions = baseline["Prediction_DAM"].to_numpy(dtype=float)
    reference_prediction = float(dam_predictions[0])
    residual = actual_energy - reference_prediction
    forecast_horizons = ((targets - origin) / pd.Timedelta(minutes=1)).astype(int)
    correction_weights = CORRECTION_INITIAL_WEIGHT * np.exp(
        -np.log(2)
        * (forecast_horizons.to_numpy(dtype=float) - 15.0)
        / CORRECTION_HALF_LIFE_MINUTES
    )
    if (
        reference_prediction > 0
        and actual_energy < MIN_ACTUAL_TO_FORECAST_RATIO * reference_prediction
    ):
        correction_weights = np.zeros(len(targets), dtype=float)
        corrections = np.zeros(len(targets), dtype=float)
    else:
        corrections = correction_weights * residual
    predictions = np.clip(
        dam_predictions + corrections,
        0,
        INCUBA_MAX_INTERVAL_ENERGY_MWH,
    )
    dark_targets = radiation <= 0
    predictions[dark_targets] = 0
    corrections[dark_targets] = -dam_predictions[dark_targets]

    if not np.isfinite(predictions).all():
        raise IncubaIntradayInputError(
            "Incuba corrected predictions contain NaN or infinite values."
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


def run_incuba_intraday_forecast(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
    adrem_forecast_path: str | Path = ADREM_DAM_RESULTS_PATH,
    weather_path: str | Path = ADREM_WEATHER_PATH,
    result_path: str | Path = INCUBA_INTRADAY_RESULTS_PATH,
) -> pd.DataFrame:
    run_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=INCUBA_TIMEZONE)
    )
    forecast_origin, last_interval_energy_mwh = get_latest_incuba_forecast_origin(
        now=run_time,
        readings_getter=readings_getter,
        latest_reading_getter=latest_reading_getter,
    )

    adrem_file = Path(adrem_forecast_path)
    if not adrem_file.is_file():
        raise IncubaIntradayInputError(
            f"Adrem 15-minute forecast was not found: {adrem_file}"
        )
    try:
        adrem_forecast = pd.read_excel(adrem_file)
    except Exception as exc:
        raise IncubaIntradayInputError(
            f"Could not read the Adrem 15-minute forecast: {exc}"
        ) from exc

    weather_file = Path(weather_path)
    if not weather_file.is_file():
        raise IncubaIntradayInputError(f"Adrem target-weather file was not found: {weather_file}")
    try:
        weather_data = pd.read_csv(weather_file)
    except Exception as exc:
        raise IncubaIntradayInputError(f"Could not read Adrem target weather: {exc}") from exc

    result = predict_incuba_intraday(
        adrem_forecast,
        weather_data,
        forecast_origin,
        last_interval_energy_mwh,
        target_start=run_time,
    )
    output_file = Path(result_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.to_excel(output_file, index=False, sheet_name="Intraday_Predictions")
    except Exception as exc:
        raise IncubaIntradayError(f"Could not write the Incuba intraday forecast: {exc}") from exc
    return result


def _derived_incuba_baseline(
    adrem_forecast: pd.DataFrame,
    origin: pd.Timestamp,
    *,
    target_start: pd.Timestamp | None,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    required = {"Data", "Interval", "Prediction"}
    missing_columns = sorted(required - set(adrem_forecast.columns))
    if missing_columns:
        raise IncubaIntradayInputError(
            "Adrem forecast is missing columns: " + ", ".join(missing_columns)
        )

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    first_target = origin + pd.Timedelta(minutes=15)
    if target_start is not None:
        first_target = max(first_target, _local_timestamp(target_start).ceil("15min"))
    targets = pd.date_range(first_target, day_end, freq="15min", tz=INCUBA_TIMEZONE)
    if len(targets) == 0:
        return targets, pd.DataFrame(columns=["Interval", "Prediction_DAM"], index=targets)

    forecast = adrem_forecast.loc[:, ["Data", "Interval", "Prediction"]].copy()
    forecast["Target_timestamp"] = _local_timestamp_series(
        forecast["Data"], "Adrem forecast"
    )
    forecast = forecast[forecast["Target_timestamp"].isin(targets)].copy()
    duplicate_targets = forecast["Target_timestamp"].duplicated(keep=False)
    if duplicate_targets.any():
        duplicate = forecast.loc[duplicate_targets, "Target_timestamp"].iloc[0]
        raise IncubaIntradayInputError(
            f"Adrem forecast has duplicate rows for {duplicate}."
        )

    forecast["_present"] = True
    forecast = forecast.set_index("Target_timestamp").reindex(targets)
    missing_rows = forecast.index[forecast["_present"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise IncubaIntradayInputError(
            f"Adrem forecast is missing {len(missing_rows)} required intervals: {preview}{suffix}"
        )

    intervals = pd.to_numeric(forecast["Interval"], errors="coerce")
    predictions = pd.to_numeric(forecast["Prediction"], errors="coerce")
    if not np.isfinite(intervals.to_numpy(dtype=float)).all():
        raise IncubaIntradayInputError("Adrem forecast contains an invalid Interval value.")
    if not np.isfinite(predictions.to_numpy(dtype=float)).all():
        raise IncubaIntradayInputError("Adrem forecast contains NaN or infinite predictions.")
    if (predictions < 0).any():
        raise IncubaIntradayInputError("Adrem forecast contains negative predictions.")

    expected_intervals = targets.hour * 4 + targets.minute // 15 + 1
    if not np.array_equal(intervals.to_numpy(dtype=int), expected_intervals):
        raise IncubaIntradayInputError(
            "Adrem forecast Interval values do not match their target timestamps."
        )

    baseline = pd.DataFrame(index=targets)
    baseline["Interval"] = expected_intervals
    baseline["Prediction_DAM"] = np.clip(
        predictions.to_numpy(dtype=float) * ADREM_TO_INCUBA_SCALE,
        0,
        INCUBA_MAX_INTERVAL_ENERGY_MWH,
    )
    return targets, baseline


def _target_radiation(weather_data: pd.DataFrame, targets: pd.DatetimeIndex) -> np.ndarray:
    required = {"period_end", "ghi"}
    missing_columns = sorted(required - set(weather_data.columns))
    if missing_columns:
        raise IncubaIntradayInputError(
            "Adrem target weather is missing columns: " + ", ".join(missing_columns)
        )

    weather = weather_data.loc[:, ["period_end", "ghi"]].copy()
    parsed_timestamps = pd.to_datetime(
        weather["period_end"], errors="coerce", utc=True, format="mixed"
    )
    if parsed_timestamps.isna().any():
        raise IncubaIntradayInputError(
            "Adrem target weather contains invalid period_end timestamps."
        )
    weather["Target_timestamp"] = parsed_timestamps.dt.tz_convert(INCUBA_TIMEZONE)
    weather = weather[weather["Target_timestamp"].isin(targets)].copy()
    duplicate_targets = weather["Target_timestamp"].duplicated(keep=False)
    if duplicate_targets.any():
        duplicate = weather.loc[duplicate_targets, "Target_timestamp"].iloc[0]
        raise IncubaIntradayInputError(
            f"Adrem target weather has duplicate rows for {duplicate}."
        )

    weather["_present"] = True
    weather = weather.set_index("Target_timestamp").reindex(targets)
    missing_rows = weather.index[weather["_present"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise IncubaIntradayInputError(
            f"Adrem target weather is missing {len(missing_rows)} required intervals: "
            f"{preview}{suffix}"
        )

    radiation = pd.to_numeric(weather["ghi"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(radiation).all():
        raise IncubaIntradayInputError(
            "Adrem target weather contains NaN or infinite radiation values."
        )
    return radiation


def _local_timestamp_series(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    if parsed.isna().any():
        raise IncubaIntradayInputError(f"{label} contains invalid Data timestamps.")
    if parsed.dt.tz is None:
        try:
            return parsed.dt.tz_localize(INCUBA_TIMEZONE)
        except Exception as exc:
            raise IncubaIntradayInputError(
                f"{label} contains ambiguous or nonexistent local timestamps."
            ) from exc
    return parsed.dt.tz_convert(INCUBA_TIMEZONE)


def _local_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(INCUBA_TIMEZONE)
    return timestamp.tz_convert(INCUBA_TIMEZONE)


def _latest_completed_origin(current_time: pd.Timestamp) -> pd.Timestamp:
    return current_time.floor("15min")


def _finite_number(value, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise IncubaIntradayInputError(f"{label} is missing or invalid.") from exc
    if not np.isfinite(numeric):
        raise IncubaIntradayInputError(f"{label} must be finite.")
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
