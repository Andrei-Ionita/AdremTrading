import unittest

from streamlit.testing.v1 import AppTest


def dashboard_app(failure_stage=None):
    from contextlib import ExitStack
    from unittest.mock import mock_open, patch

    import pandas as pd
    import streamlit as st
    import balancing
    from portfolio_export import CORRECTIONS

    frame = pd.DataFrame({'Data': [pd.Timestamp('2026-09-30 12:00')], 'Prediction': [0.1]})
    flags = dict.fromkeys(CORRECTIONS, True)

    def export_quarters(**kwargs):
        st.session_state['exports'] = st.session_state.get('exports', 0) + 1
        if failure_stage == 'export':
            raise ValueError('Test export failure')
        return frame

    with ExitStack() as stack:
        # Exercise the real page without portals, model inference, or file writes.
        for name in vars(balancing):
            if name.startswith('render_indisponibility_db_'):
                stack.enter_context(patch.object(balancing, name, return_value=(None, None, None)))
            elif name.startswith('fetching_'):
                stack.enter_context(patch.object(balancing, name, return_value=None))
            elif name.startswith('predicting_exporting_'):
                stack.enter_context(patch.object(balancing, name, return_value=frame))
        if failure_stage == 'forecast':
            stack.enter_context(patch.object(balancing, 'fetching_Astro_data',
                                             side_effect=ValueError('Test forecast failure')))
        stack.enter_context(patch.object(balancing, 'fetching_Elnet_data',
                                         side_effect=AssertionError('Legacy hourly weather must not be fetched')))
        stack.enter_context(patch.object(balancing, 'predicting_exporting_Elnet',
                                         side_effect=AssertionError('Removed Elnet hourly model must not be loaded')))
        stack.enter_context(patch.object(balancing, 'refresh_intraday_corrections', return_value=(flags, {}, {})))
        stack.enter_context(patch.object(balancing, 'create_excel_file_with_all_forecasts_15min', side_effect=export_quarters))
        stack.enter_context(patch.object(balancing, 'create_excel_file_with_all_forecasts', return_value=frame))
        stack.enter_context(patch.object(balancing, 'open', mock_open(read_data=b'workbook'), create=True))
        stack.enter_context(patch.object(balancing, 'get_issue_date', side_effect=st.stop))
        balancing.render_balancing_market_intraday_page()


class DashboardControlsTests(unittest.TestCase):
    def assert_controls_visible(self, app):
        self.assertEqual([button.label for button in app.button], [
            'Forecast Portfolio', 'Create Excel File with all the forecasts',
        ])
        self.assertEqual(app.columns[0].button[0].label, 'Forecast Portfolio')
        self.assertEqual(app.columns[1].button[0].label, 'Create Excel File with all the forecasts')

    def test_controls_remain_visible_after_forecast_finishes(self):
        app = AppTest.from_function(dashboard_app, default_timeout=30).run()
        self.assert_controls_visible(app)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['exports'], 1)
        self.assert_controls_visible(app)

    def test_controls_remain_visible_if_forecast_or_automatic_export_fails(self):
        for stage in ('forecast', 'export'):
            with self.subTest(stage=stage):
                app = AppTest.from_function(dashboard_app, kwargs={'failure_stage': stage}, default_timeout=30).run()
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 1)
                self.assertEqual(app.exception[0].message, f'Test {stage} failure')
                self.assert_controls_visible(app)

    def test_excel_action_still_creates_both_downloads(self):
        app = AppTest.from_function(dashboard_app, default_timeout=30).run()
        app.button[1].click().run()
        self.assertFalse(app.exception)
        self.assert_controls_visible(app)
        self.assertEqual(app.session_state['exports'], 1)
        links = '\n'.join(item.value for item in app.markdown)
        self.assertIn('download="Forecast.xlsx"', links)
        self.assertIn('download="Forecast_15min.xlsx"', links)


if __name__ == '__main__':
    unittest.main()
