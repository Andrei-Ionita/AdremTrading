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

## PostgreSQL storage

The worker creates `power_readings` and `power_reading_errors` automatically,
fetches configured assets in parallel, and stores all successful fields in MW.

Run one collection cycle locally:

```powershell
$env:POWER_READING_RUN_ONCE="true"
python -m power_reading.worker
```

Run continuously at the default three-minute interval:

```powershell
python -m power_reading.worker
```

The worker reads `DATABASE_URL`, matching the existing application. Optional
settings are `POWER_READING_INTERVAL_SECONDS`, `POWER_READING_MAX_WORKERS`, and a
comma-separated `POWER_READING_ASSETS` list. `POWER_READING_ASSET_TIMEOUT_SECONDS`
defaults to 120 and terminates a stuck portal browser without blocking other assets.

Create a second Railway service from this repository and set its Start Command to:

```text
python -m power_reading.worker
```

Give that service the PostgreSQL `DATABASE_URL`, portal credentials, and the same
Dockerfile build. The Streamlit service remains unchanged.

Forecast code can retrieve the previous value directly:

```python
from power_reading import get_latest_reading

previous = get_latest_reading("incuba")
previous_power_mw = previous.pv_mw if previous else None
```
