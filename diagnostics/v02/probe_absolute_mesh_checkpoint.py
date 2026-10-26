from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [
    str(HERE),
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
]

import cadquery as cq
import generate


def main() -> None:
    config = {
        "case_id": "mesh_contract_probe",
        "corner_angle_deg": 90.0,
        "parameters": {"w_fly": 60.0},
        "advanced": {"mesh_tessellation_mm": 2.0,
                     "mesh_angular_tolerance_rad": 0.35},
        "derived": {},
    }
    shapes = [
        ("probe_box", "test", cq.Workplane("XY").box(100, 80, 30).edges("|Z").fillet(5).val()),
        ("probe_sphere", "test", cq.Workplane("XY").sphere(30).val()),
    ]
    parts = [{"id": part_id, "name": part_id, "group": group, "shape": shape}
             for part_id, group, shape in shapes]
    fly_records = [{
        "family": "fan", "index": 1, "source_rafter_id": "probe_fan_01",
        "tail_length_from_S_center_mm": 50.0, "l_SE_mm": 100.0, "width_mm": 60.0,
        "E_bottom_center_mm": 5.0, "E_depth_world_Z_mm": 10.0,
        "dI_world_Z_mm": 2.0, "q_R_center_mm": 12.0,
        "side_contours": {}, "dI_A_minus_B_mm": 0.0,
        "E_depth_A_minus_B_mm": 0.0,
        "I_front_edge_fiber_records": [{"v_mm": 0.0, "q_front_mm": 1.0, "H_front_mm": 2.0}],
        "root_fiber_records": [{"v_mm": 0.0, "q_root_mm": 3.0}],
    }]
    ctx = {"parts": parts, "control": [],
           "case": {"fan_geometry": {"fan_rafter_count": 1},
                    "eave_rafters_geometry": {"count": 0}},
           "upper_board_domain": {}, "selection_rule": "probe",
           "likou_notch_performance": {}}
    summary = {"schema": "v02_mesh_checkpoint_probe_v1", "case_id": config["case_id"]}
    with tempfile.TemporaryDirectory(prefix="mesh-checkpoint-probe-", dir=HERE / "diagnostics" / "v02") as temp:
        temp_root = Path(temp)
        checkpoint_output = temp_root / "checkpoint-source"
        checkpoint_output.mkdir()
        checkpoint_path, checkpoint_seconds = generate.save_geometry_checkpoint(
            parts, [], config, fly_records, [], [], {}, ctx, {}, 0.0, checkpoint_output)
        retry_config = copy.deepcopy(config)
        retry_config["case_id"] = "mesh_contract_probe_retry"
        restored = generate.restore_geometry_checkpoint(checkpoint_path, retry_config)
        volume_deltas = []
        for source, loaded in zip(parts, restored["parts"]):
            volume_deltas.append(abs(float(source["shape"].Volume()) - float(loaded["shape"].Volume())))
        mismatch_config = copy.deepcopy(config)
        mismatch_config["advanced"]["mesh_tessellation_mm"] = 1.0
        try:
            generate.restore_geometry_checkpoint(checkpoint_path, mismatch_config)
        except ValueError as exc:
            mismatch_rejected = str(exc)
        else:
            raise AssertionError("checkpoint accepted a different mesh configuration")
        derived_mismatch = copy.deepcopy(config)
        derived_mismatch["derived"]["fan_count"] = 3
        try:
            generate.restore_geometry_checkpoint(checkpoint_path, derived_mismatch)
        except ValueError as exc:
            derived_mismatch_rejected = str(exc)
        else:
            raise AssertionError("checkpoint accepted stale derived geometry")

        export_output = temp_root / "mesh-output"
        export_output.mkdir()
        mesh, triangle_count = generate.export_mesh(restored["parts"], [], config, export_output)
        mesh_check = generate.serialized_mesh_checks(mesh)
        mesh_diagnostics = json.loads((export_output / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
        if len(restored["parts"]) != len(parts) or any(delta > 1e-9 for delta in volume_deltas):
            raise AssertionError("BRep checkpoint round trip changed part count or volume")
        if not mesh_check["all_parts_have_finite_vertices_and_valid_triangles"]:
            raise AssertionError("absolute mesh export failed serialized mesh checks")
        if not all(attempt.get("faces") for part in mesh_diagnostics["parts"]
                   for attempt in part["attempts"]):
            raise AssertionError("mesh report omitted per-face triangulation diagnostics")
        summary.update({
            "checkpoint_schema": json.loads(
                (checkpoint_path / "checkpoint.json").read_text(encoding="utf-8"))["schema"],
            "checkpoint_case_id": restored["checkpoint_case_id"],
            "current_case_id": restored["config"]["case_id"],
            "case_id_change_allowed_and_applied": (
                restored["checkpoint_case_id"] == config["case_id"] and
                restored["config"]["case_id"] == retry_config["case_id"]),
            "production_source_sha256": restored["production_source_identity"]["sha256"],
            "geometry_source_sha256": restored["geometry_source_identity"]["sha256"],
            "recovery_production_source_sha256": restored[
                "recovery_production_source_identity"]["sha256"],
            "recovery_geometry_source_sha256": restored[
                "recovery_geometry_source_identity"]["sha256"],
            "checkpoint_part_count": len(restored["parts"]),
            "checkpoint_seconds": checkpoint_seconds,
            "source_brep_volumes_mm3": [float(part["shape"].Volume()) for part in parts],
            "roundtrip_volume_delta_mm3": volume_deltas,
            "config_mismatch_rejected": mismatch_rejected,
            "derived_mismatch_rejected": derived_mismatch_rejected,
            "mesh_part_count": len(mesh["parts"]),
            "mesh_triangle_count": triangle_count,
            "serialized_mesh_passed": mesh_check["all_parts_have_finite_vertices_and_valid_triangles"],
            "mesh_part_diagnostics": [
                {"id": part["id"], "face_count": part["attempts"][0]["face_count"],
                 "mesher_is_done": part["attempts"][0]["mesher_is_done"],
                 "mesher_status_flags": part["attempts"][0]["mesher_status_flags"],
                 "triangulation_guard_passed": part["attempts"][0]["triangulation_guard_passed"],
                 "max_face_deflection_mm": max(
                     face["deflection_mm"] for face in part["attempts"][0]["faces"]
                     if face["deflection_mm"] is not None),
                 "missing_degenerate_edges": len(part["attempts"][0]["missing_degenerate_edge_polygons"]),
                 "missing_nondegenerate_edges": len(part["attempts"][0]["missing_nondegenerate_edge_polygons"])}
                for part in mesh_diagnostics["parts"]
            ],
        })
    report_path = HERE / "diagnostics" / "v02" / "absolute_mesh_checkpoint_probe.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
