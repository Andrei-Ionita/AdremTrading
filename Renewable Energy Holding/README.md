# Renewable Energy Holding

Portfolio membership starts with the delivery interval beginning October 1,
2026, 00:00 Europe/Bucharest. Motif is no longer part of the portfolio and is
excluded from all newly generated exports, including earlier delivery dates.
For forecasts spanning October 1, Renewable Energy Holding is zero before
its membership starts.

The 15-minute forecast is Elnet's uncorrected model forecast multiplied by
`2.37 / 2.7` (approximately `0.877777778`). No separate model or weather request
is needed. Elnet's live-production correction is not transferred to this asset;
no independent live-power source has been configured for Renewable Energy Holding.

The portfolio export generates
`Results_Production_Renewable_Energy_Holding_xgb_15min.xlsx` in this folder
and includes `Prediction_Renewable_Energy_Holding` in both portfolio downloads.
Hourly energy is the sum of the corresponding four quarter-hour values.
The existing input and Solcast files are preserved but are not required by
the proxy forecast.

Motif's portfolio model/weather requests and correction refresh are removed.
Its historical files, standalone forecasting tools, and power-reader configuration
are unchanged.
