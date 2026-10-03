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

## FusionSolar Session Recovery

Elnet and Horeco reuse authenticated browser state before attempting a login.
Browser state includes cookies, local storage, and tab-level session storage.
Complete snapshots reopen the authenticated plant directly; older snapshots
without tab state use the regional SSO entry point. State is saved only after
the requested plant list or overview is ready, not merely after a URL change.
State is encrypted with Fernet using an account-bound Scrypt key derived from
the existing portal credentials, and stored in `fusion_solar_sessions` in the
configured PostgreSQL database. Without a database it is stored encrypted in
the asset's ignored browser-profile directory. Password rotation invalidates
the saved state. No portal credentials or session tokens belong in Git or logs.

When FusionSolar requires CAPTCHA or a verification code, collection fails
explicitly without trying to bypass the challenge. An operator can authenticate
the Railway reader using the loopback-only, SSH-protected recovery page:

```powershell
python -B scripts/fusionsolar_reauthenticate.py --ssh-target <reader-service-instance>@ssh.railway.com
```

Add `--verify-only` to test both saved sessions in fresh browsers without
submitting credentials. This reports only power, timestamp, and safe status fields.

The helper uses existing credentials inside Railway, masks them in the page,
and requires the operator to enter the verification code. It temporarily
registers an SSH key and revokes it when finished. It does not deploy code or
write production power samples. After authentication, verify at least one full
completed quarter of regular reader samples before claiming correction is
restored. Missing samples still retain the unadjusted forecast.

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
