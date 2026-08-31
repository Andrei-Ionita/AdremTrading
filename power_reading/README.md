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

## Forecast-time interval retrieval

Production corrections retrieve one completed 15-minute interval only when a
forecast is triggered:

```python
from power_reading import read_interval_energy

energy_mwh = read_interval_energy("hng", start=interval_start, end=interval_end)
```

The background worker entry point is disabled and is not a Railway process.
Completed intervals are cached for 15 minutes to avoid reopening a portal when
the same forecast workflow requests the same asset more than once.

`read_asset()` remains available as a manual diagnostic for a current power
snapshot. It is not used to calculate production energy for forecast correction.

ADC assets intentionally estimate the quarter from the equal-weight mean of the
portal's current `15M AVG` and live power. Ulmeni has no historical source, so its
quarter estimate intentionally uses the current validated WinCC power for the
full 0.25-hour interval.
