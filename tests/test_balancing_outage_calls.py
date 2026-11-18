import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
OUTAGE_ASSETS = (
    "Astro", "Imperial", "Kahraman", "SunEnergy", "SolarEnergy", "Elnet",
    "Horeco", "Dragosel", "GESS", "NRG", "Sun_Grow_Lucia",
    "Photovoltaic_Energy_Project", "MM_MV", "Rosiori", "Necaluxan",
    "Adrem", "Anto", "Ferma", "HNG",
)
DEFAULT_ASSETS = ("AnaSun", "Start_Fotovoltaice", "GCSP")


class OutageModelCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / "balancing.py").read_text(encoding="utf-8"))
        page = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "render_balancing_market_intraday_page")
        forecast = next(node for node in ast.walk(page) if isinstance(node, ast.If)
                        and isinstance(node.test, ast.Name) and node.test.id == "forecast_requested")
        cls.calls = [node for node in ast.walk(forecast) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name)
                     and node.func.id.startswith("predicting_exporting_")
                     and node.func.id.endswith("_15min")]
        models = ast.parse((ROOT / "ml.py").read_text(encoding="utf-8"))
        cls.models = {node.name: node for node in models.body if isinstance(node, ast.FunctionDef)}

    def model_call(self, asset):
        name = f"predicting_exporting_{asset}_15min"
        calls = [call for call in self.calls if call.func.id == name]
        self.assertEqual(len(calls), 1, name)
        return calls[0]

    def test_all_outage_aware_models_receive_explicit_matching_keywords(self):
        self.assertCountEqual(
            [call.func.id for call in self.calls],
            [f"predicting_exporting_{asset}_15min" for asset in OUTAGE_ASSETS + DEFAULT_ASSETS],
        )
        expected = ("interval_from", "interval_to", "limitation_percentage")
        for asset in OUTAGE_ASSETS:
            with self.subTest(asset=asset):
                call = self.model_call(asset)
                self.assertEqual(call.args, [])
                self.assertCountEqual([kw.arg for kw in call.keywords], expected)
                for keyword in call.keywords:
                    self.assertIsInstance(keyword.value, ast.Name)
                    self.assertEqual(keyword.value.id, keyword.arg)

    def test_distinct_outage_endpoints_bind_to_actual_model_parameter_names(self):
        expected = {"interval_from": 7, "interval_to": 12, "limitation_percentage": 65}
        for asset in OUTAGE_ASSETS:
            with self.subTest(asset=asset):
                call = self.model_call(asset)
                model = Mock()
                # Evaluate only the call, never model inference, portals, or exports.
                eval(compile(ast.Expression(call), "balancing.py", "eval"),
                     {"__builtins__": {}, call.func.id: model, **expected})
                signature = inspect.Signature([
                    inspect.Parameter(arg.arg, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    for arg in self.models[call.func.id].args.args
                ])
                bound = signature.bind(*model.call_args.args, **model.call_args.kwargs)
                self.assertEqual(bound.arguments, expected)

    def test_fixed_default_models_remain_unlimited(self):
        for asset in DEFAULT_ASSETS:
            with self.subTest(asset=asset):
                call = self.model_call(asset)
                self.assertEqual([ast.literal_eval(arg) for arg in call.args], [1, 24, 0])
                self.assertEqual(call.keywords, [])


if __name__ == "__main__":
    unittest.main()
