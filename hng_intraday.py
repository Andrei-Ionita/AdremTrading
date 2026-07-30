from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
import pandas as pd


APP_ROOT = Path(__file__).resolve().parent
HNG_ID_MODEL_PATH = APP_ROOT / "HNG" / "HNG_ID_0626.joblib"
HNG_ID_WEATHER_PATH = APP_ROOT / "HNG" / "Solcast" / "Mures_15min.csv"
HNG_ID_RESULTS_PATH = APP_ROOT / "HNG" / "Results_Production_HNG_ID_15min.xlsx"
HNG_TIMEZONE = "Europe/Bucharest"
FORECAST_INTERVAL_HOURS = 0.25

HNG_ID_FEATURES = (
    "Interval",
    "Month",
    "Interval_sin",
    "Interval_cos",
    "DayofYear_sin",
    "DayofYear_cos",
    "Temperatura",
    "Nori",
    "Radiatie",
    "Dewpoint",
    "Umiditate",
    "Zenith",
    "Azimuth",
    "Solar_elevation",
    "Cos_zenith",
    "Azimuth_sin",
    "Azimuth_cos",
    "Last_Productie",
    "Forecast_horizon_minutes",
    "is_dark",
)

WEATHER_COLUMNS = {
    "air_temp": "Temperatura",
    "cloud_opacity": "Nori",
    "ghi": "Radiatie",
    "dewpoint_temp": "Dewpoint",
    "relative_humidity": "Umiditate",
    "zenith": "Zenith",
    "azimuth": "Azimuth",
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


def load_hng_intraday_bundle(model_path: str | Path = HNG_ID_MODEL_PATH) -> HNGIntradayBundle:
    path = Path(model_path)
    if not path.is_file():
        raise HNGIntradayModelError(f"HNG intraday model bundle was not found: {path}")

    try:
        stored = joblib.load(path)
    except Exception as exc:
        raise HNGIntradayModelError(f"Could not load the HNG intraday model bundle: {exc}") from exc

    if not isinstance(stored, dict):
        raise HNGIntradayModelError("The HNG intraday model bundle must be a dictionary.")

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
            "The HNG intraday model bundle is missing metadata: " + ", ".join(missing_metadata)
        )

    feature_columns = tuple(stored["feature_columns"])
    missing_features = sorted(set(HNG_ID_FEATURES) - set(feature_columns))
    unexpected_features = sorted(set(feature_columns) - set(HNG_ID_FEATURES))
    if len(feature_columns) != len(set(feature_columns)) or missing_features or unexpected_features:
        details = []
        if missing_features:
            details.append("missing " + ", ".join(missing_features))
        if unexpected_features:
            details.append("unsupported " + ", ".join(unexpected_features))
        if len(feature_columns) != len(set(feature_columns)):
            details.append("duplicate feature names")
        raise HNGIntradayModelError(
            "Stored HNG intraday features do not match the supported definitions: " + "; ".join(details)
        )

    model = stored["model"]
    if not callable(getattr(model, "predict", None)):
        raise HNGIntradayModelError("The HNG intraday bundle does not contain a usable model.")

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

    asset = str(stored["asset"])
    market = str(stored["market"])
    forecast_scope = str(stored["forecast_scope"])
    if asset != "HNG" or market != "Intraday":
        raise HNGIntradayModelError(
            f"Unexpected bundle identity: asset={asset!r}, market={market!r}."
        )
    if forecast_scope != "Every remaining interval of the same delivery day":
        raise HNGIntradayModelError(
            f"Unexpected HNG intraday forecast scope: {forecast_scope!r}."
        )

    plant_max_output = stored["plant_max_output"]
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


def get_latest_hng_forecast_origin(
    *,
    now: pd.Timestamp | None = None,
    reading_getter: Callable | None = None,
) -> tuple[pd.Timestamp, float]:
    current_time = _local_timestamp(now if now is not None else pd.Timestamp.now(tz=HNG_TIMEZONE))
    forecast_origin = current_time.floor("15min")

    if reading_getter is None:
        from power_reading.database import get_latest_reading

        reading_getter = get_latest_reading

    # get_latest_reading uses a strict '< before' filter. One microsecond includes
    # a reading recorded exactly on the selected quarter-hour boundary.
    before_utc = (forecast_origin + pd.Timedelta(microseconds=1)).tz_convert("UTC").to_pydatetime()
    try:
        reading = reading_getter("hng", before=before_utc)
    except Exception as exc:
        raise HNGIntradayInputError(f"Could not retrieve the latest HNG production: {exc}") from exc

    if reading is None:
        raise HNGIntradayInputError(
            f"No HNG production measurement is available at or before {forecast_origin}."
        )

    power_mw = _finite_number(getattr(reading, "pv_mw", None), "HNG power")
    if power_mw < 0:
        raise HNGIntradayInputError("HNG production cannot be negative.")

    observed_raw = getattr(reading, "timestamp_utc", None)
    try:
        observed_at = pd.Timestamp(observed_raw)
    except Exception as exc:
        raise HNGIntradayInputError("The latest HNG production timestamp is invalid.") from exc
    if observed_at.tzinfo is None:
        raise HNGIntradayInputError("The latest HNG production timestamp has no timezone.")
    observed_at = observed_at.tz_convert(HNG_TIMEZONE)
    if observed_at > forecast_origin:
        raise HNGIntradayInputError(
            "The selected HNG production measurement occurs after Forecast_origin."
        )
    if observed_at.date() != forecast_origin.date():
        raise HNGIntradayInputError(
            "No HNG production measurement is available for the current delivery day."
        )

    last_interval_energy_mwh = power_mw * FORECAST_INTERVAL_HOURS
    return forecast_origin, last_interval_energy_mwh


def build_hng_intraday_features(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_production: float,
    feature_columns: tuple[str, ...] = HNG_ID_FEATURES,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    origin = _local_timestamp(forecast_origin)
    if origin != origin.floor("15min"):
        raise HNGIntradayInputError("Forecast_origin must be on a 15-minute boundary.")

    production = _finite_number(last_production, "Last_Productie")
    if production < 0:
        raise HNGIntradayInputError("Last_Productie cannot be negative.")

    day_end = origin.normalize() + pd.Timedelta(hours=23, minutes=45)
    targets = pd.date_range(
        start=origin + pd.Timedelta(minutes=15),
        end=day_end,
        freq="15min",
        tz=HNG_TIMEZONE,
    )
    if len(targets) == 0:
        return targets, pd.DataFrame(columns=list(feature_columns), index=targets)

    weather = _target_weather(weather_data, targets)
    intervals = targets.hour * 4 + targets.minute // 15 + 1
    day_of_year = targets.dayofyear

    features = pd.DataFrame(index=targets)
    features["Interval"] = intervals
    features["Month"] = targets.month
    features["Interval_sin"] = np.sin(2 * np.pi * (intervals - 1) / 96)
    features["Interval_cos"] = np.cos(2 * np.pi * (intervals - 1) / 96)
    features["DayofYear_sin"] = np.sin(2 * np.pi * day_of_year / 366)
    features["DayofYear_cos"] = np.cos(2 * np.pi * day_of_year / 366)

    for column in WEATHER_COLUMNS.values():
        features[column] = weather[column].to_numpy()

    features["Solar_elevation"] = 90 - features["Zenith"]
    features["Cos_zenith"] = np.maximum(
        np.cos(np.deg2rad(features["Zenith"].to_numpy(dtype=float))), 0
    )
    features["Azimuth_sin"] = np.sin(np.deg2rad(features["Azimuth"].to_numpy(dtype=float)))
    features["Azimuth_cos"] = np.cos(np.deg2rad(features["Azimuth"].to_numpy(dtype=float)))
    features["Last_Productie"] = production
    features["Forecast_horizon_minutes"] = (
        (targets - origin) / pd.Timedelta(minutes=1)
    ).astype(int)
    features["is_dark"] = (features["Radiatie"] <= 0).astype(int)

    missing_features = sorted(set(feature_columns) - set(features.columns))
    if missing_features:
        raise HNGIntradayModelError(
            "No supported definition exists for stored features: " + ", ".join(missing_features)
        )
    features = features.loc[:, list(feature_columns)]
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        raise HNGIntradayInputError("HNG intraday model inputs contain NaN or infinite values.")

    return targets, features


def predict_hng_intraday(
    weather_data: pd.DataFrame,
    forecast_origin: pd.Timestamp,
    last_production: float,
    *,
    bundle: HNGIntradayBundle | None = None,
) -> pd.DataFrame:
    active_bundle = bundle or load_hng_intraday_bundle()
    targets, features = build_hng_intraday_features(
        weather_data,
        forecast_origin,
        last_production,
        active_bundle.feature_columns,
    )
    if features.empty:
        return _empty_result()

    try:
        raw_predictions = np.asarray(active_bundle.model.predict(features), dtype=float).reshape(-1)
    except Exception as exc:
        raise HNGIntradayModelError(f"HNG intraday model inference failed: {exc}") from exc
    if len(raw_predictions) != len(features):
        raise HNGIntradayModelError("The HNG intraday model returned an unexpected row count.")
    if not np.isfinite(raw_predictions).all():
        raise HNGIntradayModelError("HNG intraday predictions contain NaN or infinite values.")

    predictions = np.maximum(raw_predictions, 0)
    if active_bundle.plant_max_output is not None:
        predictions = np.minimum(predictions, active_bundle.plant_max_output)
    predictions[features["is_dark"].to_numpy(dtype=int) == 1] = 0

    origin = _local_timestamp(forecast_origin)
    return pd.DataFrame(
        {
            "Data": targets.tz_localize(None),
            "Interval": features["Interval"].to_numpy(dtype=int),
            "Prediction_ID": np.round(predictions, 3),
            "Forecast_origin": origin.tz_localize(None),
            "Last_Productie": features["Last_Productie"].to_numpy(dtype=float),
            "Forecast_horizon_minutes": features["Forecast_horizon_minutes"].to_numpy(dtype=int),
            "Market": active_bundle.market,
        }
    )


def run_hng_intraday_forecast(
    *,
    now: pd.Timestamp | None = None,
    reading_getter: Callable | None = None,
    model_path: str | Path = HNG_ID_MODEL_PATH,
    weather_path: str | Path = HNG_ID_WEATHER_PATH,
    result_path: str | Path = HNG_ID_RESULTS_PATH,
) -> pd.DataFrame:
    bundle = load_hng_intraday_bundle(model_path)
    forecast_origin, last_production = get_latest_hng_forecast_origin(
        now=now,
        reading_getter=reading_getter,
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
        last_production,
        bundle=bundle,
    )
    output_file = Path(result_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.to_excel(output_file, index=False, sheet_name="Intraday_Predictions")
    except Exception as exc:
        raise HNGIntradayError(f"Could not write the HNG intraday forecast: {exc}") from exc
    return result


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
            "Prediction_ID",
            "Forecast_origin",
            "Last_Productie",
            "Forecast_horizon_minutes",
            "Market",
        ]
    )
