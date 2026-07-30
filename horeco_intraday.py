from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
HORECO_ID_MODEL_PATH = APP_ROOT / "Horeco" / "rs_xgb_horeco_prod_15min_0426.pkl"
HORECO_ID_WEATHER_PATH = APP_ROOT / "Horeco" / "Solcast" / "Buzau_15min.csv"
HORECO_ID_RESULTS_PATH = APP_ROOT / "Horeco" / "Results_Production_Horeco_ID_15min.xlsx"
HORECO_TIMEZONE = "Europe/Bucharest"
FORECAST_INTERVAL_HOURS = 0.25
HORECO_MAX_INTERVAL_ENERGY_MWH = 2.275 * FORECAST_INTERVAL_HOURS
CORRECTION_DECAY_MINUTES = 180.0
LOW_BASELINE_DECAY_MINUTES = 60.0

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
    """Base error for a user-facing Horeco intraday forecast failure."""


class HorecoIntradayInputError(HorecoIntradayError):
    """Raised when live production or target weather is invalid."""


class HorecoIntradayModelError(HorecoIntradayError):
    """Raised when the stored Horeco baseline model is incompatible."""


@dataclass(frozen=True)
class HorecoBaselineModel:
    model: object
    feature_columns: tuple[str, ...] = HORECO_BASELINE_FEATURES
    plant_max_output: float = HORECO_MAX_INTERVAL_ENERGY_MWH


def load_horeco_baseline_model(
    model_path: str | Path = HORECO_ID_MODEL_PATH,
) -> HorecoBaselineModel:
    path = Path(model_path)
    if not path.is_file():
        raise HorecoIntradayModelError(f"Horeco baseline model was not found: {path}")

    try:
        model = joblib.load(path)
    except Exception as exc:
        raise HorecoIntradayModelError(f"Could not load the Horeco baseline model: {exc}") from exc

    if not callable(getattr(model, "predict", None)):
        raise HorecoIntradayModelError("The Horeco baseline model is not usable.")
    feature_count = int(getattr(model, "n_features_in_", len(HORECO_BASELINE_FEATURES)))
    if feature_count != len(HORECO_BASELINE_FEATURES):
        raise HorecoIntradayModelError(
            "The Horeco baseline model does not match the expected six input features."
        )

    return HorecoBaselineModel(model=model)


def get_latest_horeco_forecast_origin(
    *,
    now: pd.Timestamp | None = None,
    reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(
        now if now is not None else pd.Timestamp.now(tz=HORECO_TIMEZONE)
    )
    forecast_origin = current_time.floor("15min")

    if reading_getter is None:
        from power_reading.database import get_latest_reading

        reading_getter = get_latest_reading

    before_utc = (forecast_origin + pd.Timedelta(microseconds=1)).tz_convert(
        "UTC"
    ).to_pydatetime()
    try:
        reading = reading_getter("horeco", before=before_utc)
    except Exception as exc:
        raise HorecoIntradayInputError(
            f"Could not retrieve the latest Horeco production: {exc}"
        ) from exc

    if reading is None:
        raise HorecoIntradayInputError(
            f"No Horeco production measurement is available at or before {forecast_origin}."
        )

    power_mw = _finite_number(getattr(reading, "pv_mw", None), "Horeco power")
    if power_mw < 0:
        raise HorecoIntradayInputError("Horeco production cannot be negative.")

    observed_raw = getattr(reading, "timestamp_utc", None)
    try:
        observed_at = pd.Timestamp(observed_raw)
    except Exception as exc:
        raise HorecoIntradayInputError(
            "The latest Horeco production timestamp is invalid."
        ) from exc
    if observed_at.tzinfo is None:
        raise HorecoIntradayInputError(
            "The latest Horeco production timestamp has no timezone."
        )
    observed_at = observed_at.tz_convert(HORECO_TIMEZONE)
    if observed_at > forecast_origin:
        raise HorecoIntradayInputError(
            "The selected Horeco production measurement occurs after Forecast_origin."
        )
    if observed_at.date() != forecast_origin.date():
        raise HorecoIntradayInputError(
            "No Horeco production measurement is available for the current delivery day."
        )

    last_interval_energy_mwh = power_mw * FORECAST_INTERVAL_HOURS
    return forecast_origin, last_interval_energy_mwh


def build_horeco_baseline_features(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise HorecoIntradayInputError("Forecast_origin must be on a 15-minute boundary.")

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    targets = pd.date_range(start=origin, end=day_end, freq="15min", tz=HORECO_TIMEZONE)
    weather = _target_weather(weather_data, targets)

    features = pd.DataFrame(index=targets)
    features["Interval"] = targets.hour * 4 + targets.minute // 15 + 1
    for column in WEATHER_COLUMNS.values():
        features[column] = weather[column].to_numpy()
    features["Month"] = targets.month
    features["is_dark"] = (features["Radiatie"] <= 0).astype(int)
    features = features.loc[:, list(HORECO_BASELINE_FEATURES)]

    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise HorecoIntradayInputError(
            "Horeco intraday model inputs contain NaN or infinite values."
        )
    return targets, features


def predict_horeco_intraday(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_production: float,
    *,
    baseline_model: HorecoBaselineModel | None = None,
) -> pd.DataFrame:
    origin = _local_timestamp(forecast_origin)
    production = _finite_number(last_production, "Last_Productie")
    if production < 0:
        raise HorecoIntradayInputError("Last_Productie cannot be negative.")
    if origin.time() == pd.Timestamp("23:45").time():
        return _empty_result()

    active_model = baseline_model or load_horeco_baseline_model()
    targets, features = build_horeco_baseline_features(weather_data, origin)
    try:
        raw_baseline = np.asarray(
            active_model.model.predict(features.to_numpy()), dtype=float
        ).reshape(-1)
    except Exception as exc:
        raise HorecoIntradayModelError(f"Horeco baseline inference failed: {exc}") from exc
    if len(raw_baseline) != len(features):
        raise HorecoIntradayModelError("The Horeco baseline model returned an unexpected row count.")
    if not np.isfinite(raw_baseline).all():
        raise HorecoIntradayModelError(
            "Horeco baseline predictions contain NaN or infinite values."
        )

    baseline = np.clip(raw_baseline, 0, active_model.plant_max_output)
    baseline[features["is_dark"].to_numpy(dtype=int) == 1] = 0
    origin_baseline = float(baseline[0])
    future_targets = targets[1:]
    future_features = features.iloc[1:]
    future_baseline = baseline[1:]
    horizons = np.asarray(
        (future_targets - origin) / pd.Timedelta(minutes=1), dtype=float
    )
    decay = np.exp(-horizons / CORRECTION_DECAY_MINUTES)

    if origin_baseline >= 0.05:
        observed_ratio = np.clip(production / origin_baseline, 0.0, 3.0)
        correction_factor = 1.0 + (observed_ratio - 1.0) * decay
        predictions = future_baseline * correction_factor
    else:
        low_baseline_decay = np.exp(-horizons / LOW_BASELINE_DECAY_MINUTES)
        predictions = future_baseline + production * low_baseline_decay

    predictions = np.clip(predictions, 0, active_model.plant_max_output)
    predictions[future_features["is_dark"].to_numpy(dtype=int) == 1] = 0
    predictions = np.round(predictions, 3)
    future_baseline = np.round(future_baseline, 3)

    return pd.DataFrame(
        {
            "Data": future_targets.tz_localize(None),
            "Interval": future_features["Interval"].to_numpy(dtype=int),
            "Prediction_ID": predictions,
            "Baseline_prediction": future_baseline,
            "Actual_correction": np.round(predictions - future_baseline, 3),
            "Forecast_origin": origin.tz_localize(None),
            "Last_Productie": production,
            "Forecast_horizon_minutes": horizons.astype(int),
            "Market": "Intraday",
        }
    )


def run_horeco_intraday_forecast(
    *,
    now: pd.Timestamp | None = None,
    reading_getter: Callable | None = None,
    model_path: str | Path = HORECO_ID_MODEL_PATH,
    weather_path: str | Path = HORECO_ID_WEATHER_PATH,
    result_path: str | Path = HORECO_ID_RESULTS_PATH,
) -> pd.DataFrame:
    baseline_model = load_horeco_baseline_model(model_path)
    forecast_origin, last_production = get_latest_horeco_forecast_origin(
        now=now, reading_getter=reading_getter
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
        last_production,
        baseline_model=baseline_model,
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
            "Prediction_ID",
            "Baseline_prediction",
            "Actual_correction",
            "Forecast_origin",
            "Last_Productie",
            "Forecast_horizon_minutes",
            "Market",
        ]
    )
