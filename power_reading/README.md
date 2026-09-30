# Power reading module

This package moves the live portal readers into `AdremTrading` without coupling
them to Streamlit, Excel, or the forecast models.

```python
from power_reading import read_asset

reading = read_asset("incuba")
previous_power_mw = reading.pv_mw
```

All returned power fields are normalized to MW. Credentials and portal settings
use the same environment variable names as the original reader application.

Local smoke test:

```powershell
python -m power_reading.read_power_once incuba
```

Railway must have the corresponding credential variables configured. Browser
profiles are written below `POWER_READING_PROFILE_DIR` (default:
`.playwright_profiles`). SNK uses a private IP and requires Railway network access
to that endpoint; Windows-only SCADA window capture is not available on Railway.

## Production Collection and Correction

The `power-reader` Railway service runs `python -m power_reading.worker` and
stores live readings in PostgreSQL. Its default collection interval is 180
seconds. The web service reads the latest completed 15-minute interval from
that database when a forecast correction is requested. Missing or insufficient
samples retain the base forecast and produce a warning.

Corrections anchor the next forecast to the completed interval's measured
energy, in either direction, with a two-hour decay half-life. Elnet, Horeco and
HNG use their generated base forecast files. The 15-minute portfolio is joined
by timestamp, and the hourly download sums four complete corrected quarters.

The source-specific interval API is also available for diagnostics:

```python
from power_reading import read_interval_energy

energy_mwh = read_interval_energy("hng", start=interval_start, end=interval_end)
```

Direct portal intervals are cached for 15 minutes. This API is separate from
the production correction path that integrates stored worker samples.

`read_asset()` remains available as a manual diagnostic for a current power
snapshot. It is not used to calculate production energy for forecast correction.

For direct interval diagnostics, ADC combines the portal's `15M AVG` and live
power; Ulmeni uses its validated WinCC power for the 0.25-hour estimate. In the
production path, both use stored samples from the completed interval. Ulmeni's
grid-meter imports count as zero exported production; stale readings are never
carried forward to replace a missing interval.
