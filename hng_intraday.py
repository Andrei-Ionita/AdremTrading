from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
HNG_DAM_MODEL_PATH = APP_ROOT / "HNG" / "rs_xgb_hng_prod_15min_0626.pkl"
HNG_ID_WEATHER_PATH = APP_ROOT / "HNG" / "Solcast" / "Mures_15min.csv"
HNG_INTRADAY_RESULTS_PATH = (
    APP_ROOT / "HNG" / "Results_Production_HNG_DAM_Corrected_Intraday_15min.xlsx"
)
HNG_TIMEZONE = "Europe/Bucharest"
MAX_SAMPLE_GAP = pd.Timedelta(minutes=7, seconds=30)
CORRECTION_INITIAL_WEIGHT = 1.0
CORRECTION_HALF_LIFE_MINUTES = 120.0
MIN_ACTUAL_TO_FORECAST_RATIO = 0.5

HNG_DAM_FEATURES = (
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


class HNGIntradayError(RuntimeError):
    """Base error for a safe, user-facing HNG intraday forecast failure."""


class HNGIntradayInputError(HNGIntradayError):
    """Raised when production or target weather is missing or invalid."""


class HNGIntradayModelError(HNGIntradayError):
    """Raised when the stored HNG intraday bundle is incompatible."""


@dataclass(frozen=True)
class HNGIntradayBundle:
    model: object
    feature_columns: tuple[str, ...]
    plant_max_output: float | None
    asset: str
    market: str
    forecast_scope: str


def load_hng_intraday_bundle(model_path: str | Path = HNG_DAM_MODEL_PATH) -> HNGIntradayBundle:
    path = Path(model_path)
    if not path.is_file():
        raise HNGIntradayModelError(f"HNG DAM model was not found: {path}")

    try:
        stored = joblib.load(path)
    except Exception as exc:
        raise HNGIntradayModelError(f"Could not load the HNG DAM model: {exc}") from exc

    if isinstance(stored, dict):
        required_metadata = {
            "model",
            "feature_columns",
            "plant_max_output",
            "asset",
            "market",
            "forecast_scope",
        }
        missing_metadata = sorted(required_metadata - set(stored))
        if missing_metadata:
            raise HNGIntradayModelError(
                "The HNG intraday model bundle is missing metadata: "
                + ", ".join(missing_metadata)
            )
        model = stored["model"]
        feature_columns = tuple(stored["feature_columns"])
        plant_max_output = stored["plant_max_output"]
        asset = str(stored["asset"])
        market = str(stored["market"])
        forecast_scope = str(stored["forecast_scope"])
    else:
        model = stored
        feature_columns = HNG_DAM_FEATURES
        plant_max_output = None
        asset = "HNG"
        market = "Intraday"
        forecast_scope = "Every remaining interval of the same delivery day"

    missing_features = sorted(set(HNG_DAM_FEATURES) - set(feature_columns))
    unexpected_features = sorted(set(feature_columns) - set(HNG_DAM_FEATURES))
    if feature_columns != HNG_DAM_FEATURES:
        details = []
        if missing_features:
            details.append("missing " + ", ".join(missing_features))
        if unexpected_features:
            details.append("unsupported " + ", ".join(unexpected_features))
        if len(feature_columns) != len(set(feature_columns)):
            details.append("duplicate feature names")
        if not details:
            details.append("feature order differs")
        raise HNGIntradayModelError(
            "Stored HNG DAM features do not match the supported definitions: " + "; ".join(details)
        )

    if not callable(getattr(model, "predict", None)):
        raise HNGIntradayModelError("The HNG DAM model file does not contain a usable model.")

    model_feature_names = getattr(model, "feature_names_in_", None)
    if model_feature_names is not None and tuple(model_feature_names) != feature_columns:
        raise HNGIntradayModelError(
            "The model feature names do not match the bundle feature_columns order."
        )
    model_feature_count = getattr(model, "n_features_in_", len(feature_columns))
    if int(model_feature_count) != len(feature_columns):
        raise HNGIntradayModelError(
            "The model feature count does not match the bundle feature_columns."
        )

    if asset != "HNG" or market != "Intraday":
        raise HNGIntradayModelError(
            f"Unexpected bundle identity: asset={asset!r}, market={market!r}."
        )
    if forecast_scope != "Every remaining interval of the same delivery day":
        raise HNGIntradayModelError(
            f"Unexpected HNG intraday forecast scope: {forecast_scope!r}."
        )

    if plant_max_output is not None:
        plant_max_output = float(plant_max_output)
        if not np.isfinite(plant_max_output) or plant_max_output <= 0:
            raise HNGIntradayModelError("plant_max_output must be a finite positive value.")

    return HNGIntradayBundle(
        model=model,
        feature_columns=feature_columns,
        plant_max_output=plant_max_output,
        asset=asset,
        market=market,
        forecast_scope=forecast_scope,
    )


def calculate_hng_interval_energy(
    readings,
    interval_start: pd.Timestamp,
    interval_end: pd.Timestamp,
) -> float:
    start = _local_timestamp(interval_start)
    end = _local_timestamp(interval_end)
    if start >= end:
        raise HNGIntradayInputError("The HNG production interval is invalid.")

    samples = []
    for reading in readings:
        observed_raw = getattr(reading, "timestamp_utc", None)
        try:
            observed_at = pd.Timestamp(observed_raw)
        except Exception as exc:
            raise HNGIntradayInputError("An HNG production timestamp is invalid.") from exc
        if observed_at.tzinfo is None:
            raise HNGIntradayInputError("An HNG production timestamp has no timezone.")
        observed_at = observed_at.tz_convert(HNG_TIMEZONE)
        power_mw = _finite_number(getattr(reading, "pv_mw", None), "HNG power")
        if power_mw < 0:
            raise HNGIntradayInputError("HNG production cannot be negative.")
        samples.append((observed_at, power_mw))

    if not samples:
        raise HNGIntradayInputError(
            f"No HNG power samples are available for the completed interval {start} to {end}."
        )

    samples.sort(key=lambda sample: sample[0])
    timestamps = pd.DatetimeIndex(sample[0] for sample in samples)
    if timestamps.duplicated().any():
        raise HNGIntradayInputError("HNG production contains duplicate sample timestamps.")
    if timestamps[-1] > end:
        raise HNGIntradayInputError("HNG interval energy cannot use samples after the interval end.")

    interval_samples = [sample for sample in samples if start <= sample[0] <= end]
    if len(interval_samples) < 2:
        raise HNGIntradayInputError(
            "HNG interval energy requires at least two power samples from the completed interval."
        )
    gaps = pd.Series([sample[0] for sample in interval_samples]).diff().dropna()
    if (gaps > MAX_SAMPLE_GAP).any():
        raise HNGIntradayInputError("HNG power samples contain a gap larger than 7.5 minutes.")

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
        raise HNGIntradayInputError("Calculated HNG interval energy is invalid.")
    return float(energy_mwh)


def get_latest_hng_forecast_origin(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(now if now is not None else pd.Timestamp.now(tz=HNG_TIMEZONE))

    if readings_getter is None:
        from power_reading.database import get_interval_readings

        readings_getter = get_interval_readings

    forecast_origin = _latest_completed_origin(current_time)
    interval_start = forecast_origin - pd.Timedelta(minutes=15)

    try:
        readings = readings_getter(
            "hng",
            start=interval_start.tz_convert("UTC").to_pydatetime(),
            end=forecast_origin.tz_convert("UTC").to_pydatetime(),
        )
    except Exception as exc:
        raise HNGIntradayInputError(f"Could not retrieve HNG interval production: {exc}") from exc

    energy_mwh = calculate_hng_interval_energy(readings, interval_start, forecast_origin)
    return forecast_origin, energy_mwh


def build_hng_intraday_features(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    feature_columns: tuple[str, ...] = HNG_DAM_FEATURES,
    target_start: pd.Timestamp | None = None,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise HNGIntradayInputError("Forecast_origin must be on a 15-minute boundary.")

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    first_target = origin + pd.Timedelta(minutes=15)
    if target_start is not None:
        first_target = max(first_target, _local_timestamp(target_start).ceil("15min"))
    targets = pd.date_range(
        start=first_target,
        end=day_end,
        freq="15min",
        tz=HNG_TIMEZONE,
    )
    if len(targets) == 0:
        return targets, pd.DataFrame(columns=list(feature_columns), index=targets)

    return targets, _build_hng_dam_features(weather_data, targets, feature_columns)


def predict_hng_intraday(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_interval_energy_mwh: float,
    *,
    bundle: HNGIntradayBundle | None = None,
    target_start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    active_bundle = bundle or load_hng_intraday_bundle()
    targets, features = build_hng_intraday_features(
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
        raise HNGIntradayModelError(f"HNG DAM model inference failed: {exc}") from exc
    if len(raw_predictions) != len(features):
        raise HNGIntradayModelError("The HNG intraday model returned an unexpected row count.")
    if not np.isfinite(raw_predictions).all():
        raise HNGIntradayModelError("HNG intraday predictions contain NaN or infinite values.")

    dam_predictions = np.maximum(raw_predictions, 0)
    if active_bundle.plant_max_output is not None:
        dam_predictions = np.minimum(dam_predictions, active_bundle.plant_max_output)
    dark_targets = features["is_dark"].to_numpy(dtype=int) == 1
    dam_predictions[dark_targets] = 0

    origin = _local_timestamp(forecast_origin)
    actual_energy = _finite_number(last_interval_energy_mwh, "Last_Productie")
    if actual_energy < 0:
        raise HNGIntradayInputError("Last_Productie cannot be negative.")

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


def run_hng_intraday_forecast(
    *,
    now: pd.Timestamp | None = None,
    readings_getter: Callable | None = None,
    latest_reading_getter: Callable | None = None,
    model_path: str | Path = HNG_DAM_MODEL_PATH,
    weather_path: str | Path = HNG_ID_WEATHER_PATH,
    result_path: str | Path = HNG_INTRADAY_RESULTS_PATH,
) -> pd.DataFrame:
    bundle = load_hng_intraday_bundle(model_path)
    run_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=HNG_TIMEZONE)
    )
    forecast_origin, last_interval_energy_mwh = get_latest_hng_forecast_origin(
        now=run_time,
        readings_getter=readings_getter,
        latest_reading_getter=latest_reading_getter,
    )
    weather_file = Path(weather_path)
    if not weather_file.is_file():
        raise HNGIntradayInputError(f"HNG target-weather file was not found: {weather_file}")
    try:
        weather_data = pd.read_csv(weather_file)
    except Exception as exc:
        raise HNGIntradayInputError(f"Could not read HNG target weather: {exc}") from exc

    result = predict_hng_intraday(
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
        raise HNGIntradayError(f"Could not write the HNG intraday forecast: {exc}") from exc
    return result


def _build_hng_dam_features(
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

    missing_features = sorted(set(feature_columns) - set(features.columns))
    if missing_features:
        raise HNGIntradayModelError(
            "No supported definition exists for DAM features: " + ", ".join(missing_features)
        )
    features = features.loc[:, list(feature_columns)]
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise HNGIntradayInputError("HNG DAM model inputs contain NaN or infinite values.")
    return features


def _target_weather(weather_data: pd.DataFrame, targets: pd.DatetimeIndex) -> pd.DataFrame:
    required = ("period_end", *WEATHER_COLUMNS)
    missing_columns = sorted(set(required) - set(weather_data.columns))
    if missing_columns:
        raise HNGIntradayInputError(
            "HNG target weather is missing columns: " + ", ".join(missing_columns)
        )

    weather = weather_data.loc[:, list(required)].copy()
    parsed_timestamps = pd.to_datetime(
        weather["period_end"], errors="coerce", utc=True, format="mixed"
    )
    if parsed_timestamps.isna().any():
        raise HNGIntradayInputError("HNG target weather contains invalid period_end timestamps.")
    weather["Target_timestamp"] = parsed_timestamps.dt.tz_convert(HNG_TIMEZONE)
    weather = weather[weather["Target_timestamp"].isin(targets)].copy()

    duplicate_targets = weather["Target_timestamp"].duplicated(keep=False)
    if duplicate_targets.any():
        duplicate = weather.loc[duplicate_targets, "Target_timestamp"].iloc[0]
        raise HNGIntradayInputError(f"HNG target weather has duplicate rows for {duplicate}.")

    weather = weather.set_index("Target_timestamp").reindex(targets)
    missing_rows = weather.index[weather["period_end"].isna()]
    if len(missing_rows):
        preview = ", ".join(str(timestamp) for timestamp in missing_rows[:4])
        suffix = "..." if len(missing_rows) > 4 else ""
        raise HNGIntradayInputError(
            f"HNG target weather is missing {len(missing_rows)} required intervals: {preview}{suffix}"
        )

    renamed = weather.rename(columns=WEATHER_COLUMNS)
    for column in WEATHER_COLUMNS.values():
        renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
    values = renamed.loc[:, list(WEATHER_COLUMNS.values())]
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise HNGIntradayInputError("HNG target weather contains NaN or infinite required values.")
    return values


def _local_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(HNG_TIMEZONE)
    return timestamp.tz_convert(HNG_TIMEZONE)


def _latest_completed_origin(current_time: pd.Timestamp) -> pd.Timestamp:
    return current_time.floor("15min")


def _finite_number(value, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise HNGIntradayInputError(f"{label} is missing or invalid.") from exc
    if not np.isfinite(numeric):
        raise HNGIntradayInputError(f"{label} must be finite.")
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
