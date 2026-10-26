from __future__ import annotations

import json
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
from probe_shared_flyer_masterboard_entities import load_b400_config


def side_contour(side: str, edge: dict, root: dict, E_bottom_center: float) -> dict:
    q_i = float(edge["q_front_mm"])
    q_e = float(edge["q_E_mm"])
    q_r = float(root["q_root_mm"])
    h_i = float(edge["H_front_mm"])
    h_r = float(root["H_R_board_mm"])
    i_top = float(edge["I_upper_z_mm"])
    e_bottom = float(edge["E_bottom_z_mm"])
    e_top = float(edge["E_top_z_mm"])
    top_r = float(edge["R_top_z_mm"])
    return {
        "side": side,
        "v_mm": float(edge["v_mm"]),
        "width_axis_mapping": "A=v=-w_fly/2; B=v=+w_fly/2",
        "I": {"q_mm": q_i, "lower_board_top_z_mm": h_i,
              "thickness_world_Z_mm": float(edge["dI_world_Z_mm"]),
              "upper_z_mm": i_top},
        "R": {"q_mm": q_r, "lower_board_top_z_mm": h_r,
              "upper_z_mm": top_r, "root_gap_mm": float(root["root_gap_mm"])},
        "E": {"q_mm": q_e, "xy_mm": edge["E_xy_mm"],
              "lower_z_mm": e_bottom, "upper_control_z_mm": e_top,
              "thickness_world_Z_mm": float(edge["E_depth_world_Z_mm"]),
              "upper_control_source": edge["E_top_source"],
              "projected_end_curve_fraction": float(edge["E_curve_fraction"])},
        "q_S_likou_outer_intersection_mm": float(edge["q_S_mm"]),
        "straight_edge_slopes_z_per_q": {
            "tail_lower_R_to_I": (h_i - h_r) / (q_i - q_r),
            "tail_upper_R_to_I": (i_top - top_r) / (q_i - q_r),
            "outer_lower_I_to_E": (e_bottom - h_i) / (q_e - q_i),
            "outer_upper_I_to_E": (e_top - i_top) / (q_e - q_i),
        },
        "control_record": edge,
        "root_record": root,
    }


def main() -> None:
    config, source_case_id = load_b400_config()
    config["case_id"] = "export_fan01_shared_motherboard_controls_B400"
    started = time.perf_counter()
    ctx = stage05.build_stage04_base(config)
    stage04_seconds = time.perf_counter() - started
    started = time.perf_counter()
    source_order, motherboard = stage05._build_flyer_masterboard(ctx)
    motherboard_seconds = time.perf_counter() - started

    record = next(item for item in ctx["chosen"]
                  if item["source_rafter_id"] == "fan_rafter_01")
    source_item = next(item for item in source_order
                       if item["record"]["source_rafter_id"] == "fan_rafter_01")
    production_ids = [item["source_rafter_id"] for item in ctx["chosen"]]
    spatial_ids = [item["record"]["source_rafter_id"] for item in source_order]
    controls = source_item["controls"]
    roots = source_item["roots"]
    center = controls[len(controls) // 2]
    contours = [side_contour("A", controls[0], roots[0],
                             float(center["E_bottom_z_mm"])),
                side_contour("B", controls[-1], roots[-1],
                             float(center["E_bottom_z_mm"]))]

    output = {
        "case_id": config["case_id"],
        "source_case_id": source_case_id,
        "member_id": record["source_rafter_id"],
        "production_order_index_zero_based": production_ids.index(record["source_rafter_id"]),
        "motherboard_spatial_order_index_zero_based": spatial_ids.index(record["source_rafter_id"]),
        "production_order_source_rafter_ids": production_ids,
        "motherboard_spatial_order_source_rafter_ids": spatial_ids,
        "stage04_preflight_seconds": stage04_seconds,
        "shared_field_seconds": motherboard_seconds,
        "record": record,
        "controls": controls,
        "roots": roots,
        "E_bottom_center_mm": float(center["E_bottom_z_mm"]),
        "E_depth_center_mm": float(center["E_depth_world_Z_mm"]),
        "side_contours": contours,
        "motherboard_member_domain": next(
            item for item in motherboard["member_domains"]
            if item["source_rafter_id"] == record["source_rafter_id"]),
        "motherboard_shared_rails": [
            rail for rail in motherboard["shared_rails"]
            if rail.get("source_rafter_id") == record["source_rafter_id"]],
        "shared_boundary_paths": motherboard["shared_boundary_paths"],
        "masterboard_guard_results": motherboard["guard_results"],
        "BRep_status": "reuses diagnostics/v02/fan01_shared_motherboard_B400.brep; this exporter builds no member solids and performs no cut",
        "contour_status": "controls-derived contours; independently measured BRep contour checks remain in diagnostics/v02/masterboard_first_production_entity_b400.json",
    }
    path = HERE / "diagnostics" / "v02" / "fan01_shared_motherboard_controls_B400.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite control evidence: {path}")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "member_id": output["member_id"],
                      "production_order": output["production_order_index_zero_based"],
                      "spatial_order": output["motherboard_spatial_order_index_zero_based"],
                      "control_count": len(controls),
                      "side_q_E_mm": [contours[0]["E"]["q_mm"], contours[1]["E"]["q_mm"]],
                      "q_E_span_mm": abs(contours[0]["E"]["q_mm"] - contours[1]["E"]["q_mm"]),
                      "stage04_seconds": stage04_seconds,
                      "shared_field_seconds": motherboard_seconds},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
