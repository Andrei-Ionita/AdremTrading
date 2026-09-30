# Renewable Energy Holding

Portfolio membership starts with the delivery interval beginning October 1,
2026, 00:00 Europe/Bucharest. Motif remains included for earlier delivery
intervals only. Forecasts spanning that boundary contain both columns, with
zero outside each asset's membership dates; October-only forecasts omit Motif.

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

Motif's portfolio model/weather requests and correction refresh stop on October 1.
Its historical files, standalone forecasting tools, and power-reader configuration
are unchanged. These changes are local only; nothing has been pushed or deployed.
