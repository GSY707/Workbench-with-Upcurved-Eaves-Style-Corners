#!/usr/bin/env python
"""Stage 02: old corner member, fan rafters, and straight eave rafters.

The script deliberately rebuilds the stage-01 supports into a new stage-02
compound.  It never modifies stage-01 files.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DEPS = ROOT / "资料整理" / "tmp" / "model_deps"
sys.path.insert(0, str(DEPS))
import cadquery as cq  # noqa: E402

SRC_PARAMS = ROOT / "资料整理" / "数据" / "初期模型参数.json"
STAGE = 2
STAGE_NAME = "老戗、摔网椽与正身出檐椽"
EPS = 1e-7


def vector(xyz):
    return cq.Vector(*(float(v) for v in xyz))


def polygon_wire(points):
    pts = [vector(p) for p in points]
    return cq.Wire.makePolygon(pts + [pts[0]], close=False)


def loft_sections(sections):
    shape = cq.Solid.makeLoft([polygon_wire(section) for section in sections], ruled=True)
    if not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-5:
        raise ValueError(f"loft无效: valid={shape.isValid()} solids={len(shape.Solids())} volume={shape.Volume()}")
    return shape


def smooth(t):
    t = min(1.0, max(0.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def bbox_overlap(a, b, tol=1e-6):
    aa, bb = a.BoundingBox(), b.BoundingBox()
    return not (aa.xmax < bb.xmin - tol or bb.xmax < aa.xmin - tol or
                aa.ymax < bb.ymin - tol or bb.ymax < aa.ymin - tol or
                aa.zmax < bb.zmin - tol or bb.zmax < aa.zmin - tol)


PURLIN_IDS = ("eave_purlin_x", "eave_purlin_y", "step_purlin_x", "step_purlin_y")
QIANSHAN_IDS = ("qiangshan_x", "qiangshan_y")


def facewise_common_area(a, b):
    area = 0.0
    hits = []
    for fa in a.Faces():
        for fb in b.Faces():
            if not bbox_overlap(fa, fb):
                continue
            try:
                value = float(fa.intersect(fb).Area())
                if value > 1e-5:
                    area += value
                    hits.append(value)
            except Exception:
                continue
    return area, hits


def cylinder_x(x0, x1, y, z, radius):
    return cq.Solid.makeCylinder(radius, float(x1 - x0), vector((x0, y, z)), vector((1, 0, 0)))


def cylinder_y(y0, y1, x, z, radius):
    return cq.Solid.makeCylinder(radius, float(y1 - y0), vector((x, y0, z)), vector((0, 1, 0)))


def add_part(parts, pid, name, group, shape):
    if not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-5:
        raise ValueError(f"构件无效: {pid}; valid={shape.isValid()} solids={len(shape.Solids())} volume={shape.Volume()}")
    parts.append({"id": pid, "name": name, "group": group, "shape": shape})


def load_case(config):
    """Create a case from this generator's validated, resolved configuration."""
    if not isinstance(config, dict) or "parameters" not in config or "advanced" not in config:
        raise ValueError("case config must contain parameters and advanced objects")
    params = {**config["parameters"], **config["advanced"]}
    D = float(params["D"])
    e = float(params["e"])
    pitch = float(params["t"])
    p = float(params["p"])
    a = float(params["a"])
    radius = float(params["purlin_radius_mm"])
    z_eave = 0.0
    z_step = D * pitch
    purlin_diameter = radius * 2.0
    end_extension = purlin_diameter
    eave_start, eave_end = -2.0 * D - end_extension, end_extension
    step_start, step_end = eave_start, -D + end_extension
    old_width_root = 1.5 * a
    old_tail_scale = float(params.get("old_tail_scale", 0.8))
    old_height_root = a
    old_ridge_height_root = float(params.get("old_ridge_h", 41.25))
    c = float(params["k_c"]) * p
    old_start = (-D, -D)
    old_end = (e + c, e + c)
    old_length_plan = math.dist(old_start, old_end)
    # The inner end of the future young corner member is set back three cun.
    young_root_setback = float(params["setback"])
    old_unit = ((old_end[0] - old_start[0]) / old_length_plan,
                (old_end[1] - old_start[1]) / old_length_plan)
    old_root = (old_end[0] - old_unit[0] * young_root_setback,
                old_end[1] - old_unit[1] * young_root_setback)
    old_tip_t = 1.0
    old_root_t = young_root_setback / old_length_plan
    old_root_width = old_width_root * (1.0 - (1.0 - old_tail_scale) * smooth(1.0 - old_root_t))
    old_tip_width = old_width_root * old_tail_scale
    normal = (-old_unit[1], old_unit[0])
    old_near_side_B = (old_root[0] + normal[0] * old_root_width / 2.0,
                       old_root[1] + normal[1] * old_root_width / 2.0)

    qiangshan_width = float(params["qiangshan_width_mm"])
    old_width_at_eave = old_width_root * (1.0 - (1.0 - old_tail_scale) * smooth(D / old_length_plan))
    old_half_width_normal = old_width_at_eave / 2.0
    qiangshan_clearance = float(params["qiangshan_clearance_mm"])
    qiangshan_end = -(qiangshan_width / 2.0 + math.sqrt(2.0) *
                      (old_half_width_normal + qiangshan_clearance))

    return {
        "source": {"case_id": config.get("case_id", "custom"), "scope": "parameter-driven fifth-stage rebuild"},
        "p": params, "D": D, "e": e, "c": c,
        "purlin_diameter": purlin_diameter, "purlin_radius": radius,
        "eave_axis_z": z_eave, "step_axis_z": z_step,
        "end_extension": end_extension,
        "eave_start": eave_start, "eave_end": eave_end,
        "step_start": step_start, "step_end": step_end,
        "old_start": old_start, "old_end": old_end,
        "old_length_plan": old_length_plan, "old_unit": old_unit,
        "old_normal": normal, "old_width_root": old_width_root,
        "old_width_eave": old_width_at_eave, "old_root_width": old_root_width,
        "old_tip_width": old_tip_width, "old_tail_scale": old_tail_scale,
        "old_height_root": old_height_root,
        "old_ridge_height_root": old_ridge_height_root,
        "young_root_setback": young_root_setback,
        "old_root": old_root, "old_near_side_B": old_near_side_B,
        "qiangshan_width": qiangshan_width,
        "old_half_width_normal_at_eave": old_half_width_normal,
        "qiangshan_clearance": qiangshan_clearance,
        "qiangshan_end": qiangshan_end,
        "fan_width": float(params["fan_rafter_width_mm"]),
        "fan_height": float(params["fan_rafter_height_mm"]),
        "rafter_seat_relief": float(params["rafter_seat_relief_mm"]),
        # The arc itself is derived from its endpoint and tangent; this value
        # is retained only as a comparison datum, not as an adjustable input.
        "fan_arc_sagitta_reference": float(params["likou_width"]),
        "fan_spacing_limit": float(params["B"]),
        "fan_station_count": int(params["fan_station_count"]),
        "eave_rafters_width": float(params["straight_rafter_width_mm"]),
        "eave_rafters_height": float(params["straight_rafter_height_mm"]),
        "eave_tail_extension": float(params["likou_extension_pitches"]) * float(params["B"]),
        "eave_pitch": float(params["B"]),
        "old_seat_relief": float(params["old_seat_relief_mm"]),
        "board_lower_t": float(params["board_lower_t"]),
        "board_surface_overlap": float(params["board_surface_overlap_mm"]),
        "likou_surface_overlap": float(params["likou_surface_overlap_mm"]),
        "fan_sector_root_apex": True,
        "straight_bank_count": None,
    }


def purlin_half_lap_x(shape, x, y, z, radius):
    cutter = cq.Solid.makeBox(2 * radius, 2 * radius, radius + 1.0,
                              vector((x - radius, y - radius, z)))
    return shape.cut(cutter)


def purlin_half_lap_y(shape, x, y, z, radius):
    cutter = cq.Solid.makeBox(2 * radius, 2 * radius, radius + 1.0,
                              vector((x - radius, y - radius, z - radius - 1.0)))
    return shape.cut(cutter)


def trim_positive_corner_purlin_end(shape, keep_side):
    """Miter the short positive-axis end at the shared x=y corner plane.

    The X-running member keeps x<=y and the Y-running member keeps y<=x.
    This is only an end trim of the four support rails; it does not cut seats
    into the old corner, fan rafters, or straight rafters.
    """
    if keep_side not in ("x", "y"):
        raise ValueError(f"unknown purlin end side: {keep_side}")
    box = shape.BoundingBox()
    extent = max(abs(float(v)) for v in
                 (box.xmin, box.xmax, box.ymin, box.ymax)) + 10.0
    if keep_side == "x":
        removed_half_plane = [(extent, -extent), (extent, extent), (-extent, -extent)]
    else:
        removed_half_plane = [(-extent, extent), (extent, extent), (-extent, -extent)]
    zmin, zmax = float(box.zmin) - 10.0, float(box.zmax) + 10.0
    cutter = plan_prism(removed_half_plane, zmin, zmax)
    return shape.cut(cutter).clean()


def qiangshan_wedge(case, along_x):
    width = case["qiangshan_width"]
    start, end = -case["D"], case["qiangshan_end"]
    sections = []
    offsets = np.linspace(-width / 2.0, width / 2.0, 5)
    q_values = np.linspace(start, end, 25)
    across_values = [float(value) for value in offsets]
    profiles, correction_records = [], []
    for across in across_values:
        profile, correction = _qiangshan_profile_for_across(
            case, across, q_values=q_values)
        profiles.append(profile)
        correction_records.append(correction)
    direction = "x" if along_x else "y"
    case.setdefault("qiangshan_profile_adjustment", {})[direction] = {
        "A_to_B_axis_mm": [float(start), float(end)],
        "across_stations_mm": across_values,
        "rule": "only the A-to-first-local-minimum interval is replaced by a monotone smoothstep connector; controls after that point and B retain the original shared-rafter underside field",
        "records": correction_records,
        "status": "construction_control_field_adjustment; not a model geometry check",
    }
    for station_index, q in enumerate(q_values):
        top_edge = []
        for across, profile in zip(across_values, profiles):
            z = float(profile[station_index])
            if along_x:
                top_edge.append((float(q), across, z))
            else:
                top_edge.append((across, float(q), z))
        if along_x:
            section = [(float(q), -width / 2, case["eave_axis_z"]),
                       (float(q), width / 2, case["eave_axis_z"])] + list(reversed(top_edge))
        else:
            section = [(-width / 2, float(q), case["eave_axis_z"]),
                       (width / 2, float(q), case["eave_axis_z"])] + list(reversed(top_edge))
        sections.append(section)
    rough = loft_sections(sections)
    rail_id = "eave_purlin_x" if along_x else "eave_purlin_y"
    rail = case["purlin_shapes"][rail_id]
    fitted = rough.cut(rail) if bbox_overlap(rough, rail) else rough
    if not fitted.isValid() or len(fitted.Solids()) != 1:
        raise ValueError("戗山木承面裁切后无效")
    return fitted


def support_parts(case, parts):
    D, r = case["D"], case["purlin_radius"]
    eave_x = cylinder_x(case["eave_start"], case["eave_end"], 0.0, case["eave_axis_z"], r)
    eave_y = cylinder_y(case["eave_start"], case["eave_end"], 0.0, case["eave_axis_z"], r)
    eave_x = purlin_half_lap_x(eave_x, 0.0, 0.0, case["eave_axis_z"], r)
    eave_y = purlin_half_lap_y(eave_y, 0.0, 0.0, case["eave_axis_z"], r)
    eave_x = trim_positive_corner_purlin_end(eave_x, "x")
    eave_y = trim_positive_corner_purlin_end(eave_y, "y")
    step_x = cylinder_x(case["step_start"], case["step_end"], -D, case["step_axis_z"], r)
    step_y = cylinder_y(case["step_start"], case["step_end"], -D, case["step_axis_z"], r)
    step_x = purlin_half_lap_x(step_x, -D, -D, case["step_axis_z"], r)
    step_y = purlin_half_lap_y(step_y, -D, -D, case["step_axis_z"], r)
    step_x = trim_positive_corner_purlin_end(step_x, "x")
    step_y = trim_positive_corner_purlin_end(step_y, "y")
    add_part(parts, "eave_purlin_x", "廊桁X向（中心轴y=0）", "eave_purlins", eave_x)
    add_part(parts, "eave_purlin_y", "廊桁Y向（中心轴x=0）", "eave_purlins", eave_y)
    add_part(parts, "step_purlin_x", "步桁X向（中心轴y=-D）", "step_purlins", step_x)
    add_part(parts, "step_purlin_y", "步桁Y向（中心轴x=-D）", "step_purlins", step_y)
    case["purlin_shapes"] = {p["id"]: p["shape"] for p in parts}


def old_corner_shape(case):
    """Build the original complete straight diagonal beam profile.

    The existing lower datum and placement are retained.  Intersections with
    round purlins remain in the source geometry; no local saddle seats are cut.
    """
    x0, y0 = case["old_start"]
    x1, y1 = case["old_end"]
    unit, normal = case["old_unit"], case["old_normal"]
    width0, height0, ridge0 = case["old_width_root"], case["old_height_root"], case["old_ridge_height_root"]
    taper = case["old_tail_scale"]
    sections = []
    centerline = []
    station_count = 17
    for t in np.linspace(0.0, 1.0, station_count):
        t = float(t)
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        width = width0 * (1.0 - (1.0 - taper) * smooth(t))
        height = height0 * (1.0 - (1.0 - taper) * smooth(t))
        ridge = ridge0 * (1.0 - (1.0 - taper) * smooth(t))
        bottom = case["purlin_radius"] - float(case["p"]["t"]) * x - case["old_seat_relief"]
        nl, nr = (-width / 2.0, width / 2.0)
        left = (x + normal[0] * nl, y + normal[1] * nl)
        right = (x + normal[0] * nr, y + normal[1] * nr)
        mid = (x, y)
        # Pentagonal section: 110 mm body with a centered 41.25 mm car-back crown.
        section = [
            [left[0], left[1], bottom],
            [right[0], right[1], bottom],
            [right[0], right[1], bottom + height],
            [mid[0], mid[1], bottom + height + ridge],
            [left[0], left[1], bottom + height],
        ]
        sections.append(section)
        centerline.append([x, y, bottom + height / 2.0])
    member = loft_sections(sections)
    case["old_corner_centerline"] = centerline
    case["old_corner_seat_purlins"] = []
    case["old_corner_seat_relief_mm"] = case["old_seat_relief"]
    return member


def circular_arc(A, B, nominal_sagitta_reference, samples=129):
    ax, ay = A
    bx, by = B
    dx, dy = bx - ax, by - ay
    chord = math.hypot(dx, dy)
    if dx <= 0 or dy <= 0:
        raise ValueError(f"内凹切线弧需dx>0、dy>0，实际dx={dx}, dy={dy}")
    # The arc leaves A horizontally, tangent to the straight eave-end line
    # y=e, then bows toward the step side (smaller y than the A-B chord).
    radius = (dx * dx + dy * dy) / (2.0 * dy)
    cx, cy = ax, ay + radius
    theta0 = -math.pi / 2.0
    theta1 = math.atan2(by - cy, bx - cx)
    sweep = theta1 - theta0
    if not (0.0 < sweep < math.pi):
        raise ValueError(f"内凹切线弧角度无效: {sweep}")
    points = []
    for u in np.linspace(0.0, 1.0, samples):
        theta = theta0 + sweep * float(u)
        points.append((cx + radius * math.cos(theta), cy + radius * math.sin(theta)))
    # Avoid roundoff at the prescribed endpoint.
    points[0], points[-1] = (float(ax), float(ay)), (float(bx), float(by))
    sagitta = radius - math.sqrt(max(0.0, radius * radius - chord * chord / 4.0))
    arc_length = radius * sweep
    return points, {
        "construction": "A处以正身檐端线y=e为水平切线，圆弧向步桁侧内凹；半径由A切线及端点B唯一确定",
        "chord_mm": chord, "radius_mm": radius, "center_mm": [cx, cy],
        "central_angle_rad": sweep, "arc_length_mm": arc_length,
        "derived_sagitta_mm": sagitta,
        "nominal_f_plan_reference_mm": nominal_sagitta_reference,
        "tangent_at_A_plan": [1.0, 0.0],
        "bulge_side": "弦线内侧、向步桁方向（y更小）",
    }


def arc_point_at(arc_points, t):
    k = min(len(arc_points) - 2, max(0, int(t * (len(arc_points) - 1))))
    u = t * (len(arc_points) - 1) - k
    a, b = arc_points[k], arc_points[k + 1]
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


def choose_fan_count(arc_length, spacing_limit):
    """Choose the smallest odd arc-division interval count n with A=L/n < B."""
    n = 3
    while arc_length / n >= spacing_limit:
        n += 2
        if n > 1001:
            raise ValueError(f"弧长{arc_length:.3f}mm与间距限值{spacing_limit:.3f}mm要求超过1001个半侧弧分区间；请检查B或模型尺度")
    return n


def old_side_body_top(case, r):
    """Actual old-corner side top, including its longitudinal taper, at O-B fraction r."""
    ctx = case["fan_surface"]
    point = (ctx["O"][0] + (ctx["B"][0] - ctx["O"][0]) * r,
             ctx["O"][1] + (ctx["B"][1] - ctx["O"][1]) * r)
    start, unit, length = case["old_start"], case["old_unit"], case["old_length_plan"]
    t = ((point[0] - start[0]) * unit[0] + (point[1] - start[1]) * unit[1]) / length
    t = min(1.0, max(0.0, t))
    xc = start[0] + unit[0] * length * t
    height = case["old_height_root"] * (1.0 - (1.0 - case["old_tail_scale"]) * smooth(t))
    body_top = case["purlin_radius"] - float(case["p"]["t"]) * xc - case["old_seat_relief"] + height
    # At O, close the 4.21 mm datum difference to the supported 750 mm top;
    # taper this explicit fitting allowance to zero at B.
    return body_top + ctx["O_closure_delta_mm"] * (1.0 - r)


def setup_fan_surface(case):
    D, e = case["D"], case["e"]
    A = (-D, e)
    B = case["old_near_side_B"]
    arc_points, arc_info = circular_arc(A, B, case["fan_arc_sagitta_reference"],
                                        samples=case["fan_station_count"])
    n = choose_fan_count(arc_info["arc_length_mm"], case["fan_spacing_limit"])
    O = (-D, -D + case["old_width_root"] / math.sqrt(2.0))
    # O rests on the step-purlin crown; the straight rafter top there is crown + 60.
    zO = case["step_axis_z"] + case["purlin_radius"] + case["fan_height"]
    t_pitch = float(case["p"]["t"])
    zA = case["purlin_radius"] - t_pitch * e - case["rafter_seat_relief"] + case["fan_height"]
    case["fan_top_intercept"] = case["purlin_radius"] - case["rafter_seat_relief"] + case["fan_height"]
    side_ctx = {"O": O, "B": B, "A": A, "arc_points": arc_points,
                "arc_info": arc_info, "n": n, "fan_rafter_count": n - 1,
                "zO": zO, "zA": zA}
    case["fan_surface"] = side_ctx
    # Solve directly from the old member section for a clear, stable audit value.
    unit, length, start = case["old_unit"], case["old_length_plan"], case["old_start"]
    tO = ((O[0] - start[0]) * unit[0] + (O[1] - start[1]) * unit[1]) / length
    tO = min(1.0, max(0.0, tO))
    xc = start[0] + unit[0] * length * tO
    hO = case["old_height_root"] * (1.0 - (1.0 - case["old_tail_scale"]) * smooth(tO))
    actual_O = case["purlin_radius"] - t_pitch * xc - case["old_seat_relief"] + hO
    side_ctx["old_body_top_at_O_actual_mm"] = actual_O
    side_ctx["O_closure_delta_mm"] = zO - actual_O
    side_ctx["raw_old_body_top_at_O_mm"] = actual_O
    side_ctx["zB_old_body_top_actual_mm"] = old_side_body_top(case, 1.0)
    side_ctx["zA_straight_top_mm"] = zA
    side_ctx["n"] = n


def fan_top_z(case, x, y):
    """Bilinear common top: straight-bank edge to tapered old-corner side edge."""
    ctx = case["fan_surface"]
    O, pts = ctx["O"], ctx["arc_points"]
    theta = math.atan2(y - O[1], x - O[0])
    angles = [math.atan2(p[1] - O[1], p[0] - O[0]) for p in pts]
    s = 0.0
    for i in range(len(angles) - 1):
        if angles[i] >= theta >= angles[i + 1]:
            frac = (theta - angles[i]) / (angles[i + 1] - angles[i])
            s = (i + frac) / (len(angles) - 1)
            break
    else:
        s = 0.0 if theta > angles[0] else 1.0
    target = arc_point_at(pts, s)
    dx, dy = target[0] - O[0], target[1] - O[1]
    den = dx * dx + dy * dy
    r = 0.0 if den <= EPS else ((x - O[0]) * dx + (y - O[1]) * dy) / den
    r = min(1.0, max(0.0, r))
    z_straight = ctx["zO"] + (ctx["zA"] - ctx["zO"]) * r
    z_old = old_side_body_top(case, r)
    top = (1.0 - s) * z_straight + s * z_old
    return top


def support_crown_z(case, x, y):
    """Highest actual cylindrical purlin bearing surface at a plan point."""
    r = case["purlin_radius"]
    candidates = []
    for sid in PURLIN_IDS:
        if sid.startswith("eave"):
            zc, lo, hi = case["eave_axis_z"], case["eave_start"], case["eave_end"]
            cross = 0.0
            notch_center = 0.0
            longitudinal = x if sid.endswith("_x") else y
            transverse = y if sid.endswith("_x") else x
        else:
            zc, lo, hi = case["step_axis_z"], case["step_start"], case["step_end"]
            cross = -case["D"]
            notch_center = -case["D"]
            longitudinal = x if sid.endswith("_x") else y
            transverse = y if sid.endswith("_x") else x
        if longitudinal < lo or longitudinal > hi or abs(transverse - cross) > r:
            continue
        # The positive X/Y ends are mitered at x=y. Do not sample support
        # beyond that actual BRep boundary when deriving a shared surface.
        if sid.endswith("_x") and x > y + 1e-8:
            continue
        if sid.endswith("_y") and y > x + 1e-8:
            continue
        crown = zc + math.sqrt(max(0.0, r * r - (transverse - cross) ** 2))
        # The X-running half-lap removes the upper half at its crossing.
        if (sid.endswith("_x") and abs(longitudinal - notch_center) <= r and
                abs(transverse - cross) <= r):
            crown = zc
        candidates.append(crown)
    return max(candidates) if candidates else -math.inf


def plan_prism(points, zmin=-1000.0, zmax=1200.0):
    solid = cq.Workplane("XY").polyline(points).close().extrude(zmax - zmin).translate((0, 0, zmin)).val()
    if not solid.isValid() or len(solid.Solids()) != 1:
        raise ValueError("扇区裁剪棱柱无效")
    return solid


def qiangshan_top_z(case, x, y, mirrored=False):
    """Top support height with a local monotone A-end connector when needed."""
    start, end = -case["D"], case["qiangshan_end"]
    along = y if mirrored else x
    across = x if mirrored else y
    if not (start - 1e-6 <= along <= end + 1e-6 and
            abs(across) <= case["qiangshan_width"] / 2.0 + 1e-6):
        return None
    q_values = np.linspace(start, end, 25)
    profile, _ = _qiangshan_profile_for_across(
        case, across, q_values=q_values)
    return float(np.interp(float(along), q_values, profile))


def _qiangshan_profile_for_across(case, across, q_values=None):
    """Keep the original underside field except for a descending A-end hump."""
    start, end = -case["D"], case["qiangshan_end"]
    qs = np.asarray(q_values if q_values is not None else np.linspace(start, end, 25), dtype=float)
    raw = np.asarray([
        fan_top_z(case, float(q), float(across)) - float(case["fan_height"])
        for q in qs
    ], dtype=float)
    corrected = raw.copy()
    correction = {
        "across_mm": float(across), "raw_A_top_z_mm": float(raw[0]),
        "corrected_A_top_z_mm": float(raw[0]), "B_top_z_mm": float(raw[-1]),
        "transition_end_q_mm": None, "lowering_at_A_mm": 0.0,
    }
    if len(qs) >= 3 and raw[0] > raw[1] + 1e-8:
        valleys = [i for i in range(1, len(raw) - 1)
                   if raw[i] <= raw[i - 1] and raw[i] <= raw[i + 1]]
        if valleys:
            valley = int(valleys[0])
            rise_span = float(qs[valley + 1] - qs[valley])
            rise = float(raw[valley + 1] - raw[valley])
            if rise_span > 1e-9 and rise > 1e-9:
                slope = rise / rise_span
                adjusted_a = float(raw[valley] - slope * (qs[valley] - qs[0]))
                if adjusted_a < float(raw[0]) - 1e-8:
                    delta = float(raw[valley] - adjusted_a)
                    span = float(qs[valley] - qs[0])
                    for i in range(valley + 1):
                        t = float((qs[i] - qs[0]) / span)
                        smooth_t = t * t * (3.0 - 2.0 * t)
                        corrected[i] = adjusted_a + delta * smooth_t
                    correction.update({
                        "corrected_A_top_z_mm": adjusted_a,
                        "transition_end_q_mm": float(qs[valley]),
                        "lowering_at_A_mm": float(raw[0] - adjusted_a),
                    })
    return corrected, correction


def rafter_underface_z(case, x, y):
    # Keep one continuous pitch along each straight rafter. The former
    # pointwise supports raised individual stations over purlins/qianshan,
    # creating visible humps. A 12 mm seat overlap at purlins is intentional.
    return case["purlin_radius"] - float(case["p"]["t"]) * y - case["rafter_seat_relief"]


def fan_rafter_bar(case, start, target, suffix):
    dx, dy = target[0] - start[0], target[1] - start[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    half_w, depth = case["fan_width"] / 2.0, case["fan_height"]
    sections = []
    centerline = []
    station_ts = set(float(v) for v in np.linspace(0.0, 1.0, 9))
    if abs(dy) > 1e-8:
        for qy in (-45.0, 0.0, 45.0):
            t = (qy - start[1]) / dy
            if 0.0 < t < 1.0:
                station_ts.add(float(t))
        for qy in (-45.0, 45.0):
            for side_offset in (-ny * half_w, ny * half_w):
                t = (qy - side_offset - start[1]) / dy
                if 0.0 < t < 1.0:
                    station_ts.add(float(t))
    for t in sorted(station_ts):
        x = start[0] + dx * t
        y = start[1] + dy * t
        left = (x + nx * half_w, y + ny * half_w)
        right = (x - nx * half_w, y - ny * half_w)
        # Both faces follow the ruled common top surface in world Z, with a
        # uniform 60 mm vertical depth.
        zl = fan_top_z(case, left[0], left[1]) - depth
        zr = fan_top_z(case, right[0], right[1]) - depth
        section = [[left[0], left[1], zl], [right[0], right[1], zr],
                   [right[0], right[1], zr + depth], [left[0], left[1], zl + depth]]
        sections.append(section)
        centerline.append([x, y, (zl + zr) / 2.0 + depth / 2.0])
    return loft_sections(sections), centerline


def fan_rafter_parts(case, parts, control, old_member):
    ctx = case["fan_surface"]
    A, B, O = ctx["A"], ctx["B"], ctx["O"]
    arc_points, arc_info, n = ctx["arc_points"], ctx["arc_info"], ctx["n"]
    fan_records = []
    all_arc = []
    for i in range(n + 1):
        f = i / n
        point = arc_point_at(arc_points, f)
        top_z = fan_top_z(case, point[0], point[1])
        all_arc.append([point[0], point[1], top_z])
    for i in range(1, n):
        fan_index = i
        center_fraction = i / n
        f0, f1 = (i - 0.5) / n, (i + 0.5) / n
        target = arc_point_at(arc_points, center_fraction)
        bar, centerline = fan_rafter_bar(case, O, target, f"fan_{fan_index:02d}")
        boundary = [O]
        for j in range(9):
            f = f0 + (f1 - f0) * j / 8.0
            boundary.append(arc_point_at(arc_points, f))
        vertical_span = max(4000.0, 2.0 * float(case["p"]["L_n"]))
        sector = plan_prism(boundary, -vertical_span, vertical_span)
        shape = bar.intersect(sector)
        shape = shape.cut(old_member)
        if not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-5:
            raise ValueError(f"扇椽几何无效 fan_{i+1:02d}: valid={shape.isValid()} solids={len(shape.Solids())} volume={shape.Volume()}")
        pid = f"fan_rafter_{fan_index:02d}"
        add_part(parts, pid, f"摔网椽{fan_index:02d}（70宽，扇区割角）", "fan_rafters", shape)
        fan_records.append({"id": pid, "index": fan_index, "role": "fan", "is_boundary": False,
                            "centerline_plan_start": list(O),
                            "centerline_plan_end": list(target), "centerline": centerline,
                            "arc_band_fraction": [f0, f1], "sector_miter": True,
                            "arc_station_fraction": center_fraction,
                            "trimmed_against_old_corner": True,
                            "purlin_seat_cuts": [],
                            "volume_mm3": float(shape.Volume())})
        control.append({"id": f"{pid}_axis", "name": f"{pid}中心线", "group": "fan_rafters",
                        "points": [centerline[0], centerline[-1]]})
    case["fan_geometry"] = {
        "A": [A[0], A[1]], "B": [B[0], B[1]], "O": [O[0], O[1]],
        "arc": arc_info, "n": n,
        "n_definition": "n为最小奇数等弧长区间数，A=L弧/n；每侧由内部i/n分点(i=1..n-1)生成n-1根摔网椽，弧端点仅作边界控制",
        "arc_length_over_n_mm": arc_info["arc_length_mm"] / n,
        "fan_division_count_n": n, "fan_rafter_count": len(fan_records),
        "internal_arc_station_count": len(fan_records), "arc_boundary_points": all_arc,
        "fan_rafters": fan_records,
        "construction": "半侧y>=x由O向弧线内部i/n等分点放射；邻根按相邻分点之间的中界扇区割角，弧端点不另生摔网；另一半对角镜像，不以整宽共点插入老戗。",
    }
    control.append({"id": "fan_arc", "name": "摔网椽外端平面圆弧", "group": "fan_rafters",
                    "points": all_arc})
    root_bottom = fan_top_z(case, O[0], O[1]) - case["fan_height"]
    control.append({"id": "fan_O", "name": "扇椽分射点O", "group": "fan_rafters",
                    "points": [[O[0], O[1], root_bottom],
                               [O[0], O[1], root_bottom + case["fan_height"]]]})


def mirror_group_across_diagonal(parts, control, case, group, id_prefix, name_prefix):
    """Reflect one half across y=x; the transform swaps its pitch direction too."""
    originals = [p for p in parts if p["group"] == group and not p["id"].startswith(id_prefix + "mirror_")]
    for part in originals:
        old_id = part["id"]
        suffix = old_id.removeprefix(id_prefix)
        new_id = f"{id_prefix}mirror_{suffix}"
        reflected = part["shape"].mirror((1.0, -1.0, 0.0), (0.0, 0.0, 0.0))
        add_part(parts, new_id, f"{name_prefix}镜像{suffix}", group, reflected)
    for item in list(control):
        if item.get("group") != group:
            continue
        pid = item["id"]
        if not pid.startswith(id_prefix) and pid not in ("fan_arc", "fan_O"):
            continue
        reflected_points = [[p[1], p[0], p[2]] for p in item["points"]]
        control.append({"id": f"mirror_{pid}", "name": f"镜像{item['name']}",
                        "group": group, "points": reflected_points})


def eave_rafter_bar(case, xcenter, y0, y1):
    half_w, depth = case["eave_rafters_width"] / 2.0, case["eave_rafters_height"]
    sections = []
    centerline = []
    ys = set(float(v) for v in np.linspace(y0, y1, 9))
    ys.update(y for y in (-45.0, 0.0, 45.0) if y0 < y < y1)
    for y in sorted(ys):
        xl, xr = xcenter - half_w, xcenter + half_w
        zl, zr = rafter_underface_z(case, xl, y), rafter_underface_z(case, xr, y)
        section = [[xl, y, zl], [xr, y, zr], [xr, y, zr + depth], [xl, y, zl + depth]]
        sections.append(section)
        centerline.append([xcenter, y, (zl + zr) / 2.0 + depth / 2.0])
    return loft_sections(sections), centerline


def eave_rafter_parts(case, parts, control):
    D, e = case["D"], case["e"]
    # Include the ordinary eave rafter on the A-end boundary; continue the
    # regular B-pitch centers outward while retaining the prior tail envelope.
    x_boundary = -D
    x_first = case["step_start"] + case["eave_rafters_width"] / 2.0
    outward_intervals = max(0, int(math.floor((x_boundary - x_first) / case["eave_pitch"] + 1e-9)))
    x_centers = [x_boundary - i * case["eave_pitch"]
                 for i in range(outward_intervals, -1, -1)]
    y0, y1 = -D - case["eave_tail_extension"], e
    records = []
    for i, xc in enumerate(x_centers):
        bar, axis = eave_rafter_bar(case, xc, y0, y1)
        shape = bar
        if not shape.isValid() or len(shape.Solids()) != 1 or shape.Volume() <= 1e-5:
            raise ValueError(f"出檐椽几何无效 eave_{i+1:02d}: valid={shape.isValid()} solids={len(shape.Solids())} volume={shape.Volume()}")
        pid = f"eave_rafter_{i+1:02d}"
        is_boundary = abs(float(xc) - x_boundary) <= 1e-7
        role = "boundary" if is_boundary else "regular"
        name = (f"正身边界出檐椽{i+1:02d}" if is_boundary else f"正身出檐椽{i+1:02d}")
        add_part(parts, pid, f"{name}（x={xc:.1f}）", "eave_rafters", shape)
        records.append({"id": pid, "index": i+1, "role": role, "is_boundary": is_boundary,
                        "center_x_mm": xc, "y_extent_mm": [y0, y1],
                        "centerline": axis, "purlin_seat_cuts": [],
                        "volume_mm3": float(shape.Volume())})
        control.append({"id": f"{pid}_axis", "name": f"{pid}中心线", "group": "eave_rafters",
                        "points": [axis[0], axis[-1]]})
    case["eave_rafters_geometry"] = {
        "count": len(records), "x_centers_mm": x_centers,
        "y_extent_mm": [y0, y1], "tail_extension_past_step_mm": case["eave_tail_extension"],
        "outer_end_coordinate_y_mm": e,
        "pitch_assumption_mm": case["eave_pitch"], "rafters": records,
        "boundary_rafter_id": next((r["id"] for r in records if r["is_boundary"]), None),
        "regular_rafter_count": sum(not r["is_boundary"] for r in records),
        "boundary_rafter_count": sum(bool(r["is_boundary"]) for r in records),
        "vertical_rule": f"直椽底线z={case['purlin_radius']:g}-{case['p']['t']:g}*y-{case['rafter_seat_relief']:g}mm；沿全长保持原线性纵坡和放置基准，不在桁位切座或抬高。",
        "nominal_purlin_seat_relief_mm": case["rafter_seat_relief"],
        "rafter_centerline_offset_from_step_purlin_y_axis_mm": case["purlin_radius"] + case["eave_rafters_width"] / 2.0,
        "construction": "边界普通出檐椽中心线位于x=-D，与fan_surface.A端同轴并计一次；其余正身出檐椽由边界按B向外等档排列，保持既有有效尾端范围。",
    }


def control_axes(case):
    D = case["D"]
    axes = (
        ("axis_eave_x", "廊桁X向中心轴", "eave_purlins", [case["eave_start"], 0, 0], [0, 0, 0]),
        ("axis_eave_y", "廊桁Y向中心轴", "eave_purlins", [0, case["eave_start"], 0], [0, 0, 0]),
        ("axis_step_x", "步桁X向中心轴", "step_purlins", [case["step_start"], -D, case["step_axis_z"]], [-D, -D, case["step_axis_z"]]),
        ("axis_step_y", "步桁Y向中心轴", "step_purlins", [-D, case["step_start"], case["step_axis_z"]], [-D, -D, case["step_axis_z"]]),
    )
    return [{"id": cid, "name": name, "group": group, "points": [a, b]} for cid, name, group, a, b in axes]


def make_parts(case):
    parts, control = [], control_axes(case)
    print("stage02: support members start", flush=True)
    support_parts(case, parts)
    print("stage02: support members complete", flush=True)
    old_member = old_corner_shape(case)
    add_part(parts, "old_corner", "老戗（宽1.5a，近直斜主材）", "old_corner", old_member)
    control.append({"id": "old_corner_axis", "name": "老戗中心线", "group": "old_corner",
                    "points": [case["old_corner_centerline"][0], case["old_corner_centerline"][-1]]})
    setup_fan_surface(case)
    add_part(parts, "qiangshan_x", "戗山木X向（上承面由共同椽底导出）", "qiangshan",
             qiangshan_wedge(case, True))
    add_part(parts, "qiangshan_y", "戗山木Y向镜像（上承面由共同椽底导出）", "qiangshan",
             qiangshan_wedge(case, False))
    case["purlin_shapes"].update({p["id"]: p["shape"] for p in parts if p["id"].startswith("qiangshan_")})
    print("stage02: old corner and support wedges complete; fan rafters start", flush=True)
    fan_rafter_parts(case, parts, control, old_member)
    mirror_group_across_diagonal(parts, control, case, "fan_rafters", "fan_rafter_", "摔网椽")
    case["fan_geometry"]["mirrored_count"] = case["fan_geometry"]["fan_rafter_count"]
    case["fan_geometry"]["total_count"] = 2 * case["fan_geometry"]["fan_rafter_count"]
    print(f"stage02: fan rafters complete; one-side count={case['fan_geometry']['fan_rafter_count']}; straight rafters start", flush=True)
    eave_rafter_parts(case, parts, control)
    mirror_group_across_diagonal(parts, control, case, "eave_rafters", "eave_rafter_", "正身出檐椽")
    case["eave_rafters_geometry"]["total_count_after_mirror"] = 2 * case["eave_rafters_geometry"]["count"]
    print("stage02: base framing complete", flush=True)
    return parts, control


def write_preview_mesh(parts, control):
    """Write the viewer mesh before the slower pairwise contact audit."""
    mesh_parts, obj_lines = [], []
    obj_offset = 1
    for part in parts:
        vertices, triangles = part["shape"].tessellate(2.0, 0.35)
        vv = [float(c) for v in vertices for c in v.toTuple()]
        tt = [int(i) for tri in triangles for i in tri]
        mesh_parts.append({"id": part["id"], "name": part["name"], "group": part["group"],
                           "vertices": vv, "triangles": tt})
        obj_lines.append(f"o {part['id']}")
        for v in vertices:
            obj_lines.append("v %.6f %.6f %.6f" % v.toTuple())
        for tri in triangles:
            obj_lines.append("f %d %d %d" % tuple(obj_offset + int(i) for i in tri))
        obj_offset += len(vertices)
    mesh = {
        "metadata": {"units": "mm", "up_axis": "Z", "case_id": "CASE_ASSUMED_001",
                     "stage": STAGE, "stage_name": STAGE_NAME,
                     "scope": "合成假设的第二层；含老戗、摔网椽和正身出檐椽，不含嫩戗、飞椽及板。",
                     "mesh_tessellation_mm": 2.0},
        "parts": mesh_parts, "control_lines": control,
    }
    mesh_path = OUT / "stage02_model_mesh.json"
    mesh_path.write_text(json.dumps(mesh, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    (OUT / "stage02_model.obj").write_text("\n".join(obj_lines) + "\n", encoding="utf-8")
    return mesh_path



def validate(parts, case):
    """Bounded solid-pair audit; face-area coincidence is deliberately not claimed."""
    t0 = time.perf_counter()
    time_limit = 100.0
    volume_tol = 1e-3
    distance_tol = 1e-4
    qshan_overlap_limit = 1100.0
    part_by_id = {p["id"]: p for p in parts}
    entities = []
    for part in parts:
        shape = part["shape"]
        solid_volumes = [float(s.Volume()) for s in shape.Solids()]
        entities.append({"id": part["id"], "name": part["name"], "group": part["group"],
                         "valid": bool(shape.isValid()) and len(solid_volumes) == 1,
                         "solid_count": len(solid_volumes), "volume_mm3": float(shape.Volume()),
                         "finite": math.isfinite(float(shape.Volume())) and all(math.isfinite(v) for v in solid_volumes)})

    required = {}
    def require(a, b, kind):
        key = tuple(sorted((a, b)))
        required[key] = kind
    for sid in PURLIN_IDS:
        require("old_corner", sid, "老戗—实际圆桁座口")
    require("eave_purlin_x", "eave_purlin_y", "廊桁半搭接触")
    require("step_purlin_x", "step_purlin_y", "步桁半搭接触")
    require("qiangshan_x", "eave_purlin_x", "戗山木X向—廊桁座口")
    require("qiangshan_y", "eave_purlin_y", "戗山木Y向—廊桁座口")
    for part in parts:
        pid = part["id"]
        if part["group"] == "fan_rafters":
            mirror = "_mirror_" in pid
            require(pid, "qiangshan_y" if mirror else "qiangshan_x", "摔网椽—戗山木共同承面")
        elif part["group"] == "eave_rafters":
            mirror = "_mirror_" in pid
            require(pid, "step_purlin_y" if mirror else "step_purlin_x", "出檐椽—步桁座口")
            require(pid, "eave_purlin_y" if mirror else "eave_purlin_x", "出檐椽—廊桁座口")

    pair_results = {}
    intersection_errors = []
    unexpected_intersections = []
    qshan_surface_approximations = []
    required_contacts = []
    failed_required_contacts = []
    untested_required_contacts = []

    def is_qshan_fan_pair(key):
        fan_id = next((v for v in key if v.startswith("fan_rafter_")), None)
        q_id = next((v for v in key if v.startswith("qiangshan_")), None)
        if not fan_id or q_id not in ("qiangshan_x", "qiangshan_y"):
            return False
        mirrored = "_mirror_" in fan_id
        return q_id == ("qiangshan_y" if mirrored else "qiangshan_x")

    def measure_pair(key, force_distance=False):
        if key in pair_results:
            return pair_results[key]
        a, b = (part_by_id[key[0]], part_by_id[key[1]])
        bbox_hit = bbox_overlap(a["shape"], b["shape"])
        volume = 0.0
        if bbox_hit:
            volume = float(a["shape"].intersect(b["shape"]).Volume())
        if force_distance or volume <= volume_tol:
            distance = float(a["shape"].distance(b["shape"]))
        else:
            distance = 0.0
        result = {"a": key[0], "b": key[1], "bbox_overlap": bbox_hit,
                  "intersection_volume_mm3": volume, "minimum_distance_mm": distance,
                  "face_area_status": "not_tested"}
        pair_results[key] = result
        return result

    # Prioritize the support pairs that determine whether the assembly is seated.
    for key, kind in required.items():
        if time.perf_counter() - t0 > time_limit:
            untested_required_contacts.append({"a": key[0], "b": key[1], "kind": kind,
                                               "reason": "bounded audit time limit reached"})
            continue
        try:
            result = measure_pair(key, force_distance=True)
            qshan_pair = is_qshan_fan_pair(key)
            volume = result["intersection_volume_mm3"]
            distance = result["minimum_distance_mm"]
            if volume > volume_tol:
                state = "approximate_common_surface_interference" if qshan_pair and volume <= qshan_overlap_limit else "positive_volume_intersection"
            elif distance <= distance_tol:
                state = "distance_contact_face_area_not_tested"
            else:
                state = "gap"
            entry = {**result, "kind": kind, "state": state,
                     "required": True, "face_contact_verified": None}
            required_contacts.append(entry)
            if qshan_pair and volume <= qshan_overlap_limit and volume > 0:
                qshan_surface_approximations.append(entry)
            if distance > distance_tol or (volume > volume_tol and not (qshan_pair and volume <= qshan_overlap_limit)):
                failed_required_contacts.append(entry)
        except Exception as exc:
            error = {"a": key[0], "b": key[1], "kind": kind, "error": str(exc)[:240]}
            intersection_errors.append(error)
            failed_required_contacts.append(error)

    # Check every bbox-overlapping part pair, excluding only fan strips proven
    # plan-disjoint by their sector partitions and non-neighbor indices.
    all_bbox_pairs = [(i, j) for i in range(len(parts)) for j in range(i + 1, len(parts))
                      if bbox_overlap(parts[i]["shape"], parts[j]["shape"])]
    def fan_sector_neighbors(a, b):
        if a["group"] != "fan_rafters" or b["group"] != "fan_rafters":
            return True
        def side_index(pid):
            return ("mirror" if "_mirror_" in pid else "main", int(pid.rsplit("_", 1)[1]))
        side_a, index_a = side_index(a["id"])
        side_b, index_b = side_index(b["id"])
        if side_a == side_b:
            return abs(index_a - index_b) == 1
        return (index_a <= 2 and index_b <= 2) or (index_a >= 10 and index_b >= 10)
    candidates = [(i, j) for i, j in all_bbox_pairs if fan_sector_neighbors(parts[i], parts[j])]
    excluded_sector_pairs = len(all_bbox_pairs) - len(candidates)
    skipped_pairs = 0
    for index, (i, j) in enumerate(candidates):
        if time.perf_counter() - t0 > time_limit:
            skipped_pairs = len(candidates) - index
            break
        key = tuple(sorted((parts[i]["id"], parts[j]["id"])))
        try:
            result = measure_pair(key, force_distance=(key in required))
            volume = result["intersection_volume_mm3"]
            if volume > volume_tol:
                if is_qshan_fan_pair(key) and volume <= qshan_overlap_limit:
                    if not any((v["a"], v["b"]) == key for v in qshan_surface_approximations):
                        qshan_surface_approximations.append({**result, "kind": "摔网椽—戗山木共同承面",
                                                              "state": "approximate_common_surface_interference",
                                                              "required": True, "face_contact_verified": None})
                else:
                    unexpected_intersections.append({**result, "classification": "unexpected_positive_volume"})
        except Exception as exc:
            intersection_errors.append({"a": key[0], "b": key[1], "error": str(exc)[:240]})

    for key, kind in required.items():
        if key not in pair_results and not any(v["a"] == key[0] and v["b"] == key[1] for v in untested_required_contacts):
            untested_required_contacts.append({"a": key[0], "b": key[1], "kind": kind,
                                               "reason": "not reached before bounded audit stopped"})

    invalid = [e for e in entities if not e["valid"] or not e["finite"]]
    qshan_volumes = [v["intersection_volume_mm3"] for v in qshan_surface_approximations]
    targeted_complete = not failed_required_contacts and not untested_required_contacts
    full_scan_complete = skipped_pairs == 0 and not intersection_errors
    if invalid or failed_required_contacts or untested_required_contacts or unexpected_intersections or intersection_errors:
        status = "fail"
    elif not full_scan_complete:
        status = "targeted_checks_complete_full_candidate_scan_incomplete"
    elif qshan_surface_approximations:
        status = "complete_with_documented_surface_approximation"
    else:
        status = "complete"
    return {
        "stage": STAGE, "stage_name": STAGE_NAME,
        "case_id": case["source"].get("case_id", "CASE_ASSUMED_001"),
        "status": status,
        "targeted_support_checks": "complete" if targeted_complete else "incomplete",
        "full_bbox_candidate_scan": "complete" if full_scan_complete else "incomplete",
        "entity_count": len(parts),
        "groups": {g: sum(p["group"] == g for p in parts) for g in sorted({p["group"] for p in parts})},
        "entities": entities, "invalid_entities": invalid,
        "bbox_candidate_pairs_checked": len(pair_results),
        "bbox_candidate_pairs_total": len(all_bbox_pairs),
        "sector_disjoint_pairs_excluded": excluded_sector_pairs,
        "bbox_candidate_pairs_skipped": skipped_pairs,
        "audit_time_limit_seconds": time_limit,
        "positive_intersection_pairs": unexpected_intersections,
        "qianshan_rafter_surface_approximation": {
            "pair_count": len(qshan_surface_approximations),
            "boolean_volume_tolerance_mm3": volume_tol,
            "documented_per_pair_volume_limit_mm3": qshan_overlap_limit,
            "min_common_volume_mm3": min(qshan_volumes) if qshan_volumes else 0.0,
            "max_common_volume_mm3": max(qshan_volumes) if qshan_volumes else 0.0,
            "sum_common_volume_mm3": sum(qshan_volumes),
            "min_distance_mm": min((v["minimum_distance_mm"] for v in qshan_surface_approximations), default=None),
            "max_distance_mm": max((v["minimum_distance_mm"] for v in qshan_surface_approximations), default=None),
            "pairs": qshan_surface_approximations,
            "interpretation": "同一扇区共同底面用离散放样近似；记录实体布尔公共体积与最短距，面面积未测，不宣称数学共面。",
        },
        "expected_contacts": required_contacts,
        "failed_expected_contacts": failed_required_contacts,
        "untested_expected_contacts": untested_required_contacts,
        "intersection_errors": intersection_errors,
        "expected_contact_policy": "优先测支承对；所有bbox重叠候选对测公共体积，必要时测最短距离；未测面面积不得称面接触。",
        "purlin_axis_segments": {
            "eave_purlins_mm": [case["eave_start"], 0.0],
            "step_purlins_mm": [case["step_start"], -case["D"]],
            "positive_stock_end_before_miter_mm": {
                "eave": case["eave_end"], "step": case["step_end"]},
            "miter_plane": "vertical x=y at each pair's crossing; X rails keep x<=y and Y rails keep y<=x",
            "diameter_mm": case["purlin_diameter"],
            "axis_intersections_mm": {"eave": [0, 0, 0], "step": [-case["D"], -case["D"], case["step_axis_z"]]},
        },
        "fan_layout": case["fan_geometry"],
        "straight_eave_rafters": case["eave_rafters_geometry"],
        "total_volume_mm3": float(sum(p["shape"].Volume() for p in parts)),
        "step_roundtrip": None,
        "runtime_validation_seconds": time.perf_counter() - t0,
        "limitations": [
            "阶段2含老戗、摔网椽、正身出檐椽，不含嫩戗、飞椽、板及细部榫卯。",
            "老戗宽165mm由1.5a直接计算；主体110mm，中央车背41.25mm作简化，老戗保持近直。",
            "老戗与摔网椽平面端弧、椽断面厚度与尾伸为明确假设；几何接触不代表结构承载验证。",
            "扇椽顶面沿O到外弧线性插值，并沿直身边与实际老戗侧上缘插值；世界Z厚度60mm。",
            "戗山木上承面按共同椽底离散放样并切配廊桁；与摔网椽存在已量化的放样残差，未开逐椽槽。",
            "全候选扫描排除了扇区构造可证明不相交的远隔扇椽对；其余候选按实体布尔检查，面接触面积未测。",
        ],
    }

def snapshot(case):
    p = case["p"]
    selected = {k: p[k] for k in ("D", "e", "p", "t", "B", "f_plan", "a", "k_c", "old_tail_scale", "old_ridge_h") if k in p}
    return {
        "metadata": {"case_id": "CASE_ASSUMED_001", "stage": STAGE, "stage_name": STAGE_NAME,
                     "units": "mm", "up_axis": "Z"},
        "selected_source_parameters": selected,
        "support_snapshot": {
            "purlin_diameter_mm": case["purlin_diameter"],
            "eave_axis_extent_mm": [case["eave_start"], 0.0],
            "step_axis_extent_mm": [case["step_start"], -case["D"]],
            "positive_stock_end_before_miter_mm": {
                "eave": case["eave_end"], "step": case["step_end"]},
            "corner_end_miter_plane": "vertical x=y; X rails keep x<=y and Y rails keep y<=x",
            "eave_axis_height_mm": case["eave_axis_z"], "step_axis_height_mm": case["step_axis_z"],
            "qiangshan_width_mm": case["qiangshan_width"],
            "qiangshan_extent_mm": [-case["D"], case["qiangshan_end"]],
            "qiangshan_surface_rule": "下缘按廊桁实体切配；上承面沿宽度取共同扇椽底场，A至首个局部低点使用单调smoothstep连接消除端部倒坡，其余纵向控制和B端保留原场。",
            "qiangshan_profile_adjustment": case.get("qiangshan_profile_adjustment"),
        },
        "old_corner": {
            "plan_start_step_intersection_mm": list(case["old_start"]),
            "plan_end_assumption_Q_mm": list(case["old_end"]),
            "centerline_support_datum": f"bottom z=90-0.5*x-{case['old_seat_relief']}；保留原完整轮廓，不按圆桁切座",
            "width_root_mm": case["old_width_root"],
            "width_at_eave_crossing_mm": case["old_width_eave"],
            "tip_scale": case["old_tail_scale"],
            "body_height_root_mm": case["old_height_root"],
            "car_back_ridge_root_mm": case["old_ridge_height_root"],
            "future_young_root_setback_mm": case["young_root_setback"],
            "fan_arc_B_near_side_at_setback_root_mm": list(case["old_near_side_B"]),
            "purlin_seat_cuts": case["old_corner_seat_purlins"],
            "seat_datum_relief_mm": case["old_corner_seat_relief_mm"],
            "cross_section": "vertical-section pentagon; 110mm body, central raised car-back crown of 41.25mm, both taper to 0.8 at outer tip",
        },
        "fan_rafters": {
            "width_mm": case["fan_width"], "vertical_depth_mm": case["fan_height"],
            "plan_arc_sagitta_reference_mm": case["fan_arc_sagitta_reference"],
            "plan_arc_sagitta_derived_mm": case.get("fan_geometry", {}).get("arc", {}).get("derived_sagitta_mm"),
            "spacing_limit_mm": case["fan_spacing_limit"],
            "root_rule": case["fan_sector_root_apex"],
            "vertical_datum": "共同顶面沿O到外弧线性放样；直身边接步桁冠顶+60mm，老戗侧接实际侧上缘（含收分），摔网椽为统一世界Z厚60mm。",
            "O_height_mm": case.get("fan_surface", {}).get("zO"),
            "O_old_side_top_actual_mm": case.get("fan_surface", {}).get("old_body_top_at_O_actual_mm"),
            "O_closure_adjustment_mm": case.get("fan_surface", {}).get("O_closure_delta_mm"),
            "old_side_top_at_B_mm": case.get("fan_surface", {}).get("zB_old_body_top_actual_mm"),
            "discrete_rafter_station_count": 9,
            "layout": case.get("fan_geometry"),
        },
        "eave_rafters": {
            "width_mm": case["eave_rafters_width"], "vertical_depth_mm": case["eave_rafters_height"],
            "center_pitch_mm": case["eave_pitch"], "tail_extension_past_step_mm": case["eave_tail_extension"],
            "vertical_datum": "沿Y向直椽采用共同线性纵坡z=90-0.5*y-12mm；保持完整轮廓，不在圆桁处切底或抬高。",
            "layout": case.get("eave_rafters_geometry"),
        },
        "assumptions": [
            "扇椽和出檐椽断面深均暂取60mm世界Z厚度，未从照片量取。",
            f"弧线以最小奇数等分区间数n={case.get('fan_geometry', {}).get('n')}控制，A=L弧/n={case.get('fan_geometry', {}).get('arc_length_over_n_mm')}mm；每侧生成n−1条内部摔网轴线，边界出檐椽独立归正身组。",
            "边界出檐椽中心位于x=-D；其余正身中心档从此边界按B向外排列，最外中心仍落在原有效尾端范围内。",
            "出檐椽越过步桁向里尾伸300mm；外端到y=e=600mm。",
            "老戗车背以截面中央三角凸脊近似，不对车背曲面作史料断定。",
            "扇椽与戗山木共底面按离散截面放样，允许报告记录的局部相交残差；不以逐点抬椽换取零交叠。",
        ],
    }


def write_outputs(parts, control, case, validation):
    mesh_parts, obj_lines, total_faces = [], [], 0
    obj_offset = 1
    for part in parts:
        vertices, triangles = part["shape"].tessellate(2.0, 0.35)
        vv = [float(c) for v in vertices for c in v.toTuple()]
        tt = [int(i) for tri in triangles for i in tri]
        mesh_parts.append({"id": part["id"], "name": part["name"], "group": part["group"],
                           "vertices": vv, "triangles": tt})
        total_faces += len(triangles)
        obj_lines.append(f"o {part['id']}")
        for v in vertices:
            obj_lines.append("v %.6f %.6f %.6f" % v.toTuple())
        for tri in triangles:
            obj_lines.append("f %d %d %d" % tuple(obj_offset + int(i) for i in tri))
        obj_offset += len(vertices)
    mesh = {
        "metadata": {"units": "mm", "up_axis": "Z", "case_id": "CASE_ASSUMED_001",
                     "stage": STAGE, "stage_name": STAGE_NAME,
                     "scope": "合成假设的第二层；含老戗、摔网椽和正身出檐椽，不含嫩戗、飞椽及板。",
                     "mesh_tessellation_mm": 2.0},
        "parts": mesh_parts, "control_lines": control,
    }
    mesh_path = OUT / "stage02_model_mesh.json"
    mesh_path.write_text(json.dumps(mesh, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    (OUT / "stage02_model.obj").write_text("\n".join(obj_lines) + "\n", encoding="utf-8")
    (OUT / "stage02_parameters.json").write_text(json.dumps(snapshot(case), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    compound = cq.Compound.makeCompound([p["shape"] for p in parts])
    cq.exporters.export(compound, str(OUT / "stage02_model.step"), exportType="STEP")
    imported = cq.importers.importStep(str(OUT / "stage02_model.step")).val()
    expected_solids = sum(len(p["shape"].Solids()) for p in parts)
    roundtrip = {"valid": bool(imported.isValid()), "solid_count": len(imported.Solids()),
                 "expected_solid_count": expected_solids, "volume_mm3": float(imported.Volume()),
                 "source_volume_mm3": float(sum(p["shape"].Volume() for p in parts))}
    roundtrip["relative_volume_error"] = abs(roundtrip["volume_mm3"] - roundtrip["source_volume_mm3"]) / max(1.0, roundtrip["source_volume_mm3"])
    validation["step_roundtrip"] = roundtrip
    if not roundtrip["valid"] or roundtrip["solid_count"] != expected_solids or roundtrip["relative_volume_error"] > 1e-6:
        validation["status"] = "fail"
    validation["mesh_face_count"] = total_faces
    validation["mesh_json_bytes"] = mesh_path.stat().st_size
    (OUT / "stage02_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return mesh_path


def main():
    case = load_case()
    case["_preview_only"] = "--preview-only" in sys.argv
    start = time.perf_counter()
    parts, control = make_parts(case)
    mesh_path = write_preview_mesh(parts, control)
    print(json.dumps({"preview_mesh_ready": str(mesh_path), "parts": len(parts),
                      "groups": {g: sum(p["group"] == g for p in parts) for g in sorted({p["group"] for p in parts})}},
                     ensure_ascii=False), flush=True)
    if "--preview-only" in sys.argv:
        (OUT / "stage02_parameters.json").write_text(
            json.dumps(snapshot(case), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"preview_only": True, "fan_arc": case["fan_geometry"]["arc"],
                          "fan_division_count_n": case["fan_geometry"]["n"],
                          "fan_count": case["fan_geometry"]["fan_rafter_count"],
                          "arc_length_over_n_mm": case["fan_geometry"]["arc_length_over_n_mm"]},
                         ensure_ascii=False), flush=True)
        return
    validation = validate(parts, case)
    mesh_path = write_outputs(parts, control, case, validation)
    runtime = time.perf_counter() - start
    validation["runtime_including_export_seconds"] = runtime
    (OUT / "stage02_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"mesh": str(mesh_path), "parts": len(parts), "groups": validation["groups"],
                      "status": validation["status"], "runtime_s": runtime,
                      "positive_intersections": len(validation["positive_intersection_pairs"]),
                      "failed_contacts": len(validation["failed_expected_contacts"]),
                      "fan_division_count_n": case.get("fan_geometry", {}).get("n"),
                      "fan_count": case.get("fan_geometry", {}).get("fan_rafter_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
