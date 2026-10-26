import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_case(name):
    folder = ROOT / "outputs" / name
    required = ("model_mesh.json", "model.obj", "profiles.json", "layout.json",
                "parameters_resolved.json", "validation.json", "manifest.json")
    missing = [filename for filename in required if not (folder / filename).is_file()]
    if missing:
        raise FileNotFoundError(f"{name} is incomplete; missing: {', '.join(missing)}")
    return folder, {filename: json.loads((folder / filename).read_text(encoding="utf-8"))
                    for filename in ("model_mesh.json", "profiles.json", "layout.json",
                                     "parameters_resolved.json", "validation.json", "manifest.json")}


class GeneratedDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default_dir, cls.default = load_case("default")
        cls.variant_dir, cls.variant = load_case("size_variant_valid64")

    def test_artifacts_stay_in_new_outputs_tree(self):
        root = (ROOT / "outputs").resolve()
        for folder in (self.default_dir, self.variant_dir):
            self.assertTrue(folder.resolve().is_relative_to(root))
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("model.step", {entry["path"] for entry in manifest["files"]})

    def test_model_layout_ids_and_positive_piece_areas_match(self):
        for case in (self.default, self.variant):
            mesh_ids = {part["id"] for part in case["model_mesh.json"]["parts"]}
            layout = case["layout.json"]["layout"]
            profiles = case["profiles.json"]["profiles"]
            self.assertEqual(len(layout["pieces"]), len(profiles))
            self.assertTrue(all(math.isfinite(piece["area_mm2"]) and piece["area_mm2"] > 0
                                for piece in layout["pieces"]))
            self.assertTrue(all(len(piece["model_ids"]) == 2 and set(piece["model_ids"]) <= mesh_ids
                                for piece in layout["pieces"]))
            self.assertTrue(case["validation.json"]["layout"]["model_ids_match_dynamic_flyer_ids"])
            self.assertTrue(case["validation.json"]["layout"]["fan_count_matches_spacing_rule"])
            self.assertEqual(case["validation.json"]["layout"]["fan_count_actual_per_side"],
                             case["parameters_resolved.json"]["derived"]["fan_count"])
            self.assertTrue(case["validation.json"]["layout"]["board_thickness_matches_w_fly"])
            self.assertAlmostEqual(layout["board_thickness_mm"],
                                   case["parameters_resolved.json"]["parameters"]["w_fly"])
            self.assertTrue(case["validation.json"]["passed"])

    def test_variant_changes_dimensions_material_and_dynamic_fan_count(self):
        default_cfg = self.default["parameters_resolved.json"]["parameters"]
        variant_cfg = self.variant["parameters_resolved.json"]["parameters"]
        self.assertNotEqual(default_cfg["D"], variant_cfg["D"])
        self.assertNotEqual(default_cfg["w_fly"], variant_cfg["w_fly"])
        self.assertNotEqual(default_cfg["B"], variant_cfg["B"])
        default_n = self.default["validation.json"]["fan_layout"]["fan_rafter_count"]
        variant_n = self.variant["validation.json"]["fan_layout"]["fan_rafter_count"]
        self.assertNotEqual(default_n, variant_n)
        self.assertNotEqual(self.default["layout.json"]["layout"]["board_width_x_mm"],
                            self.variant["layout.json"]["layout"]["board_width_x_mm"])

    def test_recovery_rejects_a_different_config_for_existing_mesh(self):
        sys.path.insert(0, str(ROOT))
        from generate import finalize_existing_case
        with self.assertRaisesRegex(ValueError, "参数或advanced不完全一致"):
            finalize_existing_case(ROOT / "configs" / "size_variant.json",
                                   ROOT / "outputs" / "default", make_viewer=False)

    def test_cli_finalize_returns_nonzero_for_known_missing_fan(self):
        output = ROOT / "outputs" / "size_variant"
        stale_step = output / "model.step"
        stale_step.write_text("stale unrequested STEP", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "generate.py"),
             "--config", str(ROOT / "configs" / "size_variant.json"),
             "--output", str(output),
             "--finalize-existing", "--no-render"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(stale_step.exists())
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("model.step", {entry["path"] for entry in manifest["files"]})
        failed = json.loads((output / "validation.json").read_text(encoding="utf-8"))
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["layout"]["fan_count_expected_per_side"], 15)
        self.assertEqual(failed["layout"]["fan_count_actual_per_side"], 14)

    def test_normal_cli_returns_nonzero_for_pose_preflight_failure(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "generate.py"),
             "--config", str(ROOT / "configs" / "size_variant.json"),
             "--output", str(ROOT / "outputs" / "size_variant"), "--no-render"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("预检不完整，拒绝静默漏根", result.stderr)
        self.assertIn("index=15", result.stderr)


if __name__ == "__main__":
    unittest.main()
