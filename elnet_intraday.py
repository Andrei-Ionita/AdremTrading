from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
ELNET_DAM_MODEL_PATH = APP_ROOT / "Elnet" / "rs_xgb_elnet_prod_15min_0626.pkl"
ELNET_WEATHER_PATH = APP_ROOT / "Elnet" / "Solcast" / "Bucsani_15min.csv"
ELNET_INTRADAY_RESULTS_PATH = (
    APP_ROOT / "Elnet" / "Results_Production_Elnet_DAM_Corrected_Intraday_15min.xlsx"
)
ELNET_TIMEZONE = "Europe/Bucharest"
MAX_BOUNDARY_AGE = pd.Timedelta(minutes=5)
MAX_SAMPLE_GAP = pd.Timedelta(minutes=7, seconds=30)
CORRECTION_INITIAL_WEIGHT = 1.0
CORRECTION_HALF_LIFE_MINUTES = 120.0

ELNET_DAM_FEATURES = (
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


class ElnetIntradayError(RuntimeError):
    """Base error for a safe, user-facing Elnet intraday forecast failure."""


class ElnetIntradayInputError(ElnetIntradayError):
    """Raised when Elnet production or target weather is missing or invalid."""


class ElnetIntradayModelError(ElnetIntradayError):
    """Raised when the stored Elnet DAM model is incompatible."""


@dataclass(frozen=True)
class ElnetIntradayBundle:
    model: object
    feature_columns: tuple[str, ...]
    plant_max_output: float | None
    asset: str
    market: str
    forecast_scope: str


def load_elnet_intraday_bundle(
    model_path: str | Path = ELNET_DAM_MODEL_PATH,
) -> ElnetIntradayBundle:
    path = Path(model_path)
    if not path.is_file():
        raise ElnetIntradayModelError(f"Elnet DAM model was not found: {path}")

    try:
        model = joblib.load(path)
    except Exception as exc:
        raise ElnetIntradayModelError(f"Could not load the Elnet DAM model: {exc}") from exc

    if not callable(getattr(model, "predict", None)):
        raise ElnetIntradayModelError("The Elnet DAM model file does not contain a usable model.")

    feature_names = getattr(model, "feature_names_in_", None)
    if feature_names is not None and tuple(feature_names) != ELNET_DAM_FEATURES:
        raise ElnetIntradayModelError(
            "The Elnet DAM model feature names do not match the production feature order."
        )
    feature_count = getattr(model, "n_features_in_", len(ELNET_DAM_FEATURES))
    if int(feature_count) != len(ELNET_DAM_FEATURES):
        raise ElnetIntradayModelError(
            "The Elnet DAM model feature count does not match the production feature schema."
        )

    return ElnetIntradayBundle(
        model=model,
        feature_columns=ELNET_DAM_FEATURES,
        plant_max_output=None,
        asset="Elnet",
        market="Intraday",
        forecast_scope="Every remaining interval of the same delivery day",
    )


def calculate_elnet_interval_energy(
    readings,
    interval_start: pd.Timestamp,
    interval_end: pd.Timestamp,
) -> float:
    start = _local_timestamp(interval_start)
    end = _local_timestamp(interval_end)
    if start >= end:
        raise ElnetIntradayInputError("The Elnet production interval is invalid.")

    samples = []
    for reading in readings:
        observed_raw = getattr(reading, "timestamp_utc", None)
        try:
            observed_at = pd.Timestamp(observed_raw)
        except Exception as exc:
            raise ElnetIntradayInputError("An Elnet production timestamp is invalid.") from exc
        if observed_at.tzinfo is None:
            raise ElnetIntradayInputError("An Elnet production timestamp has no timezone.")
        observed_at = observed_at.tz_convert(ELNET_TIMEZONE)
        power_mw = _finite_number(getattr(reading, "pv_mw", None), "Elnet power")
        if power_mw < 0:
            raise ElnetIntradayInputError("Elnet production cannot be negative.")
        samples.append((observed_at, power_mw))

    if not samples:
        raise ElnetIntradayInputError(
            f"No Elnet power samples are available for the completed interval {start} to {end}."
        )

    samples.sort(key=lambda sample: sample[0])
    timestamps = pd.DatetimeIndex(sample[0] for sample in samples)
    if timestamps.duplicated().any():
        raise ElnetIntradayInputError("Elnet production contains duplicate sample timestamps.")
    if timestamps[-1] > end:
        raise ElnetIntradayInputError(
            "Elnet interval energy cannot use samples after the interval end."
        )

    start_candidates = [sample for sample in samples if sample[0] <= start]
    if not start_candidates:
        raise ElnetIntradayInputError(
            "Elnet interval energy requires a power sample at or before the interval start."
        )
    start_sample = start_candidates[-1]
    end_sample = samples[-1]
    if start - start_sample[0] > MAX_BOUNDARY_AGE:
        raise ElnetIntradayInputError("The Elnet sample at the interval start is too old.")
    if end - end_sample[0] > MAX_BOUNDARY_AGE:
        raise ElnetIntradayInputError("The Elnet sample at the interval end is too old.")

    relevant = [sample for sample in samples if start_sample[0] <= sample[0] <= end]
    if len(relevant) > 1:
        gaps = pd.Series([sample[0] for sample in relevant]).diff().dropna()
        if (gaps > MAX_SAMPLE_GAP).any():
            raise ElnetIntradayInputError(
                "Elnet power samples contain a gap larger than 7.5 minutes."
            )

    points = [(start, start_sample[1])]
    points.extend(sample for sample in samples if start < sample[0] < end)
    points.append((end, end_sample[1]))

    energy_mwh = 0.0
    for (left_time, left_power), (right_time, right_power) in zip(points, points[1:]):
        duration_hours = (right_time - left_time).total_seconds() / 3600
        energy_mwh += (left_power + right_power) / 2 * duration_hours
    if not np.isfinite(energy_mwh) or energy_mwh < 0:
        raise ElnetIntradayInputError("Calculated Elnet interval energy is invalid.")
    return float(energy_mwh)


def get_latest_elnet_forecast_origin(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=ELNET_TIMEZONE)
    )

    if readings_getter is None:
        from power_reading.database import get_interval_readings, get_latest_reading

        readings_getter = get_interval_readings
        latest_reading_getter = latest_reading_getter or get_latest_reading

    forecast_origin = _latest_supported_origin(
        current_time,
        latest_reading_getter,
        "elnet",
    )
    interval_start = forecast_origin - pd.Timedelta(minutes=15)

    try:
        readings = readings_getter(
            "elnet",
            start=interval_start.tz_convert("UTC").to_pydatetime(),
            end=forecast_origin.tz_convert("UTC").to_pydatetime(),
        )
    except Exception as exc:
        raise ElnetIntradayInputError(
            f"Could not retrieve Elnet interval production: {exc}"
        ) from exc

    energy_mwh = calculate_elnet_interval_energy(readings, interval_start, forecast_origin)
    return forecast_origin, energy_mwh


def build_elnet_intraday_features(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    feature_columns: tuple[str, ...] = ELNET_DAM_FEATURES,
    target_start: pd.Timestamp | None = None,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise ElnetIntradayInputError("Forecast_origin must be on a 15-minute boundary.")

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    first_target = origin + pd.Timedelta(minutes=15)
    if target_start is not None:
        first_target = max(first_target, _local_timestamp(target_start).ceil("15min"))
    targets = pd.date_range(
        start=first_target,
        end=day_end,
        freq="15min",
        tz=ELNET_TIMEZONE,
    )
    if len(targets) == 0:
        return targets, pd.DataFrame(columns=list(feature_columns), index=targets)

    return targets, _build_elnet_dam_features(weather_data, targets, feature_columns)


def predict_elnet_intraday(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_interval_energy_mwh: float,
    *,
    bundle: ElnetIntradayBundle | None = None,
    target_start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    active_bundle = bundle or load_elnet_intraday_bundle()
    targets, features = build_elnet_intraday_features(
        weather_data,
        forecast_origin,
        active_bundle.feature_columns,
        target_start,
    )
    if features.empty:
        return _empty_result()

    try:
        raw_predictions = np.asarray(active_bundle.model.predict(features), dtype=float).reshape(-1)
    except Exception as exc:
        raise ElnetIntradayModelError(f"Elnet DAM model inference failed: {exc}") from exc
    if len(raw_predictions) != len(features):
        raise ElnetIntradayModelError("The Elnet DAM model returned an unexpected row count.")
    if not np.isfinite(raw_predictions).all():
        raise ElnetIntradayModelError("Elnet DAM predictions contain NaN or infinite values.")

    dam_predictions = np.maximum(raw_predictions, 0)
    if active_bundle.plant_max_output is not None:
        dam_predictions = np.minimum(dam_predictions, active_bundle.plant_max_output)
    dark_targets = features["is_dark"].to_numpy(dtype=int) == 1
    dam_predictions[dark_targets] = 0

    origin = _local_timestamp(forecast_origin)
    actual_energy = _finite_number(last_interval_energy_mwh, "Last_Productie")
    if actual_energy < 0:
        raise ElnetIntradayInputError("Last_Productie cannot be negative.")

    reference_prediction = float(dam_predictions[0])
    residual = actual_energy - reference_prediction
    forecast_horizons = ((targets - origin) / pd.Timedelta(minutes=1)).astype(int)
    correction_weights = CORRECTION_INITIAL_WEIGHT * np.exp(
        -np.log(2)
        * (forecast_horizons.to_numpy(dtype=float) - 15.0)
        / CORRECTION_HALF_LIFE_MINUTES
    )
    corrections = correction_weights * residual
    predictions = np.maximum(dam_predictions + corrections, 0)
    if active_bundle.plant_max_output is not None:
        predictions = np.minimum(predictions, active_bundle.plant_max_output)
    predictions[dark_targets] = 0
    corrections[dark_targets] = -dam_predictions[dark_targets]

    return pd.DataFrame(
        {
            "Data": targets.tz_localize(None),
            "Interval": features["Interval"].to_numpy(dtype=int),
            "Prediction_DAM": np.round(dam_predictions, 3),
            "Correction": np.round(corrections, 3),
            "Prediction_ID": np.round(predictions, 3),
            "Forecast_origin": origin.tz_localize(None),
            "Last_Productie": actual_energy,
            "Reference_DAM_Prediction": round(reference_prediction, 3),
            "Actual_minus_DAM": round(residual, 3),
            "Correction_weight": np.round(correction_weights, 4),
            "Forecast_horizon_minutes": forecast_horizons,
            "Market": active_bundle.market,
        }
    )


def run_elnet_intraday_forecast(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
    model_path: str | Path = ELNET_DAM_MODEL_PATH,
    weather_path: str | Path = ELNET_WEATHER_PATH,
    result_path: str | Path = ELNET_INTRADAY_RESULTS_PATH,
) -> pd.DataFrame:
    bundle = load_elnet_intraday_bundle(model_path)
    run_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=ELNET_TIMEZONE)
    )
    forecast_origin, last_interval_energy_mwh = get_latest_elnet_forecast_origin(
        now=run_time,
        readings_getter=readings_getter,
        latest_reading_getter=latest_reading_getter,
    )
    weather_file = Path(weather_path)
    if not weather_file.is_file():
        raise ElnetIntradayInputError(f"Elnet target-weather file was not found: {weather_file}")
    try:
        weather_data = pd.read_csv(weather_file)
    except Exception as exc:
        raise ElnetIntradayInputError(f"Could not read Elnet target weather: {exc}") from exc

    result = predict_elnet_intraday(
        weather_data,
        forecast_origin,
        last_interval_energy_mwh,
        bundle=bundle,
        target_start=run_time,
    )
    output_file = Path(result_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.to_excel(output_file, index=False, sheet_name="Intraday_Predictions")
    except Exception as exc:
        raise ElnetIntradayError(f"Could not write the Elnet intraday forecast: {exc}") from exc
    return result


def _build_elnet_dam_features(
    weather_data: pd.DataFrame,
    timestamps: pd.DatetimeIndex,
    feature_columns: tuple[str, ...],
) -> pd.DataFrame:
    weather = _target_weather(weather_data, timestamps)
    intervals = timestamps.hour * 4 + timestamps.minute // 15 + 1

    features = pd.DataFrame(index=timestamps)
    features["Interval"] = intervals
    for column in WEATHER_COLUMNS.values():
        features[column] = weather[column].to_numpy()
    features["Month"] = timestamps.month
    features["is_dark"] = (features["Radiatie"] <= 0).astype(int)

    if tuple(feature_columns) != ELNET_DAM_FEATURES:
        raise ElnetIntradayModelError(
            "The requested Elnet DAM feature order does not match the production schema."
        )
    features = features.loc[:, list(feature_columns)]
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise ElnetIntradayInputError("Elnet DAM model inputs contain NaN or infinite values.")
    return features


def _target_weather(weather_data: pd.DataFrame, targets: pd.DatetimeIndex) -> pd.DataFrame:
    required = ("period_end", *WEATHER_COLUMNS)
    missing_columns = sorted(set(required) - set(weather_data.columns))
    if missing_columns:
        raise ElnetIntradayInputError(
            "Elnet target weather is missing columns: " + ", ".join(missing_columns)
        )

    weather = weather_data.loc[:, list(required)].copy()
    parsed_timestamps = pd.to_datetime(
        weather["period_end"], errors="coerce", utc=True, format="mixed"
    )
    if parsed_timestamps.isna().any():
        raise ElnetIntradayInputError(
            "Elnet target weather contains invalid period_end timestamps."
        )
    weather["Target_timestamp"] = parsed_timestamps.dt.tz_convert(ELNET_TIMEZONE)
    weather = weather[weather["Target_timestamp"].isin(targets)].copy()

    duplicate_targets = weather["Target_timestamp"].duplicated(keep=False)
    if duplicate_targets.any():
        duplicate = weather.loc[duplicate_targets, "Target_timestamp"].iloc[0]
        raise ElnetIntradayInputError(f"Elnet target weather has duplicate rows for {duplicate}.")

    weather = weather.set_index("Target_timestamp").reindex(targets)
    missing_rows = weather.index[weather["period_end"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise ElnetIntradayInputError(
            f"Elnet target weather is missing {len(missing_rows)} required intervals: "
            f"{preview}{suffix}"
        )

    renamed = weather.rename(columns=WEATHER_COLUMNS)
    for column in WEATHER_COLUMNS.values():
        renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
    values = renamed.loc[:, list(WEATHER_COLUMNS.values())]
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ElnetIntradayInputError(
            "Elnet target weather contains NaN or infinite required values."
        )
    return values


def _local_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(ELNET_TIMEZONE)
    return timestamp.tz_convert(ELNET_TIMEZONE)


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
        raise ElnetIntradayInputError(
            f"Could not retrieve the latest Elnet production timestamp: {exc}"
        ) from exc
    if latest is None:
        raise ElnetIntradayInputError("No Elnet production measurement is available.")

    observed_at = pd.Timestamp(getattr(latest, "timestamp_utc", None))
    if observed_at.tzinfo is None:
        raise ElnetIntradayInputError("The latest Elnet production timestamp has no timezone.")
    supported_origin = min(wall_origin, observed_at.tz_convert(ELNET_TIMEZONE).floor("15min"))
    if wall_origin - supported_origin > pd.Timedelta(minutes=15):
        raise ElnetIntradayInputError("The latest Elnet production measurement is too old.")
    return supported_origin


def _finite_number(value, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ElnetIntradayInputError(f"{label} is missing or invalid.") from exc
    if not np.isfinite(numeric):
        raise ElnetIntradayInputError(f"{label} must be finite.")
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
