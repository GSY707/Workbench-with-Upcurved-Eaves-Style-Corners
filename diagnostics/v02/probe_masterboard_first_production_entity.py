from __future__ import annotations

import json
import hashlib
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
    str(HERE / "diagnostics" / "v02"),
]

import stage05_core as stage05
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape
from probe_shared_flyer_masterboard_entities import (
    inspect_longitudinal_brep_edges,
    load_b400_config,
    local_coordinates,
)


def shape_summary(shape):
    box = shape.BoundingBox()
    return {
        "valid": bool(shape.isValid()), "solid_count": len(shape.Solids()),
        "volume_mm3": float(shape.Volume()), "face_count": len(shape.Faces()),
        "edge_count": len(shape.Edges()),
        "bbox_mm": [float(box.xmin), float(box.ymin), float(box.zmin),
                    float(box.xmax), float(box.ymax), float(box.zmax)],
    }


def nearby_side_floor_point(record, detail, v, q, target, cut):
    controls = detail["I_front_edge_fiber_records"]
    v_values = [float(edge["v_mm"]) for edge in controls]
    upper = next((index for index, value in enumerate(v_values) if value >= float(v)), len(v_values) - 1)
    lower = max(0, upper - 1)
    if upper == lower:
        raise ValueError(f"near-side sample v={v} is outside the shared control rails")
    left, right = controls[lower], controls[upper]
    fraction = (float(v) - float(left["v_mm"])) / (float(right["v_mm"]) - float(left["v_mm"]))
    q_start = (1.0 - fraction) * float(left["q_front_path_hit_mm"]) + fraction * float(right["q_front_path_hit_mm"])
    q_end = (1.0 - fraction) * float(left["q_S_mm"]) + fraction * float(right["q_S_mm"])
    lam = (float(q) - q_start) / (q_end - q_start)
    if not 0.0 <= lam <= 1.0:
        modeled_domain = False
    else:
        modeled_domain = True
    z_left = stage05.bottom_outer_line(
        record, left,
        float(left["q_front_path_hit_mm"]) * (1.0 - lam) + float(left["q_S_mm"]) * lam,
        float(left["v_mm"]), float(detail["E_bottom_center_mm"]))
    z_right = stage05.bottom_outer_line(
        record, right,
        float(right["q_front_path_hit_mm"]) * (1.0 - lam) + float(right["q_S_mm"]) * lam,
        float(right["v_mm"]), float(detail["E_bottom_center_mm"]))
    z = (1.0 - fraction) * z_left + fraction * z_right
    xy = stage05.xy_at_q(record, q, v)
    below = stage05.cq.Vector(float(xy[0]), float(xy[1]), float(z - 0.05))
    above = stage05.cq.Vector(float(xy[0]), float(xy[1]), float(z + 0.05))
    raw_below = bool(target.isInside(below, 1e-6))
    raw_above = bool(target.isInside(above, 1e-6))
    after_below = bool(cut.isInside(below, 1e-6))
    after_above = bool(cut.isInside(above, 1e-6))
    raw_covers = raw_below and raw_above
    return {
        "v_mm": float(v), "q_mm": float(q), "outer_boundary_inward_offset_mm": float(q_end - q),
        "width_interpolation_fraction": float(fraction),
        "cutter_field_longitudinal_fraction": float(lam),
        "inside_modeled_shared_cutter_domain": bool(modeled_domain),
        "motherboard_floor_z_mm": float(z),
        "raw_stock_contains_below_floor": raw_below,
        "raw_stock_contains_above_floor": raw_above,
        "raw_stock_covers_both_samples": bool(raw_covers),
        "cut_likou_contains_below_floor": after_below,
        "cut_likou_contains_above_floor": after_above,
        "cut_result_has_empty_above_solid_below": (
            bool(not after_above and after_below) if raw_covers else None),
        "exact_shared_field_floor_rule": "linear interpolation of the adjacent shared motherboard rails at the cutter lambda",
        "not_in_raw_stock_is_reported_without_treating_as_cut_failure": True,
    }


def main():
    config, source_case_id = load_b400_config()
    config["case_id"] = "probe_masterboard_first_production_entity_B400"
    started = time.perf_counter()
    ctx = stage05.build_stage04_base(config)
    stage04_seconds = time.perf_counter() - started
    started = time.perf_counter()
    source_order, motherboard = stage05._build_flyer_masterboard(ctx)
    masterboard_seconds = time.perf_counter() - started
    record = ctx["chosen"][0]
    item = next(value for value in source_order
                if value["record"]["source_rafter_id"] == record["source_rafter_id"])
    production_order_ids = [entry["source_rafter_id"] for entry in ctx["chosen"]]
    motherboard_order_ids = [entry["record"]["source_rafter_id"] for entry in source_order]

    brep_path = HERE / "diagnostics" / "v02" / "fan01_shared_motherboard_B400.brep"
    if brep_path.exists():
        raise FileExistsError(f"refusing to overwrite local BRep evidence: {brep_path}")
    original_measure = stage05._measure_side_brep_generators

    def persist_before_guard(shape, *args, **kwargs):
        if not BRepTools.Write_s(shape.wrapped, str(brep_path)) or not brep_path.is_file():
            raise RuntimeError(f"failed to persist production BRep evidence: {brep_path}")
        return original_measure(shape, *args, **kwargs)

    stage05._measure_side_brep_generators = persist_before_guard

    started = time.perf_counter()
    shape, detail = stage05.build_contact_flyer(
        record, ctx["board_sampler"], ctx["case"], ctx["end_curve"],
        ctx["selected_gamma"], masterboard_record=item)
    member_seconds = time.perf_counter() - started
    if shape.isNull() or not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-5:
        raise ValueError("production build_contact_flyer returned an invalid member")

    restored_shape = TopoDS_Shape()
    restored_builder = BRep_Builder()
    if (not BRepTools.Read_s(restored_shape, str(brep_path), restored_builder) or
            restored_shape.IsNull()):
        raise RuntimeError(f"failed to reload production BRep evidence: {brep_path}")
    restored_cq_shape = stage05.cq.Shape(restored_shape)
    reloaded_generator_checks = stage05._measure_side_brep_generators(
        restored_cq_shape, record, detail["I_front_edge_fiber_records"],
        detail["root_fiber_records"], float(detail["E_bottom_center_mm"]),
        float(detail["E_depth_world_Z_mm"]))

    contour_checks = inspect_longitudinal_brep_edges(shape, record, detail)
    target = next(part["shape"] for part in ctx["parts"] if part["id"] == "likou_x")
    started = time.perf_counter()
    cutter = stage05.contact_notch_cutter(record, detail, ctx["board_sampler"], target)
    cutter_seconds = time.perf_counter() - started
    cutter_summary = shape_summary(cutter)
    floor_samples = []
    controls = detail["I_front_edge_fiber_records"]
    for fiber_index in (0, len(controls) // 2, len(controls) - 1):
        edge = controls[fiber_index]
        q_start = float(edge["q_front_path_hit_mm"])
        q_end = float(edge["q_S_mm"])
        v = float(edge["v_mm"])
        for fraction in (0.1, 0.5, 0.9):
            q = q_start * (1.0 - fraction) + q_end * fraction
            z = stage05.bottom_outer_line(
                record, edge, q, v, float(detail["E_bottom_center_mm"]))
            xy = stage05.xy_at_q(record, q, v)
            inside_above = cutter.isInside(stage05.cq.Vector(float(xy[0]), float(xy[1]), z + 0.05), 1e-6)
            inside_below = cutter.isInside(stage05.cq.Vector(float(xy[0]), float(xy[1]), z - 0.05), 1e-6)
            floor_samples.append({
                "fiber_index": fiber_index, "side": "A" if fiber_index == 0 else (
                    "B" if fiber_index == len(controls) - 1 else "center"),
                "v_mm": v, "q_mm": q, "lambda_I_to_likou_outer": fraction,
                "motherboard_bottom_z_mm": z,
                "cutter_contains_0_05mm_above_floor": bool(inside_above),
                "cutter_contains_0_05mm_below_floor": bool(inside_below),
            })

    started = time.perf_counter()
    before_volume = float(target.Volume())
    cut = target.cut(cutter).clean()
    cut_seconds = time.perf_counter() - started
    cut_summary = shape_summary(cut)
    edge_records = detail["I_front_edge_fiber_records"]
    q_start_all = [float(edge["q_front_path_hit_mm"]) for edge in edge_records]
    q_s_all = [float(edge["q_S_mm"]) for edge in edge_records]
    notches_outboard = all(q_s > q_start for q_s, q_start in zip(q_s_all, q_start_all))
    near_outer_boundary_checks = []
    shared_paths = motherboard["shared_boundary_paths"]
    front_path = shared_paths["front_inner_xy_mm"]
    likou_outer_path = shared_paths["likou_outer_xy_mm"]
    half_width = 0.5 * float(ctx["case"]["p"]["w_fly"])
    for side, v in (("A-near", -half_width + 0.1), ("B-near", half_width - 0.1)):
        origin = [float(record["S"][0]) + float(record["width_axis"][0]) * v,
                  float(record["S"][1]) + float(record["width_axis"][1]) * v]
        inner_hit = stage05._line_polyline_intersections(
            origin, record["unit"], front_path, f"{record['source_rafter_id']} {side} inner path")
        outer_hit = stage05._line_polyline_intersections(
            origin, record["unit"], likou_outer_path, f"{record['source_rafter_id']} {side} likou outer path")
        q = float(outer_hit["q_mm"]) - 0.1
        sample = nearby_side_floor_point(record, detail, v, q, target, cut)
        sample.update({
            "side_near": side, "q_I_actual_shared_path_mm": float(inner_hit["q_mm"]),
            "q_S_actual_likou_outer_path_mm": float(outer_hit["q_mm"]),
            "q_sample_is_0_1mm_inboard_of_actual_outer_path": abs(
                float(outer_hit["q_mm"]) - q - 0.1) <= 1e-8,
        })
        near_outer_boundary_checks.append(sample)
    result = {
        "case_id": config["case_id"], "source_case_id": source_case_id,
        "scope": "single production member build_contact_flyer + actual shared-floor cutter + one actual likou_x subtraction",
        "source_member": {"source_rafter_id": record["source_rafter_id"],
                          "family": record["family"], "index": int(record["index"])},
        "production_order_index_zero_based": int(production_order_ids.index(record["source_rafter_id"])),
        "motherboard_spatial_order_index_zero_based": int(motherboard_order_ids.index(record["source_rafter_id"])),
        "production_order_source_rafter_ids": production_order_ids,
        "motherboard_spatial_order_source_rafter_ids": motherboard_order_ids,
        "stage04_preflight_seconds": float(stage04_seconds),
        "shared_field_seconds": float(masterboard_seconds),
        "production_member_build_seconds": float(member_seconds),
        "member_shape": shape_summary(shape),
        "production_member_brep": {
            "path": str(brep_path.relative_to(HERE)),
            "sha256": hashlib.sha256(brep_path.read_bytes()).hexdigest(),
            "reloaded_valid": bool(restored_cq_shape.isValid()),
            "reloaded_solid_count": len(restored_cq_shape.Solids()),
            "reloaded_volume_mm3": float(restored_cq_shape.Volume()),
            "reloaded_side_generator_checks": reloaded_generator_checks,
        },
        "A_B_longitudinal_brep_edge_checks": contour_checks,
        "q_I_A_B_mm": [float(edge_records[0]["q_front_mm"]),
                       float(edge_records[-1]["q_front_mm"])],
        "q_E_A_B_mm": [float(edge_records[0]["q_E_mm"]),
                       float(edge_records[-1]["q_E_mm"])],
        "q_E_side_span_mm": float(max(float(edge["q_E_mm"]) for edge in edge_records)
                                   - min(float(edge["q_E_mm"]) for edge in edge_records)),
        "likou_path_start_q_range_mm": [float(min(q_start_all)), float(max(q_start_all))],
        "likou_real_outer_q_range_mm": [float(min(q_s_all)), float(max(q_s_all))],
        "likou_intersection_entirely_outboard_of_I": bool(notches_outboard),
        "minimum_outboard_overlap_from_I_mm": float(min(q_s - q_start for q_s, q_start
                                                       in zip(q_s_all, q_start_all))),
        "production_cutter_build_seconds": float(cutter_seconds),
        "cutter_shape": cutter_summary,
        "floor_membership_samples": floor_samples,
        "all_floor_samples_match_shared_surface": all(
            sample["cutter_contains_0_05mm_above_floor"] and
            not sample["cutter_contains_0_05mm_below_floor"] for sample in floor_samples),
        "near_actual_outer_boundary_A_B_checks": near_outer_boundary_checks,
        "all_covered_near_outer_boundary_checks_pass": all(
            sample["cut_result_has_empty_above_solid_below"]
            for sample in near_outer_boundary_checks if sample["raw_stock_covers_both_samples"]),
        "actual_likou_before_mm3": before_volume,
        "actual_likou_after_mm3": float(cut.Volume()),
        "actual_likou_subtraction_seconds": float(cut_seconds),
        "actual_likou_cut_shape": cut_summary,
        "actual_likou_volume_reduced": float(cut.Volume()) < before_volume,
        "all_side_regions_have_contiguous_brep_edge_coverage": all(
            side["region_coverage"][region]["endpoint_coverage_error_mm"] is not None and
            side["region_coverage"][region]["endpoint_coverage_error_mm"] <= 1e-3 and
            abs(side["region_coverage"][region]["covered_length_mm"] -
                side["region_coverage"][region]["expected_length_mm"]) <= 1e-3
            for side in contour_checks for region in ("tail_R_to_I", "outer_I_to_E")),
        "all_side_regions_straight_by_sampled_control_residual": all(
            side["max_control_residual_mm"] is not None and
            side["max_control_residual_mm"] <= 1e-6 for side in contour_checks),
        "masterboard_guard_results": motherboard["guard_results"],
    }
    output = HERE / "diagnostics" / "v02" / "masterboard_fan01_actual_shared_motherboard_B400.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f"diagnostic: {output}", flush=True)
    if not cut.isValid() or len(cut.Solids()) != 1 or float(cut.Volume()) >= before_volume:
        raise ValueError("single local actual likou cutter subtraction failed validation")


if __name__ == "__main__":
    main()
