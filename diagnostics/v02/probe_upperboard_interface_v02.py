#!/usr/bin/env python
"""Bounded BRep probe for the v0.2 width-aware upper roll-board interface."""
from __future__ import annotations

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
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
from OCP.gp import gp_Pnt


def load_stage05():
    spec = importlib.util.spec_from_file_location("upperboard_probe_stage05", ENGINE / "stage05_core.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load engine/stage05_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(shape, point):
    xyz = tuple(float(v) for v in point)
    classifier = BRepClass3d_SolidClassifier(
        shape.wrapped, gp_Pnt(*xyz), 1e-6)
    state = classifier.State()
    return "IN" if state == TopAbs_IN else ("OUT" if state == TopAbs_OUT else "ON")


def distance_to_brep(shape, point):
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*(float(v) for v in point))).Vertex()
    distance = BRepExtrema_DistShapeShape(vertex, shape.wrapped, 1e-5)
    if not distance.Perform():
        raise RuntimeError("BRep point-distance query did not complete")
    return float(distance.Value())


def interp_point(a, b, fraction):
    return [float(x + (y - x) * fraction) for x, y in zip(a, b)]


def sample_section_pair(rows, band, v_fraction=0.5):
    candidates = []
    for i in range(len(rows) - 1):
        first, second = rows[i], rows[i + 1]
        if first["region"] != second["region"]:
            continue
        if first["region"] != "outer_I_to_E":
            continue
        q_values = [float(first["q_low_mm"]), float(first["q_high_mm"]),
                    float(second["q_low_mm"]), float(second["q_high_mm"])]
        if min(q_values) > 1.0:
            candidates.append((i, min(q_values)))
    if not candidates:
        raise RuntimeError(f"band {band['band_index']} has no positive-q outer section interval")
    # Use a bounded middle interval, away from the I and E cuts.
    i = candidates[len(candidates) // 2][0]
    low0, high0 = rows[i]["points_low_to_high"]
    low1, high1 = rows[i + 1]["points_low_to_high"]
    low = interp_point(low0, low1, 0.5)
    high = interp_point(high0, high1, 0.5)
    point = interp_point(low, high, v_fraction)
    return point, i


def inspect_contact(stage05, detail, band, parts_by_id, offsets, underface,
                    v_fraction, side_name):
    rows = band["sections"]
    point, row_index = sample_section_pair(rows, band, v_fraction)
    band_index = int(band["band_index"])
    upperboard = next(item for item in parts_by_id.values()
                      if item["id"] == f"upper_board_support_x_fan_{int(detail['index']):02d}_band_{band_index + 1:02d}")
    flyer = parts_by_id[detail["part_ids"][0]]["shape"]
    board = upperboard["shape"]
    # The matching support lower face follows the same ruled row and width coordinates.
    board_bottom = [float(point[0]), float(point[1]), float(point[2] + underface)]
    epsilon = 0.02
    flyer_below = [point[0], point[1], point[2] - epsilon]
    flyer_above = [point[0], point[1], point[2] + epsilon]
    board_below = [board_bottom[0], board_bottom[1], board_bottom[2] - epsilon]
    board_above = [board_bottom[0], board_bottom[1], board_bottom[2] + epsilon]
    states = {
        "flyer_below_top": classify(flyer, flyer_below),
        "flyer_above_top": classify(flyer, flyer_above),
        "board_below_bottom": classify(board, board_below),
        "board_above_bottom": classify(board, board_above),
    }
    flyer_distance = distance_to_brep(flyer, point)
    board_distance = distance_to_brep(board, board_bottom)
    delta = float(board_bottom[2] - point[2])
    if side_name in ("A", "B"):
        classifier_passed = (states["flyer_below_top"] in ("IN", "ON") and
                             states["flyer_above_top"] == "OUT" and
                             states["board_below_bottom"] == "OUT" and
                             states["board_above_bottom"] in ("IN", "ON"))
    else:
        classifier_passed = (states["flyer_below_top"] == "IN" and
                             states["flyer_above_top"] == "OUT" and
                             states["board_below_bottom"] == "OUT" and
                             states["board_above_bottom"] == "IN")
    passed = (classifier_passed and flyer_distance <= 0.02 and board_distance <= 0.02 and
              abs(delta - underface) <= 0.02)
    return {
        "fan_index": int(detail["index"]), "band_index": band_index,
        "sample_kind": side_name, "section_pair_index": int(row_index),
        "width_fraction_in_band": float(v_fraction),
        "flyer_top_point_mm": [float(x) for x in point],
        "board_bottom_point_mm": board_bottom,
        "vertical_delta_mm": delta, "target_vertical_gap_mm": float(underface),
        "point_to_actual_flyer_brep_distance_mm": flyer_distance,
        "point_to_actual_board_bottom_brep_distance_mm": board_distance,
        "classifier_states": states, "passed": bool(passed),
    }


def main():
    config_path = GENERATOR / "configs" / "default.json"
    output_path = GENERATOR / "diagnostics" / "v02" / "upperboard_interface_probe_v02.json"
    started = time.perf_counter()
    config = load_config(config_path)
    stage05 = load_stage05()
    print("upper-board probe: stage04 base", flush=True)
    ctx = stage05.build_stage04_base(config)
    print("upper-board probe: build actual flyer BReps", flush=True)
    stage05.add_contact_flyers(ctx)
    print("upper-board probe: build/clamp local board pieces", flush=True)
    board_records = stage05.add_upper_rollboards(ctx)

    pieces_by_id = {part["id"]: part for part in ctx["parts"]}
    board_parts = [part for part in ctx["parts"] if part["group"] == "upper_board"]
    invalid = [part["id"] for part in board_parts
               if not part["shape"].isValid() or len(part["shape"].Solids()) != 1 or
               float(part["shape"].Volume()) <= 1e-5]
    max_mirror_bbox_error = 0.0
    max_mirror_volume_error = 0.0
    for item in board_records:
        pid_x, pid_y = item["part_ids"]
        shape_x, shape_y = pieces_by_id[pid_x]["shape"], pieces_by_id[pid_y]["shape"]
        bbox_error = stage05.reflected_bbox_error_mm(
            stage05.exact_brep_bbox_mm(shape_x), stage05.exact_brep_bbox_mm(shape_y))
        volume_error = abs(float(shape_x.Volume()) - float(shape_y.Volume()))
        max_mirror_bbox_error = max(max_mirror_bbox_error, float(bbox_error))
        max_mirror_volume_error = max(max_mirror_volume_error, float(volume_error))

    underface = float(ctx["case"]["p"]["upper_board_surface_overlap_mm"])
    selected = {1, 6, 11}
    fan_details = {int(item["index"]): item for item in ctx["contact_fly_records"]
                   if item["family"] == "fan"}
    offsets = {}
    for item in board_records:
        if item["piece_type"] != "support":
            continue
        fan_index, band_index = item["fan_indices"][0], item["band_index"]
        detail = fan_details[fan_index]
        band = detail["upper_surface_width_bands"][band_index]
        offsets.setdefault(fan_index, {})[band_index] = stage05._support_band_offsets(
            band["sections"], float(band["v_low_mm"]), float(band["v_high_mm"]),
            float(ctx["case"]["p"]["board_upper_t"]),
            f"fan {fan_index} band {band_index}")[0]
    # Apply the same one-sided seam average used by construction.
    for fan_index in selected:
        if fan_index not in fan_details:
            continue
        detail = fan_details[fan_index]
        band_count = len(detail["upper_surface_width_bands"])
        for band_index in range(band_count - 1):
            first, second = offsets[fan_index][band_index], offsets[fan_index][band_index + 1]
            for row_index in range(len(first)):
                shared = 0.5 * (float(first[row_index][1]) + float(second[row_index][0]))
                first[row_index] = (float(first[row_index][0]), shared)
                second[row_index] = (shared, float(second[row_index][1]))

    checks = []
    for fan_index in sorted(selected):
        if fan_index not in fan_details:
            continue
        detail = fan_details[fan_index]
        for band in detail["upper_surface_width_bands"]:
            band_index = int(band["band_index"])
            if band_index == 0:
                checks.append(inspect_contact(stage05, detail, band, pieces_by_id,
                                              offsets[fan_index], underface, 0.0, "A"))
            checks.append(inspect_contact(stage05, detail, band, pieces_by_id,
                                          offsets[fan_index], underface, 0.5, "band_interior"))
            if band_index == len(detail["upper_surface_width_bands"]) - 1:
                checks.append(inspect_contact(stage05, detail, band, pieces_by_id,
                                              offsets[fan_index], underface, 1.0, "B"))
    failed_checks = [item for item in checks if not item["passed"]]
    report = {
        "case_id": config["case_id"],
        "source_config": str(config_path.resolve()),
        "piece_count_x": len(board_records), "piece_count_both_wings": len(board_parts),
        "piece_type_counts_x": {kind: sum(item["piece_type"] == kind for item in board_records)
                                for kind in ("support", "gap", "border")},
        "all_board_parts_valid_single_solid_positive_volume": not invalid,
        "invalid_board_part_ids": invalid,
        "max_reflected_exact_bbox_error_mm": max_mirror_bbox_error,
        "max_reflected_volume_error_mm3": max_mirror_volume_error,
        "upper_board_domain": ctx["upper_board_domain"],
        "board_piece_records": board_records,
        "selected_fan_indices": sorted(selected),
        "actual_brep_contact_checks": checks,
        "failed_contact_checks": failed_checks,
        "all_contact_checks_passed": bool(checks) and not failed_checks,
        "build_elapsed_seconds": float(time.perf_counter() - started),
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(output_path.resolve()),
                      "piece_count_x": report["piece_count_x"],
                      "piece_type_counts_x": report["piece_type_counts_x"],
                      "contact_checks": len(checks),
                      "failed_contact_checks": len(failed_checks),
                      "invalid_board_part_ids": invalid,
                      "max_mirror_bbox_error_mm": max_mirror_bbox_error,
                      "elapsed_seconds": report["build_elapsed_seconds"]}, ensure_ascii=False), flush=True)
    if invalid or failed_checks or not board_records:
        raise RuntimeError("upper board local BRep/contact probe failed; see diagnostic JSON")


if __name__ == "__main__":
    main()
