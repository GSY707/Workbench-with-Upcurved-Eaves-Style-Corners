import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from config import ADVANCED_DEFAULTS, PARAMETER_DEFAULTS, load_config, resolve_config


class ConfigValidationTests(unittest.TestCase):
    def test_default_config_resolves_and_derives_angles(self):
        config = load_config(ROOT / "configs" / "default.json")
        self.assertEqual(config["corner_angle_deg"], 90.0)
        self.assertGreater(config["derived"]["old_young_control_included_angle_deg"], 0.0)
        self.assertEqual(config["derived"]["fly_tail_ratio"], None)

    def test_symmetric_convex_angles_resolve_and_invalid_limits_are_rejected(self):
        for angle in (60, 90, 120):
            with self.subTest(angle=angle):
                config = resolve_config({"corner_angle_deg": angle})
                self.assertEqual(config["corner_angle_deg"], float(angle))
                self.assertEqual(config["derived"]["corner_angle_deg"], float(angle))
                self.assertGreater(config["derived"]["old_young_control_included_angle_deg"], 0.0)
        for angle in (0, 180, -1, math.nan, math.inf, True):
            with self.subTest(angle=angle), self.assertRaisesRegex(ValueError, "必须满足0 < 角度 < 180°"):
                resolve_config({"corner_angle_deg": angle})

    def test_unknown_legacy_fields_are_rejected_with_migration_hint(self):
        with self.assertRaisesRegex(ValueError, "未知字段"):
            resolve_config({"corner_angle_deg": 90, "parameters": {"middle_contact_ratio": 0.7}})
        with self.assertRaisesRegex(ValueError, "back_join_fraction"):
            resolve_config({"corner_angle_deg": 90, "parameters": {"middle_contact_ratio": 0.7}})

    def test_chord_and_positive_dimension_constraints_are_specific(self):
        with self.assertRaisesRegex(ValueError, "0 < U_n < L_n"):
            resolve_config({"corner_angle_deg": 90, "parameters": {"U_n": 900}})
        with self.assertRaisesRegex(ValueError, "parameters.w_fly必须大于0"):
            resolve_config({"corner_angle_deg": 90, "parameters": {"w_fly": 0}})

    def test_merged_size_case_changes_geometry_and_fan_spacing_input(self):
        default = load_config(ROOT / "configs" / "default.json")
        variant = load_config(ROOT / "configs" / "size_variant_valid64.json")
        self.assertNotEqual(default["parameters"]["D"], variant["parameters"]["D"])
        self.assertNotEqual(default["parameters"]["w_fly"], variant["parameters"]["w_fly"])
        self.assertNotEqual(default["parameters"]["B"], variant["parameters"]["B"])

    def test_schema_describes_every_accepted_field_and_its_default(self):
        import json
        schema = json.loads((ROOT / "parameter_schema.json").read_text(encoding="utf-8"))
        angle = schema["properties"]["corner_angle_deg"]
        self.assertEqual(angle["type"], "number")
        self.assertEqual(angle["default"], 90)
        self.assertEqual(angle["exclusiveMinimum"], 0)
        self.assertEqual(angle["exclusiveMaximum"], 180)
        self.assertIn("檐桁射线之间的内角", angle["description"])
        self.assertNotIn("const", angle)
        for section, defaults in (("parameters", PARAMETER_DEFAULTS), ("advanced", ADVANCED_DEFAULTS)):
            properties = schema["properties"][section]["properties"]
            self.assertEqual(set(properties), set(defaults))
            for key, default in defaults.items():
                with self.subTest(section=section, key=key):
                    prop = properties[key]
                    self.assertEqual(prop["default"], default)
                    self.assertTrue(prop.get("description"))
                    self.assertTrue(prop.get("x-unit"))
                    if "minimum" in prop:
                        self.assertGreaterEqual(default, prop["minimum"])
                    if "exclusiveMinimum" in prop:
                        self.assertGreater(default, prop["exclusiveMinimum"])
                    if "maximum" in prop:
                        self.assertLessEqual(default, prop["maximum"])
                    if "exclusiveMaximum" in prop:
                        self.assertLess(default, prop["exclusiveMaximum"])


if __name__ == "__main__":
    unittest.main()
