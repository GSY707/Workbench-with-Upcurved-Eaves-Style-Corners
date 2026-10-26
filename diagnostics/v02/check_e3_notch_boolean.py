#!/usr/bin/env python
"""One bounded exact Boolean check at the highest old-mesh notch residual points."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[2]
ROOT = GENERATOR.parents[1]
sys.path[:0] = [str(ROOT / "资料整理" / "tmp" / "model_deps"), str(GENERATOR / "engine")]
from config import load_config

CONFIG = GENERATOR / "configs" / "size_variant_valid64.json"
SECTION_JSON = GENERATOR / "diagnostics/v02/e3_likou_notch_sections.json"
OUTPUT = GENERATOR / "diagnostics/v02/e3_notch_boolean_check.json"


def load_stage05():
    spec = importlib.util.spec_from_file_location("check_e3_stage05", GENERATOR / "engine" / "stage05_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    import numpy as np
    started = time.perf_counter()
    stage05 = load_stage05()
    config = load_config(CONFIG)
    ctx = stage05.build_stage04_base(config)
    prior = json.loads(SECTION_JSON.read_text(encoding="utf-8"))
    candidates_by_index = {}
    for check in prior["checks"]:
        rows = check["largest_residual_sections"]
        by_q = {}
        for row in rows:
            old = by_q.get(row["q_mm"])
            if old is None or row["material_above_floor_mm"] > old["material_above_floor_mm"]:
                by_q[row["q_mm"]] = row
        # One peak point and one neighboring longitudinal section per circled candidate.
        ordered = sorted(by_q.values(), key=lambda r: r["material_above_floor_mm"], reverse=True)
        candidates_by_index[int(check["flyer_id"].rsplit("_", 1)[1])] = ordered[:2]

    chosen = {int(r["index"]): r for r in ctx["chosen"]
              if r["family"] == "straight" and int(r["index"]) in candidates_by_index}
    target = next(part["shape"] for part in ctx["parts"] if part["id"] == "likou_x")
    details = {}
    cutters = []
    for index in sorted(chosen):
        record = chosen[index]
        _, detail = stage05.build_contact_flyer(record, ctx["board_sampler"], ctx["case"])
        details[index] = (record, detail)
        cutters.append(stage05.contact_notch_cutter(record, detail, ctx["board_sampler"], target))

    before = float(target.Volume())
    print("boolean check: three-cutter exact BRep difference start", flush=True)
    cut_started = time.perf_counter()
    cut = target.cut(stage05.cq.Compound.makeCompound(cutters)).clean()
    cut_seconds = time.perf_counter() - cut_started
    if not cut.isValid() or len(cut.Solids()) != 1 or float(cut.Volume()) >= before:
        raise RuntimeError(f"three-cutter Boolean failed: valid={cut.isValid()} solids={len(cut.Solids())} before={before} after={cut.Volume()}")
    checks = []
    for index, points in sorted(candidates_by_index.items()):
        record, detail = details[index]
        edges = detail["I_front_edge_fiber_records"]
        v_knots = [float(e["v_mm"]) for e in edges]
        q_fronts = [float(e["q_front_mm"]) for e in edges]
        row_checks = []
        for item in points:
            q, v = float(item["q_mm"]), float(item["v_mm"])
            edge = min(edges, key=lambda item: abs(float(item["v_mm"]) - v))
            qi = float(edge["q_front_mm"])
            if q < qi:
                floor = stage05.board_top_at(record, ctx["board_sampler"], q, v)
            else:
                floor = stage05.bottom_outer_line(record, edge, q, v, detail["E_bottom_center_mm"])
            if floor is None:
                raise RuntimeError(f"missing floor at {index=} {q=} {v=}")
            x, y = stage05.xy_at_q(record, q, v)
            z = float(floor) + 15.0
            point = stage05.cq.Vector(float(x), float(y), z)
            row_checks.append({
                "q_mm": q, "v_mm": v, "x_mm": float(x), "y_mm": float(y),
                "z_mm": z, "old_mesh_excess_mm": float(item["material_above_floor_mm"]),
                "uncut_target_contains_point": bool(target.isInside(point, 0.05)),
                "three_cutter_result_contains_point": bool(cut.isInside(point, 0.05)),
            })
        checks.append({"candidate_id": f"straight_flying_x_{index:02d}", "point_checks": row_checks})

    # The only point left inside the Boolean result was exactly on the nominal
    # v=-32mm cutter side wall. Recheck the same notch 0.5mm inward in both
    # q and width, away from the cutter's start cap and side boundary.
    record, detail = details[1]
    edges = detail["I_front_edge_fiber_records"]
    v_knots = [float(e["v_mm"]) for e in edges]
    q_fronts = [float(e["q_front_mm"]) for e in edges]
    q_test = -56.61
    q0_test = min(q_fronts) - 0.05
    interior_checks = []
    for v_test in (-31.5, 31.5):
        edge = min(edges, key=lambda item: abs(float(item["v_mm"]) - v_test))
        qi = float(edge["q_front_mm"])
        if q_test < qi:
            floor = stage05.board_top_at(record, ctx["board_sampler"], q_test, v_test)
        else:
            floor = stage05.bottom_outer_line(record, edge, q_test, v_test, detail["E_bottom_center_mm"])
        x, y = stage05.xy_at_q(record, q_test, v_test)
        point = stage05.cq.Vector(float(x), float(y), float(floor) + 15.0)
        interior_checks.append({
            "candidate_id": "straight_flying_x_01",
            "q_mm": q_test, "q0_mm": q0_test, "q_inside_margin_mm": q_test - q0_test,
            "v_mm": v_test, "v_inside_margin_mm": 0.5,
            "x_mm": float(x), "y_mm": float(y), "z_mm": float(floor) + 15.0,
            "uncut_target_contains_point": bool(target.isInside(point, 0.05)),
            "three_cutter_result_contains_point": bool(cut.isInside(point, 0.05)),
        })

    from OCP.BRepTools import BRepTools
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    BRepTools.Clean_s(cut.wrapped)
    BRepMesh_IncrementalMesh(cut.wrapped, 2.0, False, 0.35)
    mesh_vertices, mesh_triangles = cut.tessellate(2.0, 0.35)
    mesh_part = {
        "vertices": [float(c) for vertex in mesh_vertices for c in vertex.toTuple()],
        "triangles": [int(i) for triangle in mesh_triangles for i in triangle],
    }
    from inspect_e3_likou_notch_sections import MeshTopSampler
    mesh_sampler = MeshTopSampler(mesh_part)
    new_mesh_checks = []
    for v_test in (-31.5, 31.5):
        edge = min(edges, key=lambda item: abs(float(item["v_mm"]) - v_test))
        qi = float(edge["q_front_mm"])
        floor = stage05.bottom_outer_line(record, edge, q_test, v_test, detail["E_bottom_center_mm"])
        x, y = stage05.xy_at_q(record, q_test, v_test)
        z_hits = mesh_sampler.z_hits(float(x), float(y))
        top = max(z_hits) if z_hits else None
        excess = None if top is None else float(top - floor)
        new_mesh_checks.append({
            "q_mm": q_test, "q_front_mm": qi, "v_mm": v_test,
            "x_mm": float(x), "y_mm": float(y), "seat_floor_z_mm": float(floor),
            "new_cut_mesh_top_z_mm": None if top is None else float(top),
            "new_cut_mesh_material_above_floor_mm": excess,
            "classified_as_remaining_material": bool(excess is not None and excess > 2.5),
        })

    report = {
        "case_id": "size_variant_valid64",
        "source_mesh": "outputs/workbench/case-e3b47d1b9f6a4beda5ed00e4f28ed3e7/model_mesh.json",
        "method": "cut the current reconstructed likou_x BRep with three straight-fly seat cutters, re-mesh after BRepTools.Clean_s using absolute 2mm linear deflection and 0.35rad angular tolerance, then inspect the new cut mesh at the two points 0.5mm inside the nominal width side walls.",
        "target_before_volume_mm3": before,
        "target_after_volume_mm3": float(cut.Volume()),
        "removed_volume_mm3": before - float(cut.Volume()),
        "valid_single_solid": bool(cut.isValid() and len(cut.Solids()) == 1),
        "boolean_seconds": cut_seconds,
        "absolute_remesh": {
            "linear_deflection_mm": 2.0,
            "relative_mode": False,
            "angular_tolerance_rad": 0.35,
            "vertex_count": len(mesh_vertices),
            "triangle_count": len(mesh_triangles),
        },
        "checks": checks,
        "inward_side_boundary_checks": interior_checks,
        "new_absolute_mesh_checks": new_mesh_checks,
        "elapsed_seconds": time.perf_counter() - started,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT.resolve()), "boolean_seconds": cut_seconds,
                      "point_results": [[p["uncut_target_contains_point"], p["three_cutter_result_contains_point"]]
                                        for c in checks for p in c["point_checks"]],
                      "inward_results": [p["three_cutter_result_contains_point"] for p in interior_checks]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
