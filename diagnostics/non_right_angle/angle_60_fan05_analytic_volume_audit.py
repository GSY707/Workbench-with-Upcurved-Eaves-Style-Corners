#!/usr/bin/env python
"""Bounded local fan-05 cutter audit for the 60-degree B400 case."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DIAG = Path(__file__).resolve().parent
HERE = DIAG.parents[1]
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "资料整理" / "tmp" / "model_deps"),
                str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
                str(HERE / "engine")]

import numpy as np

spec = importlib.util.spec_from_file_location("angle_audit_generate", HERE / "generate.py")
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)


def point3(xyz):
    return [float(c) for c in xyz]


def section_points(stage05, record, edges, q, upper_z, E_bottom_center):
    bottoms = []
    for edge in edges:
        v = float(edge["v_mm"])
        x, y = stage05.xy_at_q(record, q, v)
        z = stage05.bottom_outer_line(record, edge, q, v, E_bottom_center)
        bottoms.append((x, y, float(z)))
    return bottoms + [(x, y, float(upper_z)) for x, y, _ in reversed(bottoms)]


def face_area_from_shape(shape):
    return float(sum(face.Area() for face in shape.Faces()))


def main():
    started = datetime.now(timezone.utc).isoformat()
    clock_start = time.perf_counter()
    source_before = generate.production_source_identity()
    config = generate.load_config(HERE / "configs" / "angle_60.json")
    stage05 = generate.load_stage05()
    ctx = stage05.build_stage04_base(config)
    source_order, motherboard = stage05._build_flyer_masterboard(ctx)
    fan = next(r for r in ctx["chosen"] if r["family"] == "fan" and int(r["index"]) == 5)
    source_item = next(item for item in source_order
                       if item["record"]["source_rafter_id"] == fan["source_rafter_id"])
    flyer_shape, detail = stage05.build_contact_flyer(
        fan, ctx["board_sampler"], ctx["case"], ctx["end_curve"], ctx["selected_gamma"],
        masterboard_record=source_item)
    if not flyer_shape.isValid() or len(flyer_shape.Solids()) != 1:
        raise RuntimeError("fan05 flyer prerequisite is not one valid solid")
    target = next(p["shape"] for p in ctx["parts"] if p["id"] == "likou_x")
    edges = detail["I_front_edge_fiber_records"]
    if len(edges) != 17:
        raise RuntimeError(f"expected 17 source fibers, got {len(edges)}")

    stock_box = target.BoundingBox()
    upper_safety_mm = 5.0
    upper_z = float(stock_box.zmax) + upper_safety_mm
    S = np.asarray(fan["S"], dtype=float)
    U = np.asarray(fan["unit"], dtype=float)
    W = np.asarray(fan["width_axis"], dtype=float)
    stock_q = [(x - float(S[0])) * float(U[0]) +
               (y - float(S[1])) * float(U[1])
               for x in (stock_box.xmin, stock_box.xmax)
               for y in (stock_box.ymin, stock_box.ymax)]
    q_limits = (min(stock_q) - upper_safety_mm, max(stock_q) + upper_safety_mm)
    E_bottom_center = float(detail["E_bottom_center_mm"])
    upper_z = max(upper_z, max(
        stage05.bottom_outer_line(fan, edge, q, float(edge["v_mm"]), E_bottom_center)
        for q in q_limits for edge in edges) + upper_safety_mm)

    determinant = abs(float(U[0] * W[1] - U[1] * W[0]))
    q_span = float(q_limits[1] - q_limits[0])
    band_records = []
    band_shapes = []
    q_sections = [section_points(stage05, fan, edges, q, upper_z, E_bottom_center)
                  for q in q_limits]
    for index in range(len(edges) - 1):
        edge0, edge1 = edges[index], edges[index + 1]
        v0, v1 = float(edge0["v_mm"]), float(edge1["v_mm"])
        corners = []
        for q in q_limits:
            for edge in (edge0, edge1):
                v = float(edge["v_mm"])
                x, y = stage05.xy_at_q(fan, q, v)
                z = stage05.bottom_outer_line(fan, edge, q, v, E_bottom_center)
                corners.append((float(x), float(y), float(z)))
        mean_bottom_z = sum(p[2] for p in corners) / 4.0
        expected_volume = determinant * q_span * (v1 - v0) * (upper_z - mean_bottom_z)
        section0 = [q_sections[0][index], q_sections[0][index + 1],
                    q_sections[0][-(index + 2)], q_sections[0][-(index + 1)]]
        section1 = [q_sections[1][index], q_sections[1][index + 1],
                    q_sections[1][-(index + 2)], q_sections[1][-(index + 1)]]
        band = stage05.s2.loft_sections([section0, section1])
        band_shapes.append(band)
        band_records.append({
            "band_index": index,
            "v_interval_mm": [v0, v1],
            "four_bottom_corners_q0v0_q0v1_q1v0_q1v1_mm": [point3(p) for p in corners],
            "mean_four_corner_bottom_z_mm": mean_bottom_z,
            "expected_analytic_volume_mm3": expected_volume,
            "loft_brep_volume_mm3": float(band.Volume()),
            "loft_minus_expected_mm3": float(band.Volume()) - expected_volume,
            "valid": bool(band.isValid()),
            "solid_count": len(band.Solids()),
        })

    determinant_area = determinant
    internal_faces = []
    for seam in range(1, len(edges) - 1):
        edge = edges[seam]
        v = float(edge["v_mm"])
        bottom_ends = [stage05.bottom_outer_line(fan, edge, q, v, E_bottom_center)
                       for q in q_limits]
        expected_face_area = (float(np.linalg.norm(U)) * q_span *
                              (upper_z - 0.5 * sum(bottom_ends)))
        common = band_shapes[seam - 1].intersect(band_shapes[seam])
        internal_faces.append({
            "seam_index": seam,
            "v_mm": v,
            "bottom_z_q_limits_mm": [float(z) for z in bottom_ends],
            "expected_planar_common_face_area_mm2": expected_face_area,
            "brep_common_face_area_mm2": face_area_from_shape(common),
            "brep_common_face_count": len(common.Faces()),
            "brep_common_volume_mm3": float(common.Volume()),
            "brep_distance_mm": float(band_shapes[seam - 1].distance(band_shapes[seam])),
        })

    full_width = stage05.s2.loft_sections(q_sections)
    band_expected_sum = sum(r["expected_analytic_volume_mm3"] for r in band_records)
    band_brep_sum = sum(float(b.Volume()) for b in band_shapes)
    source_after = generate.production_source_identity()
    if source_after["sha256"] != source_before["sha256"]:
        raise RuntimeError("production source changed during analytic volume audit")
    report = {
        "case_id": config["case_id"],
        "corner_angle_deg": config["corner_angle_deg"],
        "config_path": "configs/angle_60.json",
        "source_identity_sha256": source_before["sha256"],
        "source_identity_unchanged": True,
        "source_files": source_before["files"],
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - clock_start,
        "scope": "single fan05 cutter field and original stage04 likou_x; not whole-model QA",
        "fan_index": 5,
        "q_limits_mm": [float(q_limits[0]), float(q_limits[1])],
        "upper_z_mm": float(upper_z),
        "width_fibers": len(edges),
        "band_count": len(band_records),
        "determinant_U_W": determinant_area,
        "expected_band_volume_sum_mm3": band_expected_sum,
        "brep_band_volume_sum_mm3": band_brep_sum,
        "brep_band_minus_expected_sum_mm3": band_brep_sum - band_expected_sum,
        "full_width_loft_volume_mm3": float(full_width.Volume()),
        "full_width_loft_minus_expected_sum_mm3": float(full_width.Volume()) - band_expected_sum,
        "full_width_loft_valid": bool(full_width.isValid()),
        "full_width_loft_solid_count": len(full_width.Solids()),
        "bands": band_records,
        "internal_seams": internal_faces,
    }
    out = DIAG / "angle_60_fan05_analytic_volume_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                   encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "source_identity_sha256", "elapsed_seconds", "q_limits_mm",
        "expected_band_volume_sum_mm3", "brep_band_volume_sum_mm3",
        "full_width_loft_volume_mm3", "band_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
