from __future__ import annotations

import json
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
]

import stage05_core as stage05


def local_coordinates(record, point):
    delta = [float(point[0]) - float(record["S"][0]),
             float(point[1]) - float(record["S"][1])]
    q = delta[0] * float(record["unit"][0]) + delta[1] * float(record["unit"][1])
    v = delta[0] * float(record["width_axis"][0]) + delta[1] * float(record["width_axis"][1])
    return float(q), float(v)


def inspect_longitudinal_brep_edges(shape, record, detail):
    half_width = 0.5 * float(detail["width_mm"])
    fibers = detail["I_front_edge_fiber_records"]
    roots = detail["root_fiber_records"]
    checks = []
    for side, fiber_index in (("A", 0), ("B", len(fibers) - 1)):
        edge = fibers[fiber_index]
        root = roots[fiber_index]
        q_i = float(edge["q_front_mm"])
        q_e = float(edge["q_E_mm"])
        q_r = float(root["q_root_mm"])
        expected_v = -half_width if side == "A" else half_width
        candidates = []
        region_intervals = {"tail_R_to_I": [], "outer_I_to_E": []}
        expected_ranges = {
            "tail_R_to_I": (float(root["q_root_mm"]), q_i),
            "outer_I_to_E": (q_i, q_e),
        }
        for brep_edge in shape.Edges():
            vertices = brep_edge.Vertices()
            if len(vertices) != 2:
                continue
            points = [vertex.Center().toTuple() for vertex in vertices]
            coords = [local_coordinates(record, point) for point in points]
            if any(abs(v - expected_v) > 1e-5 for _, v in coords):
                continue
            q_span = abs(coords[1][0] - coords[0][0])
            if q_span < 1.0:
                continue
            region = next((name for name, expected_range in expected_ranges.items()
                           if min(coords[0][0], coords[1][0]) >= min(expected_range) - 1.0 and
                           max(coords[0][0], coords[1][0]) <= max(expected_range) + 1.0), None)
            if region is None:
                continue
            region_intervals[region].append((min(coords[0][0], coords[1][0]),
                                             max(coords[0][0], coords[1][0])))
            residuals = []
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
                point = brep_edge.positionAt(fraction).toTuple()
                q, v = local_coordinates(record, point)
                if region == "tail_R_to_I":
                    expected_bottom = stage05.tail_bottom_line(q, edge, root)
                    top_root = float(edge["R_top_z_mm"])
                    top_i = float(edge["I_upper_z_mm"])
                    expected_top = stage05.linear_value_at(q, q_r, top_root, q_i, top_i)
                    endpoint_zs = (float(root["H_R_board_mm"]), float(edge["H_front_mm"]),
                                   top_root, top_i)
                else:
                    expected_bottom = stage05.bottom_outer_line(
                        record, edge, q, v, float(detail["E_bottom_center_mm"]))
                    expected_top = stage05.top_motherline(
                        record, edge, float(edge["dI_world_Z_mm"]), q, v,
                        float(detail["E_bottom_center_mm"]), float(detail["E_depth_world_Z_mm"]))
                    endpoint_zs = (float(edge["H_front_mm"]), float(edge["E_bottom_z_mm"]),
                                   float(edge["I_upper_z_mm"]), float(edge["E_top_z_mm"]))
                expected_xy = stage05.xy_at_q(record, q, v)
                xy_error = math.dist((float(point[0]), float(point[1])), expected_xy)
                z_error = min(abs(float(point[2]) - expected_bottom),
                              abs(float(point[2]) - expected_top))
                residuals.append(max(xy_error, z_error))
            candidates.append({
                "region": region, "geom_type": str(brep_edge.geomType()),
                "length_mm": float(brep_edge.Length()),
                "max_endpoint_control_residual_mm": float(max(residuals)),
                "endpoint_z_controls_mm": list(endpoint_zs),
            })
        coverage_by_region = {}
        for region, intervals in region_intervals.items():
            merged = []
            for lower, upper in sorted(intervals):
                if not merged or lower > merged[-1][1] + 1e-3:
                    merged.append([lower, upper])
                else:
                    merged[-1][1] = max(merged[-1][1], upper)
            expected_range = expected_ranges[region]
            coverage_by_region[region] = {
                "expected_q_range_mm": [float(min(expected_range)), float(max(expected_range))],
                "merged_brep_edge_q_ranges_mm": merged,
                "covered_length_mm": float(sum(upper - lower for lower, upper in merged)),
                "expected_length_mm": float(abs(expected_range[1] - expected_range[0])),
                "endpoint_coverage_error_mm": (
                    float(max(abs(merged[0][0] - min(expected_range)),
                              abs(merged[-1][1] - max(expected_range)))) if merged else None),
            }
        checks.append({"side": side, "candidate_longitudinal_edges": candidates,
                       "candidate_count": len(candidates),
                       "all_candidates_are_lines": all(item["geom_type"] == "LINE" for item in candidates),
                       "region_coverage": coverage_by_region,
                       "max_control_residual_mm": max(
                           (item["max_endpoint_control_residual_mm"] for item in candidates), default=None)})
    return checks


def load_b400_config():
    source = HERE / "outputs" / "workbench" / "case-389425e820d34287a5c23b3f36aa5a01" / "parameters_resolved.json"
    config = json.loads(source.read_text(encoding="utf-8"))
    config.pop("derived", None)
    config["case_id"] = "probe_shared_flyer_masterboard_B400_entities"
    return config, source.parent.name


def main():
    config, source_case_id = load_b400_config()
    started = time.perf_counter()
    ctx = stage05.build_stage04_base(config)
    stage04_seconds = time.perf_counter() - started
    started = time.perf_counter()
    flyers = stage05.add_contact_flyers(ctx)
    flyer_seconds = time.perf_counter() - started
    records_by_id = {record["source_rafter_id"]: record for record in ctx["chosen"]}
    shapes_by_id = {detail["source_rafter_id"]: ctx["contact_fly_shapes_x"][detail["part_ids"][0]]
                    for detail in flyers}
    entities = []
    for detail in flyers:
        source_id = detail["source_rafter_id"]
        record = records_by_id[source_id]
        shape = shapes_by_id[source_id]
        contours = inspect_longitudinal_brep_edges(shape, record, detail)
        entities.append({
            "source_rafter_id": source_id, "family": detail["family"], "index": detail["index"],
            "valid": bool(shape.isValid()), "solid_count": len(shape.Solids()),
            "volume_mm3": float(shape.Volume()), "brep_edge_count": len(shape.Edges()),
            "side_longitudinal_brep_edges": contours,
            "q_I_A_B_mm": [float(detail["I_front_edge_fiber_records"][0]["q_front_mm"]),
                           float(detail["I_front_edge_fiber_records"][-1]["q_front_mm"])],
            "q_E_A_B_mm": [float(detail["I_front_edge_fiber_records"][0]["q_E_mm"]),
                           float(detail["I_front_edge_fiber_records"][-1]["q_E_mm"])],
            "q_E_side_span_mm": float(max(edge["q_E_mm"] for edge in detail["I_front_edge_fiber_records"])
                                      - min(edge["q_E_mm"] for edge in detail["I_front_edge_fiber_records"])),
            "likou_intersection_is_outboard_of_I_path": all(
                float(edge["q_S_mm"]) > float(edge["q_front_path_hit_mm"])
                for edge in detail["I_front_edge_fiber_records"]),
            "minimum_likou_start_distance_from_I_path_mm": float(min(
                edge["q_S_mm"] - edge["q_front_path_hit_mm"]
                for edge in detail["I_front_edge_fiber_records"])),
            "side_contours": detail["side_contours"],
        })

    started = time.perf_counter()
    notch_records = stage05.cut_contact_likou_notches(ctx)
    notch_seconds = time.perf_counter() - started
    notch_floor_domains = []
    for detail in flyers:
        edges = detail["I_front_edge_fiber_records"]
        notch_floor_domains.append({
            "source_rafter_id": detail["source_rafter_id"],
            "q_start_from_shared_I_path_mm": [float(edge["q_front_path_hit_mm"]) for edge in edges],
            "q_end_at_real_likou_outer_path_mm": [float(edge["q_S_mm"]) for edge in edges],
            "all_intersections_outboard_of_I_path": all(
                float(edge["q_S_mm"]) > float(edge["q_front_path_hit_mm"]) for edge in edges),
        })

    motherboard = ctx["flyer_masterboard"]
    result = {
        "probe_case_id": config["case_id"], "source_case_id": source_case_id,
        "scope": "stage04 preflight + shared-field flyer BReps + actual likou cutter/Boolean; no upper-board or full-generator run",
        "stage04_preflight_seconds": float(stage04_seconds),
        "shared_field_and_flyer_seconds": float(flyer_seconds),
        "likou_cutter_boolean_seconds": float(notch_seconds),
        "shared_field_guard_results": motherboard["guard_results"],
        "global_member_order": motherboard["member_order_by_shared_boundary"],
        "maximum_q_E_side_span_mm": motherboard["maximum_q_E_side_span_mm"],
        "q_E_side_spans": motherboard["q_E_side_spans"],
        "member_entities": entities,
        "likou_notch_cut_records": notch_records,
        "likou_floor_domains": notch_floor_domains,
        "likou_cut_symmetry": ctx["likou_notch_performance"]["cut_symmetry"],
        "all_member_breps_valid_single_solids": all(
            entity["valid"] and entity["solid_count"] == 1 for entity in entities),
        "all_brep_sides_have_two_longitudinal_regions": all(
            all(side["candidate_count"] >= 2 for side in entity["side_longitudinal_brep_edges"])
            for entity in entities),
        "all_member_likou_intersections_outboard_of_I_path": all(
            entry["all_intersections_outboard_of_I_path"] for entry in notch_floor_domains),
    }
    output = HERE / "diagnostics" / "v02" / "shared_flyer_masterboard_b400_entity_probe.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"member_entities", "likou_notch_cut_records", "likou_floor_domains"}},
                     ensure_ascii=False, indent=2), flush=True)
    print(f"diagnostic: {output}", flush=True)


if __name__ == "__main__":
    main()
