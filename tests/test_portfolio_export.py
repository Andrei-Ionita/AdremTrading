import io
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from portfolio_export import (
    ASSET_FILES, CORRECTIONS, RENEWABLE_ENERGY_HOLDING_SCALE,
    aggregate_hourly_portfolio, build_quarter_hourly_portfolio,
)
from incuba_intraday import ADREM_TO_INCUBA_SCALE
from portfolio_intraday import START_FOTOVOLTAICE_SCALE


def forecast(times, values=0.25):
    times = pd.DatetimeIndex(times)
    return pd.DataFrame({"Data": times, "Interval": times.hour * 4 + times.minute // 15 + 1,
                         "Prediction": values})


class PortfolioExportTests(unittest.TestCase):
    def setUp(self):
        self.times = pd.date_range('2026-09-28 12:15', '2026-09-29 14:00', freq='15min')
        self.frames = {}
        for suffix, directory, filename, _ in ASSET_FILES:
            path = Path(directory) / f'Results_Production_{filename}_xgb_15min.xlsx'
            self.frames[str(path.resolve())] = forecast(self.times)

    def read_excel(self, path, *args, **kwargs):
        return self.frames[str(Path(path).resolve())].copy()

    def build(self, flags=None, weather=None):
        with (patch('pandas.read_excel', side_effect=self.read_excel),
              patch('pathlib.Path.is_file', return_value=True),
              patch('pandas.read_csv', return_value=weather)):
            return build_quarter_hourly_portfolio(flags or {})

    def test_shifted_and_shuffled_files_align_for_entire_horizon(self):
        path = str(Path('AnaSun/Results_Production_AnaSun_xgb_15min.xlsx').resolve())
        frame = forecast(self.times[1:-1], np.arange(len(self.times) - 2) / 100)
        self.frames[path] = frame.sample(frac=1, random_state=7)
        result = self.build()
        self.assertEqual(result['Data'].iloc[0], self.times[1])
        self.assertEqual(result['Data'].iloc[-1], self.times[-2])
        np.testing.assert_array_equal(result['Prediction_AnaSun'], frame['Prediction'])
        self.assertFalse(result.isna().any().any())
        self.assertEqual(result['Lookup'].iloc[0], '28.09.202651')

    def test_all_14_active_corrections_reach_both_excel_downloads(self):
        self.set_horizon(pd.date_range('2026-10-05 12:15', '2026-10-06 14:00', freq='15min'))
        self.assertEqual(len(CORRECTIONS), 14)
        self.assertNotIn('motif', CORRECTIONS)
        corrected_times = self.times[(self.times >= '2026-10-05 12:45') & (self.times < '2026-10-06')]
        for i, (_, path) in enumerate(CORRECTIONS.values()):
            self.frames[str(path.resolve())] = pd.DataFrame({
                'Data': corrected_times, 'Prediction_ID': 0.01 * (i + 1),
            })
        result = self.build(dict.fromkeys(CORRECTIONS, True))
        hourly = aggregate_hourly_portfolio(result)
        hour = hourly[(hourly.Data == pd.Timestamp('2026-10-05')) & (hourly.Interval == 14)].iloc[0]
        for i, (suffix, _) in enumerate(CORRECTIONS.values()):
            column = f'Prediction_{suffix}'
            selected = result[result.Data.isin(corrected_times)][column]
            np.testing.assert_allclose(selected, 0.01 * (i + 1))
            self.assertAlmostEqual(hour[column], 0.04 * (i + 1))
        for frame in (result, hourly):
            buffer = io.BytesIO()
            frame.to_excel(buffer, index=False)
            buffer.seek(0)
            reread = pd.read_excel(buffer)
            self.assertFalse(reread.filter(like='Prediction_').isna().any().any())
            np.testing.assert_allclose(reread.filter(like='Prediction_'), frame.filter(like='Prediction_'))

    def test_disabled_correction_uses_own_timestamp_matched_baseline(self):
        _, path = CORRECTIONS['anasun']
        self.frames[str(path.resolve())] = pd.DataFrame({'Data': self.times, 'Prediction_ID': 1.8})
        baseline_path = str(Path('AnaSun/Results_Production_AnaSun_xgb_15min.xlsx').resolve())
        self.frames[baseline_path] = forecast(self.times[1:], 0.3)
        result = self.build({'anasun': False})
        self.assertTrue(result['Prediction_AnaSun'].eq(0.3).all())
        self.assertEqual(result['Data'].iloc[0], self.times[1])

    def test_derived_assets_use_uncorrected_parent_baseline(self):
        _, path = CORRECTIONS['ulmeni']
        self.frames[str(path.resolve())] = pd.DataFrame({'Data': self.times, 'Prediction_ID': 0.8})
        result = self.build({'ulmeni': True})
        np.testing.assert_allclose(result['Prediction_SolEn_Ulmeni'], 0.8)
        np.testing.assert_allclose(result['Prediction_Start_Fotovoltaice'], 0.25 * START_FOTOVOLTAICE_SCALE)
        np.testing.assert_allclose(result['Prediction_Incuba'], 0.25 * ADREM_TO_INCUBA_SCALE)

    def test_only_weather_confirmed_dark_gaps_are_filled(self):
        path = str(Path('Astro/Results_Production_Astro_xgb_15min.xlsx').resolve())
        missing = pd.Timestamp('2026-09-29 03:00')
        self.frames[path] = self.frames[path][self.frames[path].Data != missing]
        weather = pd.DataFrame({'period_end': [missing.tz_localize('Europe/Bucharest').tz_convert('UTC')], 'ghi': [0]})
        result = self.build(weather=weather)
        self.assertEqual(result.loc[result.Data == missing, 'Prediction_Astro'].item(), 0)
        self.assertFalse(result.isna().any().any())
        with self.assertRaisesRegex(ValueError, 'Astro is missing forecast production'):
            self.build(weather=weather.assign(ghi=10))
        with self.assertRaisesRegex(ValueError, 'Astro is missing forecast production'):
            self.build(weather=weather.iloc[:0])

    def test_duplicate_base_forecasts_are_rejected(self):
        path = str(Path('Astro/Results_Production_Astro_xgb_15min.xlsx').resolve())
        self.frames[path] = pd.concat([self.frames[path], self.frames[path].iloc[:1]])
        with self.assertRaisesRegex(ValueError, 'duplicate forecast timestamps'):
            self.build()

    def test_dark_gap_accepts_mixed_solcast_date_formats(self):
        path = str(Path('Astro/Results_Production_Astro_xgb_15min.xlsx').resolve())
        missing = pd.Timestamp('2026-09-29 03:00')
        self.frames[path] = self.frames[path][self.frames[path].Data != missing]
        weather = pd.DataFrame({'period_end': ['2026-09-28T23:45:00Z', '2026-09-29'], 'ghi': [0, 0]})
        result = self.build(weather=weather)
        self.assertEqual(result.loc[result.Data == missing, 'Prediction_Astro'].item(), 0)

    def test_disjoint_forecast_dates_fail_clearly(self):
        path = str(Path('Astro/Results_Production_Astro_xgb_15min.xlsx').resolve())
        self.frames[path] = forecast(self.times + pd.Timedelta(days=30))
        with self.assertRaisesRegex(ValueError, 'no common delivery horizon'):
            self.build()

    def test_hourly_does_not_treat_partial_hours_as_complete(self):
        frame = pd.DataFrame({'Data': pd.date_range('2026-09-28 12:30', periods=7, freq='15min'),
                              'Prediction_Astro': [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]})
        result = aggregate_hourly_portfolio(frame)
        self.assertEqual(result.Interval.tolist(), [14])
        self.assertAlmostEqual(result.Prediction_Astro.item(), 1.8)
        self.assertEqual(result.Lookup.item(), '28.09.202614')

    def set_horizon(self, times):
        self.times = pd.DatetimeIndex(times)
        for path in self.frames:
            self.frames[path] = forecast(self.times)

    def test_motif_is_excluded_even_from_september_delivery_dates(self):
        result = self.build()
        self.assertNotIn('Prediction_Motif', result)
        self.assertNotIn('Prediction_Motif', aggregate_hourly_portfolio(result))
        self.assertNotIn('Prediction_Renewable_Energy_Holding', result)
        self.assertEqual(len(result.filter(like='Prediction_').columns), 23)

    def test_stale_motif_forecasts_do_not_affect_portfolio(self):
        path = str(Path('Motif/Results_Production_Motif_xgb_15min.xlsx').resolve())
        self.frames[path] = forecast(self.times[1:-1], 0.3)
        result = self.build({'motif': True})
        self.assertEqual(result.Data.tolist(), self.times.tolist())
        self.assertNotIn('Prediction_Motif', result)

    def test_october_uses_elnet_proxy_without_reading_motif_or_new_asset_files(self):
        self.set_horizon(pd.date_range('2026-10-01 10:00', periods=8, freq='15min'))
        elnet_path = str(Path('Elnet/Results_Production_Elnet_xgb_15min.xlsx').resolve())
        self.frames[elnet_path] = forecast(self.times, np.arange(8) * 0.05).iloc[::-1]
        result = self.build({'motif': True})
        column = 'Prediction_Renewable_Energy_Holding'
        self.assertNotIn('Prediction_Motif', result)
        self.assertEqual(len(result.filter(like='Prediction_').columns), 24)
        np.testing.assert_allclose(result[column], np.arange(8) * 0.05 * (2.37 / 2.7))
        hourly = aggregate_hourly_portfolio(result)
        np.testing.assert_allclose(hourly[column], [0.3 * (2.37 / 2.7), 1.1 * (2.37 / 2.7)])
        self.assertFalse(result.isna().any().any())
        for frame in (result, hourly):
            buffer = io.BytesIO()
            frame.to_excel(buffer, index=False)
            buffer.seek(0)
            reread = pd.read_excel(buffer)
            self.assertNotIn('Prediction_Motif', reread)
            np.testing.assert_allclose(reread[column], frame[column])

    def test_crossing_horizon_switches_at_bucharest_midnight(self):
        self.set_horizon(pd.date_range('2026-09-30 23:00', periods=8, freq='15min'))
        correction_path = Path('Motif/Results_Production_Motif_DAM_Corrected_Intraday_15min.xlsx')
        self.frames[str(correction_path.resolve())] = pd.DataFrame({
            'Data': self.times, 'Prediction_ID': 0.4,
        })
        result = self.build({'motif': True})
        self.assertEqual(result.Data.tolist(), self.times.tolist())
        self.assertNotIn('Prediction_Motif', result)
        np.testing.assert_allclose(result.Prediction_Renewable_Energy_Holding,
                                   [0] * 4 + [0.25 * RENEWABLE_ENERGY_HOLDING_SCALE] * 4)
        hourly = aggregate_hourly_portfolio(result)
        self.assertNotIn('Prediction_Motif', hourly)
        np.testing.assert_allclose(hourly.Prediction_Renewable_Energy_Holding,
                                   [0, RENEWABLE_ENERGY_HOLDING_SCALE])

    def test_renewable_does_not_inherit_elnet_live_correction(self):
        self.set_horizon(pd.date_range('2026-10-01 10:00', periods=4, freq='15min'))
        _, path = CORRECTIONS['elnet']
        self.frames[str(path.resolve())] = pd.DataFrame({'Data': self.times, 'Prediction_ID': 0.6})
        result = self.build({'elnet': True})
        np.testing.assert_allclose(result.Prediction_Elnet, 0.6)
        np.testing.assert_allclose(result.Prediction_Renewable_Energy_Holding,
                                   0.25 * RENEWABLE_ENERGY_HOLDING_SCALE)

    def test_renewable_own_correction_overrides_proxy_without_changing_elnet(self):
        self.set_horizon(pd.date_range('2026-10-05 10:00', periods=4, freq='15min'))
        _, path = CORRECTIONS['elnet']
        self.frames[str(path.resolve())] = pd.DataFrame({'Data': self.times, 'Prediction_ID': 0.6})
        _, path = CORRECTIONS['renewable_energy_holding']
        self.frames[str(path.resolve())] = pd.DataFrame({'Data': self.times, 'Prediction_ID': [0.1, 0.2, 0.3, 0.4]})
        flags = {'elnet': True, 'renewable_energy_holding': True}
        result = self.build(flags)
        np.testing.assert_allclose(result.Prediction_Elnet, 0.6)
        np.testing.assert_allclose(result.Prediction_Renewable_Energy_Holding, [0.1, 0.2, 0.3, 0.4])
        self.assertAlmostEqual(aggregate_hourly_portfolio(result).Prediction_Renewable_Energy_Holding.item(), 1.0)
        pd.testing.assert_frame_equal(result, self.build(flags))
        fallback = self.build({'elnet': True, 'renewable_energy_holding': False})
        np.testing.assert_allclose(fallback.Prediction_Renewable_Energy_Holding, 0.25 * RENEWABLE_ENERGY_HOLDING_SCALE)

    def test_renewable_correction_cannot_activate_pre_october_dates(self):
        self.set_horizon(pd.date_range('2026-09-30 23:00', periods=8, freq='15min'))
        _, path = CORRECTIONS['renewable_energy_holding']
        self.frames[str(path.resolve())] = pd.DataFrame({'Data': self.times, 'Prediction_ID': 0.1})
        result = self.build({'renewable_energy_holding': True})
        np.testing.assert_allclose(result.Prediction_Renewable_Energy_Holding, [0] * 4 + [0.1] * 4)

    def test_switch_uses_bucharest_not_utc_calendar_date(self):
        self.set_horizon(pd.date_range('2026-09-30 21:00Z', periods=4, freq='15min'))
        result = self.build()
        self.assertNotIn('Prediction_Motif', result)
        self.assertEqual(result.Data.iloc[0], pd.Timestamp('2026-10-01'))


if __name__ == '__main__':
    unittest.main()
