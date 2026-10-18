"""Replay deployed weather/models and real readings without overwriting forecasts."""
import contextlib
import io
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd


def verify(*, refresh_weather=False):
    import ml
    import portfolio_intraday as intraday
    from forecast_time import delivery_times
    from incuba_intraday import get_latest_incuba_forecast_origin, predict_incuba_intraday
    from portfolio_export import ASSET_FILES, CORRECTIONS, build_quarter_hourly_portfolio, aggregate_hourly_portfolio, write_portfolio_excel

    root = Path(__file__).resolve().parents[1]
    original = Path.cwd()
    report = {"baselines": [], "corrections": []}
    with tempfile.TemporaryDirectory(prefix="portfolio-verify-") as temporary:
        stage = Path(temporary)
        try:
            for _, directory, _, location in ASSET_FILES:
                target = stage / directory
                (target / "Solcast").mkdir(parents=True, exist_ok=True)
                if not refresh_weather:
                    (target / "Solcast" / f"{location}_15min.csv").symlink_to(
                        root / directory / "Solcast" / f"{location}_15min.csv")
                for model in (root / directory).glob("*.pkl"):
                    (target / model.name).symlink_to(model)
            os.chdir(stage)
            for suffix, directory, filename, _ in ASSET_FILES:
                with contextlib.redirect_stdout(io.StringIO()):
                    if refresh_weather:
                        getattr(ml, f"fetching_{filename}_data_15min")()
                    getattr(ml, f"predicting_exporting_{filename}_15min")(1, 24, 0)
                frame = pd.read_excel(stage / directory / f"Results_Production_{filename}_xgb_15min.xlsx")
                stamps = delivery_times(frame)
                assert not stamps.duplicated().any()
                assert np.isfinite(frame.Prediction).all() and frame.Prediction.ge(0).all()
                report["baselines"].append({"asset": suffix, "rows": len(frame),
                    "first": str(stamps.min()), "last": str(stamps.max())})

            now = pd.Timestamp.now(tz="Europe/Bucharest")
            flags = {}
            staged_corrections = {}
            for asset, (suffix, output_path) in CORRECTIONS.items():
                result_path = stage / output_path.relative_to(root)
                staged_corrections[asset] = (suffix, result_path)
                try:
                    if asset == "incuba":
                        origin, energy = get_latest_incuba_forecast_origin(now=now)
                        result = predict_incuba_intraday(
                            pd.read_excel(stage / "Adrem/Results_Production_Adrem_xgb_15min.xlsx"),
                            pd.read_csv(stage / "Adrem/Solcast/Apold_15min.csv"), origin, energy, target_start=now)
                    else:
                        name = {"ferma": "FERMA", "renewable_energy_holding": "RENEWABLE_ENERGY_HOLDING"}.get(asset, asset.upper())
                        config = getattr(intraday, name + "_INTRADAY_CONFIG")
                        config = replace(config, dam_results_path=stage / config.dam_results_path.relative_to(root),
                                         weather_path=stage / config.weather_path.relative_to(root),
                                         intraday_results_path=result_path)
                        result = intraday.run_portfolio_intraday_forecast(config, now=now)
                    result_path.parent.mkdir(parents=True, exist_ok=True)
                    result.to_excel(result_path, index=False)
                    flags[asset] = not result.empty
                    if result.empty:
                        report["corrections"].append({"asset": asset, "status": "no_remaining_intervals"})
                        continue
                    assert not delivery_times(result).duplicated().any()
                    assert np.isfinite(result.Prediction_ID).all() and result.Prediction_ID.ge(0).all()
                    expected = result.Prediction_DAM + result.Correction
                    assert np.allclose(expected, result.Prediction_ID, atol=0.00101)
                    report["corrections"].append({"asset": asset, "status": "verified",
                        "origin": str(result.Forecast_origin.iloc[0]), "rows": len(result),
                        "actual_mwh": float(result.Last_Productie.iloc[0]),
                        "baseline_first_mwh": float(result.Prediction_DAM.iloc[0]),
                        "corrected_first_mwh": float(result.Prediction_ID.iloc[0]),
                        "weight": float(result.Correction_weight.iloc[0])})
                except Exception as exc:
                    # Do not print portal/DB exception text, which may contain secrets.
                    message = str(exc).lower()
                    category = "missing_samples" if "no " in message and "power samples" in message else (
                        "insufficient_samples" if "at least two" in message else (
                        "sample_gap" if "gap larger" in message else type(exc).__name__))
                    entry = {"asset": asset, "status": "blocked", "reason": category}
                    try:
                        from power_reading.database import get_latest_reading
                        key = "incuba" if asset == "incuba" else config.asset_key
                        latest = get_latest_reading(key)
                        entry["latest_reading_utc"] = latest.timestamp_utc if latest else None
                    except Exception:
                        pass
                    report["corrections"].append(entry)
                    flags[asset] = False

            from unittest.mock import patch
            with patch.dict(CORRECTIONS, staged_corrections):
                quarters = build_quarter_hourly_portfolio(flags)
            hours = aggregate_hourly_portfolio(quarters)
            assert not quarters.Lookup.duplicated().any()
            assert not hours.Lookup.duplicated().any()
            for frame in (quarters, hours):
                assert np.isfinite(frame.filter(like="Prediction_").to_numpy()).all()
                output = io.BytesIO()
                write_portfolio_excel(frame, output)
                output.seek(0)
                reread = pd.read_excel(output)
                assert len(frame) == len(reread)
                assert reread.Data.eq(reread.Data.dt.normalize()).all()
                assert "Data_UTC" not in reread.columns
            report["export"] = {"quarters": len(quarters), "hours": len(hours),
                                "assets": len(quarters.filter(like="Prediction_").columns),
                                "duplicate_utc": int(quarters.Data_UTC.duplicated().sum())}
        finally:
            os.chdir(original)
    return report


if __name__ == "__main__":
    try:
        print(json.dumps(verify()))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}))
        raise SystemExit(1)
