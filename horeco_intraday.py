from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
HORECO_DAM_MODEL_PATH = APP_ROOT / "Horeco" / "rs_xgb_horeco_prod_15min_0426.pkl"
HORECO_WEATHER_PATH = APP_ROOT / "Horeco" / "Solcast" / "Buzau_15min.csv"
HORECO_INTRADAY_RESULTS_PATH = (
    APP_ROOT / "Horeco" / "Results_Production_Horeco_DAM_Corrected_Intraday_15min.xlsx"
)
# Compatibility aliases for the existing UI imports.
HORECO_ID_MODEL_PATH = HORECO_DAM_MODEL_PATH
HORECO_ID_WEATHER_PATH = HORECO_WEATHER_PATH
HORECO_ID_RESULTS_PATH = HORECO_INTRADAY_RESULTS_PATH
HORECO_TIMEZONE = "Europe/Bucharest"
FORECAST_INTERVAL_HOURS = 0.25
HORECO_MAX_INTERVAL_ENERGY_MWH = 2.275 * FORECAST_INTERVAL_HOURS
MAX_BOUNDARY_AGE = pd.Timedelta(minutes=5)
MAX_SAMPLE_GAP = pd.Timedelta(minutes=7, seconds=30)
CORRECTION_INITIAL_WEIGHT = 1.0
CORRECTION_HALF_LIFE_MINUTES = 120.0
MIN_ACTUAL_TO_FORECAST_RATIO = 0.5

HORECO_BASELINE_FEATURES = (
    "Interval",
    "Temperatura",
    "Nori",
    "Radiatie",
    "Month",
    "is_dark",
)

WEATHER_COLUMNS = {
    "air_temp": "Temperatura",
    "cloud_opacity": "Nori",
    "ghi": "Radiatie",
}


class HorecoIntradayError(RuntimeError):
    """Base error for a safe, user-facing Horeco intraday forecast failure."""


class HorecoIntradayInputError(HorecoIntradayError):
    """Raised when Horeco production or target weather is missing or invalid."""


class HorecoIntradayModelError(HorecoIntradayError):
    """Raised when the stored Horeco DAM model is incompatible."""


@dataclass(frozen=True)
class HorecoBaselineModel:
    model: object
    feature_columns: tuple[str, ...] = HORECO_BASELINE_FEATURES
    plant_max_output: float = HORECO_MAX_INTERVAL_ENERGY_MWH


def load_horeco_baseline_model(
    model_path: str | Path = HORECO_DAM_MODEL_PATH,
) -> HorecoBaselineModel:
    path = Path(model_path)
    if not path.is_file():
        raise HorecoIntradayModelError(f"Horeco DAM model was not found: {path}")

    try:
        model = joblib.load(path)
    except Exception as exc:
        raise HorecoIntradayModelError(f"Could not load the Horeco DAM model: {exc}") from exc

    if not callable(getattr(model, "predict", None)):
        raise HorecoIntradayModelError("The Horeco DAM model file does not contain a usable model.")
    feature_names = getattr(model, "feature_names_in_", None)
    if feature_names is not None and tuple(feature_names) != HORECO_BASELINE_FEATURES:
        raise HorecoIntradayModelError(
            "The Horeco DAM model feature names do not match the production feature order."
        )
    feature_count = int(getattr(model, "n_features_in_", len(HORECO_BASELINE_FEATURES)))
    if feature_count != len(HORECO_BASELINE_FEATURES):
        raise HorecoIntradayModelError(
            "The Horeco DAM model does not match the expected six input features."
        )

    return HorecoBaselineModel(model=model)


def calculate_horeco_interval_energy(
    readings,
    interval_start: pd.Timestamp,
    interval_end: pd.Timestamp,
) -> float:
    start = _local_timestamp(interval_start)
    end = _local_timestamp(interval_end)
    if start >= end:
        raise HorecoIntradayInputError("The Horeco production interval is invalid.")

    samples = []
    for reading in readings:
        observed_raw = getattr(reading, "timestamp_utc", None)
        try:
            observed_at = pd.Timestamp(observed_raw)
        except Exception as exc:
            raise HorecoIntradayInputError("A Horeco production timestamp is invalid.") from exc
        if observed_at.tzinfo is None:
            raise HorecoIntradayInputError("A Horeco production timestamp has no timezone.")
        observed_at = observed_at.tz_convert(HORECO_TIMEZONE)
        power_mw = _finite_number(getattr(reading, "pv_mw", None), "Horeco power")
        if power_mw < 0:
            raise HorecoIntradayInputError("Horeco production cannot be negative.")
        samples.append((observed_at, power_mw))

    if not samples:
        raise HorecoIntradayInputError(
            f"No Horeco power samples are available for the completed interval {start} to {end}."
        )

    samples.sort(key=lambda sample: sample[0])
    timestamps = pd.DatetimeIndex(sample[0] for sample in samples)
    if timestamps.duplicated().any():
        raise HorecoIntradayInputError("Horeco production contains duplicate sample timestamps.")
    if timestamps[-1] > end:
        raise HorecoIntradayInputError(
            "Horeco interval energy cannot use samples after the interval end."
        )

    start_candidates = [sample for sample in samples if sample[0] <= start]
    if not start_candidates:
        raise HorecoIntradayInputError(
            "Horeco interval energy requires a power sample at or before the interval start."
        )
    start_sample = start_candidates[-1]
    end_sample = samples[-1]
    if start - start_sample[0] > MAX_BOUNDARY_AGE:
        raise HorecoIntradayInputError("The Horeco sample at the interval start is too old.")
    if end - end_sample[0] > MAX_BOUNDARY_AGE:
        raise HorecoIntradayInputError("The Horeco sample at the interval end is too old.")

    relevant = [sample for sample in samples if start_sample[0] <= sample[0] <= end]
    if len(relevant) > 1:
        gaps = pd.Series([sample[0] for sample in relevant]).diff().dropna()
        if (gaps > MAX_SAMPLE_GAP).any():
            raise HorecoIntradayInputError(
                "Horeco power samples contain a gap larger than 7.5 minutes."
            )

    points = [(start, start_sample[1])]
    points.extend(sample for sample in samples if start < sample[0] < end)
    points.append((end, end_sample[1]))

    energy_mwh = 0.0
    for (left_time, left_power), (right_time, right_power) in zip(points, points[1:]):
        duration_hours = (right_time - left_time).total_seconds() / 3600
        energy_mwh += (left_power + right_power) / 2 * duration_hours
    if not np.isfinite(energy_mwh) or energy_mwh < 0:
        raise HorecoIntradayInputError("Calculated Horeco interval energy is invalid.")
    return float(energy_mwh)


def get_latest_horeco_forecast_origin(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=HORECO_TIMEZONE)
    )

    if readings_getter is None:
        from power_reading.database import get_interval_readings, get_latest_reading

        readings_getter = get_interval_readings
        latest_reading_getter = latest_reading_getter or get_latest_reading

    forecast_origin = _latest_supported_origin(
        current_time,
        latest_reading_getter,
        "horeco",
    )
    interval_start = forecast_origin - pd.Timedelta(minutes=15)

    try:
        readings = readings_getter(
            "horeco",
            start=interval_start.tz_convert("UTC").to_pydatetime(),
            end=forecast_origin.tz_convert("UTC").to_pydatetime(),
        )
    except Exception as exc:
        raise HorecoIntradayInputError(
            f"Could not retrieve Horeco interval production: {exc}"
        ) from exc

    energy_mwh = calculate_horeco_interval_energy(readings, interval_start, forecast_origin)
    return forecast_origin, energy_mwh


def build_horeco_baseline_features(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    feature_columns: tuple[str, ...] = HORECO_BASELINE_FEATURES,
    target_start: pd.Timestamp | None = None,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise HorecoIntradayInputError("Forecast_origin must be on a 15-minute boundary.")

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    first_target = origin + pd.Timedelta(minutes=15)
    if target_start is not None:
        first_target = max(first_target, _local_timestamp(target_start).ceil("15min"))
    targets = pd.date_range(
        start=first_target,
        end=day_end,
        freq="15min",
        tz=HORECO_TIMEZONE,
    )
    if len(targets) == 0:
        return targets, pd.DataFrame(columns=list(feature_columns), index=targets)

    weather = _target_weather(weather_data, targets)
    features = pd.DataFrame(index=targets)
    features["Interval"] = targets.hour * 4 + targets.minute // 15 + 1
    for column in WEATHER_COLUMNS.values():
        features[column] = weather[column].to_numpy()
    features["Month"] = targets.month
    features["is_dark"] = (features["Radiatie"] <= 0).astype(int)

    if tuple(feature_columns) != HORECO_BASELINE_FEATURES:
        raise HorecoIntradayModelError(
            "The requested Horeco DAM feature order does not match the production schema."
        )
    features = features.loc[:, list(feature_columns)]
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise HorecoIntradayInputError(
            "Horeco DAM model inputs contain NaN or infinite values."
        )
    return targets, features


def predict_horeco_intraday(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_interval_energy_mwh: float,
    *,
    baseline_model: HorecoBaselineModel | None = None,
    target_start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    active_model = baseline_model or load_horeco_baseline_model()
    targets, features = build_horeco_baseline_features(
        weather_data,
        forecast_origin,
        active_model.feature_columns,
        target_start,
    )
    if features.empty:
        return _empty_result()

    try:
        raw_baseline = np.asarray(active_model.model.predict(features), dtype=float).reshape(-1)
    except Exception as exc:
        raise HorecoIntradayModelError(f"Horeco DAM model inference failed: {exc}") from exc
    if len(raw_baseline) != len(features):
        raise HorecoIntradayModelError("The Horeco DAM model returned an unexpected row count.")
    if not np.isfinite(raw_baseline).all():
        raise HorecoIntradayModelError(
            "Horeco DAM predictions contain NaN or infinite values."
        )

    baseline = np.clip(raw_baseline, 0, active_model.plant_max_output)
    dark_targets = features["is_dark"].to_numpy(dtype=int) == 1
    baseline[dark_targets] = 0

    origin = _local_timestamp(forecast_origin)
    actual_energy = _finite_number(last_interval_energy_mwh, "Last_Productie")
    if actual_energy < 0:
        raise HorecoIntradayInputError("Last_Productie cannot be negative.")

    reference_prediction = float(baseline[0])
    residual = actual_energy - reference_prediction
    horizons = ((targets - origin) / pd.Timedelta(minutes=1)).astype(int)
    correction_weights = CORRECTION_INITIAL_WEIGHT * np.exp(
        -np.log(2)
        * (horizons.to_numpy(dtype=float) - 15.0)
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
        baseline + corrections,
        0,
        active_model.plant_max_output,
    )
    predictions[dark_targets] = 0
    corrections[dark_targets] = -baseline[dark_targets]

    return pd.DataFrame(
        {
            "Data": targets.tz_localize(None),
            "Interval": features["Interval"].to_numpy(dtype=int),
            "Prediction_DAM": np.round(baseline, 3),
            "Correction": np.round(corrections, 3),
            "Prediction_ID": np.round(predictions, 3),
            "Forecast_origin": origin.tz_localize(None),
            "Last_Productie": actual_energy,
            "Reference_DAM_Prediction": round(reference_prediction, 3),
            "Actual_minus_DAM": round(residual, 3),
            "Correction_weight": np.round(correction_weights, 4),
            "Forecast_horizon_minutes": horizons,
            "Market": "Intraday",
        }
    )


def run_horeco_intraday_forecast(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
    model_path: str | Path = HORECO_DAM_MODEL_PATH,
    weather_path: str | Path = HORECO_WEATHER_PATH,
    result_path: str | Path = HORECO_INTRADAY_RESULTS_PATH,
) -> pd.DataFrame:
    baseline_model = load_horeco_baseline_model(model_path)
    run_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=HORECO_TIMEZONE)
    )
    forecast_origin, last_interval_energy_mwh = get_latest_horeco_forecast_origin(
        now=run_time,
        readings_getter=readings_getter,
        latest_reading_getter=latest_reading_getter,
    )
    weather_file = Path(weather_path)
    if not weather_file.is_file():
        raise HorecoIntradayInputError(f"Horeco target-weather file was not found: {weather_file}")
    try:
        weather_data = pd.read_csv(weather_file)
    except Exception as exc:
        raise HorecoIntradayInputError(f"Could not read Horeco target weather: {exc}") from exc

    result = predict_horeco_intraday(
        weather_data,
        forecast_origin,
        last_interval_energy_mwh,
        baseline_model=baseline_model,
        target_start=run_time,
    )
    output_file = Path(result_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.to_excel(output_file, index=False, sheet_name="Intraday_Predictions")
    except Exception as exc:
        raise HorecoIntradayError(f"Could not write the Horeco intraday forecast: {exc}") from exc
    return result


def _target_weather(weather_data: pd.DataFrame, targets: pd.DatetimeIndex) -> pd.DataFrame:
    required = ("period_end", *WEATHER_COLUMNS)
    missing_columns = sorted(set(required) - set(weather_data.columns))
    if missing_columns:
        raise HorecoIntradayInputError(
            "Horeco target weather is missing columns: " + ", ".join(missing_columns)
        )

    weather = weather_data.loc[:, list(required)].copy()
    parsed = pd.to_datetime(weather["period_end"], errors="coerce", utc=True, format="mixed")
    if parsed.isna().any():
        raise HorecoIntradayInputError(
            "Horeco target weather contains invalid period_end timestamps."
        )
    weather["Target_timestamp"] = parsed.dt.tz_convert(HORECO_TIMEZONE)
    weather = weather[weather["Target_timestamp"].isin(targets)].copy()

    duplicates = weather["Target_timestamp"].duplicated(keep=False)
    if duplicates.any():
        duplicate = weather.loc[duplicates, "Target_timestamp"].iloc[0]
        raise HorecoIntradayInputError(f"Horeco target weather has duplicate rows for {duplicate}.")

    weather = weather.set_index("Target_timestamp").reindex(targets)
    missing_rows = weather.index[weather["period_end"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise HorecoIntradayInputError(
            f"Horeco target weather is missing {len(missing_rows)} required intervals: "
            f"{preview}{suffix}"
        )

    renamed = weather.rename(columns=WEATHER_COLUMNS)
    for column in WEATHER_COLUMNS.values():
        renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
    values = renamed.loc[:, list(WEATHER_COLUMNS.values())]
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise HorecoIntradayInputError(
            "Horeco target weather contains NaN or infinite required values."
        )
    return values


def _local_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(HORECO_TIMEZONE)
    return timestamp.tz_convert(HORECO_TIMEZONE)


def _latest_supported_origin(
    current_time: pd.Timestamp,
    latest_reading_getter: Callable | None,
    asset: str,
) -> pd.Timestamp:
    wall_origin = current_time.floor("15min")
    if latest_reading_getter is None:
        return wall_origin

    before_utc = (current_time + pd.Timedelta(microseconds=1)).tz_convert(
        "UTC"
    ).to_pydatetime()
    try:
        latest = latest_reading_getter(asset, before=before_utc)
    except Exception as exc:
        raise HorecoIntradayInputError(
            f"Could not retrieve the latest Horeco production timestamp: {exc}"
        ) from exc
    if latest is None:
        raise HorecoIntradayInputError("No Horeco production measurement is available.")

    observed_at = pd.Timestamp(getattr(latest, "timestamp_utc", None))
    if observed_at.tzinfo is None:
        raise HorecoIntradayInputError(
            "The latest Horeco production timestamp has no timezone."
        )
    supported_origin = min(wall_origin, observed_at.tz_convert(HORECO_TIMEZONE).floor("15min"))
    if wall_origin - supported_origin > pd.Timedelta(minutes=15):
        raise HorecoIntradayInputError("The latest Horeco production measurement is too old.")
    return supported_origin


def _finite_number(value, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise HorecoIntradayInputError(f"{label} is missing or invalid.") from exc
    if not np.isfinite(numeric):
        raise HorecoIntradayInputError(f"{label} must be finite.")
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
