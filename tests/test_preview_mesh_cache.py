from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import generate  # noqa: E402
from workbench_backend import (WorkbenchBackend, calculation_key,
                               source_fingerprint,
                               verify_output_source_identity)  # noqa: E402
from _workbench_fixtures import default_config  # noqa: E402


class PreviewIdentityAndMeshCacheTests(unittest.TestCase):
    def test_preview_and_exact_keys_differ_and_old_case_defaults_to_exact(self):
        config = default_config(ROOT)
        fingerprint = "a" * 64
        self.assertNotEqual(calculation_key(config, fingerprint, "preview"),
                            calculation_key(config, fingerprint, "exact"))
        self.assertEqual(calculation_key(config, fingerprint),
                         calculation_key(config, fingerprint, "exact"))
        public = WorkbenchBackend._public_case({"id": "legacy", "validated": True})
        self.assertEqual(public["mode"], "exact")
        self.assertEqual(public["status"], "exact_ready")

    def test_geometry_source_identity_includes_both_mode_branches(self):
        source = (ROOT / "generate.py").read_text(encoding="utf-8")
        pipeline = generate._geometry_pipeline_source(source)
        self.assertIn("add_preview_contact_flyers", pipeline)
        self.assertIn("add_contact_flyers", pipeline)
        self.assertIn("add_preview_combination_blank", pipeline)
        self.assertIn("add_combination", pipeline)

    def test_current_source_identity_chain_is_required_for_new_registration(self):
        identity = generate.current_source_identity_record()
        self.assertEqual(identity["production"]["sha256"], source_fingerprint(ROOT))
        documents = [{"source_identity": identity} for _ in range(4)]
        verified = verify_output_source_identity(
            documents, identity["production"]["sha256"])
        self.assertEqual(verified, identity)
        with self.assertRaisesRegex(ValueError, "production source identity"):
            verify_output_source_identity(documents, "0" * 64)
        with self.assertRaisesRegex(ValueError, "文件清单或摘要无效"):
            verify_output_source_identity(
                [{"source_identity": {"production": identity["production"],
                                       "geometry": {"sha256": "0" * 64, "files": []}}}],
                identity["production"]["sha256"])

    def test_deflection_preflight_rejects_before_node_or_triangle_extraction(self):
        class FakeTriangulation:
            def NbNodes(self):
                return 7

            def NbTriangles(self):
                return 6

            def Deflection(self):
                return 0.5

            def Node(self, _index):
                raise AssertionError("Node() must not be called after a known deflection failure")

            def Triangles(self):
                raise AssertionError("Triangles() must not be called after a known deflection failure")

        class FakeFace:
            wrapped = object()

            def Edges(self):
                return []

        class FakeShape:
            wrapped = object()

            def Faces(self):
                return [FakeFace()]

        class FakeMesher:
            def __init__(self, *_args):
                pass

            def IsDone(self):
                return True

            def GetStatusFlags(self):
                return 0

        brep_module = types.ModuleType("OCP.BRep")
        brep_module.BRep_Tool = types.SimpleNamespace(
            Triangulation_s=lambda *_args: FakeTriangulation())
        mesh_module = types.ModuleType("OCP.BRepMesh")
        mesh_module.BRepMesh_IncrementalMesh = FakeMesher
        tools_module = types.ModuleType("OCP.BRepTools")
        tools_module.BRepTools = types.SimpleNamespace(
            Clean_s=lambda *_args: None,
            Triangulation_s=lambda *_args: False)
        topabs_module = types.ModuleType("OCP.TopAbs")
        topabs_module.TopAbs_Orientation = types.SimpleNamespace(TopAbs_REVERSED=object())
        toploc_module = types.ModuleType("OCP.TopLoc")
        toploc_module.TopLoc_Location = type("FakeLocation", (), {})
        fake_modules = {"OCP.BRep": brep_module, "OCP.BRepMesh": mesh_module,
                        "OCP.BRepTools": tools_module, "OCP.TopAbs": topabs_module,
                        "OCP.TopLoc": toploc_module}
        with mock.patch.dict(sys.modules, fake_modules):
            vertices, triangles, report, accepted = generate.absolute_face_mesh(
                FakeShape(), 1.0, 0.35, requested_upper_limit=0.1)
        self.assertFalse(accepted)
        self.assertEqual(vertices, [])
        self.assertEqual(triangles, [])
        self.assertFalse(report["mesh_extraction_performed"])
        self.assertFalse(report["triangle_index_validation_performed"])
        self.assertEqual(report["face_node_count_total"], 7)
        self.assertEqual(report["face_triangle_count_total"], 6)
        self.assertIn("one_or_more_faces_exceed_requested_linear_deflection",
                      report["rejection_reasons"])

    def test_corrupt_cache_falls_back_and_failed_mesh_is_never_cached(self):
        import cadquery as cq

        config = {"case_id": "mesh_cache_probe", "corner_angle_deg": 90.0,
                  "advanced": {"mesh_tessellation_mm": 1.0,
                                "mesh_angular_tolerance_rad": 0.35}}
        def make_part():
            return {"id": "box", "name": "Box", "group": "test",
                    "shape": cq.Solid.makeBox(10.0, 10.0, 10.0)}

        with tempfile.TemporaryDirectory(prefix="preview-mesh-cache-test-") as temp:
            root = Path(temp)
            cache_dir = root / "cache"
            out_a, out_b = root / "first", root / "second"
            out_a.mkdir()
            out_b.mkdir()
            mesh_a, _ = generate.export_mesh([make_part()], [], config, out_a,
                                              mode="preview", mesh_cache_dir=cache_dir)
            self.assertGreater(sum(len(p["triangles"]) // 3 for p in mesh_a["parts"]), 0)
            entries = list(cache_dir.glob("*.json.gz"))
            self.assertEqual(len(entries), 1)

            out_warm = root / "warm-exact"
            out_warm.mkdir()
            mesh_warm, _ = generate.export_mesh([make_part()], [], config, out_warm,
                                                 mode="exact", mesh_cache_dir=cache_dir)
            self.assertGreater(sum(len(p["triangles"]) // 3 for p in mesh_warm["parts"]), 0)
            diag_warm = json.loads((out_warm / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diag_warm["summary"]["mesh_cache_hit_attempt_count"], 1)

            entries[0].write_bytes(b"corrupt gzip cache")

            mesh_b, _ = generate.export_mesh([make_part()], [], config, out_b,
                                              mode="exact", mesh_cache_dir=cache_dir)
            self.assertGreater(sum(len(p["triangles"]) // 3 for p in mesh_b["parts"]), 0)
            diag_b = json.loads((out_b / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diag_b["summary"]["mesh_cache_invalid_entry_count"], 1)
            self.assertEqual(diag_b["summary"]["mesh_cache_written_attempt_count"], 1)

            out_failed = root / "failed"
            out_failed.mkdir()
            rejected = {"mesh_ready": False, "mesh_extraction_performed": False,
                        "triangle_index_validation_performed": False,
                        "rejection_reasons": ["test_rejection"], "faces": []}
            with mock.patch.object(generate, "absolute_face_mesh",
                                   return_value=([], [], rejected, False)):
                with self.assertRaises(ValueError):
                    generate.export_mesh([make_part()], [], config, out_failed,
                                         mode="preview", mesh_cache_dir=root / "failed-cache")
            self.assertEqual(list((root / "failed-cache").glob("*.json.gz")), [])
            failed_diag = json.loads((out_failed / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(len(failed_diag["parts"][0]["attempts"]), 6)
            self.assertTrue(all(attempt["rejection_reasons"] == ["test_rejection"]
                                for attempt in failed_diag["parts"][0]["attempts"]))

    def test_cached_fine_mesh_skips_known_coarser_retry_targets(self):
        import cadquery as cq

        config = {"case_id": "mesh_cache_fine_start_probe", "corner_angle_deg": 90.0,
                  "advanced": {"mesh_tessellation_mm": 2.0,
                                "mesh_angular_tolerance_rad": 0.35}}
        def make_part():
            return {"id": "fine_box", "name": "Fine box", "group": "test",
                    "shape": cq.Solid.makeBox(10.0, 10.0, 10.0)}
        with tempfile.TemporaryDirectory(prefix="mesh-cache-fine-start-") as temp:
            root = Path(temp)
            cache_dir = root / "cache"
            first_out, second_out = root / "first", root / "second"
            first_out.mkdir()
            second_out.mkdir()
            real_absolute_face_mesh = generate.absolute_face_mesh
            called_targets = []

            def reject_until_fine(shape, target, angular, **kwargs):
                called_targets.append(float(target))
                if float(target) > 0.125:
                    report = {"requested_linear_deflection_mm": float(target),
                              "requested_linear_deflection_upper_limit_mm": 2.0,
                              "mesher_linear_deflection_target_mm": float(target),
                              "angular_tolerance_rad": float(angular), "mesh_ready": False,
                              "mesh_extraction_performed": False,
                              "triangle_index_validation_performed": False,
                              "rejection_reasons": ["synthetic_coarser_target_reject"],
                              "faces": []}
                    return [], [], report, False
                return real_absolute_face_mesh(shape, target, angular, **kwargs)

            with mock.patch.object(generate, "absolute_face_mesh", side_effect=reject_until_fine):
                mesh_first, _ = generate.export_mesh([make_part()], [], config, first_out,
                                                      mode="preview", mesh_cache_dir=cache_dir)
            self.assertEqual(called_targets, [2.0, 1.0, 0.5, 0.25, 0.125])
            self.assertGreater(sum(len(item["triangles"]) // 3
                                   for item in mesh_first["parts"]), 0)

            called_targets.clear()
            with mock.patch.object(generate, "absolute_face_mesh",
                                   wraps=real_absolute_face_mesh) as remesh:
                mesh_second, _ = generate.export_mesh([make_part()], [], config, second_out,
                                                       mode="preview", mesh_cache_dir=cache_dir)
            diagnostic = json.loads((second_out / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(remesh.call_count, 0,
                             json.dumps(diagnostic["parts"][0], ensure_ascii=False))
            self.assertEqual(called_targets, [])
            self.assertEqual(mesh_first["parts"][0]["triangles"],
                             mesh_second["parts"][0]["triangles"])
            part_report = diagnostic["parts"][0]
            self.assertEqual(part_report["mesh_cache_start_tolerance_mm"], 0.125)
            self.assertEqual(part_report["skipped_coarser_retry_tolerances_mm"],
                             [2.0, 1.0, 0.5, 0.25])
            self.assertEqual(len(part_report["attempts"]), 1)
            self.assertTrue(part_report["attempts"][0]["mesh_cache_hit"])
            self.assertEqual(diagnostic["summary"]["mesh_cache_hit_attempt_count"], 1)
            self.assertEqual(diagnostic["summary"]["mesh_cache_miss_attempt_count"], 0)
            self.assertEqual(diagnostic["summary"]["skipped_coarser_retry_attempt_count"], 4)


if __name__ == "__main__":
    unittest.main()
