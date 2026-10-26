from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
CASE = HERE / "outputs" / "workbench" / "case-252a03d1611a49338e9f71c1f9bedff5"
CHECKPOINT = CASE / "geometry_checkpoint"
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


def main() -> None:
    checkpoint = json.loads((CHECKPOINT / "checkpoint.json").read_text(encoding="utf-8"))
    entry = next(part for part in checkpoint["parts"] if part["id"] == "likou_x")
    brep_path = CHECKPOINT / entry["brep"]
    if sha256(brep_path) != entry["sha256"]:
        raise AssertionError("checkpoint BRep hash mismatch")

    old_diagnostics = json.loads(SOURCE_DIAGNOSTICS.read_text(encoding="utf-8"))
    old_part = next(part for part in old_diagnostics["parts"] if part["id"] == "likou_x")
    old_attempts = []
    for attempt in old_part["attempts"]:
        face_defs = [face["deflection_mm"] for face in attempt["faces"]
                     if face["deflection_mm"] is not None]
        old_attempts.append({
            "mesher_target_mm": attempt.get("mesher_linear_deflection_target_mm",
                                           attempt.get("requested_linear_deflection_mm")),
            "mesher_is_done": attempt["mesher_is_done"],
            "mesher_status_flags": attempt["mesher_status_flags"],
            "triangulation_guard_passed": attempt["triangulation_guard_passed"],
            "face_count": attempt["face_count"],
            "faces_with_triangles": sum(face["triangle_count"] > 0 for face in attempt["faces"]),
            "max_face_recorded_deflection_mm": max(face_defs, default=None),
            "faces_over_2mm": sum(value > 2.0 + 1e-9 for value in face_defs),
            "missing_nondegenerate_edges": len(attempt["missing_nondegenerate_edge_polygons"]),
            "missing_degenerate_edges": len(attempt["missing_degenerate_edge_polygons"]),
            "missing_unclassified_edges": len(attempt["missing_unclassified_edge_polygons"]),
            "mesh_ready": attempt["mesh_ready"],
        })

    restored = TopoDS_Shape()
    if not BRepTools.Read_s(restored, str(brep_path), BRep_Builder()) or restored.IsNull():
        raise AssertionError("cannot read checkpoint BRep")
    shape = cq.Shape.cast(restored)
    solid_count = len(shape.Solids())
    volume = float(shape.Volume())
    valid = bool(shape.isValid())
    tolerance = 1.0
    request_limit = 2.0
    vertices, triangles, report, accepted = generate.absolute_face_mesh(
        shape, tolerance, 0.35, requested_upper_limit=request_limit)
    face_defs = [face["deflection_mm"] for face in report["faces"]
                 if face["deflection_mm"] is not None]
    finite_vertices = all(math.isfinite(value) for vertex in vertices for value in vertex)
    indices_in_range = all(0 <= index < len(vertices) for triangle in triangles for index in triangle)
    outcome = {
        "schema": "v02_likou_export_upper_bound_probe_v1",
        "checkpoint_case_id": checkpoint["checkpoint_case_id"],
        "checkpoint_path": str(CHECKPOINT),
        "part_id": entry["id"],
        "part_name": entry["name"],
        "brep_file": entry["brep"],
        "brep_sha256": entry["sha256"],
        "production_source_sha256_at_checkpoint": checkpoint["production_source_identity"]["sha256"],
        "geometry_source_sha256_at_checkpoint": checkpoint["geometry_source_identity"]["sha256"],
        "recovery_production_source_sha256": generate.production_source_identity()["sha256"],
        "recovery_geometry_source_sha256": generate.geometry_source_identity()["sha256"],
        "original_failure_attempts": old_attempts,
        "replay": {
            "requested_upper_limit_mm": request_limit,
            "mesher_target_mm": tolerance,
            "angular_tolerance_rad": 0.35,
            "accepted": accepted,
            "triangulation_guard_at_request_limit": report["triangulation_guard_at_requested_upper_limit_passed"],
            "triangulation_guard_at_mesher_target": report["triangulation_guard_at_mesher_target_passed"],
            "mesher_is_done": report["mesher_is_done"],
            "mesher_status_flags": report["mesher_status_flags"],
            "face_count": report["face_count"],
            "all_faces_complete": report["all_faces_complete"],
            "all_faces_have_positive_triangles": all(
                face["triangle_count"] > 0 for face in report["faces"]),
            "all_faces_within_requested_limit": report["all_faces_within_linear_limit"],
            "all_faces_within_mesher_target": report["all_faces_within_mesher_target"],
            "max_face_recorded_deflection_mm": max(face_defs, default=None),
            "faces_over_requested_limit": sum(value > request_limit + 1e-9 for value in face_defs),
            "missing_nondegenerate_edges": len(report["missing_nondegenerate_edge_polygons"]),
            "missing_degenerate_edges": len(report["missing_degenerate_edge_polygons"]),
            "missing_unclassified_edges": len(report["missing_unclassified_edge_polygons"]),
            "all_triangle_indices_valid": report["all_triangle_indices_valid"] and indices_in_range,
            "all_vertices_finite": finite_vertices,
            "vertex_count": len(vertices),
            "triangle_count": len(triangles),
            "rejection_reasons": report["rejection_reasons"],
            "per_face": report["faces"],
        },
        "brep_checks": {
            "is_valid": valid,
            "solid_count": solid_count,
            "positive_finite_volume": math.isfinite(volume) and volume > 1e-5,
            "volume_mm3": volume,
            "checkpoint_volume_mm3": entry["volume_mm3"],
            "volume_delta_mm3": abs(volume - float(entry["volume_mm3"])),
        },
    }
    outcome["passed"] = bool(
        accepted and valid and solid_count == 1 and math.isfinite(volume) and volume > 1e-5 and
        report["mesher_is_done"] and report["mesher_status_flags"] == 0 and
        report["triangulation_guard_at_requested_upper_limit_passed"] and
        report["all_faces_complete"] and report["all_faces_within_linear_limit"] and
        not report["missing_nondegenerate_edge_polygons"] and
        not report["missing_unclassified_edge_polygons"] and
        report["all_triangle_indices_valid"] and finite_vertices and bool(triangles) and
        abs(volume - float(entry["volume_mm3"])) <= max(1e-7, abs(volume) * 1e-10))
    output = HERE / "diagnostics" / "v02" / "likou_export_upper_bound_probe_v02.json"
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                      encoding="utf-8")
    if not outcome["passed"]:
        raise AssertionError(f"upper-bound mesh replay failed; see {output}")
    print(json.dumps({key: value for key, value in outcome.items() if key != "replay"},
                     ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({key: value for key, value in outcome["replay"].items()
                      if key != "per_face"}, ensure_ascii=False, indent=2, allow_nan=False))
    print(f"full face details: {output}")


if __name__ == "__main__":
    main()
