#!/usr/bin/env python
"""Parametric fifth-stage geometry: pointed flyers, combination stock, roll boards."""
from __future__ import annotations

import importlib.util
import math
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
STAGE_NAME = "飞椽贴板尖尾、菱角木与扁担木、卷戗板"

SPEC4 = importlib.util.spec_from_file_location("stage04_for_stage05", OUT / "stage04_core.py")
s4 = importlib.util.module_from_spec(SPEC4)
SPEC4.loader.exec_module(s4)
s2 = s4.s2
cq = s4.cq
np = s2.np


def add(parts, pid, name, group, shape):
    s2.add_part(parts, pid, name, group, shape)


def build_stage04_base(config):
    print(f"stage05: dynamic stage04 base start ({config.get('case_id')})", flush=True)
    ctx = s4.build_base_and_preflight(config)
    failures = ctx.get("pose_failures") or []
    expected = int(ctx["case"]["fan_geometry"]["fan_rafter_count"])
    actual = sum(record.get("family") == "fan" for record in ctx.get("chosen", []))
    if failures or actual != expected:
        details = "; ".join(
            f"index={item.get('index')} reason={item.get('reason')}"
            for item in failures
        ) or "未提供单根失败明细"
        raise ValueError(
            f"立脚飞椽预检不完整，拒绝静默漏根：按间距规则应有{expected}根/侧，"
            f"通过{actual}根/侧；失败{len(failures)}项。{details}"
        )
    s4.extend_likou(ctx)
    print(f"stage05: dynamic stage04 base ready ({len(ctx['parts'])} parts)", flush=True)
    return ctx


def center_at_q(record, q):
    return (float(record["S"][0]) + float(record["unit"][0]) * float(q),
            float(record["S"][1]) + float(record["unit"][1]) * float(q))


def flyer_depth_world_z_at(record, q):
    """Return the shared vertical depth at absolute flyer station q, independent of width fiber."""
    q_root = float(record.get("q_R0_mm", -float(record["ordinary_tail_mm"])))
    q_tip = float(record["l_SE_mm"])
    h_root = float(record.get(
        "root_thickness_R0_normal_mm",
        record.get("root_thickness_normal_mm", record["tip_thickness_normal_mm"])))
    h_tip = float(record["tip_thickness_normal_mm"])
    if q_tip - q_root <= 1e-12:
        raise ValueError(f"{record['family']} {record['index']}: root-to-tip thickness span is not positive")
    fraction = (float(q) - q_root) / (q_tip - q_root)
    thickness_normal = h_root + (h_tip - h_root) * fraction
    return float(thickness_normal * math.sqrt(1.0 + float(record.get("g", 0.0)) ** 2))


def fly_bottom_and_top(record, q, v):
    P = center_at_q(record, q)
    bottom = s4.sample_bottom_line(record, record["R"], record["bR_mm"], record["bE_mm"], P, v)
    depth = flyer_depth_world_z_at(record, q)
    top = bottom + depth
    return bottom, top


TAIL_WIDTH_STATIONS = 17
FRONT_EDGE_SEARCH_SPAN_MM = 160.0
FRONT_EDGE_COARSE_STEP_MM = 4.0
FRONT_EDGE_INBOARD_BIAS_MM = 0.05
TAIL_SCAN_STEP_MM = 5.0
TAIL_TARGET_CLOSURE_GAP_MM = 0.001
TAIL_KERNEL_CAP_FALLBACK_MM = 0.01
EDGE_CHORD_CLEARANCE_MM = 0.01
ROOT_CHORD_CLEARANCE_MM = 0.01
UPPERBOARD_DOMAIN_ENDPOINT_MARGIN_MM = 0.5


def width_band_count(record, case):
    """Refine cross-width bands at the corner end, scaled with dynamic count."""
    family = str(record["family"])
    index = int(record["index"])
    count = int(case["fan_geometry"]["fan_rafter_count"]) if family == "fan" else len(case["eave_rafters_geometry"]["rafters"])
    fraction = (index - 1) / max(1, count - 1)
    if family == "fan" and fraction > 0.84:
        return 8
    if family == "fan" and fraction > 0.70:
        return 4
    return 1


def width_interp(v, knots, values):
    return float(np.interp(float(v), np.asarray(knots, dtype=float), np.asarray(values, dtype=float)))


def xy_at_q(record, q, v=0.0):
    S = np.asarray(record["S"], dtype=float)
    U = np.asarray(record["unit"], dtype=float)
    W = np.asarray(record["width_axis"], dtype=float)
    P = S + U * float(q) + W * float(v)
    return float(P[0]), float(P[1])


def board_top_at(record, board_sampler, q, v):
    x, y = xy_at_q(record, q, v)
    hit = board_sampler.z_range(x, y)
    return None if hit is None else float(hit["max_z_mm"])


def find_actual_front_edge(record, board_sampler, v, case):
    """Find the board-coverage boundary nearest the Stage-04 I estimate.

    Coverage proceeds inward from this edge. The local interval nearest the
    prior I estimate is selected so a disconnected neighboring panel cannot
    silently redefine the contact datum.
    """
    S = np.asarray(record["S"], dtype=float)
    U = np.asarray(record["unit"], dtype=float)
    I = np.asarray(record["I"], dtype=float)
    q_guess = float(np.dot(I - S, U))
    search = float(case["p"]["flyer_front_edge_search_span_mm"])
    step = float(case["p"]["flyer_front_edge_coarse_step_mm"])
    offsets = np.arange(-search, search + 0.5 * step, step)
    qs = q_guess + offsets
    states = [board_top_at(record, board_sampler, q, v) is not None for q in qs]
    runs = []
    j = 0
    while j < len(states):
        if not states[j]:
            j += 1
            continue
        k = j
        while k + 1 < len(states) and states[k + 1]:
            k += 1
        runs.append((j, k))
        j = k + 1
    if not runs:
        raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: no lower-board coverage near I")

    def run_distance(run):
        a, b = float(qs[run[0]]), float(qs[run[1]])
        return 0.0 if a <= q_guess <= b else min(abs(q_guess - a), abs(q_guess - b))

    first, last = min(runs, key=run_distance)
    if last >= len(qs) - 1:
        raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: board edge lies beyond bounded search")
    q_inside, q_outside = float(qs[last]), float(qs[last + 1])
    # Refine the last covered point against the first uncovered point.
    for _ in range(20):
        q_mid = 0.5 * (q_inside + q_outside)
        if board_top_at(record, board_sampler, q_mid, v) is None:
            q_outside = q_mid
        else:
            q_inside = q_mid
        if q_outside - q_inside <= 0.005:
            break
    q_front = q_inside - float(case["p"]["flyer_edge_inboard_bias_mm"])
    h_front = board_top_at(record, board_sampler, q_front, v)
    if h_front is None:
        raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: configured inboard bias missed board")
    return {"v_mm": float(v), "q_front_mm": float(q_front), "H_front_mm": float(h_front),
            "q_edge_bracket_mm": [float(q_inside), float(q_outside)],
            "bracket_width_mm": float(q_outside - q_inside),
            "prior_I_q_mm": float(q_guess)}


def _cross2(a, b):
    return float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])


def _line_polyline_intersections(origin, direction, path, label):
    """Return unique intersections of a longitudinal flyer plane with a control path."""
    origin = np.asarray(origin, dtype=float)
    direction = np.asarray(direction, dtype=float)
    hits = []
    for segment_index, (point_a, point_b) in enumerate(zip(path[:-1], path[1:])):
        point_a = np.asarray(point_a, dtype=float)
        delta = np.asarray(point_b, dtype=float) - point_a
        denominator = _cross2(direction, delta)
        if abs(denominator) <= 1e-11:
            continue
        offset = point_a - origin
        q = _cross2(offset, delta) / denominator
        fraction = _cross2(offset, direction) / denominator
        if -1e-9 <= fraction <= 1.0 + 1e-9:
            point = origin + q * direction
            hit = {"segment_index": int(segment_index), "q_mm": float(q),
                   "segment_fraction": float(fraction),
                   "point_xy_mm": [float(point[0]), float(point[1])]}
            existing = next((item for item in hits
                             if math.dist(item["point_xy_mm"], hit["point_xy_mm"]) <= 1e-5), None)
            if existing is None:
                hits.append(hit)
    if len(hits) != 1:
        points = [item["point_xy_mm"] for item in hits]
        raise ValueError(
            f"{label}: 控制线应有且仅有一个路径交点，实际{len(hits)}个；交点={points}")
    return hits[0]


def _monotonic_sign(values, label, tolerance_mm=1e-5):
    differences = np.diff(np.asarray(values, dtype=float))
    if not len(differences):
        raise ValueError(f"{label}: 横向控制站不足")
    if np.all(differences > tolerance_mm):
        return 1
    if np.all(differences < -tolerance_mm):
        return -1
    bad = next((index for index, value in enumerate(differences)
                if abs(float(value)) <= tolerance_mm or
                (float(value) > 0.0) != (float(differences[0]) > 0.0)), 0)
    raise ValueError(
        f"{label}: 横向控制路径站不严格单调；纤维{bad}->{bad + 1}的差为"
        f"{float(differences[bad]):.8g}mm")


def _minimum_ruled_separation(point0_start, point1_start, point0_end, point1_end):
    """Minimum XY gap between two rails linearly ruled over one longitudinal phase."""
    start = np.asarray(point1_start, dtype=float)[:2] - np.asarray(point0_start, dtype=float)[:2]
    end = np.asarray(point1_end, dtype=float)[:2] - np.asarray(point0_end, dtype=float)[:2]
    delta = end - start
    denominator = float(np.dot(delta, delta))
    fraction = (0.0 if denominator <= 1e-18 else
                min(1.0, max(0.0, -float(np.dot(start, delta)) / denominator)))
    return float(np.linalg.norm(start + fraction * delta)), float(fraction)


def _common_flyer_boundaries(ctx):
    """Build the common front/end control paths, including straight-bank extensions."""
    case = ctx["case"]
    p = case["p"]
    half_width = 0.5 * float(p["w_fly"])
    straight = [record for record in ctx["chosen"] if record["family"] == "straight"]
    candidate_x = [float(record["S"][0]) + float(record["width_axis"][0]) * v
                   for record in straight for v in (-half_width, half_width)]
    x_min = min(candidate_x, default=min(float(point[0]) for point in ctx["inner_path"]))
    x_min = min(x_min, min(float(point[0]) for point in ctx["inner_path"])) - 5.0
    inner_path = [(float(x), float(y)) for x, y in ctx["inner_path"]]
    end_curve = ctx["end_curve"]
    front_y = float(case["e"]) - float(p["likou_width"])
    outer_y = float(case["e"]) + float(p["p"])
    likou_y = float(case["e"])
    boundary_x = float(case["old_start"][0])
    front_path = [(float(x_min), front_y), *inner_path]
    outer_path = [(float(x_min), outer_y), (boundary_x, outer_y)]
    outer_path.extend((float(x), float(y)) for x, y in end_curve["points"][1:])
    likou_outer_path = [(float(x_min), likou_y),
                        *((float(x), float(y)) for x, y in ctx["path"])]
    extension = {
        "method": "extend the existing common inner path and end curve with straight-bank control lines",
        "front_extension_xy_mm": [[float(x_min), front_y], list(inner_path[0])],
        "front_extension_length_mm": float(math.dist((x_min, front_y), inner_path[0])),
        "outer_extension_xy_mm": [[float(x_min), outer_y], [boundary_x, outer_y]],
        "outer_extension_length_mm": float(math.dist(
            (x_min, outer_y), (boundary_x, outer_y))),
        "likou_outer_extension_xy_mm": [[float(x_min), likou_y],
                                         [float(ctx["path"][0][0]), float(ctx["path"][0][1])]],
        "likou_outer_extension_length_mm": float(math.dist(
            (x_min, likou_y), (float(ctx["path"][0][0]), float(ctx["path"][0][1])))),
        "likou_extension_actual_outer_edge_x_mm": float(
            ctx.get("likou_extension", {}).get("outer_edge_x_mm", x_min)),
        "front_extension_x_min_mm": float(x_min),
        "front_extension_y_mm": float(front_y),
        "outer_extension_y_mm": float(outer_y),
    }
    return front_path, outer_path, likou_outer_path, extension


def _build_flyer_masterboard(ctx):
    """Create one continuous, piecewise-ruled control field before building any flyer solid."""
    case = ctx["case"]
    p = case["p"]
    half_width = 0.5 * float(p["w_fly"])
    width_stations = int(p["flyer_width_fiber_stations"])
    v_values = [float(value) for value in np.linspace(-half_width, half_width, width_stations)]
    front_path, outer_path, likou_outer_path, path_extension = _common_flyer_boundaries(ctx)
    source_records = list(ctx["chosen"])
    source_order = []
    for record in source_records:
        family, index = str(record["family"]), int(record["index"])
        label = f"{family} {index:02d}"
        S = np.asarray(record["S"], dtype=float)
        U = np.asarray(record["unit"], dtype=float)
        W = np.asarray(record["width_axis"], dtype=float)
        q_R0 = float(record.get("q_R0_mm", -float(record["ordinary_tail_mm"])))
        raw_controls = []
        for fiber_index, v in enumerate(v_values):
            origin = S + W * float(v)
            inner_hit = _line_polyline_intersections(
                origin, U, front_path, f"{label} v={v:.3f} inner control")
            tip_hit = _line_polyline_intersections(
                origin, U, outer_path, f"{label} v={v:.3f} tip control")
            seat_hit = _line_polyline_intersections(
                origin, U, likou_outer_path, f"{label} v={v:.3f} likou outer edge")
            q_path = float(inner_hit["q_mm"])
            q_i = q_path - float(p["flyer_edge_inboard_bias_mm"])
            i_xy = S + U * q_i + W * float(v)
            h_i = board_top_at(record, ctx["board_sampler"], q_i, float(v))
            if h_i is None:
                raise ValueError(
                    f"{label} v={v:.3f}: shared inner-path I lies outside lower-board coverage "
                    f"after {float(p['flyer_edge_inboard_bias_mm']):.4f}mm inboard bias")
            q_e = float(tip_hit["q_mm"])
            e_xy = np.asarray(tip_hit["point_xy_mm"], dtype=float)
            xy_from_q = np.asarray(xy_at_q(record, q_e, float(v)), dtype=float)
            if float(np.linalg.norm(xy_from_q - e_xy)) > 1e-4:
                raise ValueError(f"{label} v={v:.3f}: tip intersection is outside its longitudinal plane")
            e_bottom, _ = fly_bottom_and_top(record, q_e, float(v))
            if family == "fan":
                fraction = float(s4.arc_fraction_at_point(e_xy, ctx["end_curve"]))
                e_top = float(s4.fly_top_at(e_xy, fraction, float(ctx["selected_gamma"]), ctx["end_curve"]))
                e_source = "shared outer end-curve intersection and fly_top_at control curve"
            else:
                fraction = float(record.get("outer_curve_fraction", 0.0))
                e_depth = flyer_depth_world_z_at(record, q_e)
                e_top = float(e_bottom + e_depth)
                e_source = "stage04 tip-thickness plane evaluated on shared tip boundary"
            if family == "fan":
                e_depth = flyer_depth_world_z_at(record, q_e)
                e_bottom = float(e_top - e_depth)
                e_source += "; lower surface follows the same q-based thickness field"
            d_i = flyer_depth_world_z_at(record, q_i)
            s_hit = seat_hit
            q_s = float(s_hit["q_mm"])
            s_xy = np.asarray(s_hit["point_xy_mm"], dtype=float)
            inner_station = float(s4.find_path_station(inner_hit["point_xy_mm"], front_path))
            outer_station = float(s4.find_path_station(tip_hit["point_xy_mm"], outer_path))
            seat_station = float(s4.find_path_station(s_hit["point_xy_mm"], likou_outer_path))
            if not (q_i < q_s < q_e):
                raise ValueError(
                    f"{label} v={v:.3f}: longitudinal boundaries are not ordered "
                    f"I/S/E ({q_i:.5f}, {q_s:.5f}, {q_e:.5f})")
            raw_controls.append({
                "fiber_index": int(fiber_index), "v_mm": float(v),
                "q_front_mm": float(q_i), "q_front_path_hit_mm": float(q_path),
                "q_E_mm": float(q_e), "q_S_mm": float(q_s),
                "H_front_mm": float(h_i), "E_bottom_z_mm": float(e_bottom),
                "E_top_z_mm": float(e_top), "E_depth_world_Z_mm": float(e_top - e_bottom),
                "E_xy_mm": [float(e_xy[0]), float(e_xy[1])],
                "E_top_source": e_source, "E_curve_fraction": float(fraction),
                "S_xy_mm": [float(s_xy[0]), float(s_xy[1])],
                "inner_path_point_xy_mm": [float(c) for c in inner_hit["point_xy_mm"]],
                "outer_path_point_xy_mm": [float(c) for c in tip_hit["point_xy_mm"]],
                "inner_path_station_mm": inner_station,
                "outer_path_station_mm": outer_station,
                "likou_outer_path_station_mm": seat_station,
                "inner_intersection_count": 1, "tip_intersection_count": 1,
                "likou_outer_intersection_count": 1,
                "inner_segment_index": int(inner_hit["segment_index"]),
                "tip_segment_index": int(tip_hit["segment_index"]),
                "likou_outer_segment_index": int(seat_hit["segment_index"]),
                "dI_world_Z_mm": d_i,
            })

        inner_stations = [item["inner_path_station_mm"] for item in raw_controls]
        outer_stations = [item["outer_path_station_mm"] for item in raw_controls]
        inner_sign = _monotonic_sign(inner_stations, f"{label} inner-boundary W_i")
        outer_sign = _monotonic_sign(outer_stations, f"{label} tip-boundary W_i")
        if inner_sign != outer_sign:
            raise ValueError(
                f"{label}: I/E 横向带域方向相反 (inner sign={inner_sign}, tip sign={outer_sign})")
        for item in raw_controls:
            item["I_upper_z_mm"] = float(item["H_front_mm"] + item["dI_world_Z_mm"])

        edge = {**record, "_masterboard_controls": raw_controls,
                "_masterboard_front_sign": int(inner_sign),
                "_masterboard_outer_sign": int(outer_sign)}
        center = raw_controls[len(raw_controls) // 2]
        center_edge = {**center}
        E_bottom_center = float(center["E_bottom_z_mm"])
        E_depth_center = float(center["E_depth_world_Z_mm"])
        max_tail_length = float(p["flyer_tail_root_max_search_mm"])
        roots = []
        for control in raw_controls:
            endpoint = find_tail_endpoint(
                edge, ctx["board_sampler"], control,
                float(control["dI_world_Z_mm"]), E_bottom_center, E_depth_center,
                max_tail_length, case)
            q_root = float(endpoint["q_root_mm"])
            h_root = board_top_at(record, ctx["board_sampler"], q_root, float(control["v_mm"]))
            if h_root is None:
                raise ValueError(
                    f"{label} v={control['v_mm']:.3f}: shared tail root is outside lower-board coverage")
            top_root = top_motherline(
                edge, control, float(control["dI_world_Z_mm"]), q_root,
                float(control["v_mm"]), E_bottom_center, E_depth_center)
            root_gap = float(top_root - h_root)
            if root_gap <= 0.0:
                raise ValueError(
                    f"{label} v={control['v_mm']:.3f}: shared top generator crosses board at R by "
                    f"{-root_gap:.6g}mm")
            root_bottom = float(top_root - flyer_depth_world_z_at(record, q_root))
            control["R_xy_mm"] = [float(c) for c in xy_at_q(record, q_root, float(control["v_mm"]))]
            control["R_bottom_z_mm"] = root_bottom
            control["R_board_top_z_mm"] = float(h_root)
            control["R_top_z_mm"] = float(top_root)
            control["R_member_bottom_z_mm"] = root_bottom
            control["root_gap_mm"] = root_gap
            roots.append({**endpoint, "v_mm": float(control["v_mm"]),
                          "H_R_board_mm": float(h_root), "R_top_z_mm": float(top_root),
                          "R_bottom_z_mm": root_bottom,
                          "thickness_world_Z_mm": float(flyer_depth_world_z_at(record, q_root)),
                          "root_gap_mm": root_gap})

        source_order.append({
            "record": record, "controls": raw_controls, "roots": roots,
            "inner_station_span_mm": [float(min(inner_stations)), float(max(inner_stations))],
            "outer_station_span_mm": [float(min(outer_stations)), float(max(outer_stations))],
            "inner_sign": int(inner_sign), "outer_sign": int(outer_sign),
            "center_q_E_mm": float(center["q_E_mm"]),
            "center_q_S_mm": float(center["q_S_mm"]),
            "center_q_I_mm": float(center["q_front_mm"]),
            "center_H_I_mm": float(center["H_front_mm"]),
            "center_dI_mm": float(center["dI_world_Z_mm"]),
            "center_E_bottom_mm": E_bottom_center,
            "center_E_top_mm": float(center["E_top_z_mm"]),
        })

    source_order.sort(key=lambda item: item["inner_station_span_mm"][0])
    outer_order = sorted(source_order, key=lambda item: item["outer_station_span_mm"][0])
    if [item["record"]["source_rafter_id"] for item in source_order] != [
            item["record"]["source_rafter_id"] for item in outer_order]:
        raise ValueError("全翼 W_i 顺序在共同内边界与梢端控制边界之间反转")
    gaps = []
    minimum_boundary_gap = float("inf")
    for index, (left, right) in enumerate(zip(source_order[:-1], source_order[1:])):
        inner_gap = float(right["inner_station_span_mm"][0] - left["inner_station_span_mm"][1])
        outer_gap = float(right["outer_station_span_mm"][0] - left["outer_station_span_mm"][1])
        if inner_gap <= 1e-4 or outer_gap <= 1e-4:
            raise ValueError(
                f"global band order overlap at {left['record']['source_rafter_id']} / "
                f"{right['record']['source_rafter_id']}: inner={inner_gap:.6g}mm outer={outer_gap:.6g}mm")
        minimum_boundary_gap = min(minimum_boundary_gap, inner_gap, outer_gap)
        gaps.append({"gap_index": int(index), "left_source_rafter_id": left["record"]["source_rafter_id"],
                     "right_source_rafter_id": right["record"]["source_rafter_id"],
                     "inner_boundary_gap_mm": inner_gap, "tip_boundary_gap_mm": outer_gap})

    def rail_points(item, fiber_index):
        control = item["controls"][fiber_index]
        return {
            "R_bottom": [*control["R_xy_mm"], float(control["R_bottom_z_mm"])],
            "R_top": [*control["R_xy_mm"], float(control["R_top_z_mm"])],
            "I_bottom": [*xy_at_q(item["record"], control["q_front_mm"], control["v_mm"]),
                         float(control["H_front_mm"])],
            "I_top": [*xy_at_q(item["record"], control["q_front_mm"], control["v_mm"]),
                      float(control["I_upper_z_mm"])],
            "E_bottom": [*control["E_xy_mm"], float(control["E_bottom_z_mm"])],
            "E_top": [*control["E_xy_mm"], float(control["E_top_z_mm"])],
        }

    member_patches = []
    field_rails = []
    member_by_id = {}
    minimum_field_gap = float("inf")
    for member_order, item in enumerate(source_order):
        record = item["record"]
        item["member_order"] = int(member_order)
        key = str(record["source_rafter_id"])
        member_by_id[record["source_rafter_id"]] = item
        rail_ids = []
        for fiber_index, control in enumerate(item["controls"]):
            rail_id = f"{key}_v{fiber_index:02d}"
            rail_ids.append(rail_id)
            control["masterboard_rail_id"] = rail_id
            field_rails.append({
                "rail_id": rail_id, "member_order": int(member_order),
                "source_rafter_id": record["source_rafter_id"],
                "family": str(record["family"]), "index": int(record["index"]),
                "role": str(record.get("role", "fan" if record["family"] == "fan" else "regular")),
                "is_boundary": bool(record.get("is_boundary", False)),
                "fiber_index": int(fiber_index), "v_mm": float(control["v_mm"]),
                "q_I_mm": float(control["q_front_mm"]), "q_S_mm": float(control["q_S_mm"]),
                "q_E_mm": float(control["q_E_mm"]),
                **rail_points(item, fiber_index),
            })
        for fiber_index in range(len(item["controls"]) - 1):
            left = item["controls"][fiber_index]
            right = item["controls"][fiber_index + 1]
            for phase_name, start_fields, end_fields in (
                    ("tail_R_to_I", ("R_bottom", "R_top"), ("I_bottom", "I_top")),
                    ("outer_I_to_E", ("I_bottom", "I_top"), ("E_bottom", "E_top"))):
                left_points = rail_points(item, fiber_index)
                right_points = rail_points(item, fiber_index + 1)
                for surface_name, start_field, end_field in zip(
                        ("bottom", "top"), start_fields, end_fields):
                    separation, phase_fraction = _minimum_ruled_separation(
                        left_points[start_field], right_points[start_field],
                        left_points[end_field], right_points[end_field])
                    minimum_field_gap = min(minimum_field_gap, separation)
                    if separation <= 1e-3:
                        raise ValueError(
                    f"{key} width patch {fiber_index} {phase_name} {surface_name}: "
                            f"相邻控制轨折返/交叉，最小XY间距={separation:.6g}mm "
                            f"(lambda={phase_fraction:.6g})")
                member_patches.append({
                    "patch_id": f"member_{key}_band_{fiber_index:02d}_{phase_name}",
                    "kind": "member_band", "region": phase_name,
                    "source_rafter_id": record["source_rafter_id"],
                    "member_index": int(record["index"]),
                    "left_rail_id": rail_ids[fiber_index],
                    "right_rail_id": rail_ids[fiber_index + 1],
                    "v_domain_mm": [float(left["v_mm"]), float(right["v_mm"])],
                    "interpolation": "bilinear across the adjacent shared rails; each rail is piecewise straight in its longitudinal phase",
                })

    gap_patches = []
    seam_error_max = 0.0
    for gap_index, gap in enumerate(gaps):
        left_item = member_by_id[gap["left_source_rafter_id"]]
        right_item = member_by_id[gap["right_source_rafter_id"]]
        left_control = max(left_item["controls"], key=lambda control: control["inner_path_station_mm"])
        right_control = min(right_item["controls"], key=lambda control: control["inner_path_station_mm"])
        left_index = int(left_control["fiber_index"])
        right_index = int(right_control["fiber_index"])
        left_rail_id = left_control["masterboard_rail_id"]
        right_rail_id = right_control["masterboard_rail_id"]
        left_points = rail_points(left_item, left_index)
        right_points = rail_points(right_item, right_index)
        for phase_name, start_fields, end_fields in (
                ("tail_R_to_I", ("R_bottom", "R_top"), ("I_bottom", "I_top")),
                ("outer_I_to_E", ("I_bottom", "I_top"), ("E_bottom", "E_top"))):
            for surface_name, start_field, end_field in zip(
                    ("bottom", "top"), start_fields, end_fields):
                separation, phase_fraction = _minimum_ruled_separation(
                    left_points[start_field], right_points[start_field],
                    left_points[end_field], right_points[end_field])
                minimum_field_gap = min(minimum_field_gap, separation)
                if separation <= 1e-3:
                    raise ValueError(
                        f"gap {gap_index} {phase_name} {surface_name}: 相邻 W_i 外侧轨交叉/退化，"
                        f"最小XY间距={separation:.6g}mm (lambda={phase_fraction:.6g})")
        virtual_rails = []
        for lateral_fraction in np.linspace(0.0, 1.0, 5):
            fraction = float(lateral_fraction)
            rail = {"lateral_fraction": fraction}
            for field in ("R_bottom", "R_top", "I_bottom", "I_top", "E_bottom", "E_top"):
                a, b = np.asarray(left_points[field], dtype=float), np.asarray(right_points[field], dtype=float)
                rail[field] = [float(c) for c in (a * (1.0 - fraction) + b * fraction)]
            virtual_rails.append(rail)
        gap_patch = {
            "patch_id": f"gap_{gap_index:02d}_{gap['left_source_rafter_id']}_{gap['right_source_rafter_id']}",
            "kind": "inter-rafter_gap", "left_member_source_rafter_id": gap["left_source_rafter_id"],
            "right_member_source_rafter_id": gap["right_source_rafter_id"],
            "left_shared_rail_id": left_rail_id, "right_shared_rail_id": right_rail_id,
            "inner_boundary_gap_mm": float(gap["inner_boundary_gap_mm"]),
            "tip_boundary_gap_mm": float(gap["tip_boundary_gap_mm"]),
            "virtual_rail_count": len(virtual_rails), "virtual_rails": virtual_rails,
            "interpolation": "the same shared member-edge rails bound a linearly interpolated gap patch in both R-to-I and I-to-E regions",
        }
        # The member edge and gap patch use exactly the same endpoint records.
        for field in ("R_bottom", "R_top", "I_bottom", "I_top", "E_bottom", "E_top"):
            seam_error_max = max(seam_error_max,
                                 float(np.linalg.norm(np.asarray(virtual_rails[0][field]) -
                                                      np.asarray(left_points[field]))),
                                 float(np.linalg.norm(np.asarray(virtual_rails[-1][field]) -
                                                      np.asarray(right_points[field]))))
        gap_patches.append(gap_patch)

    member_records = []
    max_qe_span = 0.0
    q_e_spans = []
    for item in source_order:
        record = item["record"]
        q_e_values = [float(control["q_E_mm"]) for control in item["controls"]]
        q_e_span = float(max(q_e_values) - min(q_e_values))
        max_qe_span = max(max_qe_span, q_e_span)
        q_e_spans.append({"source_rafter_id": record["source_rafter_id"],
                          "family": str(record["family"]), "index": int(record["index"]),
                          "q_E_range_mm": [float(min(q_e_values)), float(max(q_e_values))],
                          "q_E_span_mm": q_e_span})
        member_records.append({
            "member_order": len(member_records), "source_rafter_id": record["source_rafter_id"],
            "family": str(record["family"]), "index": int(record["index"]),
            "role": str(record.get("role", "fan" if record["family"] == "fan" else "regular")),
            "is_boundary": bool(record.get("is_boundary", False)),
            "source_centerline_controls": {
                "S_xy_mm": [float(c) for c in record["S"]],
                "E_xy_mm": [float(c) for c in record["E"]],
                "unit_xy": [float(c) for c in record["unit"]],
                "width_axis_xy": [float(c) for c in record["width_axis"]],
                "I_center_input_xy_mm": [float(c) for c in record["I"]],
                "q_I_input_mm": float(record.get("q_I_mm", 0.0)),
                "l_SE_input_mm": float(record["l_SE_mm"]),
                "root_thickness_normal_mm": float(record.get("root_thickness_normal_mm", 0.0)),
                "tip_thickness_normal_mm": float(record.get("tip_thickness_normal_mm", 0.0)),
            },
            "W_i": {
                "v_domain_mm": [-half_width, half_width],
                "longitudinal_boundaries": "q_I(v) from the shared front path; q_E(v) from the shared tip path",
                "side_A_rail_id": item["controls"][0]["masterboard_rail_id"],
                "side_B_rail_id": item["controls"][-1]["masterboard_rail_id"],
                "inner_station_span_mm": item["inner_station_span_mm"],
                "tip_station_span_mm": item["outer_station_span_mm"],
                "inner_station_order_sign": int(item["inner_sign"]),
                "tip_station_order_sign": int(item["outer_sign"]),
                "intersection_counts": {"inner_each_fiber": 1, "tip_each_fiber": 1,
                                         "likou_outer_each_fiber": 1},
                "q_E_range_mm": [float(min(q_e_values)), float(max(q_e_values))],
                "q_E_span_mm": q_e_span,
            },
            "member_rail_ids": [control["masterboard_rail_id"] for control in item["controls"]],
            "member_band_patch_ids": [patch["patch_id"] for patch in member_patches
                                      if patch["source_rafter_id"] == record["source_rafter_id"]],
        })

    motherboard = {
        "schema_version": "shared_flyer_masterboard_v1",
        "construction": "one continuous piecewise-ruled virtual motherboard is defined from centerline input controls, shared front/tip boundary paths, and ordered lateral rails; each physical flyer is extracted as M intersect W_i",
        "surface_formula": "each shared upper rail is straight in q across R-I and I-E; the lower surface is upper(q,v)-T(q), with one linear root-to-tip T(q) shared across width; member bands and gaps interpolate the same adjacent rails",
        "not_a_physical_board": True,
        "source_control_layer": "existing stage04 centerline I/E, unit/width axes, root/tip thickness, stage03 board sampler, and configured transition power",
        "thickness_field": {
            "rule": "for each source flyer member, one thickness T(q) at a given absolute longitudinal station is shared by all width fibers",
            "interpolation": "linear from each source member q_R0 to l_SE using its existing root/tip thickness controls",
            "world_Z_conversion": "normal thickness multiplied by sqrt(1 + g^2)",
            "gap_rule": "inter-rafter gap rails interpolate the already-defined adjacent member boundary rails",
        },
        "shared_boundary_paths": {
            "front_inner_xy_mm": [[float(c) for c in point] for point in front_path],
            "tip_outer_xy_mm": [[float(c) for c in point] for point in outer_path],
            "likou_outer_xy_mm": [[float(c) for c in point] for point in likou_outer_path],
            "path_extension": path_extension,
            "i_edge_inboard_bias_mm": float(p["flyer_edge_inboard_bias_mm"]),
        },
        "member_order_by_shared_boundary": [item["record"]["source_rafter_id"] for item in source_order],
        "member_domains": member_records,
        "shared_rails": field_rails,
        "member_band_patches": member_patches,
        "gap_patches": gap_patches,
        "guard_results": {
            "unique_intersections_per_fiber": True,
            "strictly_monotonic_member_width_stations": True,
            "inner_tip_member_order_matches": True,
            "member_and_gap_rails_non_crossing": True,
            "minimum_boundary_gap_mm": float(minimum_boundary_gap),
            "minimum_ruled_xy_rail_separation_mm": float(minimum_field_gap),
            "maximum_shared_seam_endpoint_error_mm": float(seam_error_max),
        },
        "q_E_side_spans": q_e_spans,
        "maximum_q_E_side_span_mm": float(max_qe_span),
        "gap_filling_rule": "inter-rafter patches linearly interpolate the adjacent shared edge rails, including their R, I, and E controls; no member is built independently before this field exists",
        "likou_cutter_rule": "each cutter reuses the corresponding member W_i and the same I-to-E lower motherboard surface upper(q,v)-T(q) through its complete real likou intersection",
        "physical_member_width_stations": int(width_stations),
        "physical_member_band_count": int(width_stations - 1),
        "local_field_patch_count": int(len(member_patches) + 2 * len(gap_patches)),
    }
    return source_order, motherboard


def linear_value_at(q, q0, z0, q1, z1):
    """Evaluate one straight longitudinal profile, with linear extrapolation."""
    span = float(q1) - float(q0)
    if abs(span) <= 1e-12:
        raise ValueError("纵向直线端点重合")
    return float(z0) + (float(z1) - float(z0)) * (float(q) - float(q0)) / span


def top_motherline(record, edge, dI_z, q, v, E_bottom_center, E_depth_z):
    q_e = float(edge.get("q_E_mm", record["l_SE_mm"]))
    lam = (float(q) - float(edge["q_front_mm"])) / (q_e - float(edge["q_front_mm"]))
    side_dI = float(edge.get("dI_world_Z_mm", dI_z))
    edge_top = float(edge["H_front_mm"]) + side_dI
    if "E_top_z_mm" in edge:
        end_top = float(edge["E_top_z_mm"])
    else:
        end_top = (float(E_bottom_center) + float(record["g"]) * float(v)
                   + float(E_depth_z))
    return (1.0 - lam) * edge_top + lam * end_top


def bottom_outer_line(record, edge, q, v, E_bottom_center):
    top = top_motherline(record, edge, float(edge.get("dI_world_Z_mm", 0.0)),
                         q, v, E_bottom_center,
                         float(edge.get("E_depth_world_Z_mm", 0.0)))
    return float(top - flyer_depth_world_z_at(record, q))


def tail_bottom_line(q, edge, root):
    """Straight R-to-I lower edge matching the shared top field minus T(q)."""
    root_bottom = float(root.get("R_bottom_z_mm", root["H_R_board_mm"]))
    return linear_value_at(q, root["q_root_mm"], root_bottom,
                           edge["q_front_mm"], edge["H_front_mm"])


def exact_brep_bbox_mm(shape):
    """Return exact-geometry BRep bounds, ignoring cached face triangulations."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    if box.IsVoid():
        raise ValueError("无法从BRep计算精确包围盒")
    return tuple(float(value) for value in box.Get())


def reflected_bbox_error_mm(source_bbox, reflected_bbox):
    """Compare exact bounds after the angle-aware rigid reflection was applied."""
    return max(abs(float(a) - float(b)) for a, b in zip(source_bbox, reflected_bbox))


def find_tail_endpoint(record, board_sampler, edge, dI_z, E_bottom_center, E_depth_z, max_length, case):
    qI = float(edge["q_front_mm"])
    v = float(edge["v_mm"])
    gap_target = TAIL_TARGET_CLOSURE_GAP_MM

    def gap_at(q):
        h = board_top_at(record, board_sampler, q, v)
        if h is None:
            return None
        return top_motherline(record, edge, dI_z, q, v, E_bottom_center, E_depth_z) - h

    q_hi = qI
    g_hi = gap_at(q_hi)
    if g_hi is None or g_hi <= gap_target:
        raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: I has no positive flyer depth ({g_hi})")
    step = float(case["p"]["flyer_tail_search_step_mm"])
    count = int(math.ceil(float(max_length) / step))
    bracket = None
    for j in range(1, count + 1):
        q_lo = qI - j * step
        g_lo = gap_at(q_lo)
        if g_lo is None:
            raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: lower-board mesh missing before pointed tail root q={q_lo:.3f}")
        if g_lo <= gap_target:
            bracket = (q_lo, g_lo, q_hi, g_hi)
            break
        q_hi, g_hi = q_lo, g_lo
    if bracket is None:
        raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: no pointed tail root within {max_length:.1f}mm")

    q_lo, g_lo, q_hi, g_hi = bracket
    # q_lo is farther inboard; find the first positive-domain endpoint.
    for _ in range(24):
        q_mid = 0.5 * (q_lo + q_hi)
        g_mid = gap_at(q_mid)
        if g_mid is None:
            raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: board coverage missing while refining tail root")
        if g_mid > gap_target:
            q_hi, g_hi = q_mid, g_mid
        else:
            q_lo, g_lo = q_mid, g_mid
        if q_hi - q_lo <= 0.002:
            break
    q_root, gap_root = (q_hi, g_hi) if abs(g_hi - gap_target) <= abs(g_lo - gap_target) else (q_lo, g_lo)
    return {"q_root_mm": float(q_root), "root_gap_mm": float(gap_root),
            "tail_length_from_I_mm": float(qI - q_root),
            "tail_length_from_S_mm": float(-q_root)}


def _build_contact_flyer_legacy(record, board_sampler, case, end_curve=None, gamma=None):
    """Retained temporarily for source comparison; the active path uses the shared field below."""
    build_started = time.perf_counter()
    p = case["p"]
    half_width = float(p["w_fly"]) / 2.0
    width_stations = int(p["flyer_width_fiber_stations"])
    v_values = np.linspace(-half_width, half_width, width_stations)
    bands = width_band_count(record, case)
    knot_indices = np.rint(np.linspace(0, len(v_values) - 1, bands + 1)).astype(int).tolist()
    v_shape = [float(v_values[i]) for i in knot_indices]

    measured_edges = [find_actual_front_edge(record, board_sampler, float(v), case)
                      for v in v_values]
    measured_shape_edges = [measured_edges[i] for i in knot_indices]
    shape_edge_qs = [float(edge["q_front_mm"]) for edge in measured_shape_edges]
    edge_fit_before = [
        float(edge["q_front_mm"]) - width_interp(v, v_shape, shape_edge_qs)
        for v, edge in zip(v_values, measured_edges)
    ]
    edge_chord_inboard_mm = max(0.0, -min(edge_fit_before)) + float(p["flyer_edge_fit_clearance_mm"])
    edges = []
    for edge in measured_edges:
        q_shape = float(edge["q_front_mm"]) - edge_chord_inboard_mm
        h_shape = board_top_at(record, board_sampler, q_shape, float(edge["v_mm"]))
        if h_shape is None:
            raise ValueError(
                f"{record['family']} {record['index']} v={edge['v_mm']:.2f}: "
                f"safe ruled I chord misses board after {edge_chord_inboard_mm:.4f}mm inward fit")
        edges.append({**edge, "measured_q_front_mm": float(edge["q_front_mm"]),
                      "measured_H_front_mm": float(edge["H_front_mm"]),
                      "q_front_mm": float(q_shape), "H_front_mm": float(h_shape)})

    edges_shape = [edges[i] for i in knot_indices]
    edge_shape_qs = [float(edge["q_front_mm"]) for edge in edges_shape]
    edge_shape_hs = [float(edge["H_front_mm"]) for edge in edges_shape]
    qR0 = float(record.get("q_R0_mm", -float(record["ordinary_tail_mm"])))
    qE = float(record["l_SE_mm"])
    is_fan = record["family"] == "fan"
    if is_fan and (end_curve is None or gamma is None):
        raise ValueError("立脚飞椽需要本次 stage04 梢端控制曲线及 gamma，不能退回单侧厚度")

    e_bottom_values = []
    e_top_values = []
    e_top_sources = []
    e_xy_values = []
    e_fractions = []
    for v in v_values:
        e_bottom, _ = fly_bottom_and_top(record, qE, float(v))
        e_xy = xy_at_q(record, qE, float(v))
        if is_fan:
            fraction = s4.arc_fraction_at_point(e_xy, end_curve)
            e_top = float(s4.fly_top_at(e_xy, fraction, float(gamma), end_curve))
            source = "fly_top_at(E_plus_width_axis_v, projected_end_curve_fraction, gamma)"
            e_fractions.append(float(fraction))
        else:
            e_top = float(e_bottom + s4.plane_depth_at(record, qE, qR0))
            source = "stage04_tip_thickness_plane"
            e_fractions.append(float(record.get("outer_curve_fraction", 0.0)))
        if e_top - e_bottom <= 1e-6:
            raise ValueError(
                f"{record['family']} {record['index']} v={float(v):.2f}: "
                f"梢端上控制面低于下控制面 ({e_top - e_bottom:.6f}mm)")
        e_bottom_values.append(float(e_bottom))
        e_top_values.append(float(e_top))
        e_top_sources.append(source)
        e_xy_values.append([float(e_xy[0]), float(e_xy[1])])

    center_idx = len(v_values) // 2
    if is_fan:
        dI_values = [float(s4.plane_depth_at(record, float(edge["q_front_mm"]), qR0))
                     for edge in edges]
    else:
        # Keep the established straight-eave thickness template unchanged.
        dI_center = float(s4.plane_depth_at(
            record, float(measured_edges[center_idx]["q_front_mm"]), qR0))
        dI_values = [dI_center for _ in edges]
    for i, edge in enumerate(edges):
        edge["dI_world_Z_mm"] = float(dI_values[i])
        edge["I_upper_z_mm"] = float(edge["H_front_mm"] + dI_values[i])
        edge["E_bottom_z_mm"] = float(e_bottom_values[i])
        edge["E_top_z_mm"] = float(e_top_values[i])
        edge["E_depth_world_Z_mm"] = float(e_top_values[i] - e_bottom_values[i])
        edge["E_xy_mm"] = e_xy_values[i]
        edge["E_top_source"] = e_top_sources[i]
        edge["E_curve_fraction"] = e_fractions[i]

    center_edge = edges[center_idx]
    dI_z = float(center_edge["dI_world_Z_mm"])
    E_bottom_center = float(center_edge["E_bottom_z_mm"])
    E_depth_z = float(center_edge["E_depth_world_Z_mm"])

    max_tail_length = float(p["flyer_tail_root_max_search_mm"])
    roots = [find_tail_endpoint(record, board_sampler, edge,
                                float(edge["dI_world_Z_mm"]), E_bottom_center, E_depth_z,
                                max_tail_length, case)
             for edge in edges]
    roots_shape_measured = [roots[i] for i in knot_indices]
    measured_root_qs = [float(root["q_root_mm"]) for root in roots_shape_measured]
    root_fit_before = [
        float(root["q_root_mm"]) - width_interp(v, v_shape, measured_root_qs)
        for v, root in zip(v_values, roots)
    ]
    root_chord_outboard_mm = max(0.0, max(root_fit_before)) + float(p["flyer_root_fit_clearance_mm"])
    root_shape_qs = [float(root["q_root_mm"]) + root_chord_outboard_mm
                     for root in roots_shape_measured]
    root_shape_qs_all = [width_interp(v, v_shape, root_shape_qs) for v in v_values]

    root_records = []
    root_gap_samples = []
    for v, edge, measured_root, q_root in zip(v_values, edges, roots, root_shape_qs_all):
        h_root = board_top_at(record, board_sampler, float(q_root), float(v))
        if h_root is None:
            raise ValueError(f"{record['family']} {record['index']} v={float(v):.2f}: ruled tail root misses lower-board mesh")
        top_root = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                  float(q_root), float(v), E_bottom_center, E_depth_z)
        root_gap = float(top_root - h_root)
        if root_gap <= 0.0:
            raise ValueError(
                f"{record['family']} {record['index']} v={float(v):.2f}: "
                f"ruled root crosses lower board by {-root_gap:.5f}mm")
        root_gap_samples.append(root_gap)
        root_records.append({
            "v_mm": float(v), "measured_q_root_mm": float(measured_root["q_root_mm"]),
            "q_root_mm": float(q_root), "H_R_board_mm": float(h_root),
            "root_gap_mm": root_gap,
            "tail_length_from_I_mm": float(edge["q_front_mm"] - q_root),
            "tail_length_from_S_mm": float(-q_root),
        })

    edge_fit_residual_q = []
    edge_fit_residual_h = []
    root_fit_residual_q = []
    for v, measured_edge, edge, root in zip(v_values, measured_edges, edges, roots):
        edge_fit_residual_q.append(
            float(measured_edge["q_front_mm"]) - width_interp(v, v_shape, edge_shape_qs))
        edge_fit_residual_h.append(
            float(measured_edge["H_front_mm"]) - width_interp(v, v_shape, edge_shape_hs))
        root_fit_residual_q.append(
            float(root["q_root_mm"]) - width_interp(v, v_shape, root_shape_qs))

    band_sections = [[] for _ in range(bands)]
    upper_surface_band_sections = [[] for _ in range(bands)]
    min_depth, max_depth = float("inf"), -float("inf")
    root_brep_endpoint_gaps = []
    max_board_surface_fit_residual = 0.0
    max_tail_top_motherline_fit_residual = 0.0
    max_outer_bottom_line_fit_residual = 0.0
    max_outer_top_line_fit_residual = 0.0
    tail_board_differences = []
    side_tail_board_differences = {0: [], len(v_values) - 1: []}
    side_line_residuals = {
        0: {"tail_bottom": 0.0, "tail_top": 0.0, "outer_bottom": 0.0, "outer_top": 0.0},
        len(v_values) - 1: {"tail_bottom": 0.0, "tail_top": 0.0, "outer_bottom": 0.0, "outer_top": 0.0},
    }
    tail_stations = 12
    root_knots = [root_records[i] for i in knot_indices]
    for t in np.linspace(0.0, 1.0, tail_stations + 1):
        q_endpoints, bottom_endpoints, top_endpoints = [], [], []
        for v, edge, root in zip(v_shape, edges_shape, root_knots):
            q = float(root["q_root_mm"]) * (1.0 - float(t)) + float(edge["q_front_mm"]) * float(t)
            if is_fan:
                bottom = tail_bottom_line(q, edge, root)
            else:
                bottom = board_top_at(record, board_sampler, q, v)
                if bottom is None:
                    raise ValueError(f"{record['family']} {record['index']} v={v:.2f}: tail station has no board hit at q={q:.3f}")
            top = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                 q, v, E_bottom_center, E_depth_z)
            q_endpoints.append(float(q))
            bottom_endpoints.append(float(bottom))
            top_endpoints.append(float(top))
        gaps = [float(top - bottom) for top, bottom in zip(top_endpoints, bottom_endpoints)]
        if float(t) <= 1e-12:
            root_brep_endpoint_gaps = gaps
        if min(gaps) <= TAIL_TARGET_CLOSURE_GAP_MM * 0.25:
            raise ValueError(f"{record['family']} {record['index']} tail station has nonpositive fitted depth {min(gaps):.6g}mm")
        for band_index in range(bands):
            i0, i1 = band_index, band_index + 1
            section = [
                (*xy_at_q(record, q_endpoints[i0], v_shape[i0]), bottom_endpoints[i0]),
                (*xy_at_q(record, q_endpoints[i1], v_shape[i1]), bottom_endpoints[i1]),
                (*xy_at_q(record, q_endpoints[i1], v_shape[i1]), top_endpoints[i1]),
                (*xy_at_q(record, q_endpoints[i0], v_shape[i0]), top_endpoints[i0]),
            ]
            band_sections[band_index].append(section)
            upper_surface_band_sections[band_index].append({
                "region": "tail_R_to_I",
                "lambda": float(t),
                "v_low_mm": float(v_shape[i0]), "v_high_mm": float(v_shape[i1]),
                "q_low_mm": float(q_endpoints[i0]), "q_high_mm": float(q_endpoints[i1]),
                "points_low_to_high": [[float(c) for c in section[3]],
                                       [float(c) for c in section[2]]],
            })
        min_depth, max_depth = min(min_depth, min(gaps)), max(max_depth, max(gaps))

        for i, (v, edge, root) in enumerate(zip(v_values, edges, root_records)):
            q = width_interp(v, v_shape, q_endpoints)
            bottom_surface = width_interp(v, v_shape, bottom_endpoints)
            top_surface = width_interp(v, v_shape, top_endpoints)
            h_board = board_top_at(record, board_sampler, q, float(v))
            if h_board is None:
                raise ValueError(f"{record['family']} {record['index']} v={v:.2f} tail line check has no board hit")
            signed_board_difference = float(bottom_surface - h_board)
            tail_board_differences.append(signed_board_difference)
            max_board_surface_fit_residual = max(max_board_surface_fit_residual,
                                                 abs(signed_board_difference))
            if i in side_tail_board_differences:
                side_tail_board_differences[i].append(signed_board_difference)
                expected_bottom = (tail_bottom_line(q, edge, root) if is_fan else
                                   float(bottom_surface))
                side_line_residuals[i]["tail_bottom"] = max(
                    side_line_residuals[i]["tail_bottom"],
                    abs(float(bottom_surface) - expected_bottom))
                expected_top = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                              q, float(v), E_bottom_center, E_depth_z)
                side_line_residuals[i]["tail_top"] = max(
                    side_line_residuals[i]["tail_top"], abs(float(top_surface) - expected_top))
            top_exact = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                       q, float(v), E_bottom_center, E_depth_z)
            max_tail_top_motherline_fit_residual = max(
                max_tail_top_motherline_fit_residual, abs(float(top_exact) - float(top_surface)))

    outer_stations = 12
    for t in np.linspace(1.0 / outer_stations, 1.0, outer_stations):
        q_endpoints, bottom_endpoints, top_endpoints = [], [], []
        for v, edge in zip(v_shape, edges_shape):
            q = float(edge["q_front_mm"]) + float(t) * (qE - float(edge["q_front_mm"]))
            bottom = bottom_outer_line(record, edge, q, v, E_bottom_center)
            top = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                 q, v, E_bottom_center, E_depth_z)
            q_endpoints.append(float(q))
            bottom_endpoints.append(float(bottom))
            top_endpoints.append(float(top))
        depths = [float(top - bottom) for top, bottom in zip(top_endpoints, bottom_endpoints)]
        if min(depths) <= 1e-6:
            raise ValueError(
                f"{record['family']} {record['index']} outer straight section has nonpositive depth "
                f"{min(depths):.6g}mm")
        for band_index in range(bands):
            i0, i1 = band_index, band_index + 1
            section = [
                (*xy_at_q(record, q_endpoints[i0], v_shape[i0]), bottom_endpoints[i0]),
                (*xy_at_q(record, q_endpoints[i1], v_shape[i1]), bottom_endpoints[i1]),
                (*xy_at_q(record, q_endpoints[i1], v_shape[i1]), top_endpoints[i1]),
                (*xy_at_q(record, q_endpoints[i0], v_shape[i0]), top_endpoints[i0]),
            ]
            band_sections[band_index].append(section)
            upper_surface_band_sections[band_index].append({
                "region": "outer_I_to_E",
                "lambda": float(t),
                "v_low_mm": float(v_shape[i0]), "v_high_mm": float(v_shape[i1]),
                "q_low_mm": float(q_endpoints[i0]), "q_high_mm": float(q_endpoints[i1]),
                "points_low_to_high": [[float(c) for c in section[3]],
                                       [float(c) for c in section[2]]],
            })
        min_depth, max_depth = min(min_depth, min(depths)), max(max_depth, max(depths))
        for i, (v, edge) in enumerate(zip(v_values, edges)):
            q = width_interp(v, v_shape, q_endpoints)
            bottom_exact = bottom_outer_line(record, edge, q, float(v), E_bottom_center)
            bottom_surface = width_interp(v, v_shape, bottom_endpoints)
            top_exact = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                       q, float(v), E_bottom_center, E_depth_z)
            top_surface = width_interp(v, v_shape, top_endpoints)
            bottom_residual = abs(float(bottom_exact) - float(bottom_surface))
            top_residual = abs(float(top_exact) - float(top_surface))
            max_outer_bottom_line_fit_residual = max(max_outer_bottom_line_fit_residual, bottom_residual)
            max_outer_top_line_fit_residual = max(max_outer_top_line_fit_residual, top_residual)
            if i in side_line_residuals:
                side_line_residuals[i]["outer_bottom"] = max(
                    side_line_residuals[i]["outer_bottom"], bottom_residual)
                side_line_residuals[i]["outer_top"] = max(
                    side_line_residuals[i]["outer_top"], top_residual)

    band_shapes = [s2.loft_sections(sec) for sec in band_sections]
    band_solid_counts = [len(part.Solids()) for part in band_shapes]
    band_validity = [bool(part.isValid()) for part in band_shapes]
    shape = band_shapes[0]
    for band_index, band_shape in enumerate(band_shapes[1:], start=1):
        shape = shape.fuse(band_shape).clean()
        if shape.isNull() or len(shape.Solids()) != 1:
            raise ValueError(
                f"{record['family']} {record['index']} width band fusion {band_index} "
                f"produced {len(shape.Solids())} solids")
    if not shape.isValid() or len(shape.Solids()) != 1:
        raise ValueError(
            f"{record['family']} {record['index']} fused width bands invalid: "
            f"valid={shape.isValid()} solids={len(shape.Solids())}")

    edge_shape_center = width_interp(0.0, v_shape, edge_shape_qs)
    edge_shape_center_h = width_interp(0.0, v_shape, edge_shape_hs)
    root_shape_center = width_interp(0.0, v_shape, root_shape_qs)
    tail_lengths = [float(edge["q_front_mm"] - root["q_root_mm"])
                    for edge, root in zip(edges, root_records)]
    front_residuals = []
    for v in v_values:
        q = width_interp(float(v), v_shape, edge_shape_qs)
        h = board_top_at(record, board_sampler, q, float(v))
        h_fit = width_interp(float(v), v_shape, edge_shape_hs)
        if h is None:
            raise ValueError(f"{record['family']} {record['index']} v={float(v):.2f}: fitted I line misses lower-board mesh")
        front_residuals.append(float(h - h_fit))

    side_contours = []
    if is_fan:
        for fiber_index, side_label in ((0, "A"), (len(v_values) - 1, "B")):
            edge = edges[fiber_index]
            root = root_records[fiber_index]
            q_i, h_i = float(edge["q_front_mm"]), float(edge["H_front_mm"])
            q_r, h_r = float(root["q_root_mm"]), float(root["H_R_board_mm"])
            i_top = float(edge["I_upper_z_mm"])
            e_bottom = float(edge["E_bottom_z_mm"])
            e_top = float(edge["E_top_z_mm"])
            top_r = top_motherline(record, edge, float(edge["dI_world_Z_mm"]),
                                   q_r, float(edge["v_mm"]), E_bottom_center, E_depth_z)
            side_contours.append({
                "side": side_label,
                "v_mm": float(edge["v_mm"]),
                "width_axis_mapping": "A=v=-w_fly/2; B=v=+w_fly/2",
                "I": {"q_mm": q_i, "lower_board_top_z_mm": h_i,
                      "thickness_world_Z_mm": float(edge["dI_world_Z_mm"]),
                      "upper_z_mm": i_top},
                "R": {"q_mm": q_r, "lower_board_top_z_mm": h_r,
                      "upper_z_mm": float(top_r), "root_gap_mm": float(top_r - h_r)},
                "E": {"q_mm": qE, "xy_mm": edge["E_xy_mm"],
                      "lower_z_mm": e_bottom, "upper_control_z_mm": e_top,
                      "thickness_world_Z_mm": float(edge["E_depth_world_Z_mm"]),
                      "upper_control_source": edge["E_top_source"],
                      "projected_end_curve_fraction": float(edge["E_curve_fraction"])},
                "straight_edge_slopes_z_per_q": {
                    "tail_lower_R_to_I": float((h_i - h_r) / (q_i - q_r)),
                    "tail_upper_R_to_I": float((i_top - top_r) / (q_i - q_r)),
                    "outer_lower_I_to_E": float((e_bottom - h_i) / (qE - q_i)),
                    "outer_upper_I_to_E": float((e_top - i_top) / (qE - q_i)),
                },
                "straightness_max_abs_residual_mm": {
                    "tail_lower": float(side_line_residuals[fiber_index]["tail_bottom"]),
                    "tail_upper": float(side_line_residuals[fiber_index]["tail_top"]),
                    "outer_lower": float(side_line_residuals[fiber_index]["outer_bottom"]),
                    "outer_upper": float(side_line_residuals[fiber_index]["outer_top"]),
                },
                "tail_bottom_minus_lower_board_mm": {
                    "min": float(min(side_tail_board_differences[fiber_index])),
                    "max": float(max(side_tail_board_differences[fiber_index])),
                    "max_abs": float(max(abs(x) for x in side_tail_board_differences[fiber_index])),
                    "negative_samples": sum(x < -1e-6 for x in side_tail_board_differences[fiber_index]),
                    "sample_count": len(side_tail_board_differences[fiber_index]),
                    "sign_rule": "negative means the straight lower edge is below the sampled board top",
                },
            })

        details = {
        "family": record["family"], "index": int(record["index"]),
        "role": str(record.get("role", "fan" if record["family"] == "fan" else "regular")),
        "is_boundary": bool(record.get("is_boundary", False)),
        "source_rafter_id": record["source_rafter_id"], "part_ids": [],
        "profile_basis": "centerline_approximation",
        "S": list(map(float, record["S"])), "E": list(map(float, record["E"])),
        "unit": list(map(float, record["unit"])), "width_axis": list(map(float, record["width_axis"])),
        "l_SE_mm": float(record["l_SE_mm"]), "width_mm": 2.0 * half_width,
        "old_I_world_xy_mm": list(map(float, record["I"])),
        "q_I_source_mm": float(record.get("q_I_mm", float(np.dot(
            np.asarray(record["I"]) - np.asarray(record["S"]), np.asarray(record["unit"]))))),
        "q_I_center_mm": float(edge_shape_center), "H_I_center_mm": float(edge_shape_center_h),
        "q_I_center_measured_mm": float(measured_edges[center_idx]["q_front_mm"]),
        "H_I_center_measured_mm": float(measured_edges[center_idx]["H_front_mm"]),
        "dI_world_Z_mm": dI_z, "E_bottom_center_mm": E_bottom_center,
        "E_depth_world_Z_mm": E_depth_z,
        "E_top_center_z_mm": float(center_edge["E_top_z_mm"]),
        "g_E_section_original": float(record["g"]),
        "width_ruled_band_count": int(bands), "width_knot_indices": [int(i) for i in knot_indices],
        "width_knot_v_mm": [float(v) for v in v_shape],
        "upper_surface_width_bands": [
            {"band_index": int(i), "v_low_mm": float(v_shape[i]),
             "v_high_mm": float(v_shape[i + 1]), "sections": sections}
            for i, sections in enumerate(upper_surface_band_sections)],
        "band_brep_solid_counts": band_solid_counts, "band_brep_validity": band_validity,
        "fused_band_solid_count": len(shape.Solids()), "fused_band_valid": bool(shape.isValid()),
        "build_seconds": float(time.perf_counter() - build_started),
        "I_front_edge_bias_inboard_mm": float(p["flyer_edge_inboard_bias_mm"]),
        "piecewise_edge_inboard_fit_mm": float(edge_chord_inboard_mm),
        "piecewise_root_outboard_fit_mm": float(root_chord_outboard_mm),
        "I_front_edge_search_span_mm": float(p["flyer_front_edge_search_span_mm"]),
        "I_front_edge_fiber_records": edges,
        "I_front_edge_measured_fiber_records": measured_edges,
        "shape_I_edge_fit_residual_q_range_mm": [float(min(edge_fit_residual_q)), float(max(edge_fit_residual_q))],
        "shape_I_edge_fit_residual_H_range_mm": [float(min(edge_fit_residual_h)), float(max(edge_fit_residual_h))],
        "q_R_center_mm": float(root_shape_center),
        "q_R_center_measured_mm": float(roots[center_idx]["q_root_mm"]),
        "q_R_span_mm": [float(min(root_shape_qs)), float(max(root_shape_qs))],
        "shape_R_edge_fit_residual_q_range_mm": [float(min(root_fit_residual_q)), float(max(root_fit_residual_q))],
        "tail_length_from_I_center_mm": float(edge_shape_center - root_shape_center),
        "tail_length_from_S_center_mm": float(-root_shape_center),
        "tail_length_from_S_fiber_range_mm": [float(min(-r["q_root_mm"] for r in root_records)),
                                                float(max(-r["q_root_mm"] for r in root_records))],
        "tail_length_is_board_contact_derived": True,
        "root_fiber_records": root_records,
        "I_contact_height_residual_range_mm": [float(min(front_residuals)), float(max(front_residuals))],
        "tail_bottom_rule": ("R→I lower edge is a straight line between actual board-top samples at R and I; "
                             "signed deviation from sampled board top is diagnostic only"
                             if is_fan else
                             "straight-bank tail bottom retains the existing sampled board-following rule"),
        "tail_bottom_minus_lower_board_mm": {
            "min": float(min(tail_board_differences)),
            "max": float(max(tail_board_differences)),
            "max_abs": float(max(abs(x) for x in tail_board_differences)),
            "negative_samples": sum(x < -1e-6 for x in tail_board_differences),
            "sample_count": len(tail_board_differences),
            "sign_rule": "negative means the constructed lower edge is below the sampled board top",
        },
        "tail_root_top_gap_target_mm": TAIL_TARGET_CLOSURE_GAP_MM,
        "ruled_tail_root_BRep_section_thickness_range_mm": [
            float(min(root_brep_endpoint_gaps)), float(max(root_brep_endpoint_gaps))],
        "ruled_tail_root_sampled_top_to_board_gap_range_mm": [
            float(min(root_gap_samples)), float(max(root_gap_samples))],
        "ruled_tail_root_sample_count": len(root_gap_samples),
        "sampled_depth_range_world_Z_mm": [float(min_depth), float(max_depth)],
        "maximum_sampled_board_surface_fit_residual_mm": float(max_board_surface_fit_residual),
        "maximum_sampled_tail_top_vs_motherline_residual_mm": float(max_tail_top_motherline_fit_residual),
        "maximum_sampled_outer_bottom_straight_line_residual_mm": float(max_outer_bottom_line_fit_residual),
        "maximum_sampled_outer_top_straight_line_residual_mm": float(max_outer_top_line_fit_residual),
        "loft_shape_width_profiles": int(len(v_shape)),
        "loft_width_stations": int(len(v_values)),
        "tail_longitudinal_sections": tail_stations + 1,
        "outer_longitudinal_sections": outer_stations,
        "board_surface_queries_for_loft": (tail_stations + 1 + outer_stations) * (len(v_values) + len(v_shape)),
        "E_section_preserved": True,
        "side_contours": side_contours,
        "A_minus_B_thickness_mm": ({
            "at_I": float(edges[0]["dI_world_Z_mm"] - edges[-1]["dI_world_Z_mm"]),
            "at_E": float(edges[0]["E_depth_world_Z_mm"] - edges[-1]["E_depth_world_Z_mm"]),
        } if is_fan else None),
        "construction": (
            "立脚飞椽：A/B 两侧分别以各自 I 处望板上表面和 plane_depth_at(q_I) 定上端，"
            "E 处上端分别投影到 stage04 梢端控制曲线 fly_top_at；I→E 的上下缘均按各侧端点作直线。"
            "R→I 下缘按实际 R/I 望板上表面端点作直线，板面偏差只记录、不拟合成曲线。"
            f"宽向以{bands}个分段横向直纹带({len(v_shape)}个共用结点)构造；"
            "排料仍使用v=0中心截面近似。"
            if is_fan else
            f"正身飞椽保留中心厚度模板和望板贴合尾底；宽向以{bands}个分段横向直纹带"
            f"({len(v_shape)}个共用结点)构造。"
        ),
    }
    return shape, details


def build_contact_flyer(record, board_sampler, case, end_curve=None, gamma=None,
                        masterboard_record=None):
    """Extract one member solid from the already-created shared motherboard field."""
    if masterboard_record is None:
        raise ValueError("build_contact_flyer requires the prebuilt shared flyer motherboard record")
    build_started = time.perf_counter()
    p = case["p"]
    controls = masterboard_record["controls"]
    roots = masterboard_record["roots"]
    v_values = [float(control["v_mm"]) for control in controls]
    if len(controls) < 3 or len(controls) != len(roots) or len(v_values) % 2 != 1:
        raise ValueError(
            f"{record['family']} {record['index']}: shared field needs an odd matched fiber set")
    center_idx = len(v_values) // 2
    bands = len(v_values) - 1
    knot_indices = list(range(len(v_values)))
    v_shape = list(v_values)
    center_edge = controls[center_idx]
    E_bottom_center = float(center_edge["E_bottom_z_mm"])
    E_top_center = float(center_edge["E_top_z_mm"])
    E_depth_center = float(center_edge["E_depth_world_Z_mm"])
    qI_center = float(center_edge["q_front_mm"])
    qE_center = float(center_edge["q_E_mm"])
    H_I_center = float(center_edge["H_front_mm"])
    dI_center = float(center_edge["dI_world_Z_mm"])

    tail_stations = 12
    outer_stations = 12
    upper_sections_by_band = [[] for _ in range(bands)]
    member_band_sections = [[] for _ in range(bands)]
    min_depth, max_depth = float("inf"), -float("inf")

    def append_motherboard_section(bottom_points, top_points, q_values, region, fraction):
        # Each ruled patch uses adjacent rails from the shared field. The patches
        # are fused into one member; they are not emitted as separate parts.
        for band_index in range(bands):
            low, high = band_index, band_index + 1
            band_section = [bottom_points[low], bottom_points[high],
                            top_points[high], top_points[low]]
            member_band_sections[band_index].append(band_section)
            upper_sections_by_band[band_index].append({
                "region": region, "lambda": float(fraction),
                "v_low_mm": float(v_values[low]), "v_high_mm": float(v_values[high]),
                "q_low_mm": float(q_values[low]), "q_high_mm": float(q_values[high]),
                "points_low_to_high": [[float(c) for c in band_section[3]],
                                       [float(c) for c in band_section[2]]],
            })

    for t in np.linspace(0.0, 1.0, tail_stations + 1):
        bottom_points, top_points, q_values = [], [], []
        for edge, root in zip(controls, roots):
            q = (float(root["q_root_mm"]) * (1.0 - float(t)) +
                 float(edge["q_front_mm"]) * float(t))
            bottom = float(tail_bottom_line(q, edge, root))
            top = float(top_motherline(
                record, edge, float(edge["dI_world_Z_mm"]), q, float(edge["v_mm"]),
                E_bottom_center, E_depth_center))
            depth = top - bottom
            if depth <= TAIL_TARGET_CLOSURE_GAP_MM * 0.25:
                raise ValueError(
                    f"{record['family']} {record['index']} v={edge['v_mm']:.3f} tail lambda={t:.4f}: "
                    f"motherboard depth is nonpositive ({depth:.6g}mm)")
            xy = xy_at_q(record, q, float(edge["v_mm"]))
            bottom_points.append((float(xy[0]), float(xy[1]), bottom))
            top_points.append((float(xy[0]), float(xy[1]), top))
            q_values.append(float(q))
            min_depth, max_depth = min(min_depth, depth), max(max_depth, depth)
        append_motherboard_section(bottom_points, top_points, q_values,
                                   "tail_R_to_I", t)

    # Continue through the same global field at I; only the I section is shared by both phases.
    for t in np.linspace(0.0, 1.0, outer_stations + 1)[1:]:
        bottom_points, top_points, q_values = [], [], []
        for edge in controls:
            q = (float(edge["q_front_mm"]) * (1.0 - float(t)) +
                 float(edge["q_E_mm"]) * float(t))
            bottom = float(bottom_outer_line(record, edge, q, float(edge["v_mm"]), E_bottom_center))
            top = float(top_motherline(
                record, edge, float(edge["dI_world_Z_mm"]), q, float(edge["v_mm"]),
                E_bottom_center, E_depth_center))
            depth = top - bottom
            if depth <= 1e-6:
                raise ValueError(
                    f"{record['family']} {record['index']} v={edge['v_mm']:.3f} outer lambda={t:.4f}: "
                    f"motherboard depth is nonpositive ({depth:.6g}mm)")
            xy = xy_at_q(record, q, float(edge["v_mm"]))
            bottom_points.append((float(xy[0]), float(xy[1]), bottom))
            top_points.append((float(xy[0]), float(xy[1]), top))
            q_values.append(float(q))
            min_depth, max_depth = min(min_depth, depth), max(max_depth, depth)
        append_motherboard_section(bottom_points, top_points, q_values,
                                   "outer_I_to_E", t)

    band_shapes = []
    for band_index, sections in enumerate(member_band_sections):
        band_shape = s2.loft_sections(sections)
        if band_shape.isNull() or len(band_shape.Solids()) != 1:
            raise ValueError(
                f"{record['family']} {record['index']} shared motherboard band {band_index} "
                f"({v_values[band_index]:.3f}..{v_values[band_index + 1]:.3f}mm) "
                f"did not produce one member solid: solids={len(band_shape.Solids())}")
        band_shapes.append(band_shape)
    shape = band_shapes[0]
    for band_index, band_shape in enumerate(band_shapes[1:], start=1):
        shape = shape.fuse(band_shape).clean()
        if shape.isNull() or len(shape.Solids()) != 1:
            raise ValueError(
                f"{record['family']} {record['index']} shared motherboard band fusion {band_index} "
                f"did not produce one member solid: solids={len(shape.Solids())}")

    center_root = roots[center_idx]
    measured_edges = [{**edge, "measured_q_front_mm": float(edge["q_front_path_hit_mm"]),
                       "measured_H_front_mm": float(edge["H_front_mm"])} for edge in controls]
    root_fiber_records = [dict(root) for root in roots]
    side_contours = []
    if record["family"] == "fan":
        for fiber_index, side_label in ((0, "A"), (len(controls) - 1, "B")):
            edge = controls[fiber_index]
            root = roots[fiber_index]
            q_i = float(edge["q_front_mm"])
            q_e = float(edge["q_E_mm"])
            q_r = float(root["q_root_mm"])
            h_i = float(edge["H_front_mm"])
            lower_r = float(root["R_bottom_z_mm"])
            board_r = float(root["H_R_board_mm"])
            i_top = float(edge["I_upper_z_mm"])
            e_bottom = float(edge["E_bottom_z_mm"])
            e_top = float(edge["E_top_z_mm"])
            top_r = float(edge["R_top_z_mm"])
            side_contours.append({
                "side": side_label,
                "v_mm": float(edge["v_mm"]),
                "width_axis_mapping": "A=v=-w_fly/2; B=v=+w_fly/2",
                "I": {"q_mm": q_i, "lower_z_mm": h_i,
                      "thickness_world_Z_mm": float(edge["dI_world_Z_mm"]),
                      "upper_z_mm": i_top},
                "R": {"q_mm": q_r, "member_lower_z_mm": lower_r,
                      "board_top_reference_z_mm": board_r,
                      "upper_z_mm": top_r, "root_gap_mm": float(root["root_gap_mm"]),
                      "thickness_world_Z_mm": float(root["thickness_world_Z_mm"])},
                "E": {"q_mm": q_e, "xy_mm": edge["E_xy_mm"],
                      "lower_z_mm": e_bottom, "upper_control_z_mm": e_top,
                      "thickness_world_Z_mm": float(edge["E_depth_world_Z_mm"]),
                      "upper_control_source": edge["E_top_source"],
                      "projected_end_curve_fraction": float(edge["E_curve_fraction"])},
                "q_S_likou_outer_intersection_mm": float(edge["q_S_mm"]),
                "straight_edge_slopes_z_per_q": {
                    "tail_lower_R_to_I": float((h_i - lower_r) / (q_i - q_r)),
                    "tail_upper_R_to_I": float((i_top - top_r) / (q_i - q_r)),
                    "outer_lower_I_to_E": float((e_bottom - h_i) / (q_e - q_i)),
                    "outer_upper_I_to_E": float((e_top - i_top) / (q_e - q_i)),
                },
            })

    tail_lengths = [float(edge["q_front_mm"] - root["q_root_mm"])
                    for edge, root in zip(controls, roots)]
    q_root0 = float(record.get("q_R0_mm", -float(record["ordinary_tail_mm"])))
    details = {
        "family": str(record["family"]), "index": int(record["index"]),
        "role": str(record.get("role", "fan" if record["family"] == "fan" else "regular")),
        "is_boundary": bool(record.get("is_boundary", False)),
        "source_rafter_id": record["source_rafter_id"], "part_ids": [],
        "profile_basis": "shared_virtual_motherboard",
        "S": list(map(float, record["S"])), "E": list(map(float, record["E"])),
        "unit": list(map(float, record["unit"])),
        "width_axis": list(map(float, record["width_axis"])),
        "l_SE_mm": float(record["l_SE_mm"]), "width_mm": float(p["w_fly"]),
        "old_I_world_xy_mm": list(map(float, record["I"])),
        "q_I_source_mm": float(record.get("q_I_mm", qI_center)),
        "q_I_center_mm": qI_center, "H_I_center_mm": H_I_center,
        "q_I_center_measured_mm": float(center_edge["q_front_path_hit_mm"]),
        "H_I_center_measured_mm": H_I_center,
        "dI_world_Z_mm": dI_center,
        "E_bottom_center_mm": E_bottom_center,
        "E_depth_world_Z_mm": E_depth_center,
        "E_top_center_z_mm": E_top_center,
        "q_E_center_mm": qE_center,
        "q_S_center_mm": float(center_edge["q_S_mm"]),
        "g_E_section_original": float(record["g"]),
        "thickness_profile": {
            "rule": "T(q) is shared across the flyer width at the same absolute longitudinal station",
            "interpolation": "linear from q_R0 to l_SE using the existing root and tip thickness controls",
            "q_R0_mm": q_root0,
            "q_tip_mm": float(record["l_SE_mm"]),
            "root_thickness_normal_mm": float(record.get("root_thickness_R0_normal_mm",
                                                    record.get("root_thickness_normal_mm", 0.0))),
            "tip_thickness_normal_mm": float(record["tip_thickness_normal_mm"]),
            "world_Z_conversion": "normal thickness multiplied by sqrt(1 + g^2)",
        },
        "width_ruled_band_count": int(bands),
        "width_knot_indices": knot_indices,
        "width_knot_v_mm": [float(v) for v in v_shape],
        "upper_surface_width_bands": [
            {"band_index": int(index), "v_low_mm": float(v_shape[index]),
             "v_high_mm": float(v_shape[index + 1]), "sections": rows}
            for index, rows in enumerate(upper_sections_by_band)],
        "cross_width_cell_count": int(len(band_shapes)),
        "cross_width_cell_rule": "adjacent straight-ruled patches of the shared W_i field are fused into one physical flyer; they are not separate parts",
        "member_solid_count": len(shape.Solids()),
        "build_seconds": float(time.perf_counter() - build_started),
        "I_front_edge_bias_inboard_mm": float(p["flyer_edge_inboard_bias_mm"]),
        "I_front_edge_search_span_mm": float(p["flyer_front_edge_search_span_mm"]),
        "I_front_edge_fiber_records": controls,
        "I_front_edge_measured_fiber_records": measured_edges,
        "q_R_center_mm": float(center_root["q_root_mm"]),
        "q_R_span_mm": [float(min(root["q_root_mm"] for root in roots)),
                        float(max(root["q_root_mm"] for root in roots))],
        "tail_length_from_I_center_mm": float(qI_center - center_root["q_root_mm"]),
        "tail_length_from_S_center_mm": float(-center_root["q_root_mm"]),
        "tail_length_from_S_fiber_range_mm": [float(min(-root["q_root_mm"] for root in roots)),
                                               float(max(-root["q_root_mm"] for root in roots))],
        "tail_length_is_board_contact_derived": True,
        "root_fiber_records": root_fiber_records,
        "tail_bottom_rule": "R-to-I lower surface follows the shared top field minus T(q); it meets the lower-board top at I",
        "tail_root_top_gap_target_mm": TAIL_TARGET_CLOSURE_GAP_MM,
        "ruled_tail_root_sampled_top_to_board_gap_range_mm": [
            float(min(root["root_gap_mm"] for root in roots)),
            float(max(root["root_gap_mm"] for root in roots))],
        "loft_shape_width_profiles": int(len(v_shape)),
        "loft_width_stations": int(len(v_values)),
        "tail_longitudinal_sections": int(tail_stations + 1),
        "outer_longitudinal_sections": int(outer_stations + 1),
        "E_section_preserved": True,
        "side_contours": side_contours,
        "motherboard_member_patch_ids": masterboard_record.get("member_band_patch_ids", []),
        "masterboard_source_rafter_order": int(masterboard_record["member_order"]),
        "construction": (
            f"从预先建立的共享虚拟母板 M 取 W_i：{len(v_values)} 条横向控制轨形成分片直纹母面，"
            f"共 {bands} 个相邻轨区间并融合成一根飞椽。上下表面在同一 q 使用共同厚度 T(q)，"
            "R-I 与 I-E 纵向边保持直线；A/B 梢端分别由各自 W_i 与共同端界交会确定。"
            "里口槽刀复用同一 W_i 与 I-E 下表面；排料继续使用 v=0 中心截面近似。"
        ),
    }
    return shape, details

def _preview_contact_flyer(ctx, record, masterboard_record):
    """Build a lightweight flyer from the shared center R/I/E profile."""
    controls = masterboard_record["controls"]
    roots = masterboard_record["roots"]
    center_index = len(controls) // 2
    control, root = controls[center_index], roots[center_index]
    if abs(float(control["v_mm"])) > 1e-8:
        raise ValueError(f"preview {record['source_rafter_id']} center control is not v=0")
    q_r, q_i, q_e = (float(root["q_root_mm"]), float(control["q_front_mm"]),
                     float(control["q_E_mm"]))
    if not q_r < q_i < q_e:
        raise ValueError(f"preview {record['source_rafter_id']} R/I/E order is invalid")
    rows = [
        (q_r, float(root["R_bottom_z_mm"]), float(root["R_top_z_mm"])),
        (q_i, float(control["H_front_mm"]), float(control["I_upper_z_mm"])),
        (q_e, float(control["E_bottom_z_mm"]), float(control["E_top_z_mm"])),
    ]
    points = []
    for q, bottom, _ in rows:
        x, y = xy_at_q(record, q, 0.0)
        points.append([float(x), float(y), bottom])
    for q, _, top in reversed(rows):
        x, y = xy_at_q(record, q, 0.0)
        points.append([float(x), float(y), top])

    width = float(ctx["case"]["p"]["w_fly"])
    width_axis = [float(value) for value in record["width_axis"]]
    width_vec = [width_axis[0] * width, width_axis[1] * width,
                 float(record.get("g", 0.0)) * width]
    start_shift = [-0.5 * value for value in width_vec]
    a_points = [[point[0] + start_shift[0], point[1] + start_shift[1],
                 point[2] + start_shift[2]] for point in points]
    wire_points = [cq.Vector(*point) for point in a_points]
    wire = cq.Wire.makePolygon(wire_points + [wire_points[0]], close=False)
    shape = cq.Solid.extrudeLinear(wire, [], cq.Vector(*width_vec))
    if shape.isNull() or not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-5:
        raise ValueError(f"preview flyer {record['source_rafter_id']} is not one valid solid")
    if len(shape.Faces()) > 20:
        raise ValueError(f"preview flyer {record['source_rafter_id']} has over 20 BRep faces")
    return shape, {
        "source_rafter_id": str(record["source_rafter_id"]),
        "family": str(record["family"]), "index": int(record["index"]),
        "center_fiber_v_mm": float(control["v_mm"]),
        "width_mm": width, "width_vector_3d_mm": width_vec,
        "center_profile_rie_bottom_then_top_mm": points,
        "part_face_count": len(shape.Faces()), "volume_mm3": float(shape.Volume()),
        "valid_single_solid": True,
        "preview_geometry_rule": "v=0 R/I/E center profile rigidly extruded along the actual 3D width vector; A/B use identical geometry by construction",
    }


def add_preview_contact_flyers(ctx):
    """Create one rigidly extruded center-profile solid for each selected flyer."""
    parts, control = ctx["parts"], ctx["control"]
    source_order, motherboard = _build_flyer_masterboard(ctx)
    ctx["flyer_masterboard"] = motherboard
    master_by_source = {item["record"]["source_rafter_id"]: item for item in source_order}
    if set(master_by_source) != {record["source_rafter_id"] for record in ctx["chosen"]}:
        raise ValueError("preview center controls do not cover every selected flyer exactly once")
    preview_records = []
    for record in ctx["chosen"]:
        master = master_by_source[record["source_rafter_id"]]
        shape, detail = _preview_contact_flyer(ctx, record, master)
        family, index = str(record["family"]), int(record["index"])
        if family == "fan":
            pid_x, group = f"flying_fan_x_{index:02d}", s4.WING_GROUP
            name_x = f"预览立脚飞椽基准翼-{index:02d}（中心纵剖等宽挤出）"
        else:
            pid_x, group = f"straight_flying_x_{index:02d}", s4.STRAIGHT_GROUP
            name_x = f"预览正身飞椽基准翼-{index:02d}（中心纵剖等宽挤出）"
        pid_y = pid_x.replace("_x_", "_y_")
        add(parts, pid_x, name_x, group, shape)
        mirrored = s2.mirror_shape(ctx["case"], shape)
        add(parts, pid_y, name_x.replace("基准翼", "镜像翼"), group, mirrored)
        detail["part_ids"] = [pid_x, pid_y]
        detail["part_face_counts"] = {pid_x: len(shape.Faces()), pid_y: len(mirrored.Faces())}
        detail["part_volumes_mm3"] = {pid_x: float(shape.Volume()), pid_y: float(mirrored.Volume())}
        detail["mirrored_valid_single_solid"] = bool(mirrored.isValid() and len(mirrored.Solids()) == 1)
        preview_records.append(detail)
    ctx["contact_fly_records"] = preview_records
    ctx["contact_fly_shapes_x"] = {
        record["part_ids"][0]: next(part["shape"] for part in parts if part["id"] == record["part_ids"][0])
        for record in preview_records}
    ctx["flyer_masterboard"]["physical_member_part_ids_x"] = [r["part_ids"][0] for r in preview_records]
    ctx["flyer_masterboard"]["physical_member_part_ids_y"] = [r["part_ids"][1] for r in preview_records]
    return preview_records


def add_contact_flyers(ctx):
    parts, control = ctx["parts"], ctx["control"]
    fly_records = []
    shape_by_x = {}
    print(f"stage05: flyer BRep build start ({len(ctx['chosen'])} records)", flush=True)
    source_order, motherboard = _build_flyer_masterboard(ctx)
    ctx["flyer_masterboard"] = motherboard
    master_by_source = {item["record"]["source_rafter_id"]: item for item in source_order}
    if set(master_by_source) != {record["source_rafter_id"] for record in ctx["chosen"]}:
        raise ValueError("shared flyer motherboard does not cover every selected member exactly once")
    for record in ctx["chosen"]:
        shape, detail = build_contact_flyer(
            record, ctx["board_sampler"], ctx["case"], ctx["end_curve"], ctx["selected_gamma"],
            masterboard_record=master_by_source[record["source_rafter_id"]])
        if record["family"] == "fan":
            pid_x = f"flying_fan_x_{int(record['index']):02d}"
            pid_y = f"flying_fan_y_{int(record['index']):02d}"
            group = s4.WING_GROUP
            name_x = f"立脚飞椽基准翼-{int(record['index']):02d}（贴板尖尾）"
        else:
            pid_x = f"straight_flying_x_{int(record['index']):02d}"
            pid_y = f"straight_flying_y_{int(record['index']):02d}"
            group = s4.STRAIGHT_GROUP
            name_x = f"正身飞椽基准翼-{int(record['index']):02d}（贴板尖尾）"
        add(parts, pid_x, name_x, group, shape)
        add(parts, pid_y, name_x.replace("基准翼", "镜像翼"), group,
            s2.mirror_shape(ctx["case"], shape))
        detail["part_ids"] = [pid_x, pid_y]
        detail["member_solid_count"] = len(shape.Solids())
        detail["geometry_assessment"] = "not_run"
        shape_by_x[pid_x] = shape
        center_tail = detail["root_fiber_records"][len(detail["root_fiber_records"]) // 2]["q_root_mm"]
        center_end = float(detail["q_E_center_mm"])
        axis_points = []
        for t in np.linspace(0.0, 1.0, 8):
            q = float(center_tail) * (1.0 - float(t)) + float(detail["q_I_center_mm"]) * float(t)
            center_edge = detail["I_front_edge_fiber_records"][len(detail["I_front_edge_fiber_records"]) // 2]
            center_root = detail["root_fiber_records"][len(detail["root_fiber_records"]) // 2]
            h = tail_bottom_line(q, center_edge, center_root)
            top = top_motherline(record, center_edge,
                                 detail["dI_world_Z_mm"], q, 0.0, detail["E_bottom_center_mm"], detail["E_depth_world_Z_mm"])
            axis_points.append((*xy_at_q(record, q, 0.0), float(h if h is not None else top)))
        for t in np.linspace(0.0, 1.0, 8):
            q = float(detail["q_I_center_mm"]) + float(t) * (center_end - float(detail["q_I_center_mm"]))
            b = bottom_outer_line(record, detail["I_front_edge_fiber_records"][len(detail["I_front_edge_fiber_records"]) // 2],
                                  q, 0.0, detail["E_bottom_center_mm"])
            axis_points.append((*xy_at_q(record, q, 0.0), float(b)))
        control.append({"id": pid_x + "_contact_axis", "name": name_x + "底线", "group": group, "points": axis_points})
        control.append({"id": pid_y + "_contact_axis", "name": name_x.replace("基准翼", "镜像翼") + "底线", "group": group,
                        "points": [s2.reflect_xyz(ctx["case"], p) for p in axis_points]})
        fly_records.append(detail)
        print(f"stage05: flyer {record['family']} {int(record['index']):02d} complete", flush=True)
    ctx["contact_fly_records"] = fly_records
    ctx["contact_fly_shapes_x"] = shape_by_x
    ctx["flyer_masterboard"]["physical_member_part_ids_x"] = [
        record["part_ids"][0] for record in fly_records]
    ctx["flyer_masterboard"]["physical_member_part_ids_y"] = [
        record["part_ids"][1] for record in fly_records]
    print("stage05: flyer BRep build complete", flush=True)
    return fly_records


def _contact_notch_cutter_legacy(record, detail, board_sampler, target_shape, upper_safety_mm=5.0):
    edges = detail["I_front_edge_fiber_records"]
    v_values = np.asarray([e["v_mm"] for e in edges], dtype=float)
    q_fronts = np.asarray([e["q_front_mm"] for e in edges], dtype=float)
    q0 = float(np.min(q_fronts)) - 0.05
    q1 = 0.0
    upper_z = float(target_shape.BoundingBox().zmax) + float(upper_safety_mm)
    roots = detail["root_fiber_records"]
    root_v = np.asarray([r["v_mm"] for r in roots], dtype=float)
    root_q = np.asarray([r["q_root_mm"] for r in roots], dtype=float)
    root_h = np.asarray([r["H_R_board_mm"] for r in roots], dtype=float)
    sections = []
    station_count = max(8, int(math.ceil((q1 - q0) / 15.0)))
    for q in np.linspace(q0, q1, station_count + 1):
        bottom_points = []
        for i, v in enumerate(v_values):
            edge = edges[i]
            qI = float(edge["q_front_mm"])
            if float(q) < qI and record["family"] == "fan":
                root = {"q_root_mm": float(np.interp(float(v), root_v, root_q)),
                        "H_R_board_mm": float(np.interp(float(v), root_v, root_h))}
                bottom = tail_bottom_line(float(q), edge, root)
            elif float(q) < qI:
                bottom = board_top_at(record, board_sampler, float(q), float(v))
                if bottom is None:
                    raise ValueError(
                        f"likou notch {record['family']} {record['index']} misses lower board "
                        f"q={q:.2f}, v={v:.2f}")
            else:
                bottom = bottom_outer_line(
                    record, edge, float(q), float(v), detail["E_bottom_center_mm"])
            bottom_points.append((*xy_at_q(record, float(q), float(v)), float(bottom)))
        section = bottom_points + [(x, y, float(upper_z)) for x, y, _ in reversed(bottom_points)]
        sections.append(section)
    cutter = s2.loft_sections(sections)
    if not cutter.isValid() or len(cutter.Solids()) != 1:
        raise ValueError(
            f"invalid {record['family']} {record['index']} likou notch cutter: "
            f"valid={cutter.isValid()} solids={len(cutter.Solids())}")
    return cutter


def _contact_notch_cutter_from_fibers(record, edges, q_limits, upper_z, E_bottom_center):
    """Close one slot cutter from the original shared longitudinal fibers."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.BRepLib import BRepLib
    from OCP.ShapeFix import ShapeFix_Shell

    if len(edges) < 3 or len(edges) % 2 != 1:
        raise ValueError(f"shared-field slot requires an odd fiber count >=3; got {len(edges)}")
    q0, q1 = map(float, q_limits)
    if not q1 > q0:
        raise ValueError(f"shared-field slot q interval is not positive: {q_limits}")

    def vector(point):
        return cq.Vector(*(float(value) for value in point))

    def polygon_face(points, label):
        if len(points) < 3:
            raise ValueError(f"{label}: polygon has fewer than three points")
        vertices = [vector(point) for point in points]
        wire = cq.Wire.makePolygon(vertices + [vertices[0]], close=False)
        face = cq.Face.makeFromWires(wire)
        if face.isNull() or not face.isValid():
            raise ValueError(f"{label}: failed to make a valid planar face")
        return face

    bottom_points = [[], []]
    top_points = [[], []]
    for qi, q in enumerate((q0, q1)):
        for edge in edges:
            v = float(edge["v_mm"])
            x, y = xy_at_q(record, q, v)
            z = bottom_outer_line(record, edge, q, v, E_bottom_center)
            bottom_points[qi].append((float(x), float(y), float(z)))
            top_points[qi].append((float(x), float(y), float(upper_z)))

    # bottom_outer_line is affine in q for each W_i. Represent each source
    # fiber as its exact endpoint line, then form each neighboring bottom
    # patch directly from those shared curves. This avoids loft wire remapping.
    bottom_fibers = [cq.Edge.makeLine(vector(bottom_points[0][i]),
                                      vector(bottom_points[1][i]))
                     for i in range(len(edges))]
    top_fibers = [cq.Edge.makeLine(vector(top_points[0][i]),
                                   vector(top_points[1][i]))
                  for i in range(len(edges))]
    if any(edge.isNull() or not edge.isValid() for edge in (*bottom_fibers, *top_fibers)):
        raise ValueError("shared-field slot contains an invalid longitudinal fiber")

    faces = [cq.Face.makeRuledSurface(bottom_fibers[i], bottom_fibers[i + 1])
             for i in range(len(edges) - 1)]
    faces.extend((cq.Face.makeRuledSurface(bottom_fibers[0], top_fibers[0]),
                  cq.Face.makeRuledSurface(bottom_fibers[-1], top_fibers[-1])))
    faces.append(polygon_face([top_points[0][0], top_points[1][0],
                               top_points[1][-1], top_points[0][-1]],
                              "shared-field slot top"))
    faces.append(polygon_face([*bottom_points[0], *reversed(top_points[0])],
                              "shared-field slot q0 cap"))
    faces.append(polygon_face([*bottom_points[1], *reversed(top_points[1])],
                              "shared-field slot q1 cap"))
    if len(faces) != len(edges) + 4 or any(face.isNull() or not face.isValid() for face in faces):
        raise ValueError("shared-field slot source faces are incomplete or invalid")

    sewing = BRepBuilderAPI_Sewing(1e-6, True, True, True, False)
    for face in faces:
        sewing.Add(face.wrapped)
    sewing.Perform()
    sewn = cq.Shape.cast(sewing.SewedShape())
    shells = sewn.Shells()
    if len(shells) != 1 or not sewn.isValid():
        raise ValueError(
            f"shared-field slot faces did not sew to one valid shell: "
            f"shape={sewn.ShapeType()} shells={len(shells)} valid={sewn.isValid()}")
    fix = ShapeFix_Shell(shells[0].wrapped)
    fix.Perform()
    shell = cq.Shell(fix.Shell())
    cutter = cq.Solid.makeSolid(shell)
    if not BRepLib.OrientClosedSolid_s(cutter.wrapped):
        raise ValueError("shared-field slot shell is not closed and orientable")
    if cutter.isNull() or not cutter.isValid() or len(cutter.Solids()) != 1:
        raise ValueError(
            f"invalid shared-field slot cutter: valid={cutter.isValid()} "
            f"solids={len(cutter.Solids())} volume={cutter.Volume()}")
    return cutter


def contact_notch_cutter(record, detail, board_sampler, target_shape, upper_safety_mm=5.0):
    """Extend the shared W_i motherboard bottom through the complete real likou overlap."""
    edges = detail["I_front_edge_fiber_records"]
    if not edges or any("q_S_mm" not in edge for edge in edges):
        raise ValueError(f"{record['family']} {record['index']}: missing shared likou-edge intersections")
    stock_box = target_shape.BoundingBox()
    upper_z = float(stock_box.zmax) + float(upper_safety_mm)
    # The path samples locate the seat, not the ends of the cutting tool.
    # Carry the longitudinal cutting planes through the entire stock envelope;
    # stopping at path chords leaves thin ribs against the curved stock faces.
    S, U = record["S"], record["unit"]
    stock_q = [(x - float(S[0])) * float(U[0]) +
               (y - float(S[1])) * float(U[1])
               for x in (stock_box.xmin, stock_box.xmax)
               for y in (stock_box.ymin, stock_box.ymax)]
    q_limits = (min(stock_q) - upper_safety_mm, max(stock_q) + upper_safety_mm)
    upper_z = max(upper_z, max(
        bottom_outer_line(record, edge, q, float(edge["v_mm"]),
                          float(detail["E_bottom_center_mm"]))
        for q in q_limits for edge in edges) + float(upper_safety_mm))
    cutter = _contact_notch_cutter_from_fibers(
        record, edges, q_limits, upper_z, float(detail["E_bottom_center_mm"]))
    if not cutter.isValid() or len(cutter.Solids()) != 1 or float(cutter.Volume()) <= 1e-5:
        raise ValueError(
            f"invalid shared-field {record['family']} {record['index']} likou cutter: "
            f"valid={cutter.isValid()} solids={len(cutter.Solids())} volume={cutter.Volume()}")
    return cutter


def cut_contact_likou_notches(ctx):
    by = {p["id"]: p for p in ctx["parts"]}
    target_x = by["likou_x"]["shape"]
    target_y = by["likou_y"]["shape"]
    cutters_x = []
    records = list(zip(ctx["chosen"], ctx["contact_fly_records"]))
    total = len(records)
    print(f"stage05: likou notch cutters start ({total} flyer records)", flush=True)
    cutter_build_started = time.perf_counter()
    for index, (record, detail) in enumerate(records, 1):
        label = f"{record['family']} {int(record['index']):02d}"
        print(f"stage05: likou cutter {index}/{total} {label} start", flush=True)
        started = time.perf_counter()
        cutter = contact_notch_cutter(record, detail, ctx["board_sampler"], target_x)
        cutters_x.append(cutter)
        print(f"stage05: likou cutter {index}/{total} {label} complete ({time.perf_counter() - started:.1f}s)", flush=True)
    cutter_build_seconds = float(time.perf_counter() - cutter_build_started)

    mirror_started = time.perf_counter()
    mirrored_target_x = s2.mirror_shape(ctx["case"], target_x)
    bbox_tolerance_mm = 1e-5
    source_bbox_error = None
    source_bbox_error_reason = None
    try:
        mirrored_source_bbox = exact_brep_bbox_mm(mirrored_target_x)
        source_y_bbox = exact_brep_bbox_mm(target_y)
        source_bbox_error = max(abs(a - b) for a, b in zip(mirrored_source_bbox, source_y_bbox))
    except Exception as exc:
        source_bbox_error_reason = f"exact BRep bounds unavailable: {type(exc).__name__}: {exc}"
    source_volume_error = abs(float(target_x.Volume()) - float(target_y.Volume()))
    source_volume_limit = max(1e-5, float(target_x.Volume()) * 1e-9)
    source_mirror_confirmed = bool(
        source_bbox_error is not None
        and source_bbox_error <= bbox_tolerance_mm
        and source_volume_error <= source_volume_limit
        and mirrored_target_x.isValid()
        and len(mirrored_target_x.Solids()) == 1
        and target_y.isValid()
        and len(target_y.Solids()) == 1)
    fallback_reason = source_bbox_error_reason
    if not source_mirror_confirmed and fallback_reason is None:
        fallback_reason = (
            "source BRep mirror check failed: "
            f"exact_bbox_error={source_bbox_error:.8g}mm "
            f"volume_error={source_volume_error:.8g}mm3")
    sampled_source_bbox_error = None
    try:
        sampled_x_bbox = target_x.BoundingBox()
        sampled_y_bbox = target_y.BoundingBox()
        sampled_mirror_bbox = mirrored_target_x.BoundingBox()
        sampled_pairs = [
            (sampled_mirror_bbox.xmin, sampled_y_bbox.xmin),
            (sampled_mirror_bbox.xmax, sampled_y_bbox.xmax),
            (sampled_mirror_bbox.ymin, sampled_y_bbox.ymin),
            (sampled_mirror_bbox.ymax, sampled_y_bbox.ymax),
            (sampled_mirror_bbox.zmin, sampled_y_bbox.zmin),
            (sampled_mirror_bbox.zmax, sampled_y_bbox.zmax),
        ]
        sampled_source_bbox_error = max(abs(float(a) - float(b)) for a, b in sampled_pairs)
    except Exception:
        pass

    relevant = [c for c in cutters_x if s2.bbox_overlap(target_x, c)]
    if not relevant:
        raise ValueError("no revised flying-rafter seats overlap likou_x")
    before_x = float(target_x.Volume())
    print(f"stage05: likou x boolean start ({len(relevant)}/{len(cutters_x)} cutters overlap)", flush=True)
    x_boolean_started = time.perf_counter()
    cut_x = target_x.cut(cq.Compound.makeCompound(relevant)).clean()
    x_boolean_seconds = float(time.perf_counter() - x_boolean_started)
    if not cut_x.isValid() or len(cut_x.Solids()) != 1 or cut_x.Volume() >= before_x:
        raise ValueError(
            f"revised likou seat cut failed for x: valid={cut_x.isValid()} "
            f"solids={len(cut_x.Solids())} before={before_x} after={cut_x.Volume()}")
    by["likou_x"]["shape"] = cut_x
    print(f"stage05: likou x boolean complete ({x_boolean_seconds:.1f}s)", flush=True)

    cut_volume_x = float(cut_x.Volume())
    before_y = float(target_y.Volume())
    cut_y = None
    y_method = ""
    y_fallback_reason = fallback_reason
    y_mirror_seconds = 0.0
    y_boolean_seconds = 0.0
    relevant_y = []
    candidate_y_bbox_error = None
    candidate_y_volume_error = None
    if source_mirror_confirmed:
        y_mirror_started = time.perf_counter()
        candidate_y = s2.mirror_shape(ctx["case"], cut_x).clean()
        y_mirror_seconds = float(time.perf_counter() - y_mirror_started)
        candidate_y_volume_error = abs(cut_volume_x - float(candidate_y.Volume()))
        candidate_valid = bool(candidate_y.isValid() and len(candidate_y.Solids()) == 1
                               and float(candidate_y.Volume()) < before_y)
        try:
            candidate_y_bbox_error = reflected_bbox_error_mm(
                exact_brep_bbox_mm(cut_x), exact_brep_bbox_mm(candidate_y))
        except Exception as exc:
            candidate_valid = False
            y_fallback_reason = f"exact cut BRep bounds unavailable: {type(exc).__name__}: {exc}"
        candidate_valid = bool(candidate_valid
                                and candidate_y_volume_error <= max(1e-5, abs(cut_volume_x) * 1e-9)
                                and candidate_y_bbox_error is not None
                                and candidate_y_bbox_error <= bbox_tolerance_mm)
        if candidate_valid:
            cut_y = candidate_y
            y_method = "mirror validated x-side cut; one Boolean total"
        else:
            if y_fallback_reason is None:
                y_fallback_reason = (
                    "mirrored cut candidate failed validity, solid-count, exact-BBox, or volume check: "
                    f"bbox_error={candidate_y_bbox_error}; volume_error={candidate_y_volume_error}")
    if cut_y is None:
        mirrored_cutters_y = [s2.mirror_shape(ctx["case"], c).clean()
                              for c in relevant]
        relevant_y = [c for c in mirrored_cutters_y if s2.bbox_overlap(target_y, c)]
        if relevant_y:
            y_boolean_started = time.perf_counter()
            cut_y = target_y.cut(cq.Compound.makeCompound(relevant_y)).clean()
            y_boolean_seconds = float(time.perf_counter() - y_boolean_started)
            y_method = "separate y-side compound Boolean after mirror check fallback"
        else:
            cut_y = target_y
            y_method = "no mirrored cutters overlap y-side target; retained valid target"
        print(f"stage05: likou y mirror fallback ({y_method}; {y_boolean_seconds:.3f}s)", flush=True)

    if not cut_y.isValid() or len(cut_y.Solids()) != 1:
        raise ValueError(
            f"likou y cut is invalid: valid={cut_y.isValid()} solids={len(cut_y.Solids())}")
    cut_volume_y = float(cut_y.Volume())
    if relevant_y and cut_volume_y >= before_y:
        raise ValueError(
            f"separate likou y Boolean removed no volume: before={before_y} after={cut_volume_y}")
    cut_volume_limit = max(1e-5, abs(cut_volume_x) * 1e-9)
    cut_bbox_error = None
    try:
        cut_bbox_error = reflected_bbox_error_mm(exact_brep_bbox_mm(cut_x), exact_brep_bbox_mm(cut_y))
    except Exception:
        pass
    by["likou_y"]["shape"] = cut_y
    mirror_pair_check_seconds = float(time.perf_counter() - mirror_started)
    if y_method.startswith("mirror"):
        print(f"stage05: likou y mirrored from x ({y_mirror_seconds:.3f}s; no second Boolean)", flush=True)

    summary = [
        {"likou_id": "likou_x", "candidate_count": len(cutters_x),
         "bbox_overlap_cutter_count": len(relevant), "valid_single_solid": True,
         "removed_volume_mm3": before_x - cut_volume_x,
         "cut_method": "one exact compound cut of the x-side likou followed by clean",
         "floor_rule": "fiberwise flyer seat floor; upper cutter limit is target BRep zmax + 5mm",
         "boolean_seconds": x_boolean_seconds},
        {"likou_id": "likou_y", "candidate_count": len(cutters_x),
         "bbox_overlap_cutter_count": (len(relevant) if y_method.startswith("mirror")
                                       else len(relevant_y)),
         "valid_single_solid": True,
         "removed_volume_mm3": before_y - cut_volume_y,
         "cut_method": y_method,
         "floor_rule": "exact reflection of the x-side fiberwise flyer seat floor",
         "boolean_seconds": y_boolean_seconds, "mirror_seconds": y_mirror_seconds,
         "fallback_reason": y_fallback_reason},
    ]
    ctx["likou_contact_notches"] = summary
    ctx["likou_notch_performance"] = {
        "cutter_build_seconds": cutter_build_seconds,
        "x_boolean_seconds": x_boolean_seconds,
        "y_mirror_seconds": y_mirror_seconds,
        "mirror_pair_check_seconds": mirror_pair_check_seconds,
        "y_boolean_seconds": y_boolean_seconds,
        "y_cut_method": y_method,
        "y_fallback_reason": y_fallback_reason,
        "source_symmetry": {
            "rule": "stage03 creates likou_y by reflecting likou_x; stage04 extension is reflected from the same x extension",
            "verification_basis": "BRepBndLib.AddOptimal_s(useTriangulation=False,useShapeTolerance=False) plus exact volume equality",
            "verified": bool(source_mirror_confirmed),
            "source_bbox_max_abs_error_mm": (float(source_bbox_error) if source_bbox_error is not None else None),
            "ordinary_tessellation_bbox_diagnostic_max_abs_error_mm": (
                float(sampled_source_bbox_error) if sampled_source_bbox_error is not None else None),
            "source_volume_abs_error_mm3": float(source_volume_error),
            "source_volume_tolerance_mm3": float(source_volume_limit),
            "bbox_tolerance_mm": float(bbox_tolerance_mm),
            "fallback_reason": fallback_reason,
        },
        "cut_symmetry": {
            "mirrored_bbox_max_abs_error_mm": (float(cut_bbox_error) if cut_bbox_error is not None else None),
            "cut_volume_abs_error_mm3": float(abs(cut_volume_x - cut_volume_y)),
            "cut_volume_tolerance_mm3": float(cut_volume_limit),
            "mirrored_candidate_bbox_error_mm": (
                float(candidate_y_bbox_error) if candidate_y_bbox_error is not None else None),
            "mirrored_candidate_volume_error_mm3": (
                float(candidate_y_volume_error) if candidate_y_volume_error is not None else None),
            "matches": bool(cut_bbox_error is not None and cut_bbox_error <= bbox_tolerance_mm
                             and abs(cut_volume_x - cut_volume_y) <= cut_volume_limit),
        },
    }
    return summary


def build_scraping_member(record, board_sampler, inboard=200.0, overlap=60.0,
                          width=55.0, board_overlap=0.2, fly_overlap=0.2,
                          min_inboard_depth=8.0):
    """A ruled wedge rests on the actual lower-board mesh and overlaps the fly at R."""
    qR = float(record["q_R_mm"])
    q0 = qR - float(inboard)
    q1 = qR + float(overlap)
    w = np.asarray(record["width_axis"], dtype=float)
    half = float(width) / 2.0
    v_samples = np.linspace(-half, half, 9)

    def board_profile(q):
        C = center_at_q(record, q)
        vals = []
        for v in v_samples:
            xy = (C[0] + w[0] * float(v), C[1] + w[1] * float(v))
            hit = board_sampler.z_range(float(xy[0]), float(xy[1]))
            if hit is None:
                return None
            vals.append(float(hit["max_z_mm"]))
        return vals

    # The support must remain seated on measured board triangles over its whole
    # footprint, not only at the rafter's cropped end.
    q_values = [q0 + (qR - q0) * i / 10.0 for i in range(11)] + [qR + overlap * i / 3.0 for i in (1, 2, 3)]
    q_values = [float(v) for v in q_values]
    board_by_q = {}
    for q in q_values:
        zs = board_profile(q)
        if zs is None:
            raise ValueError(f"scraping support {record['index']} lacks full-width lower-board coverage at q={q:.2f}")
        board_by_q[q] = zs

    board_R = board_by_q[qR]
    end_thickness = []
    for v, zb in zip(v_samples, board_R):
        b, _ = fly_bottom_and_top(record, qR, float(v))
        end_thickness.append(max(0.4, b - zb + board_overlap + fly_overlap))

    sections = []
    min_depth = float("inf")
    max_depth = -float("inf")
    clearance_samples = []
    for q in q_values:
        C = center_at_q(record, q)
        zs = board_by_q[q]
        bottoms = [z - board_overlap for z in zs]
        tops = []
        if q <= qR + 1e-8:
            t = max(0.0, min(1.0, (q - q0) / max(qR - q0, 1e-9)))
            for v, zbot, hR in zip(v_samples, bottoms, end_thickness):
                thick = float(min_inboard_depth) * (1.0 - t) + hR * t
                tops.append(zbot + thick)
        else:
            for v, zbot, zb in zip(v_samples, bottoms, zs):
                b, _ = fly_bottom_and_top(record, q, float(v))
                tops.append(b + fly_overlap)
                clearance_samples.append(float(b - zb))
        for zbot, ztop in zip(bottoms, tops):
            depth = ztop - zbot
            min_depth = min(min_depth, depth)
            max_depth = max(max_depth, depth)
            if depth < 0.35:
                raise ValueError(f"scraping support {record['index']} would taper below 0.35mm at q={q:.2f}")
        plan = [(C[0] + float(v) * w[0], C[1] + float(v) * w[1]) for v in v_samples]
        section = [(x, y, z) for (x, y), z in zip(plan, bottoms)]
        section += [(x, y, z) for (x, y), z in zip(reversed(plan), reversed(tops))]
        sections.append(section)

    shape = s2.loft_sections(sections)
    return shape, {
        "inboard_extension_mm": float(inboard), "rafter_overlap_mm": float(overlap),
        "width_mm": float(width), "lower_board_underlap_mm": float(board_overlap),
        "rafter_underface_overlap_mm": float(fly_overlap),
        "minimum_inboard_end_depth_mm": float(min_inboard_depth),
        "q_range_mm": [q0, q1], "q_R_mm": qR,
        "board_coverage_samples": len(q_values) * len(v_samples),
        "board_missing_samples": 0,
        "sampled_support_depth_range_mm": [float(min_depth), float(max_depth)],
        "overlap_zone_clearance_range_mm": [float(min(clearance_samples)), float(max(clearance_samples))],
        "construction": "9横向采样点拟合实际下层板三角网顶面；R以内渐楔，R至R+60沿飞椽底面搭接。",
    }


def add_scraping_blocks(ctx):
    parts = ctx["parts"]
    fan = sorted((r for r in ctx["chosen"] if r["family"] == "fan"), key=lambda r: int(r["index"]))
    records = []
    for r in fan:
        if r["type"] == "ordinary_tail_board_supported":
            records.append({"index": int(r["index"]), "side": "x", "source_rafter_id": f"flying_fan_x_{int(r['index']):02d}",
                            "classification": "full_R0_tail; no added scraping member; qR board clearance remains about 0.1mm"})
            continue
        shape, info = build_scraping_member(r, ctx["board_sampler"])
        idx = int(r["index"])
        pid_x = f"scraping_fan_x_{idx:02d}"
        pid_y = f"scraping_fan_y_{idx:02d}"
        add(parts, pid_x, f"擦脚木拟合基准翼-{idx:02d}", "scraping_blocks", shape)
        mirrored = s2.mirror_shape(ctx["case"], shape)
        add(parts, pid_y, f"擦脚木拟合镜像翼-{idx:02d}", "scraping_blocks", mirrored)
        records.append({"index": idx, "side": "x", "source_rafter_id": f"flying_fan_x_{idx:02d}",
                        "part_ids": [pid_x, pid_y], "classification": "geometrically trimmed tail support interface; not a historical type assertion",
                        **info})
    return records


def fan_top_at_q(record, t):
    q = float(t) * float(record["l_SE_mm"])
    qI = float(record["q_I_center_mm"])
    lam = (q - qI) / (float(record["l_SE_mm"]) - qI)
    bottom = (1.0 - lam) * float(record["H_I_center_mm"]) + lam * float(record["E_bottom_center_mm"])
    top = (1.0 - lam) * (float(record["H_I_center_mm"]) + float(record["dI_world_Z_mm"])) + \
        lam * (float(record["E_bottom_center_mm"]) + float(record["E_depth_world_Z_mm"]))
    return q, center_at_q(record, q), top


def interp_linear_extrap(values, u):
    """Interpolate within the fan, and linearly extrapolate the two edge half bays."""
    n = len(values)
    if n < 2:
        raise ValueError("need at least two fan rays for interpolation")
    u = float(u)
    if u < 0.0:
        i = 0
        f = u
    elif u > n - 1:
        i = n - 2
        f = u - i
    else:
        i = min(int(math.floor(u)), n - 2)
        f = u - i
    return values[i] * (1.0 - f) + values[i + 1] * f


def rollboard_lower_point(records, u, t, underface=0.2):
    samples = [fan_top_at_q(r, float(t)) for r in records]
    x = interp_linear_extrap([d[1][0] for d in samples], u)
    y = interp_linear_extrap([d[1][1] for d in samples], u)
    z = interp_linear_extrap([d[2] for d in samples], u) + float(underface)
    return np.asarray([x, y, z], dtype=float)


def _width_linear_extrapolate(xs, ys, x):
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    x = float(x)
    if len(xs) < 2:
        raise ValueError("宽向直纹外推至少需要两个实际纤维结点")
    if x < xs[0]:
        i0, i1 = 0, 1
    elif x > xs[-1]:
        i0, i1 = len(xs) - 2, len(xs) - 1
    else:
        i1 = max(1, min(len(xs) - 1, int(np.searchsorted(xs, x, side="right"))))
        i0 = i1 - 1
    if abs(float(xs[i1] - xs[i0])) <= 1e-12:
        raise ValueError("宽向结点重合，不能插值上表面")
    f = (x - float(xs[i0])) / float(xs[i1] - xs[i0])
    return float(ys[i0] + f * (ys[i1] - ys[i0]))


def _flyer_top_at_width_knot(detail, edge_index, q):
    edge = detail["I_front_edge_fiber_records"][int(edge_index)]
    q_i = float(edge["q_front_mm"])
    q = float(q)
    if q < q_i - 1e-9:
        root = detail["root_fiber_records"][int(edge_index)]
        q_r = float(root["q_root_mm"])
        z_r = top_motherline(detail, edge, float(edge["dI_world_Z_mm"]), q_r,
                             float(edge["v_mm"]), float(detail["E_bottom_center_mm"]),
                             float(detail["E_depth_world_Z_mm"]))
        z_i = float(edge["H_front_mm"]) + float(edge["dI_world_Z_mm"])
        return linear_value_at(q, q_r, z_r, q_i, z_i)
    return top_motherline(detail, edge, float(edge["dI_world_Z_mm"]), q,
                          float(edge["v_mm"]), float(detail["E_bottom_center_mm"]),
                          float(detail["E_depth_world_Z_mm"]))


def fan_upper_z_at_q(detail, q, v_mm):
    """Sample the shared ruled top at the same longitudinal lambda across width."""
    edges = detail["I_front_edge_fiber_records"]
    knot_indices = [int(i) for i in detail["width_knot_indices"]]
    knot_edges = [edges[i] for i in knot_indices]
    knot_vs = [float(edge["v_mm"]) for edge in knot_edges]
    v = float(v_mm)
    q_i_v = _width_linear_extrapolate(
        [float(edge["v_mm"]) for edge in edges],
        [float(edge["q_front_mm"]) for edge in edges], v)
    q_e_v = _width_linear_extrapolate(
        [float(edge["v_mm"]) for edge in edges],
        [float(edge.get("q_E_mm", detail["l_SE_mm"])) for edge in edges], v)
    roots = detail.get("root_fiber_records", [])
    if roots:
        root_vs = [float(root.get("v_mm", edges[index]["v_mm"]))
                   for index, root in enumerate(roots)]
        q_r_v = _width_linear_extrapolate(
            root_vs, [float(root["q_root_mm"]) for root in roots], v)
    else:
        q_r_v = q_i_v
    if float(q) >= q_i_v:
        if abs(q_e_v - q_i_v) <= 1e-12:
            raise ValueError(f"fan {detail.get('index')}: shared I/E longitudinal span is zero")
        longitudinal_lambda = (float(q) - q_i_v) / (q_e_v - q_i_v)
        knot_zs = []
        for edge_index in knot_indices:
            edge = edges[edge_index]
            q_i = float(edge["q_front_mm"])
            q_e = float(edge.get("q_E_mm", detail["l_SE_mm"]))
            q_on_rail = q_i + longitudinal_lambda * (q_e - q_i)
            knot_zs.append(top_motherline(
                detail, edge, float(edge["dI_world_Z_mm"]), q_on_rail,
                float(edge["v_mm"]), float(detail["E_bottom_center_mm"]),
                float(detail["E_depth_world_Z_mm"])))
    else:
        if abs(q_i_v - q_r_v) <= 1e-12:
            raise ValueError(f"fan {detail.get('index')}: shared R/I tail span is zero")
        longitudinal_lambda = (float(q) - q_r_v) / (q_i_v - q_r_v)
        knot_zs = []
        for edge_index in knot_indices:
            edge = edges[edge_index]
            root = roots[edge_index]
            q_i = float(edge["q_front_mm"])
            q_r = float(root["q_root_mm"])
            q_on_rail = q_r + longitudinal_lambda * (q_i - q_r)
            knot_zs.append(top_motherline(
                detail, edge, float(edge["dI_world_Z_mm"]), q_on_rail,
                float(edge["v_mm"]), float(detail["E_bottom_center_mm"]),
                float(detail["E_depth_world_Z_mm"])))
    return _width_linear_extrapolate(knot_vs, knot_zs, float(v_mm))


def fan_upper_surface_at_q(detail, q, v_mm):
    """Sample an actual flyer side/width top point for bridge and border strips."""
    x, y = xy_at_q(detail, float(q), float(v_mm))
    z = fan_upper_z_at_q(detail, float(q), float(v_mm))
    return np.asarray([x, y, z], dtype=float)


def _band_row_points(row):
    points = row["points_low_to_high"]
    return (np.asarray(points[0], dtype=float), np.asarray(points[1], dtype=float))


def _vertical_offset_from_tangents(tangent_long, tangent_cross, thickness, label):
    normal = np.cross(np.asarray(tangent_long, dtype=float), np.asarray(tangent_cross, dtype=float))
    norm = float(np.linalg.norm(normal))
    if norm <= 1e-10:
        raise ValueError(f"{label}: 上板局部底面法向退化")
    cosine = abs(float(normal[2])) / norm
    if cosine < 0.5:
        raise ValueError(f"{label}: 上板底面过陡，无法稳定换算竖向厚度 n_z={cosine:.5g}")
    return float(thickness) / cosine, cosine


def _support_band_offsets(rows, v_low, v_high, thickness, label):
    """Approximate normal thickness using differences confined to one flyer band."""
    offsets = []
    cosines = []
    for j, row in enumerate(rows):
        region = row.get("region")
        same_region = [i for i, candidate in enumerate(rows) if candidate.get("region") == region]
        position = same_region.index(j)
        if len(same_region) >= 2:
            i0 = same_region[max(0, position - 1)]
            i1 = same_region[min(len(same_region) - 1, position + 1)]
        else:
            i0, i1 = max(0, j - 1), min(len(rows) - 1, j + 1)
        if i0 == i1:
            raise ValueError(f"{label}: 上板实际断面不足两个站位")
        p_lo, p_hi = _band_row_points(row)
        p0_lo, p0_hi = _band_row_points(rows[i0])
        p1_lo, p1_hi = _band_row_points(rows[i1])
        q_low_0, q_low_1 = float(rows[i0]["q_low_mm"]), float(rows[i1]["q_low_mm"])
        q_high_0, q_high_1 = float(rows[i0]["q_high_mm"]), float(rows[i1]["q_high_mm"])
        if abs(q_low_1 - q_low_0) <= 1e-9 or abs(q_high_1 - q_high_0) <= 1e-9:
            raise ValueError(f"{label}: 纵向上板断面坐标重合")
        long_lo = (p1_lo - p0_lo) / (q_low_1 - q_low_0)
        long_hi = (p1_hi - p0_hi) / (q_high_1 - q_high_0)
        cross = (p_hi - p_lo) / max(float(v_high) - float(v_low), 1e-9)
        z0, c0 = _vertical_offset_from_tangents(long_lo, cross, thickness, label)
        z1, c1 = _vertical_offset_from_tangents(long_hi, cross, thickness, label)
        offsets.append((z0, z1))
        cosines.append((c0, c1))
    return offsets, cosines


def build_support_rollboard_tile(detail, band, underface=0.2, thickness=13.75,
                                 offsets_override=None, rows_override=None):
    """Build one board support from the flyer's exact saved ruled-band sections."""
    rows = list(band["sections"] if rows_override is None else rows_override)
    if len(rows) < 2:
        raise ValueError(f"fan {detail['index']} band {band['band_index']}: 缺少实际顶面断面")
    v_low, v_high = float(band["v_low_mm"]), float(band["v_high_mm"])
    base_offsets, cosines = _support_band_offsets(
        rows, v_low, v_high, thickness,
        f"fan {detail['index']} band {band['band_index']}")
    offsets = (base_offsets if offsets_override is None else
               [(float(pair[0]), float(pair[1])) for pair in offsets_override])
    sections = []
    for row, (zoff_low, zoff_high) in zip(rows, offsets):
        p_low, p_high = _band_row_points(row)
        lower_low = p_low + np.asarray([0.0, 0.0, float(underface)])
        lower_high = p_high + np.asarray([0.0, 0.0, float(underface)])
        top_high = lower_high + np.asarray([0.0, 0.0, zoff_high])
        top_low = lower_low + np.asarray([0.0, 0.0, zoff_low])
        sections.append([tuple(lower_low), tuple(lower_high), tuple(top_high), tuple(top_low)])
    shape = s2.loft_sections(sections)
    all_offsets = [value for pair in offsets for value in pair]
    all_cosines = [value for pair in cosines for value in pair]
    normal_equivalent = [offset * cosine
                         for offset_pair, cosine_pair in zip(offsets, cosines)
                         for offset, cosine in zip(offset_pair, cosine_pair)]
    return shape, offsets, {
        "station_count": len(rows),
        "width_band_mm": [v_low, v_high],
        "vertical_offset_range_mm": [float(min(all_offsets)), float(max(all_offsets))],
        "normal_equivalent_thickness_range_mm": [float(min(normal_equivalent)),
                                                 float(max(normal_equivalent))],
        "vertical_normal_cosine_range": [float(min(all_cosines)), float(max(all_cosines))],
        "normal_thickness_is_sampled_local_approximation": True,
        "normal_difference_scope": "only adjacent longitudinal stations and endpoints within this actual width band",
    }


def _old_rollboard_domain_polygon(records):
    """Extract only the former board's XY footprint, including both half bays."""
    count = len(records)
    u_values = [-0.5] + [float(i) for i in range(count)] + [float(count) - 0.5]
    near = [rollboard_lower_point(records, u, 0.0)[:2].tolist() for u in u_values]
    far = [rollboard_lower_point(records, u, 1.0)[:2].tolist() for u in u_values]
    polygon = near + list(reversed(far))
    cleaned = []
    for point in polygon:
        if not cleaned or math.dist(cleaned[-1], point) > 1e-7:
            cleaned.append(point)
    if len(cleaned) > 2 and math.dist(cleaned[0], cleaned[-1]) <= 1e-7:
        cleaned.pop()
    if len(cleaned) < 4:
        raise ValueError("旧卷戗板XY覆盖域Ω退化，无法裁剪新承托板")
    area2 = sum(cleaned[i][0] * cleaned[(i + 1) % len(cleaned)][1] -
                cleaned[(i + 1) % len(cleaned)][0] * cleaned[i][1]
                for i in range(len(cleaned)))
    if abs(area2) <= 1e-6:
        raise ValueError("旧卷戗板XY覆盖域Ω面积接近零")
    if area2 < 0.0:
        cleaned.reverse()
    return cleaned


def _clip_upperboard_piece(shape, clips, label):
    clipped = shape
    for clip in clips:
        if not s2.bbox_overlap(clipped, clip):
            return None
        clipped = clipped.intersect(clip).clean()
        if clipped.isNull() or not clipped.Solids():
            return None
    if not clipped.isValid() or len(clipped.Solids()) != 1 or float(clipped.Volume()) <= 1e-5:
        raise ValueError(f"卷戗板裁剪后无效: {label}; valid={clipped.isValid()} solids={len(clipped.Solids())} volume={clipped.Volume()}")
    return clipped


def _fan_side_surface(detail, q, side):
    v = -0.5 * float(detail["width_mm"]) if side == "A" else 0.5 * float(detail["width_mm"])
    edge = detail["I_front_edge_fiber_records"][0 if side == "A" else -1]
    q_s = float(edge.get("q_S_mm", 0.0))
    q_e = float(edge.get("q_E_mm", detail["l_SE_mm"]))
    center_fraction = float(q) / max(1e-12, float(detail["l_SE_mm"]))
    q_side = q_s + center_fraction * (q_e - q_s)
    return fan_upper_surface_at_q(detail, q_side, v)


def _fan_side_vertical_offset(detail, side, q, offset_cache):
    bands = detail["upper_surface_width_bands"]
    band = bands[0] if side == "A" else bands[-1]
    band_index = int(band["band_index"])
    row_offsets = offset_cache[band_index]
    endpoint_index = 0 if side == "A" else 1
    field = "q_low_mm" if side == "A" else "q_high_mm"
    rows = band["sections"]
    q_rows = [float(row[field]) for row in rows]
    z_rows = [float(pair[endpoint_index]) for pair in row_offsets]
    order = np.argsort(q_rows)
    edge = detail["I_front_edge_fiber_records"][0 if side == "A" else -1]
    q_s = float(edge.get("q_S_mm", 0.0))
    q_e = float(edge.get("q_E_mm", detail["l_SE_mm"]))
    center_fraction = float(q) / max(1e-12, float(detail["l_SE_mm"]))
    q_side = q_s + center_fraction * (q_e - q_s)
    return _width_linear_extrapolate([q_rows[i] for i in order],
                                     [z_rows[i] for i in order], q_side)


def _fraction_stations(details, sides=None):
    fractions = set(float(v) for v in np.linspace(0.0, 1.0, 17))
    for detail, side in (sides or []):
        edge = detail["I_front_edge_fiber_records"][0 if side == "A" else -1]
        fractions.add(float(edge.get("q_S_mm", 0.0)) / float(detail["l_SE_mm"]))
    return sorted(min(1.0, max(0.0, value)) for value in fractions)


def _omega_band_q_bounds(detail, band, omega_polygon, margin_mm=0.0):
    """Project Ω ∩ an infinite flyer-width band onto that flyer's q axis."""
    origin = np.asarray(detail["S"], dtype=float)
    unit = np.asarray(detail["unit"], dtype=float)
    width_axis = np.asarray(detail["width_axis"], dtype=float)
    v_low, v_high = float(band["v_low_mm"]), float(band["v_high_mm"])
    polygon = [np.asarray(point, dtype=float) for point in omega_polygon]
    candidates = []
    tolerance = 1e-8

    def qv(point):
        delta = point - origin
        return float(np.dot(delta, unit)), float(np.dot(delta, width_axis))

    for point in polygon:
        _, v = qv(point)
        if v_low - tolerance <= v <= v_high + tolerance:
            candidates.append(point)
    for index, point_a in enumerate(polygon):
        point_b = polygon[(index + 1) % len(polygon)]
        _, v_a = qv(point_a)
        _, v_b = qv(point_b)
        if abs(v_b - v_a) <= tolerance:
            continue
        for boundary in (v_low, v_high):
            fraction = (boundary - v_a) / (v_b - v_a)
            if -tolerance <= fraction <= 1.0 + tolerance:
                candidates.append(point_a + min(1.0, max(0.0, fraction)) * (point_b - point_a))
    if not candidates:
        raise ValueError(
            f"fan {detail['index']} width band {band['band_index']}: Ω与宽带不相交")
    unique_candidates = []
    for point in candidates:
        if not any(float(np.linalg.norm(point - existing)) <= 1e-7
                   for existing in unique_candidates):
            unique_candidates.append(point)
    q_values = [qv(point)[0] for point in unique_candidates]
    margin = max(0.0, float(margin_mm))
    return min(q_values) - margin, max(q_values) + margin, len(unique_candidates)


def _extend_support_band_rows(rows, offsets, q_min, q_max, label, q_vertex_count=None,
                              margin_mm=0.0):
    """Add only endpoint rows, extending each band terminal ruled section by shared λ."""
    if len(rows) < 2 or len(offsets) != len(rows):
        raise ValueError(f"{label}: Ω端延拓需要至少两个且逐行配对的真实断面")
    q_min, q_max = float(q_min), float(q_max)
    first_q = [float(rows[0]["q_low_mm"]), float(rows[0]["q_high_mm"])]
    second_q = [float(rows[1]["q_low_mm"]), float(rows[1]["q_high_mm"])]
    previous_q = [float(rows[-2]["q_low_mm"]), float(rows[-2]["q_high_mm"])]
    last_q = [float(rows[-1]["q_low_mm"]), float(rows[-1]["q_high_mm"])]
    start_dq = [second_q[j] - first_q[j] for j in range(2)]
    end_dq = [last_q[j] - previous_q[j] for j in range(2)]
    if min(start_dq + end_dq) <= 1e-9:
        raise ValueError(f"{label}: 首末断面q没有沿S→E单调递增")
    start_lambda = max([0.0] + [(first_q[j] - q_min) / start_dq[j] for j in range(2)])
    end_lambda = max([0.0] + [(q_max - last_q[j]) / end_dq[j] for j in range(2)])

    def extrapolate_row(base, adjacent, factor, direction, region):
        base_points = [np.asarray(point, dtype=float) for point in base["points_low_to_high"]]
        adjacent_points = [np.asarray(point, dtype=float) for point in adjacent["points_low_to_high"]]
        # Both calls pass the adjacent row inward: first->next at the start,
        # last->previous at the end. Subtracting that inward delta extrapolates
        # outwards at either endpoint.
        sign = -1.0
        points = [base_points[j] + sign * factor * (adjacent_points[j] - base_points[j])
                  for j in range(2)]
        row = dict(base)
        row.update({
            "region": region,
            "omega_extension_lambda": float(factor),
            "q_low_mm": float(base["q_low_mm"] + sign * factor *
                               (float(adjacent["q_low_mm"]) - float(base["q_low_mm"]))),
            "q_high_mm": float(base["q_high_mm"] + sign * factor *
                                (float(adjacent["q_high_mm"]) - float(base["q_high_mm"]))),
            "points_low_to_high": [[float(c) for c in point] for point in points],
        })
        return row

    extended_rows = list(rows)
    extended_offsets = [(float(pair[0]), float(pair[1])) for pair in offsets]
    if start_lambda > 1e-12:
        start_row = extrapolate_row(rows[0], rows[1], start_lambda, "start",
                                    "omega_endpoint_extension_start")
        start_offsets = tuple(float(offsets[0][j]) - start_lambda *
                              (float(offsets[1][j]) - float(offsets[0][j]))
                              for j in range(2))
        extended_rows.insert(0, start_row)
        extended_offsets.insert(0, start_offsets)
    if end_lambda > 1e-12:
        end_row = extrapolate_row(rows[-1], rows[-2], end_lambda, "end",
                                  "omega_endpoint_extension_end")
        end_offsets = tuple(float(offsets[-1][j]) + end_lambda *
                            (float(offsets[-1][j]) - float(offsets[-2][j]))
                            for j in range(2))
        extended_rows.append(end_row)
        extended_offsets.append(end_offsets)
    minimum_offset = min(value for pair in extended_offsets for value in pair)
    if minimum_offset <= 1e-6:
        raise ValueError(
            f"{label}: Ω端无椽延拓区板厚非正 ({minimum_offset:.6g}mm), "
            f"lambda=({start_lambda:.6g},{end_lambda:.6g})")
    return extended_rows, extended_offsets, {
        "omega_band_q_bounds_mm": [q_min, q_max],
        "omega_band_q_vertex_count": q_vertex_count,
        "omega_endpoint_margin_mm": float(margin_mm),
        "endpoint_extension_lambda_start": float(start_lambda),
        "endpoint_extension_lambda_end": float(end_lambda),
        "endpoint_extension_added_rows": int(start_lambda > 1e-12) + int(end_lambda > 1e-12),
        "endpoint_extension_min_vertical_thickness_mm": float(minimum_offset),
        "extension_rule": "Ω与该宽带无限平面条带交集顶点投影到q轴；首末真实断面共同λ外推，仅新增端行",
    }


def _shared_omega_fraction_bounds(details, omega_endpoints, margin_mm=UPPERBOARD_DOMAIN_ENDPOINT_MARGIN_MM):
    """Derive common flyer fractions covering the local Ω endpoint vertices."""
    if not details:
        raise ValueError("卷戗板端边延伸缺少飞椽直线")
    fractions = [0.0, 1.0]
    lengths = []
    for detail in details:
        length = float(detail["l_SE_mm"])
        if length <= 0.0:
            raise ValueError("卷戗板端边延伸需要有效飞椽纵向长度")
        lengths.append(length)
        origin = np.asarray(detail["S"], dtype=float)
        unit = np.asarray(detail["unit"], dtype=float)
        for point in omega_endpoints:
            q = float(np.dot(np.asarray(point, dtype=float) - origin, unit))
            fractions.append(q / length)
    margin_fraction = max(0.0, float(margin_mm)) / min(lengths)
    return min(fractions) - margin_fraction, max(fractions) + margin_fraction, {
        "shared_longitudinal_lambda_bounds": [min(fractions) - margin_fraction,
                                               max(fractions) + margin_fraction],
        "endpoint_margin_mm": float(margin_mm),
        "bound_source": "对应Ω局部起终边端点投影到各所选飞椽q轴后取共同λ范围",
    }


def _assert_no_line_crossing(point0, point1, lambda_min, lambda_max, label):
    """A linearly ruled bridge must keep a nonzero cross-gap through its Ω extension."""
    p0 = np.asarray(point0(0.0), dtype=float)[:2]
    p1 = np.asarray(point1(0.0), dtype=float)[:2]
    q0 = np.asarray(point0(1.0), dtype=float)[:2]
    q1 = np.asarray(point1(1.0), dtype=float)[:2]
    separation0, separation_delta = p1 - p0, (q1 - q0) - (p1 - p0)
    denom = float(np.dot(separation_delta, separation_delta))
    closest = (float(np.dot(separation0, separation_delta) / denom) * -1.0
               if denom > 1e-16 else float(lambda_min))
    closest = min(float(lambda_max), max(float(lambda_min), closest))
    minimum = float(np.linalg.norm(separation0 + closest * separation_delta))
    if minimum <= 1e-3:
        raise ValueError(
            f"{label}: Ω端λ延拓将使两侧直线相交或退化; "
            f"lambda=[{lambda_min:.6g},{lambda_max:.6g}], min_gap={minimum:.6g}mm")
    return minimum


def _build_bridge_tile(point0, point1, fractions, underface, thickness,
                       endpoint0_offsets=None, endpoint1_offsets=None, label="board bridge"):
    tops0 = [np.asarray(point0(float(t)), dtype=float) for t in fractions]
    tops1 = [np.asarray(point1(float(t)), dtype=float) for t in fractions]
    lower0 = [point + np.asarray([0.0, 0.0, float(underface)]) for point in tops0]
    lower1 = [point + np.asarray([0.0, 0.0, float(underface)]) for point in tops1]
    offsets0, offsets1, cosines = [], [], []
    for j, t in enumerate(fractions):
        j0, j1 = max(0, j - 1), min(len(fractions) - 1, j + 1)
        if j0 == j1:
            raise ValueError(f"{label}: 纵向断面不足")
        dt = float(fractions[j1]) - float(fractions[j0])
        d0 = (lower0[j1] - lower0[j0]) / dt
        d1 = (lower1[j1] - lower1[j0]) / dt
        cross = 0.5 * ((lower1[j] - lower0[j]) +
                       (lower1[j0] - lower0[j0]) +
                       (lower1[j1] - lower0[j1])) / 3.0
        z0, c0 = _vertical_offset_from_tangents(d0, cross, thickness, label)
        z1, c1 = _vertical_offset_from_tangents(d1, cross, thickness, label)
        if endpoint0_offsets is not None:
            z0 = float(endpoint0_offsets[j])
        if endpoint1_offsets is not None:
            z1 = float(endpoint1_offsets[j])
        offsets0.append(z0)
        offsets1.append(z1)
        cosines.append((c0, c1))
    sections = []
    for p0, p1, z0, z1 in zip(lower0, lower1, offsets0, offsets1):
        sections.append([tuple(p0), tuple(p1),
                         (float(p1[0]), float(p1[1]), float(p1[2] + z1)),
                         (float(p0[0]), float(p0[1]), float(p0[2] + z0))])
    shape = s2.loft_sections(sections)
    equivalent = []
    for j in range(1, len(fractions) - 1):
        dt = float(fractions[j + 1]) - float(fractions[j - 1])
        tangent = 0.5 * ((lower0[j + 1] - lower0[j - 1]) +
                         (lower1[j + 1] - lower1[j - 1])) / dt
        across = 0.5 * ((lower1[j] - lower0[j]) +
                        (lower1[j - 1] - lower0[j - 1]) +
                        (lower1[j + 1] - lower0[j + 1])) / 3.0
        _, cosine = _vertical_offset_from_tangents(tangent, across, thickness, label)
        mean_offset = 0.5 * (offsets0[j] + offsets1[j])
        equivalent.append(float(mean_offset * cosine))
    return shape, {
        "station_count": len(fractions),
        "normal_equivalent_thickness_sampled_mm": ([float(min(equivalent)), float(max(equivalent))]
                                                    if equivalent else [float(thickness), float(thickness)]),
        "vertical_offset_range_mm": [float(min(offsets0 + offsets1)), float(max(offsets0 + offsets1))],
        "normal_thickness_is_sampled_local_approximation": True,
        "edge_offsets_match_actual_support_strips": bool(endpoint0_offsets is not None or endpoint1_offsets is not None),
    }


def _continuous_rollboard_solid(sections):
    """Interpolate both skins on one tensor grid, then sew their exact boundaries."""
    from scipy.interpolate import make_interp_spline
    from OCP.Geom import Geom_BSplineSurface
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
    from OCP.gp import gp_Pnt
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeEdge
    from OCP.ShapeFix import ShapeFix_Shell
    from OCP.BRepLib import BRepLib

    lower = np.asarray([s["lower"] for s in sections], dtype=float)
    upper = np.asarray([s["upper"] for s in sections], dtype=float)
    # Longitudinal samples were generated at uniform fractions. Across the
    # wing, shared plan-chord parameters retain the nonuniform rail spacing.
    u = np.linspace(0.0, 1.0, lower.shape[0])
    spans = np.linalg.norm(np.diff(lower[:, :, :2], axis=1), axis=2).mean(axis=0)
    v = np.r_[0.0, np.cumsum(spans)]
    v /= v[-1]

    def knots(values):
        unique, counts = np.unique(values, return_counts=True)
        k = TColStd_Array1OfReal(1, len(unique))
        m = TColStd_Array1OfInteger(1, len(unique))
        for i, (value, count) in enumerate(zip(unique, counts), 1):
            k.SetValue(i, float(value)); m.SetValue(i, int(count))
        return k, m

    def skin(points):
        su = make_interp_spline(u, points, k=3, axis=0)
        sv = make_interp_spline(v, su.c, k=3, axis=1)
        poles = TColgp_Array2OfPnt(1, sv.c.shape[1], 1, sv.c.shape[0])
        # scipy puts the coefficient axis first even when interpolation axis=1.
        for i in range(sv.c.shape[1]):
            for j in range(sv.c.shape[0]):
                poles.SetValue(i + 1, j + 1, gp_Pnt(*map(float, sv.c[j, i])))
        ku, mu = knots(su.t); kv, mv = knots(sv.t)
        surface = Geom_BSplineSurface(poles, ku, kv, mu, mv, 3, 3, False, False)
        return cq.Face(BRepBuilderAPI_MakeFace(surface, 1e-7).Face())

    def boundaries(face):
        surface = face._geomAdaptor()
        u0, u1, v0, v1 = face.uvBounds()
        def edge(curve, first, last):
            return cq.Edge(BRepBuilderAPI_MakeEdge(curve, first, last).Edge())
        return [edge(surface.UIso(u0), v0, v1), edge(surface.UIso(u1), v0, v1),
                edge(surface.VIso(v0), u0, u1), edge(surface.VIso(v1), u0, u1)]

    bottom, top = skin(lower), skin(upper)
    walls = [cq.Face.makeRuledSurface(a, b)
             for a, b in zip(boundaries(bottom), boundaries(top))]
    shell = cq.Shell.makeShell([bottom, top, *walls])
    fix = ShapeFix_Shell(shell.wrapped)
    fix.Perform()
    raw = cq.Solid.makeSolid(cq.Shell(fix.Shell()))
    if not BRepLib.OrientClosedSolid_s(raw.wrapped):
        raise ValueError("全翼卷戗板外壳未能封闭并定向")
    return raw


def add_upper_rollboards(ctx):
    """Build one smooth, full-width lofted roll board per wing from the shared flyer top field."""
    parts = ctx["parts"]
    fan = sorted((r for r in ctx["contact_fly_records"] if r["family"] == "fan"),
                 key=lambda r: int(r["index"]))
    if len(fan) < 2:
        raise ValueError(f"upper roll board requires at least two dynamic fan rafters; got {len(fan)}")
    boundary_supports = [r for r in ctx["contact_fly_records"]
                         if r["family"] == "straight" and
                         (r.get("is_boundary") is True or r.get("role") == "boundary")]
    if len(boundary_supports) != 1:
        raise ValueError(f"upper roll board requires exactly one explicit boundary straight flyer; got {len(boundary_supports)}")
    if any(r.get("family") == "fan" and r.get("is_boundary") for r in fan):
        raise ValueError("boundary support is classified as a fan rafter")
    supports = [*fan, *boundary_supports]

    def center_path_station(detail):
        edges = detail["I_front_edge_fiber_records"]
        center = edges[len(edges) // 2]
        return float(center["inner_path_station_mm"])

    direction_span = center_path_station(fan[-1]) - center_path_station(fan[0])
    if abs(direction_span) <= 1e-8:
        raise ValueError("upper roll board fan support path stations do not establish an ordering direction")
    direction = 1.0 if direction_span > 0.0 else -1.0
    ordered_supports = sorted(supports, key=lambda detail: direction * center_path_station(detail))
    support_ids = [str(detail["source_rafter_id"]) for detail in ordered_supports]
    if len(support_ids) != len(set(support_ids)):
        raise ValueError("upper roll board support source_rafter_id values are not unique")
    support_stations = [direction * center_path_station(detail) for detail in ordered_supports]
    if any(b - a <= 1e-8 for a, b in zip(support_stations, support_stations[1:])):
        raise ValueError("upper roll board support path stations are not strictly ordered")
    thickness = float(ctx["case"]["p"]["board_upper_t"])
    underface = float(ctx["case"]["p"]["upper_board_surface_overlap_mm"])
    if underface < 0.0:
        raise ValueError("卷戗板底面与飞椽之间的竖向间隙不能为负")

    rails = []
    for detail in ordered_supports:
        for fiber_index, edge in enumerate(detail["I_front_edge_fiber_records"]):
            rails.append({
                "source_rafter_id": str(detail["source_rafter_id"]),
                "family": str(detail["family"]),
                "index": int(detail["index"]),
                "role": str(detail.get("role", "fan" if detail["family"] == "fan" else "regular")),
                "is_boundary": bool(detail.get("is_boundary", False)),
                "fiber_index": int(fiber_index),
                "detail": detail,
                "edge": edge,
                "inner_station_mm": float(edge["inner_path_station_mm"]),
                "outer_station_mm": float(edge["outer_path_station_mm"]),
            })
    ordered_rails = sorted(rails, key=lambda rail: direction * rail["inner_station_mm"])
    outer_order = sorted(rails, key=lambda rail: direction * rail["outer_station_mm"])
    rail_ids = [(item["source_rafter_id"], item["fiber_index"]) for item in ordered_rails]
    outer_ids = [(item["source_rafter_id"], item["fiber_index"]) for item in outer_order]
    if rail_ids != outer_ids:
        raise ValueError("卷戗板全翼控制轨在内缘与外缘之间顺序反转")

    def boundary_sample(detail, u_boundary, fraction):
        xy = rollboard_lower_point(ordered_supports, u_boundary, fraction)[:2]
        delta = np.asarray(xy, dtype=float) - np.asarray(detail["S"], dtype=float)
        unit = np.asarray(detail["unit"], dtype=float)
        width_axis = np.asarray(detail["width_axis"], dtype=float)
        q = float(np.dot(delta, unit))
        v = float(np.dot(delta, width_axis))
        z = fan_upper_z_at_q(detail, q, v)
        return np.asarray([float(xy[0]), float(xy[1]), float(z)], dtype=float), q

    longitudinal_fractions = [float(value) for value in np.linspace(0.0, 1.0, 33)]
    section_rows = []
    for fraction in longitudinal_fractions:
        upper_points = []
        q_values = []
        boundary, q_boundary = boundary_sample(ordered_supports[0], -0.5, fraction)
        upper_points.append(boundary)
        q_values.append(q_boundary)
        previous_source_rafter_id = None
        previous_point = None
        previous_q = None
        for rail in ordered_rails:
            detail, edge = rail["detail"], rail["edge"]
            q_s = float(edge["q_S_mm"])
            q_e = float(edge["q_E_mm"])
            q = (1.0 - fraction) * q_s + fraction * q_e
            v = float(edge["v_mm"])
            point = fan_upper_surface_at_q(detail, q, v)
            point = np.asarray(point, dtype=float)
            if (previous_source_rafter_id is not None and
                    rail["source_rafter_id"] != previous_source_rafter_id):
                for gap_fraction in (0.25, 0.5, 0.75):
                    gap_point = previous_point * (1.0 - gap_fraction) + point * gap_fraction
                    upper_points.append(gap_point)
                    q_values.append(float(previous_q * (1.0 - gap_fraction) + q * gap_fraction))
            upper_points.append(point)
            q_values.append(q)
            previous_source_rafter_id = rail["source_rafter_id"]
            previous_point = point
            previous_q = q
        boundary, q_boundary = boundary_sample(
            ordered_supports[-1], float(len(ordered_supports)) - 0.5, fraction)
        upper_points.append(boundary)
        q_values.append(q_boundary)
        section_rows.append({
            "fraction": fraction,
            "upper_points": [np.asarray(point, dtype=float) for point in upper_points],
            "q_values": [float(q) for q in q_values],
        })

    expected_point_count = len(section_rows[0]["upper_points"])
    if any(len(row["upper_points"]) != expected_point_count for row in section_rows):
        raise ValueError("卷戗板连续曲面各纵向截面的宽向拓扑不一致")

    # The single global field supplies normals across flyer rails, gaps, and the two half-bays.
    # This replaces per-band thickness estimates that created visible width seams.
    offset_rows = []
    offset_min, offset_max = float("inf"), -float("inf")
    normal_cosine_min = 1.0
    for row_index, row in enumerate(section_rows):
        width_coords = [0.0]
        for a, b in zip(row["upper_points"][:-1], row["upper_points"][1:]):
            span = float(np.linalg.norm(np.asarray(b, dtype=float)[:2] -
                                        np.asarray(a, dtype=float)[:2]))
            if span <= 1e-8:
                raise ValueError("卷戗板连续截面出现重合宽向控制点")
            width_coords.append(width_coords[-1] + span)
        row_offsets = []
        for point_index, upper in enumerate(row["upper_points"]):
            q0_index = max(0, row_index - 1)
            q1_index = min(len(section_rows) - 1, row_index + 1)
            dq = (float(section_rows[q1_index]["q_values"][point_index]) -
                  float(section_rows[q0_index]["q_values"][point_index]))
            if abs(dq) <= 1e-8:
                raise ValueError("卷戗板连续曲面纵向控制站重合")
            tangent_long = (
                section_rows[q1_index]["upper_points"][point_index] -
                section_rows[q0_index]["upper_points"][point_index]) / dq
            u0_index = max(0, point_index - 1)
            u1_index = min(expected_point_count - 1, point_index + 1)
            du = width_coords[u1_index] - width_coords[u0_index]
            if du <= 1e-8:
                raise ValueError("卷戗板连续曲面宽向控制站重合")
            tangent_cross = (
                row["upper_points"][u1_index] - row["upper_points"][u0_index]) / du
            vertical_offset, cosine = _vertical_offset_from_tangents(
                tangent_long, tangent_cross, thickness,
                f"continuous roll board fraction={row['fraction']:.4f} point={point_index}")
            row_offsets.append(float(vertical_offset))
            offset_min, offset_max = min(offset_min, vertical_offset), max(offset_max, vertical_offset)
            normal_cosine_min = min(normal_cosine_min, cosine)
        offset_rows.append(row_offsets)

    sections = []
    for row, offsets in zip(section_rows, offset_rows):
        lower = [point + np.asarray([0.0, 0.0, underface])
                 for point in row["upper_points"]]
        upper = [point + np.asarray([0.0, 0.0, offset])
                 for point, offset in zip(lower, offsets)]
        sections.append({"lower": lower, "upper": upper})

    # The S/E rail field and extrapolated half-bays define the continuous
    # boundary itself; the legacy centerline polygon is not a clipping tool.
    board = _continuous_rollboard_solid(sections)
    if board.isNull() or not board.Solids():
        raise ValueError("全翼连续卷戗板构造返回空实体")
    if len(board.Solids()) != 1:
        raise ValueError(f"全翼连续卷戗板构造形成{len(board.Solids())}个实体")
    pid_x, pid_y = "upper_board_x_continuous", "upper_board_y_continuous"
    add(parts, pid_x, "卷戗板基准翼（全宽连续曲面）", "upper_board", board)
    mirrored = s2.mirror_shape(ctx["case"], board).clean()
    add(parts, pid_y, "卷戗板镜像翼（全宽连续曲面）", "upper_board", mirrored)

    board_record = {
        "piece_type": "continuous_wing",
        "fan_indices": [int(record["index"]) for record in fan],
        "support_source_rafter_ids": support_ids,
        "boundary_support_source_rafter_ids": [str(r["source_rafter_id"]) for r in boundary_supports],
        "part_ids": [pid_x, pid_y],
        "longitudinal_section_count": len(longitudinal_fractions),
        "width_control_point_count_per_section": expected_point_count,
        "underface_vertical_gap_mm": underface,
        "board_thickness_mm": thickness,
        "normal_offset_vertical_range_mm": [float(offset_min), float(offset_max)],
        "minimum_local_vertical_normal_cosine": float(normal_cosine_min),
        "clipped_to_old_xy_domain": False,
        "construction": (
            "全翼按内/外边界站位排序的真实摔网飞椽轨与正身边界飞椽轨及椽间插值点构造单一宽向B样条截面场；"
            "统一使用全翼纵向/横向切向求局部厚度偏置，内缘取实际里口外缘S轨、外缘取E轨及两端半档；上下共用参数的连续插值曲面直接封成每翼单一BRep。"),
    }
    ctx["upper_board_pieces"] = [board_record]
    ctx["upper_board_domain"] = {
        "support_source_rafter_ids": support_ids,
        "boundary_support_source_rafter_ids": [str(r["source_rafter_id"]) for r in boundary_supports],
        "inner_boundary_xy_samples_mm": [list(map(float, p[:2])) for p in section_rows[0]["upper_points"]],
        "outer_boundary_xy_samples_mm": [list(map(float, p[:2])) for p in section_rows[-1]["upper_points"]],
        "domain_source": "实际里口外缘q_S至飞椽梢端q_E的全翼连续曲面边界；两端沿既有S/E轨外推半档，不再裁到旧中心线折线Ω。",
        "old_centerline_surface_used_for_top_z": False,
        "top_source": "stage05飞椽共享顶场的全宽轨与椽间线性插值",
        "cross_width_interpolation": "shared plan-chord parameter tensor B-spline interpolation",
        "longitudinal_interpolation": "shared uniform station parameter cubic B-spline interpolation",
        "board_piece_count_per_wing": 1,
        "underface_vertical_gap_mm": underface,
        "thickness_source": "one continuous full-wing surface normal approximation, not per-flyer width-band offsets",
        "longitudinal_section_count": len(longitudinal_fractions),
        "width_control_point_count_per_section": expected_point_count,
    }
    return [board_record]


def local_xy(u, v, origin_xy, case):
    axis_u = np.asarray(case["old_unit"], dtype=float)
    axis_v = np.asarray(case["old_normal"], dtype=float)
    p = np.asarray(origin_xy, dtype=float) + float(u) * axis_u + float(v) * axis_v
    return float(p[0]), float(p[1])


def local_uv(x, y, origin_xy, case):
    delta = np.asarray([float(x), float(y)], dtype=float) - np.asarray(origin_xy, dtype=float)
    axis_u = np.asarray(case["old_unit"], dtype=float)
    axis_v = np.asarray(case["old_normal"], dtype=float)
    return float(np.dot(delta, axis_u)), float(np.dot(delta, axis_v))


def circumcircle_through(p0, p1, p2):
    """Return the unique circle through three 2D points, rejecting a flat fit."""
    x0, y0 = map(float, p0)
    x1, y1 = map(float, p1)
    x2, y2 = map(float, p2)
    determinant = 2.0 * (x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1))
    scale = max(math.dist(p0, p1), math.dist(p1, p2), math.dist(p2, p0), 1.0)
    if abs(determinant) <= 1e-10 * scale * scale:
        raise ValueError("组合木接背三点近共线，不能构造有限圆弧；请调整组合端点或接背参数")
    q0, q1, q2 = x0*x0 + y0*y0, x1*x1 + y1*y1, x2*x2 + y2*y2
    cx = (q0 * (y1 - y2) + q1 * (y2 - y0) + q2 * (y0 - y1)) / determinant
    cz = (q0 * (x2 - x1) + q1 * (x0 - x2) + q2 * (x1 - x0)) / determinant
    radius = math.hypot(x0 - cx, y0 - cz)
    return float(cx), float(cz), float(radius)


def circle_branch_z(u, circle, branch_sign):
    cu, cz, radius = circle
    radicand = radius * radius - (float(u) - cu) ** 2
    if radicand < -1e-7 * max(radius * radius, 1.0):
        raise ValueError(f"组合木圆弧在u={u:.3f}mm处没有实交点")
    return float(cz + branch_sign * math.sqrt(max(0.0, radicand)))


def solid_mesh_sampler(shapes, groups, case):
    pieces = []
    for pid, name, group, shape in shapes:
        verts, tris = shape.tessellate(float(case["p"]["mesh_tessellation_mm"]),
                                       float(case["p"]["mesh_angular_tolerance_rad"]))
        pieces.append({"id": pid, "name": name, "group": group,
                       "vertices": [float(c) for v in verts for c in v.toTuple()],
                       "triangles": [int(i) for tri in tris for i in tri]})
    return s4.MeshSurface({"parts": pieces}, set(groups))


def _build_combination_stock(ctx):
    by = {p["id"]: p for p in ctx["parts"]}
    old = by["old_corner"]["shape"]
    young = by["young_corner"]["shape"]
    sampler = solid_mesh_sampler([
        ("old_corner", "老戗", "old_corner", old),
        ("young_corner", "嫩戗", "young_corner", young),
    ], {"old_corner", "young_corner"}, ctx["case"])
    p = ctx["case"]["p"]
    root_xy = list(ctx["young_info"]["root_center_plan_mm"])
    D = float(ctx["case"]["D"])
    uB = float(p["combination_back_u_ratio_D"]) * D
    uA = float(p["combination_front_u_ratio_D"]) * D
    if not uB < 0.0 < uA:
        raise ValueError(f"组合木弦端点需跨过嫩戗接背原点u=0；当前uB={uB:g}, uA={uA:g}")
    width = float(ctx["young_info"]["tip_width_mm"]) * float(p["combination_width_ratio_young_tip"])
    v_values = np.linspace(-width / 2.0, width / 2.0, int(p["combination_cross_width_stations"]))

    def F(u, v):
        x, y = local_xy(u, v, root_xy, ctx["case"])
        hit = sampler.z_range(x, y)
        if hit is None:
            raise ValueError(f"combination member missed current corner top at u={u:.2f}, v={v:.2f}")
        return float(hit["max_z_mm"])

    fB0, fA0 = F(uB, 0.0), F(uA, 0.0)
    chord = np.array([uA - uB, fA0 - fB0], dtype=float)
    chord_length = float(np.linalg.norm(chord))
    if chord_length <= 1e-8:
        raise ValueError("组合木接背弦长为零")
    fraction = float(p["back_join_fraction"])
    down_normal = np.array([chord[1], -chord[0]], dtype=float) / chord_length
    join = np.array([uB, fB0], dtype=float) + fraction * chord + \
        down_normal * (float(p["combination_sagitta_ratio_chord"]) * chord_length)
    circle = circumcircle_through((uB, fB0), (uA, fA0), join)
    branch_sign = 1.0 if join[1] >= circle[1] else -1.0
    if abs(circle_branch_z(float(join[0]), circle, branch_sign) - float(join[1])) > 1e-5:
        raise ValueError("组合木接背圆弧无法通过所选弦向接背点")
    sections = []
    min_depth = float("inf")
    max_depth = -float("inf")
    below_upper_count = 0
    max_upper_deficit = 0.0
    # Keep the circle's derived upper envelope unchanged. Where U falls below
    # the measured source top F, the blank section is placed entirely below U
    # and is removed by the exact old/young BRep subtraction. Where U>F, the
    # blank starts at F-0.2mm to ensure a robust Boolean contact before that
    # same subtraction; no artificial cap remains above U.
    trim = float(p["combination_end_trim_mm"])
    if trim * 2.0 >= (uA - uB):
        raise ValueError(f"组合木端部裁边 {trim:g}mm 大于可用弦长的一半")
    u_start, u_end = uB + trim, uA - trim
    for u in np.linspace(u_start, u_end, int(p["combination_u_stations"])):
        tau = (float(u) - uB) / (uA - uB)
        upper_center = circle_branch_z(float(u), circle, branch_sign)
        low_edge = []
        top_edge = []
        for v in v_values:
            f = F(float(u), float(v))
            fb, fa = F(uB, float(v)), F(uA, float(v))
            upper = upper_center + (1.0 - tau) * (fb - fB0) + tau * (fa - fA0)
            depth = upper - f
            min_depth = min(min_depth, depth)
            max_depth = max(max_depth, depth)
            if depth < 0.0:
                below_upper_count += 1
                max_upper_deficit = max(max_upper_deficit, -depth)
            x, y = local_xy(float(u), float(v), root_xy, ctx["case"])
            low_edge.append((x, y, min(f, upper) - 0.2))
            top_edge.append((x, y, upper))
        sections.append(low_edge + list(reversed(top_edge)))
    print("stage05: combination stock loft", flush=True)
    blank = s2.loft_sections(sections)
    print("stage05: combination stock loft complete", flush=True)
    z_values_f = [F(float(u), 0.0) for u in np.linspace(u_start, u_end, 129)]
    z_values_u = [circle_branch_z(float(u), circle, branch_sign)
                  for u in np.linspace(u_start, u_end, 129)]
    z_split = 0.5 * (min(z_values_f) + min(z_values_u))
    info = {
        "coordinate_frame": {"origin_xy_mm": root_xy, "u_axis": list(map(float, ctx["case"]["old_unit"])),
                             "v_axis": list(map(float, ctx["case"]["old_normal"])), "u_direction": "toward eave"},
        "width_mm": width, "width_ratio_to_young_tip": float(p["combination_width_ratio_young_tip"]),
        "A_u_z_mm": [uA, fA0], "B_u_z_mm": [uB, fB0],
        "back_join_fraction": fraction,
        "back_join_point_u_z_mm": join.tolist(),
        "circle_center_u_z_mm": [circle[0], circle[1]], "radius_mm": circle[2],
        "derived_chord_sagitta_mm": float(p["combination_sagitta_ratio_chord"]) * chord_length,
        "upper_profile": "circle through dynamically sampled old/young corner top at endpoints and the configured back_join_fraction station with chord-relative downward sagitta; cross-width top offsets interpolate from actual old/young top surfaces",
        "lower_profile": "F(u,v)=highest surface of only current old_corner/young_corner solids; mesh sampled at 1mm tessellation then new material cut against both BReps",
        "u_trim_end_mm": [u_start - uB, uA - u_end],
        "sampled_nominal_combination_depth_mm": [float(min_depth), float(max_depth)],
        "sampled_u_below_F_count": int(below_upper_count),
        "max_sampled_U_below_F_mm": float(max_upper_deficit),
        "z_split_mm": float(z_split),
        "split_rule": "z_split=0.5*(min_u F(u,0)+min_u U(u)); lower菱角木, upper扁担木; both share horizontal interface",
        "top_material_contact": "stock blank starts 0.2mm below the sampled old_corner/young_corner top before optional exact Boolean fitting",
        "pocket_fit_sample_grid": {"u_stations": int(p["combination_u_stations"]), "width_stations": len(v_values)},
        "probe_reference_grid": {"u_stations": 129, "width_stations": 9},
        "back_join_parameter_interpretation": "弦向分位；从B点沿接背弦量到老/嫩戗接背控制站位，不是面积比",
        "historical_height_rule_status": "book p128 states combined height >= 2/3 of the flying-corner horizontal level, but the datum is unresolved; current t=0.7 does not prove that criterion",
        "assumptions": ["接背上轮廓由动态端点与back_join_fraction处弦偏共同确定为圆弧", "组合木宽度按嫩戗梢部宽度比例", "水平拆分为两件", "组合侧轮廓与BRep切配为几何拟合，不等同传统榫接验证"]
    }
    return blank, info


def add_preview_combination_blank(ctx):
    """Add the original stock blank without fitting or horizontal split Booleans."""
    blank, info = _build_combination_stock(ctx)
    if not blank.isValid() or len(blank.Solids()) != 1 or blank.Volume() <= 1e-4:
        raise ValueError("preview combination stock is not one valid solid")
    info.update({"status": "preview_uncut_stock_blank",
                 "part_id": "combination_preview_blank",
                 "old_young_boolean_cuts_run": False,
                 "horizontal_split_run": False,
                 "exact_fit_and_split_required": True})
    add(ctx["parts"], "combination_preview_blank",
        "预览组合木料坯（未切老戗/嫩戗，未分菱角木/扁担木）",
        "combination_preview_blank", blank)
    ctx["combination_info"] = info
    return info


def make_combination_blank(ctx):
    """Build, fit, and split the combination timber for exact generation."""
    blank, info = _build_combination_stock(ctx)
    by = {part["id"]: part for part in ctx["parts"]}
    old, young = by["old_corner"]["shape"], by["young_corner"]["shape"]
    z_split = float(info["z_split_mm"])
    obstacles = cq.Compound.makeCompound([old, young])
    print("stage05: subtracting old/young BReps from combination stock", flush=True)
    cut = blank.cut(obstacles).clean()
    print("stage05: combination BRep cut complete", flush=True)
    if not cut.isValid() or len(cut.Solids()) != 1 or cut.Volume() <= 1e-4:
        raise ValueError(f"combination blank after old/young fit is not a single solid: valid={cut.isValid()} solids={len(cut.Solids())} volume={cut.Volume()}")
    bbox = cut.BoundingBox()
    zmin, zmax = float(bbox.zmin) - 100.0, float(bbox.zmax) + 100.0
    xmin, xmax = float(bbox.xmin) - 100.0, float(bbox.xmax) + 100.0
    ymin, ymax = float(bbox.ymin) - 100.0, float(bbox.ymax) + 100.0
    below = cq.Solid.makeBox(xmax - xmin, ymax - ymin, z_split - zmin,
                             cq.Vector(xmin, ymin, zmin))
    above = cq.Solid.makeBox(xmax - xmin, ymax - ymin, zmax - z_split,
                             cq.Vector(xmin, ymin, z_split))
    lingjiao = cut.intersect(below).clean()
    biandan = cut.intersect(above).clean()
    print("stage05: split lingjiao/biandan complete", flush=True)
    for name, shape in (("菱角木", lingjiao), ("扁担木", biandan)):
        if not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-4:
            raise ValueError(f"{name} split piece invalid/multisolid: valid={shape.isValid()} solids={len(shape.Solids())} volume={shape.Volume()}")
    info.update({"status": "exact_fitted_and_split",
                 "old_young_boolean_cuts_run": True,
                 "horizontal_split_run": True,
                 "top_material_contact": "new stock cut by old_corner+young_corner; lower boundary conforms to actual corner top mesh, with 0.2mm stock allowance before BRep cut"})
    return lingjiao, biandan, info


def add_combination(ctx):
    lingjiao, biandan, info = make_combination_blank(ctx)
    add(ctx["parts"], "lingjiao_main", "菱角木（下部楔形拟合）", "lingjiao", lingjiao)
    add(ctx["parts"], "biandan_main", "扁担木（上部后延弧背拟合）", "biandan", biandan)
    ctx["combination_info"] = info
    return info
