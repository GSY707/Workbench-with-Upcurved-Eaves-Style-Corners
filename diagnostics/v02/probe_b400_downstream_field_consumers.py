from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
]

import stage05_core as stage05


def main() -> None:
    config_path = (HERE / "outputs" / "workbench" /
                   "case-389425e820d34287a5c23b3f36aa5a01" / "parameters_resolved.json")
    controls_path = HERE / "diagnostics" / "v02" / "fan01_shared_motherboard_controls_B400.json"
    output_path = HERE / "diagnostics" / "v02" / "B400_downstream_field_consumers_probe.json"
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite downstream field consumer evidence: {output_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = json.loads(controls_path.read_text(encoding="utf-8"))
    record = dict(source["record"])
    controls = source["controls"]
    roots = source["roots"]
    center = len(controls) // 2
    record.update({
        "source_rafter_id": source["member_id"],
        "profile_basis": "shared_virtual_motherboard",
        "width_mm": float(max(control["v_mm"] for control in controls) -
                           min(control["v_mm"] for control in controls)),
        "I_front_edge_fiber_records": controls,
        "root_fiber_records": roots,
        "E_bottom_center_mm": float(source["E_bottom_center_mm"]),
        "E_depth_world_Z_mm": float(source["E_depth_center_mm"]),
        "dI_world_Z_mm": float(controls[center]["dI_world_Z_mm"]),
        "q_R_center_mm": float(roots[center]["q_root_mm"]),
        "side_contours": source["side_contours"],
    })

    generate_spec = importlib.util.spec_from_file_location("generator_downstream_probe", HERE / "generate.py")
    if generate_spec is None or generate_spec.loader is None:
        raise RuntimeError("failed to load generator layout/report helpers")
    generator = importlib.util.module_from_spec(generate_spec)
    generate_spec.loader.exec_module(generator)
    profile = generator.make_profiles([record])[0]
    if not math.isfinite(float(profile["area_mm2"])) or float(profile["area_mm2"]) <= 0.0:
        raise ValueError(f"center-fiber layout profile is invalid: {profile['area_mm2']}")

    contours = record["side_contours"]
    if [item["side"] for item in contours] != ["A", "B"]:
        raise ValueError(f"stage05 report expected A/B side contours; got {[item.get('side') for item in contours]}")
    side_report = {
        "source_rafter_id": record["source_rafter_id"],
        "index": int(record["index"]),
        "profile_basis": record["profile_basis"],
        "sides": contours,
        "dI_A_minus_B_mm": (
            float(contours[0]["I"]["thickness_world_Z_mm"]) -
            float(contours[1]["I"]["thickness_world_Z_mm"])),
        "E_depth_A_minus_B_mm": (
            float(contours[0]["E"]["thickness_world_Z_mm"]) -
            float(contours[1]["E"]["thickness_world_Z_mm"])),
    }

    # Exercise the exact upper-board section fields consumed by _support_band_offsets,
    # derived only from the already-saved B400 shared controls; no member BRep is rebuilt.
    bands = []
    for band_index, (low, high) in enumerate(zip(controls[:-1], controls[1:])):
        rows = []
        for fraction in (0.2, 0.45, 0.7, 1.0):
            points = []
            q_values = []
            for edge in (low, high):
                q_i = float(edge["q_front_mm"])
                q_e = float(edge["q_E_mm"])
                q = q_i * (1.0 - fraction) + q_e * fraction
                point_xy = stage05.xy_at_q(record, q, float(edge["v_mm"]))
                point_z = stage05.top_motherline(
                    record, edge, float(edge["dI_world_Z_mm"]), q,
                    float(edge["v_mm"]), float(record["E_bottom_center_mm"]),
                    float(record["E_depth_world_Z_mm"]))
                points.append([float(point_xy[0]), float(point_xy[1]), float(point_z)])
                q_values.append(q)
            rows.append({
                "region": "outer_I_to_E", "lambda": fraction,
                "v_low_mm": float(low["v_mm"]), "v_high_mm": float(high["v_mm"]),
                "q_low_mm": q_values[0], "q_high_mm": q_values[1],
                "points_low_to_high": points,
            })
        offsets, cosines = stage05._support_band_offsets(
            rows, float(low["v_mm"]), float(high["v_mm"]),
            float(config["parameters"]["board_upper_t"]),
            f"B400 fan01 field interface band {band_index}")
        flat_offsets = [float(value) for pair in offsets for value in pair]
        flat_cosines = [float(value) for pair in cosines for value in pair]
        if not all(math.isfinite(value) and value > 0.0 for value in flat_offsets):
            raise ValueError(f"upper-board support offsets are invalid for band {band_index}")
        if not all(math.isfinite(value) and value >= 0.5 for value in flat_cosines):
            raise ValueError(f"upper-board support normal is invalid for band {band_index}")
        bands.append({
            "band_index": band_index, "v_range_mm": [float(low["v_mm"]), float(high["v_mm"])],
            "row_count": len(rows), "offset_range_mm": [min(flat_offsets), max(flat_offsets)],
            "normal_cosine_range": [min(flat_cosines), max(flat_cosines)],
        })

    if len(bands) != 16:
        raise ValueError(f"B400 fan01 should have 16 ruled width bands, got {len(bands)}")
    guards = source["masterboard_guard_results"]
    if not all(value is True if isinstance(value, bool) else value is not None and value >= 0
               for value in guards.values()):
        raise ValueError(f"saved B400 masterboard guards failed: {guards}")

    result = {
        "source_case_id": source["source_case_id"],
        "source_member_id": source["member_id"],
        "source_brep_status": source["BRep_status"],
        "source_contour_status": source["contour_status"],
        "source_member_domain_W_i": source["motherboard_member_domain"]["W_i"],
        "layout_consumer": {
            "consumer": "generate.make_profiles",
            "profile_basis": profile["profile_basis"],
            "profile_id": profile["id"],
            "positive_finite_area_mm2": float(profile["area_mm2"]),
            "model_ids": profile["model_ids"],
            "source_center_fiber_v_mm": float(profile["source_values"]["fiber_v_mm"]),
        },
        "stage05_report_consumer": {
            "consumer": "generate.py validation.stage05.fan_side_contours fields",
            "source_rafter_id": side_report["source_rafter_id"],
            "profile_basis": side_report["profile_basis"],
            "side_labels": [item["side"] for item in side_report["sides"]],
            "dI_A_minus_B_mm": side_report["dI_A_minus_B_mm"],
            "E_depth_A_minus_B_mm": side_report["E_depth_A_minus_B_mm"],
        },
        "upper_board_consumer": {
            "consumer": "stage05_core._support_band_offsets on shared-field top section schema",
            "source_field": "saved B400 controls plus I/E global motherline interpolation",
            "band_count": len(bands),
            "all_bands_readable_with_finite_positive_offsets": True,
            "bands": bands,
        },
        "no_stage04_or_member_brep_rebuilt": True,
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"summary": str(output_path),
                      "layout_profile_area_mm2": profile["area_mm2"],
                      "report_side_labels": [item["side"] for item in contours],
                      "upper_board_band_count": len(bands),
                      "all_upper_board_bands_readable": True,
                      "no_stage04_or_member_brep_rebuilt": True},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
