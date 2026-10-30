import json
import hashlib
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate  # noqa: E402


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


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def stamp_synthetic_current_source(output):
    """Give an isolated fixture a self-consistent v0.4 provenance chain."""
    identity = generate.current_source_identity_record()
    resolved_path = output / "parameters_resolved.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["mode"] = "exact"
    resolved["source_identity"] = identity
    write_json(resolved_path, resolved)

    mesh_path = output / "model_mesh.json"
    mesh = json.loads(mesh_path.read_text(encoding="utf-8"))
    mesh.setdefault("metadata", {})["mode"] = "exact"
    mesh["metadata"]["source_identity"] = identity
    write_json(mesh_path, mesh)

    validation_path = output / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["mode"] = "exact"
    validation["source_identity"] = identity
    write_json(validation_path, validation)
    write_json(output / "manifest.json",
               generate.build_manifest(output, resolved["case_id"], mode="exact",
                                       source_identity=identity))
    return identity


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
        from generate import finalize_existing_case
        with tempfile.TemporaryDirectory(prefix="finalize-mismatch-", dir=ROOT / "outputs") as temp:
            copied = Path(temp) / "default"
            shutil.copytree(ROOT / "outputs" / "default", copied)
            stamp_synthetic_current_source(copied)
            different_config = json.loads((ROOT / "configs" / "default.json").read_text(encoding="utf-8"))
            different_config["parameters"]["D"] = float(different_config["parameters"]["D"]) + 1.0
            different_config_path = Path(temp) / "same_case_different_parameters.json"
            write_json(different_config_path, different_config)
            with self.assertRaisesRegex(ValueError, "参数或advanced不完全一致"):
                finalize_existing_case(different_config_path,
                                       copied, make_viewer=False)

    def test_finalize_rejects_legacy_output_without_source_before_any_write(self):
        from generate import finalize_existing_case
        source = ROOT / "outputs" / "size_variant_valid64"
        with tempfile.TemporaryDirectory(prefix="finalize-legacy-source-", dir=ROOT / "outputs") as temp:
            output = Path(temp) / "legacy_case"
            shutil.copytree(source, output)
            stale_step = output / "model.step"
            stale_step.write_text("stale unrequested STEP", encoding="utf-8")
            guarded = ("parameters_resolved.json", "validation.json", "manifest.json", "model.step")
            before = {name: (output / name).read_bytes() for name in guarded}
            with self.assertRaisesRegex(ValueError, "source identity"):
                finalize_existing_case(ROOT / "configs" / "size_variant_valid64.json",
                                       output, make_viewer=False)
            self.assertEqual({name: (output / name).read_bytes() for name in guarded}, before)

    def test_finalize_rejects_a_missing_flyer_in_temporary_output(self):
        from generate import finalize_existing_case
        source = ROOT / "outputs" / "size_variant_valid64"
        with tempfile.TemporaryDirectory(prefix="finalize-missing-flyer-", dir=ROOT / "outputs") as temp:
            output = Path(temp) / "size_variant_valid64"
            shutil.copytree(source, output)
            stamp_synthetic_current_source(output)
            mesh_path = output / "model_mesh.json"
            profiles_path = output / "profiles.json"
            layout_path = output / "layout.json"
            mesh = json.loads(mesh_path.read_text(encoding="utf-8"))
            profile_doc = json.loads(profiles_path.read_text(encoding="utf-8"))
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            victims = profile_doc["profiles"][-2:]
            del profile_doc["profiles"][-2:]
            removed_profile_ids = {profile["id"] for profile in victims}
            removed_ids = {part_id for profile in victims for part_id in profile["model_ids"]}
            mesh["parts"] = [part for part in mesh["parts"] if part["id"] not in removed_ids]
            layout["layout"]["pieces"] = [piece for piece in layout["layout"]["pieces"]
                                             if piece["id"] not in removed_profile_ids]
            layout["input"]["profile_count"] = len(profile_doc["profiles"])
            write_json(mesh_path, mesh)
            profile_doc["source_mesh_sha256"] = hashlib.sha256(mesh_path.read_bytes()).hexdigest()
            write_json(profiles_path, profile_doc)
            write_json(layout_path, layout)
            # Regenerate the fixture manifest only; all writes stay in this temp copy.
            write_json(output / "manifest.json",
                       generate.build_manifest(output, "size_variant_valid64", mode="exact",
                                               source_identity=mesh["metadata"]["source_identity"]))
            guarded_files = ("parameters_resolved.json", "validation.json", "manifest.json")
            before = {name: (output / name).read_bytes() for name in guarded_files}

            with self.assertRaisesRegex(ValueError, "恢复输出的立脚模板数"):
                finalize_existing_case(ROOT / "configs" / "size_variant_valid64.json",
                                       output, make_viewer=False)
            self.assertEqual({name: (output / name).read_bytes() for name in guarded_files}, before)

    def test_finalize_rejects_preview_mode_before_writing(self):
        from generate import finalize_existing_case
        with tempfile.TemporaryDirectory(prefix="finalize-mode-", dir=ROOT / "outputs") as temp:
            output = Path(temp) / "mode_mismatch"
            shutil.copytree(ROOT / "outputs" / "size_variant_valid64", output)
            resolved_path = output / "parameters_resolved.json"
            resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
            resolved["mode"] = "preview"
            write_json(resolved_path, resolved)
            guarded = ("parameters_resolved.json", "validation.json", "manifest.json")
            before = {name: (output / name).read_bytes() for name in guarded}
            with self.assertRaisesRegex(ValueError, "不是同一exact模式"):
                finalize_existing_case(ROOT / "configs" / "size_variant_valid64.json",
                                       output, make_viewer=False)
            self.assertEqual({name: (output / name).read_bytes() for name in guarded}, before)

    def test_finalize_rejects_mismatched_saved_source_before_writing(self):
        from generate import finalize_existing_case
        with tempfile.TemporaryDirectory(prefix="finalize-source-mismatch-", dir=ROOT / "outputs") as temp:
            output = Path(temp) / "source_mismatch"
            shutil.copytree(ROOT / "outputs" / "size_variant_valid64", output)
            identity = deepcopy(stamp_synthetic_current_source(output))
            production = identity["production"]
            production["files"][0]["sha256"] = "f" * 64
            production["sha256"] = generate._identity_digest(production["files"])
            for filename, getter, setter in (
                    ("parameters_resolved.json", lambda d: d, lambda d, value: d.__setitem__("source_identity", value)),
                    ("model_mesh.json", lambda d: d["metadata"], lambda d, value: d["metadata"].__setitem__("source_identity", value)),
                    ("validation.json", lambda d: d, lambda d, value: d.__setitem__("source_identity", value))):
                path = output / filename
                document = json.loads(path.read_text(encoding="utf-8"))
                setter(document, identity)
                write_json(path, document)
            write_json(output / "manifest.json",
                       generate.build_manifest(output, "size_variant_valid64", mode="exact",
                                               source_identity=identity))
            guarded = ("parameters_resolved.json", "validation.json", "manifest.json")
            before = {name: (output / name).read_bytes() for name in guarded}
            with self.assertRaisesRegex(ValueError, "production source"):
                finalize_existing_case(ROOT / "configs" / "size_variant_valid64.json",
                                       output, make_viewer=False)
            self.assertEqual({name: (output / name).read_bytes() for name in guarded}, before)

    def test_finalize_accepts_trusted_current_exact_copy_and_removes_stale_step(self):
        from generate import finalize_existing_case
        source = ROOT / "outputs" / "workbench_preview_implementation_20261030" / "exact_120_cachefix"
        with tempfile.TemporaryDirectory(prefix="finalize-current-exact-", dir=ROOT / "outputs") as temp:
            output = Path(temp) / "exact_120_cachefix"
            shutil.copytree(source, output)
            (output / "model.step").write_text("stale unrequested STEP", encoding="utf-8")

            result = finalize_existing_case(ROOT / "configs" / "angle_120.json",
                                            output, make_viewer=False)

            self.assertTrue(result["passed"])
            validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
            self.assertTrue(validation["passed"])
            self.assertEqual(validation["mode"], "exact")
            self.assertFalse((output / "model.step").exists())
            resolved = json.loads((output / "parameters_resolved.json").read_text(encoding="utf-8"))
            self.assertEqual(resolved["source_identity"], generate.current_source_identity_record())

    def test_stage05_missing_root_preflight_is_rejected_without_cad(self):
        stage05 = generate.load_stage05()
        fixture = {"pose_failures": [{"index": 15, "reason": "synthetic missing root"}],
                   "case": {"fan_geometry": {"fan_rafter_count": 14}},
                   "chosen": [{"family": "fan"} for _ in range(13)]}
        with mock.patch.object(stage05.s4, "build_base_and_preflight", return_value=fixture):
            with self.assertRaisesRegex(ValueError, "立脚飞椽预检不完整，拒绝静默漏根"):
                stage05.build_stage04_base({"case_id": "synthetic_missing_root"})


if __name__ == "__main__":
    unittest.main()
