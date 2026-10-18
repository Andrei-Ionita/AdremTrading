"""Delivery-time identity shared by forecast generation, correction and export."""
import pandas as pd

LOCAL_TIMEZONE = "Europe/Bucharest"
UTC_COLUMN = "Data_UTC"


def utc_strings(values):
    return pd.DatetimeIndex(values).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def solcast_times(values):
    stamps = pd.to_datetime(values, errors="raise", utc=True, format="mixed")
    if stamps.isna().any() or stamps.duplicated().any():
        raise ValueError("Solcast timestamps must be present and unique.")
    return stamps.dt.tz_convert(LOCAL_TIMEZONE)


def delivery_times(frame):
    """Read explicit UTC identity, retaining compatibility with older local files."""
    local = pd.to_datetime(frame["Data"], errors="raise", format="mixed")
    if local.isna().any():
        raise ValueError("Forecast contains invalid Data timestamps.")
    if UTC_COLUMN in frame:
        stamps = pd.DatetimeIndex(pd.to_datetime(
            frame[UTC_COLUMN], errors="raise", utc=True, format="mixed"
        )).tz_convert(LOCAL_TIMEZONE)
        displayed = pd.DatetimeIndex(local)
        if displayed.tz is not None:
            displayed = displayed.tz_convert(LOCAL_TIMEZONE).tz_localize(None)
        if stamps.isna().any() or not stamps.tz_localize(None).equals(displayed):
            raise ValueError("Forecast Data and Data_UTC timestamps disagree.")
        return stamps
    stamps = pd.DatetimeIndex(local)
    if stamps.tz is not None:
        return stamps.tz_convert(LOCAL_TIMEZONE)
    # Only infer a repeated hour when both folds appear in chronological order.
    # An isolated ambiguous timestamp or a nonexistent spring hour still fails.
    try:
        return stamps.tz_localize(LOCAL_TIMEZONE, ambiguous="infer", nonexistent="raise")
    except Exception as exc:
        raise ValueError("Forecast contains ambiguous or nonexistent local timestamps; regenerate with Data_UTC.") from exc


def round_delivery(timestamp, method):
    return getattr(timestamp.tz_convert("UTC"), method)("15min").tz_convert(LOCAL_TIMEZONE)


def local_day_end(timestamp):
    return (timestamp.normalize() + pd.DateOffset(days=1)) - pd.Timedelta(minutes=15)


def delivery_intervals(stamps, minutes=15):
    """Sequential market intervals: 92/96/100 quarters on 23/24/25-hour days."""
    stamps = pd.DatetimeIndex(stamps)
    return ((stamps - stamps.normalize()) / pd.Timedelta(minutes=minutes)).astype(int) + 1
