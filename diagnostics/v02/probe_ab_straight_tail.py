#!/usr/bin/env python
"""Bounded geometry probe for A/B flyer sides and straight R-I tails."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[2]
ROOT = GENERATOR.parents[1]
ENGINE = GENERATOR / "engine"
sys.path[:0] = [str(ROOT / "资料整理" / "tmp" / "model_deps"), str(ENGINE)]

from config import load_config


def load_stage05():
    spec = importlib.util.spec_from_file_location("probe_stage05", ENGINE / "stage05_core.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load engine/stage05_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bottom_line_at(q, q_root, z_root, q_front, z_front):
    t = (float(q) - float(q_root)) / (float(q_front) - float(q_root))
    return float(z_root) + t * (float(z_front) - float(z_root))


def probe_side(stage05, record, detail, sampler, likou_sampler, case, v, side_label):
    edge = min(detail["I_front_edge_fiber_records"],
               key=lambda item: abs(float(item["v_mm"]) - float(v)))
    root = min(detail["root_fiber_records"],
               key=lambda item: abs(float(item["v_mm"]) - float(v)))
    q_front = float(edge["q_front_mm"])
    h_front = float(edge["H_front_mm"])
    d_i = float(edge["dI_world_Z_mm"])
    x_i, y_i = stage05.xy_at_q(record, q_front, float(v))
    likou_i = likou_sampler.z_range(x_i, y_i)
    x_s, y_s = stage05.xy_at_q(record, 0.0, float(v))
    board_s = sampler.z_range(x_s, y_s)
    likou_s = likou_sampler.z_range(x_s, y_s)
    q_root = float(root["q_root_mm"])
    h_root = float(root["H_R_board_mm"])
    q_samples = [float(q) for q in stage05.np.linspace(q_root, q_front, 21)]
    residuals = []
    thicknesses = []
    for q in q_samples:
        h_board = stage05.board_top_at(record, sampler, q, v)
        if h_board is None:
            continue
        bottom_line = stage05.tail_bottom_line(q, edge, root)
        top_line = stage05.top_motherline(
            record, edge, d_i, q, v, detail["E_bottom_center_mm"], detail["E_depth_world_Z_mm"])
        residuals.append(float(bottom_line - h_board))
        thicknesses.append(float(top_line - bottom_line))
    if len(residuals) < 19:
        raise ValueError(f"{record['family']} {record['index']} {side_label}: only {len(residuals)}/21 tail board samples")
    return {
        "side": side_label,
        "v_mm": float(v),
        "q_I_mm": q_front,
        "H_I_board_mm": h_front,
        "dI_world_Z_mm": d_i,
        "I_upper_world_Z_mm": h_front + d_i,
        "E_bottom_z_mm": float(edge["E_bottom_z_mm"]),
        "E_upper_control_z_mm": float(edge["E_top_z_mm"]),
        "E_depth_world_Z_mm": float(edge["E_depth_world_Z_mm"]),
        "E_upper_control_source": edge["E_top_source"],
        "projected_end_curve_fraction": float(edge["E_curve_fraction"]),
        "I_likou_mesh_z_range_mm": likou_i,
        "I_likou_top_minus_board_top_mm": None if likou_i is None else float(likou_i["max_z_mm"] - h_front),
        "S_board_top_z_mm": None if board_s is None else float(board_s["max_z_mm"]),
        "S_likou_mesh_z_range_mm": likou_s,
        "R_q_mm": q_root,
        "R_board_z_mm": float(h_root),
        "root_gap_to_target_mm": float(root["root_gap_mm"]),
        "tail_bottom_line_minus_board_mm": {
            "min": float(min(residuals)),
            "max": float(max(residuals)),
            "max_abs": float(max(abs(x) for x in residuals)),
            "samples": len(residuals),
        },
        "tail_line_thickness_mm": {
            "min": float(min(thicknesses)),
            "max": float(max(thicknesses)),
        },
    }


def probe_notch_residual(stage05, record, ctx):
    """Point-sample the post-cut likou above the exact new flyer seat floor."""
    parts = {part["id"]: part for part in ctx["parts"]}
    target = parts["likou_x"]["shape"]
    shape, detail = stage05.build_contact_flyer(
        record, ctx["board_sampler"], ctx["case"], ctx["end_curve"], ctx["selected_gamma"])
    if not shape.isValid() or len(shape.Solids()) != 1:
        raise RuntimeError("representative flyer failed its solid check")
    cutter = stage05.contact_notch_cutter(record, detail, ctx["board_sampler"], target)
    cutter_compound = stage05.cq.Compound.makeCompound([cutter])
    cut = target.cut(cutter_compound).clean()
    if not cut.isValid() or len(cut.Solids()) != 1 or cut.Volume() >= target.Volume():
        raise RuntimeError("bounded notched likou probe failed its solid/volume checks")

    hits = []
    missing_board = 0
    half_width = float(ctx["case"]["p"]["w_fly"]) / 2.0
    edge_records = detail["I_front_edge_fiber_records"]
    v_knots = [float(item["v_mm"]) for item in edge_records]
    q_fronts = [float(item["q_front_mm"]) for item in edge_records]
    q0 = min(q_fronts)
    qs = stage05.np.linspace(q0 + 1.0, -1.0, 4)
    vs = stage05.np.linspace(-half_width + 1.0, half_width - 1.0, 5)
    for q in qs:
        for v in vs:
            q_i = float(stage05.width_interp(v, v_knots, q_fronts))
            if float(q) < q_i:
                root_records = detail["root_fiber_records"]
                root_v = [float(item["v_mm"]) for item in root_records]
                root_q = stage05.width_interp(v, root_v, [float(item["q_root_mm"]) for item in root_records])
                root_h = stage05.width_interp(v, root_v, [float(item["H_R_board_mm"]) for item in root_records])
                edge = min(edge_records, key=lambda item: abs(float(item["v_mm"]) - float(v)))
                root = {"q_root_mm": root_q, "H_R_board_mm": root_h}
                floor = stage05.tail_bottom_line(float(q), edge, root)
            else:
                edge = min(edge_records, key=lambda item: abs(float(item["v_mm"]) - float(v)))
                floor = stage05.bottom_outer_line(record, edge, float(q), float(v),
                                                  detail["E_bottom_center_mm"])
            x, y = stage05.xy_at_q(record, float(q), float(v))
            z_values = stage05.np.arange(float(floor) + 2.0, float(floor) + 158.0, 12.0)
            for z in z_values:
                if cut.isInside(stage05.cq.Vector(x, y, float(z)), 1e-4):
                    hits.append({"flyer_index": int(record["index"]), "q_mm": float(q),
                                 "v_mm": float(v), "z_mm": float(z),
                                 "floor_z_mm": float(floor), "height_above_floor_mm": float(z-floor)})
                    if len(hits) >= 100:
                        break
            if len(hits) >= 100:
                break
        if len(hits) >= 100:
            break
    return {
        "target_id": "likou_x",
        "cut_solid_valid": bool(cut.isValid()),
        "cut_solid_count": len(cut.Solids()),
        "removed_volume_mm3": float(target.Volume() - cut.Volume()),
        "sampled_above_seat_floor_residual_count": len(hits),
        "sampled_residual_examples": hits[:20],
        "missing_board_samples": missing_board,
        "sampling": "one representative flyer; 4 q stations x 5 width stations x 12mm z interval",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=GENERATOR / "configs" / "default.json")
    parser.add_argument("--output", type=Path, default=GENERATOR / "diagnostics" / "v02" / "ab_straight_tail_probe_v02.json")
    parser.add_argument("--indices", type=int, nargs="+", default=None,
                        help="fan indices to probe; default is first, middle, and last")
    parser.add_argument("--skip-notch-probe", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    config = load_config(args.config)
    stage05 = load_stage05()
    ctx = stage05.build_stage04_base(config)
    case = ctx["case"]
    fan_records = sorted((record for record in ctx["chosen"] if record["family"] == "fan"),
                         key=lambda item: int(item["index"]))
    selected_indices = args.indices or sorted({int(fan_records[0]["index"]),
                                                int(fan_records[len(fan_records) // 2]["index"]),
                                                int(fan_records[-1]["index"])})
    chosen = [record for record in fan_records if int(record["index"]) in set(selected_indices)]
    if {int(record["index"]) for record in chosen} != set(selected_indices):
        raise RuntimeError(f"requested fan indices were not present: {selected_indices}")

    side_profiles = []
    for record in chosen:
        print(f"probe: A/B side stations for fan {record['index']}", flush=True)
        half_width = float(case["p"]["w_fly"]) / 2.0
        _, detail = stage05.build_contact_flyer(
            record, ctx["board_sampler"], case, ctx["end_curve"], ctx["selected_gamma"])
        sides = [probe_side(stage05, record, detail, ctx["board_sampler"], ctx["likou_sampler"], case, -half_width, "A(v=-w/2)"),
                 probe_side(stage05, record, detail, ctx["board_sampler"], ctx["likou_sampler"], case, half_width, "B(v=+w/2)")]
        side_profiles.append({"family": "fan", "index": int(record["index"]),
                              "source_id": record["source_rafter_id"], "sides": sides,
                              "dI_A_minus_B_mm": float(sides[0]["dI_world_Z_mm"] - sides[1]["dI_world_Z_mm"])})
        interim = {"case_id": config["case_id"], "selected_flyers": side_profiles,
                   "base_and_side_probe_elapsed_seconds": float(time.perf_counter() - started)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(interim, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    notch_probe = None
    if not args.skip_notch_probe:
        print("probe: single fan-06 likou seat residual section", flush=True)
        notch_probe = probe_notch_residual(stage05, chosen[1], ctx)
    report = {
        "case_id": config["case_id"],
        "source_config": str(args.config.resolve()),
        "construction_assumptions": {
            "I_lower_point": "actual lower-board top at each side's fitted I station after the existing 0.01mm ruled-edge safety fit",
            "I_thickness": "stage04 plane_depth_at evaluated at that side's actual q_I",
            "E_upper": "fly_top_at evaluated from each width-offset E point projected to the existing stage04 end curve",
            "R": "first inward intersection of the side-specific straight top motherline with actual lower-board top",
            "R_to_I_lower_edge": "straight line between actual board top at R and I; signed board gap/penetration is measured",
            "A_B_mapping": "A is v=-w_fly/2; B is v=+w_fly/2 along record.width_axis",
        },
        "selected_flyers": side_profiles,
        "old_geometry_likou_notch_probe": notch_probe,
        "profile_basis": "centerline_approximation",
        "base_and_probe_elapsed_seconds": float(time.perf_counter() - started),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    notch_residual_count = None if notch_probe is None else notch_probe.get(
        "sampled_above_seat_floor_residual_count",
        notch_probe.get("sampled_above_seat_floor_residual_count_capped"))
    print(json.dumps({"output": str(args.output.resolve()), "selected_indices": selected_indices,
                      "notch_residual_count": notch_residual_count,
                      "elapsed_seconds": report["base_and_probe_elapsed_seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
