from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
SNAPSHOT = HERE / "diagnostics" / "v02" / "B400_geometry_snapshot"
CASE = HERE / "outputs" / "workbench" / "case-db7c892d368348d397d02b26f69ad10b"
SOURCE_DIAGNOSTICS = CASE / "mesh_export_diagnostics.json"
sys.path[:0] = [
    str(HERE),
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
]

import cadquery as cq
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape

import generate


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_shape(path: Path):
    wrapped = TopoDS_Shape()
    if not BRepTools.Read_s(wrapped, str(path), BRep_Builder()) or wrapped.IsNull():
        raise RuntimeError(f"cannot read checkpoint BRep: {path}")
    return cq.Shape.cast(wrapped)


def attempt_summary(target: float, report: dict, accepted: bool) -> dict:
    face_values = [face["deflection_mm"] for face in report["faces"]
                   if face.get("deflection_mm") is not None]
    return {
        "mesher_target_mm": target,
        "accepted": accepted,
        "mesher_is_done": report["mesher_is_done"],
        "mesher_status_flags": report["mesher_status_flags"],
        "triangulation_guard_at_2mm": report["triangulation_guard_at_requested_upper_limit_passed"],
        "face_count": report["face_count"],
        "faces_with_positive_triangles": sum(face["triangle_count"] > 0 for face in report["faces"]),
        "all_face_indices_valid": report["all_triangle_indices_valid"],
        "max_face_recorded_deflection_mm": max(face_values, default=None),
        "faces_over_2mm": sum(value > 2.0 + 1e-9 for value in face_values),
        "missing_nondegenerate_edges": len(report["missing_nondegenerate_edge_polygons"]),
        "missing_degenerate_edges": len(report["missing_degenerate_edge_polygons"]),
        "missing_unclassified_edges": len(report["missing_unclassified_edge_polygons"]),
        "rejection_reasons": report["rejection_reasons"],
        "worst_faces": sorted(
            ({"face_index": face["face_index"], "deflection_mm": face["deflection_mm"],
              "triangle_count": face["triangle_count"],
              "boundary_edge_count": face["boundary_edge_count"],
              "missing_edges": face["boundary_edges_missing_polygon"]}
             for face in report["faces"] if face.get("deflection_mm") is not None),
            key=lambda face: face["deflection_mm"], reverse=True)[:12],
    }


def main() -> None:
    checkpoint_path = SNAPSHOT / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    entry = next(part for part in checkpoint["parts"]
                 if part["id"] == "lower_board_straight_x_03")
    brep_path = SNAPSHOT / entry["brep"]
    if sha256(brep_path) != entry["sha256"]:
        raise AssertionError("snapshot BRep hash mismatch")
    shape = read_shape(brep_path)
    volume = float(shape.Volume())
    old_diagnostics = json.loads(SOURCE_DIAGNOSTICS.read_text(encoding="utf-8"))
    old_part = next(part for part in old_diagnostics["parts"]
                    if part["id"] == "lower_board_straight_x_03")
    prior_attempts = []
    for attempt in old_part["attempts"]:
        face_values = [face["deflection_mm"] for face in attempt["faces"]
                       if face.get("deflection_mm") is not None]
        prior_attempts.append({
            "mesher_target_mm": attempt["mesher_linear_deflection_target_mm"],
            "mesh_ready": attempt["mesh_ready"],
            "triangulation_guard_at_2mm": attempt["triangulation_guard_at_requested_upper_limit_passed"],
            "mesher_is_done": attempt["mesher_is_done"],
            "mesher_status_flags": attempt["mesher_status_flags"],
            "face_count": attempt["face_count"],
            "max_face_recorded_deflection_mm": max(face_values, default=None),
            "faces_over_2mm": sum(value > 2.0 + 1e-9 for value in face_values),
            "missing_nondegenerate_edges": len(attempt["missing_nondegenerate_edge_polygons"]),
            "missing_degenerate_edges": len(attempt["missing_degenerate_edge_polygons"]),
            "missing_unclassified_edges": len(attempt["missing_unclassified_edge_polygons"]),
            "worst_faces": sorted(
                ({"face_index": face["face_index"], "deflection_mm": face["deflection_mm"],
                  "triangle_count": face["triangle_count"]}
                 for face in attempt["faces"] if face.get("deflection_mm") is not None),
                key=lambda face: face["deflection_mm"], reverse=True)[:12],
        })

    refinements = []
    for target in (0.0625, 0.03125, 0.015625):
        working_shape = read_shape(brep_path)
        vertices, triangles, report, accepted = generate.absolute_face_mesh(
            working_shape, target, 0.35, requested_upper_limit=2.0)
        result = attempt_summary(target, report, accepted)
        result["vertex_count"] = len(vertices)
        result["triangle_count"] = len(triangles)
        result["all_vertices_finite"] = all(
            math.isfinite(value) for vertex in vertices for value in vertex)
        refinements.append(result)
        if accepted:
            break

    integrated_shape = read_shape(brep_path)
    with tempfile.TemporaryDirectory(prefix="b400-lower-board-mesh-export-",
                                     dir=HERE / "diagnostics" / "v02") as temp_dir:
        mesh, triangle_count = generate.export_mesh(
            [{"id": entry["id"], "name": entry["name"], "group": entry["group"],
              "shape": integrated_shape}],
            checkpoint["control"], checkpoint["resolved_config"], Path(temp_dir))
        serialized = generate.serialized_mesh_checks(mesh)
        export_diagnostics = json.loads(
            (Path(temp_dir) / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
        exported_part = export_diagnostics["parts"][0]
        final_attempt = exported_part["attempts"][-1]
        exported_face_values = [face["deflection_mm"] for face in final_attempt["faces"]
                                if face.get("deflection_mm") is not None]
        integrated_export = {
            "retry_targets_mm": export_diagnostics["retry_tolerances_mm"],
            "accepted_target_mm": exported_part["accepted_tolerance_mm"],
            "attempt_count": len(exported_part["attempts"]),
            "requested_upper_limit_mm": final_attempt["requested_linear_deflection_upper_limit_mm"],
            "max_face_recorded_deflection_mm": max(exported_face_values, default=None),
            "triangulation_guard_at_2mm": final_attempt[
                "triangulation_guard_at_requested_upper_limit_passed"],
            "all_faces_complete": final_attempt["all_faces_complete"],
            "all_faces_within_2mm": final_attempt["all_faces_within_linear_limit"],
            "missing_nondegenerate_edges": len(final_attempt["missing_nondegenerate_edge_polygons"]),
            "missing_unclassified_edges": len(final_attempt["missing_unclassified_edge_polygons"]),
            "serialized_mesh_passed": serialized["all_parts_have_finite_vertices_and_valid_triangles"],
            "mesh_triangle_count": triangle_count,
            "effective_mesh_max_face_deflection_mm": mesh["metadata"][
                "mesh_effective_max_face_deflection_mm"],
        }

    output = {
        "schema": "v02_b400_lower_board_mesh_refinement_probe_v1",
        "checkpoint_case_id": checkpoint["checkpoint_case_id"],
        "part_id": entry["id"],
        "brep_file": entry["brep"],
        "brep_sha256": entry["sha256"],
        "production_source_sha256": checkpoint["production_source_identity"]["sha256"],
        "geometry_source_sha256": checkpoint["geometry_source_identity"]["sha256"],
        "recovery_production_source_sha256": generate.production_source_identity()["sha256"],
        "recovery_geometry_source_sha256": generate.geometry_source_identity()["sha256"],
        "brep_checks": {"is_valid": bool(shape.isValid()), "solid_count": len(shape.Solids()),
                        "volume_mm3": volume,
                        "checkpoint_volume_mm3": entry["volume_mm3"],
                        "volume_delta_mm3": abs(volume - float(entry["volume_mm3"]))},
        "failure_attempts": prior_attempts,
        "refinement_attempts": refinements,
        "integrated_export_probe": integrated_export,
        "passed": bool(refinements and refinements[-1]["accepted"] and
                        integrated_export["accepted_target_mm"] <= 0.0625 and
                        integrated_export["max_face_recorded_deflection_mm"] <= 2.0 + 1e-9 and
                        integrated_export["triangulation_guard_at_2mm"] and
                        integrated_export["serialized_mesh_passed"] and shape.isValid() and
                        len(shape.Solids()) == 1 and volume > 1e-5),
    }
    output_path = HERE / "diagnostics" / "v02" / "b400_lower_board_x03_mesh_refinement_probe.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    if not output["passed"]:
        raise AssertionError(f"no accepted finite refinement found; see {output_path}")
    print(json.dumps({key: value for key, value in output.items()
                      if key not in ("failure_attempts", "refinement_attempts")},
                     ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"failure_attempts": prior_attempts,
                      "refinement_attempts": refinements,
                      "integrated_export_probe": integrated_export},
                     ensure_ascii=False, indent=2, allow_nan=False))
    print(f"report: {output_path}")


if __name__ == "__main__":
    main()
