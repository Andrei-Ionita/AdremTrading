# Production Correction Audit

Verified on 28 September 2026, 12:31-12:36 Europe/Bucharest.
Deployed and local commit: `6c000ad21b1fa6b4360c8f6a8876cec989405d78`.

## Scope and Method

Authenticated Railway and inspected the active production services. Both
AdremTrading and power-reader are running. The README statement that the
background reader is disabled is outdated.

Queried the production database from the app container using a read-only
connection. Executed the deployed `refresh_intraday_corrections()` and both
portfolio exporters against current production inputs. Captured their file
writes in memory, serialized both workbooks to XLSX, and read them back.
Compared the existing saved correction files with the existing production
`Forecast_15min.xlsx` as well.

The fresh audit did not regenerate weather or base model forecasts: the deployed
input files had already been generated around 12:15 that day. It did not replace
production forecasts, modify credentials, push code, or deploy changes. The
temporary Railway SSH key was removed after verification.

## Collection and Export Results

All 14 assets configured for correction had five usable samples in the completed
12:15-12:30 quarter. The latest collection ages were 0.13-2.83 minutes at the
initial database check. All interval integrations and all correction runners
succeeded with no errors.

The table uses average MW equivalents for comparison: interval MWh multiplied
by four. Actual refers to the completed 12:15-12:30 quarter. Forecast columns
refer to the first target, timestamped 12:45, from the audit run.

| Asset | Actual Average MW | Runner Baseline MW | Corrected MW | Result |
|---|---:|---:|---:|---|
| Astro | 2.629 | 2.816 | 2.628 | Downward adjustment |
| Imperial | 3.406 | 2.864 | 3.404 | Upward adjustment |
| Elnet | 2.956 | 1.504 | 2.956 | Upward adjustment |
| Horeco | 1.846 | 1.624 | 1.844 | Upward adjustment |
| Incuba | 0.481 | 0.472 | 0.480 | Upward adjustment |
| Motif | 1.996 | 1.648 | 1.996 | Upward adjustment |
| Anto | 0.687 | 0.648 | 0.688 | Upward adjustment |
| Ferma Frumusica | 2.279 | 2.160 | 2.280 | Upward adjustment |
| Start Fotovoltaice | 0.063 | 0.864 | 0.864 | Downward adjustment suppressed |
| MM&MV | 4.845 | 4.308 | 4.844 | Upward adjustment |
| AnaSun | 7.441 | 7.256 | 7.440 | Upward adjustment |
| HNG | 0.780 | 0.480 | 0.780 | Upward adjustment |
| Necaluxan | 25.554 | 24.516 | 25.552 | Upward adjustment |
| Ulmeni | 3.671 | 3.856 | 3.672 | Downward adjustment |

For each asset, all 45 audit correction rows matched the in-memory 15-minute
portfolio export exactly. All 46 rows per asset in the previously saved
correction files also matched the saved production 15-minute portfolio export.
No corrected predictions were negative or nonfinite. The configured AnaSun,
Ulmeni, and Start Fotovoltaice caps were not exceeded in this run; this does not
establish physical caps for configurations that have no explicit cap.

These results establish collection and export transport for this observed run,
not forecast accuracy or uninterrupted portal availability. In particular,
Aurora stores collection timestamps while retaining individual chart source
timestamps in source metadata, so collection freshness alone is not proof that
the underlying chart values updated at that instant.

## Confirmed Problems

### 1. Large Downward Deviations Are Ignored

`portfolio_intraday.py:309` and `portfolio_intraday.py:360` suppress correction
when actual production is below 50% of the reference forecast. Similar checks
exist in the dedicated Elnet, Horeco, HNG, and Incuba runners.

This occurred for Start Fotovoltaice during the audit: average production was
0.063 MW, but the next forecast remained 0.864 MW. Its runner returned success
with a zero correction weight, so there was no skipped-correction warning.

### 2. The Hourly Download Does Not Carry the Corrections

`balancing.py:446` builds the hourly workbook independently of the corrected
15-minute workbook. The intraday UI calls this function before the 15-minute
export. The two downloads therefore disagree.

For example, the 13:00 Elnet row was 0.853 MWh in the hourly output, while the
four corresponding corrected quarters summed to 2.645 MWh. MM&MV, HNG, Anto,
Motif, Ferma, and Necaluxan had missing hourly values in the checked hours.
Incuba had no hourly column. The workbook roundtrip contained 2,032 missing
prediction cells overall; this count includes the full exported horizon.

### 3. Three Correction Runners Use Older Models

| Asset | Base Forecast Model in ml.py | Correction Model |
|---|---|---|
| Elnet | `rs_xgb_elnet_prod_15min_0726.pkl` | `rs_xgb_elnet_prod_15min_0626.pkl` |
| Horeco | `rs_xgb_horeco_prod_15min_0826.pkl` | `rs_xgb_horeco_prod_15min_0426.pkl` |
| HNG | `rs_xgb_hng_prod_15min_0826.pkl` | `rs_xgb_hng_prod_15min_0626.pkl` |

These runners rebuild their baseline from the older model, so the correction
does not use the same baseline as the latest base forecast. See the model
constants at line 13 in each runner and `ml.py:4126`, `ml.py:4328`, and
`ml.py:6048`.

### Intended Behavior: The Latest Interval Anchors Future Forecasts

The deployed run measured 12:15-12:30 but used the first target forecast at
12:45 as the reference. See `portfolio_intraday.py:305` and
`portfolio_intraday.py:452`, with equivalent logic in the four dedicated runners.

With an initial correction weight of one, this anchors the first target to the
previous interval's production, subject to caps. The user confirmed this is the
intended correction method. It was initially classified incorrectly as a defect
and is preserved in the fix.

### 4. Uncorrected Baselines Are Joined by Row Position

`balancing.py:550` onward assigns each asset's base prediction series by row
index onto Astro's timestamps. In the deployed inputs, Astro started at 12:15
and many assets started at 12:30. Their base forecasts are therefore displaced
by one quarter where no timestamp-based correction overlay replaces them.

The corrected rows checked above are correctly aligned by timestamp. The
remaining base forecast horizon is still affected. The serialized 15-minute
workbook contained 16 missing prediction cells overall.

## Follow-up Work

Remove suppression of large downward corrections, use the current base
forecast files, join portfolio inputs by timestamp, and derive the hourly
export from the final corrected quarters. Preserve the agreed anchoring of
future forecasts to the latest measured interval.

## Fix Verification, 29 September 2026

The local candidate implements these changes. Missing base rows are filled
with zero only when the matching asset weather confirms darkness. The export
uses the delivery horizon common to all assets and excludes incomplete edge
hours from the hourly download.

At 10:47 Europe/Bucharest, the candidate was executed in a separate Railway
process with current production samples and input files. No deployed code or
forecast files were overwritten.

- 13 currently readable assets produced corrections and all corrected rows
  matched their exported 15-minute values.
- Both workbooks survived XLSX serialization/readback with no missing or
  negative prediction values: 669 quarters, 166 complete hours, 24 assets.
- Every hourly value matched the sum of its four final forecast quarters.
- HNG's actual zero correctly reduced the next forecast to zero, instead of
  retaining its 0.114 MWh baseline.
- The Start Fotovoltaice incident is covered by a regression test: measured
  0.015782 MWh now produces 0.016 MWh, replacing the 0.216 MWh baseline.

Ulmeni is currently blocked by an expired certificate on its external portal.
The worker's latest reading is from 28 September, 19:19 local time. The fix
rejects that stale sample and reports the missing current interval. The portal
operator must renew the certificate before live correction can resume; TLS
verification has not been disabled. Valid net-import readings from its grid
meter are treated as zero export in interval calculations.
