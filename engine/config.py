"""Strict input validation and derived-parameter reporting."""
from __future__ import annotations

import json
import math
from pathlib import Path


PARAMETER_DEFAULTS = {
    "D": 1200.0, "e": 600.0, "p": 300.0, "t": 0.5, "B": 200.0,
    "L_n": 900.0, "U_n": 480.0, "a": 110.0, "w_fly": 60.0,
    "k_c": 0.9, "old_tail_scale": 0.8, "old_ridge_h": 41.25,
    "young_root_scale": 0.8, "young_tip_scale": 0.8, "setback": 82.5,
    "likou_width": 55.0, "likou_h_regular": 70.0,
    "likou_h_corner_ratio": 2.25, "likou_extension_pitches": 1.5,
    "board_lower_t": 20.0, "board_upper_t": 13.75,
    "fly_tip_t": 40.0, "fly_corner_root_ratio": 0.65,
    "back_join_fraction": 0.7,
}

ADVANCED_DEFAULTS = {
    "purlin_radius_mm": 90.0, "qiangshan_width_mm": 90.0,
    "qiangshan_clearance_mm": 20.0, "fan_rafter_width_mm": 70.0,
    "fan_rafter_height_mm": 60.0, "rafter_seat_relief_mm": 12.0,
    "straight_rafter_width_mm": 70.0, "straight_rafter_height_mm": 60.0,
    "old_seat_relief_mm": 25.0, "board_surface_overlap_mm": 0.2,
    "likou_surface_overlap_mm": 0.2,
    "fan_station_count": 129, "flyer_width_fiber_stations": 17,
    "flyer_edge_inboard_bias_mm": 0.05, "flyer_tail_search_step_mm": 5.0,
    "flyer_front_edge_search_span_mm": 160.0,
    "flyer_front_edge_coarse_step_mm": 4.0,
    "flyer_tail_root_max_search_mm": 2200.0,
    "flyer_edge_fit_clearance_mm": 0.01,
    "flyer_root_fit_clearance_mm": 0.01,
    "flyer_outer_tip_offset_mm": -3.5,
    "flyer_tip_transition_power": 3.0,
    "flyer_contact_tail_gap_mm": 1.0,
    "flyer_seat_delta_mm": 3.0,
    "upper_board_surface_overlap_mm": 0.2,
    "combination_back_u_ratio_D": -0.559,
    "combination_front_u_ratio_D": 0.41478875,
    "combination_sagitta_ratio_chord": 0.11794,
    "combination_width_ratio_young_tip": 0.75,
    "combination_cross_width_stations": 7,
    "combination_u_stations": 33,
    "combination_end_trim_mm": 10.0,
    "mesh_tessellation_mm": 2.0,
    "mesh_angular_tolerance_rad": 0.35,
}

TOP_KEYS = {"case_id", "corner_angle_deg", "parameters", "advanced"}
LEGACY_HINTS = {
    "middle_contact_ratio": "已更名为 back_join_fraction（老/嫩戗接背点的弦向分位）",
    "young_sag_ratio": "当前嫩戗车背弧由根梢车背高度派生，不接受独立输入",
    "f_plan": "平面弧矢高由端点与正身端切线派生，不接受独立输入",
    "included_angle": "控制线夹角由 L_n 与 U_n 派生，不接受独立输入",
    "fly_tail_ratio": "普通尾长1.5目标已停用；尖尾由触底位置确定",
    "fly_outer_start_top_z_mm": "绝对高程已改为相对参数 flyer_outer_tip_offset_mm；默认高程自动随坡度和有效截面派生",
}


def _keys(obj, allowed, where):
    if not isinstance(obj, dict):
        raise ValueError(f"{where}必须是JSON对象")
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        messages = []
        for key in unknown:
            hint = LEGACY_HINTS.get(key)
            messages.append(f"{key!r}" + (f"（{hint}）" if hint else ""))
        raise ValueError(f"{where}包含未知字段：{', '.join(messages)}；未知参数不会被忽略")


def _merge(section, defaults, name):
    _keys(section, defaults, name)
    result = dict(defaults)
    result.update(section)
    for key, value in result.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name}.{key}必须是有限数值")
        result[key] = float(value)
    return result


def resolve_config(data: dict) -> dict:
    _keys(data, TOP_KEYS, "配置顶层")
    if "corner_angle_deg" not in data:
        raise ValueError("corner_angle_deg为必填项；本期只支持90°对称角部")
    angle = data["corner_angle_deg"]
    if (isinstance(angle, bool) or not isinstance(angle, (int, float)) or
            not math.isfinite(float(angle)) or abs(float(angle) - 90.0) > 1e-9):
        raise ValueError(f"corner_angle_deg={angle!r}不受支持；本期只支持90°，不会静默忽略")
    params = _merge(data.get("parameters", {}), PARAMETER_DEFAULTS, "parameters")
    advanced = _merge(data.get("advanced", {}), ADVANCED_DEFAULTS, "advanced")
    case_id = data.get("case_id", "custom")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id必须是非空字符串")

    strictly_positive = ["D", "e", "p", "t", "B", "L_n", "U_n", "a", "w_fly",
                         "old_ridge_h", "setback", "likou_width", "likou_h_regular",
                         "likou_h_corner_ratio", "likou_extension_pitches", "board_lower_t",
                         "board_upper_t", "fly_tip_t"]
    for key in strictly_positive:
        if params[key] <= 0:
            raise ValueError(f"parameters.{key}必须大于0")
    if params["U_n"] >= params["L_n"]:
        raise ValueError(f"必须满足0 < U_n < L_n；当前 U_n={params['U_n']:g}, L_n={params['L_n']:g}")
    for key in ("old_tail_scale", "young_root_scale", "young_tip_scale"):
        if not 0.0 < params[key] <= 1.0:
            raise ValueError(f"parameters.{key}必须在(0, 1]范围内")
    if params["k_c"] <= 0:
        raise ValueError("parameters.k_c必须大于0")
    if not 0.0 < params["fly_corner_root_ratio"] <= 1.0:
        raise ValueError("parameters.fly_corner_root_ratio必须在(0, 1]范围内")
    if not 0.05 <= params["back_join_fraction"] <= 0.95:
        raise ValueError("parameters.back_join_fraction必须在[0.05, 0.95]范围内，避免接背点落在端部退刀区")

    positive_advanced = ["purlin_radius_mm", "qiangshan_width_mm", "fan_rafter_width_mm",
                         "fan_rafter_height_mm", "straight_rafter_width_mm",
                         "straight_rafter_height_mm", "flyer_front_edge_search_span_mm",
                         "flyer_front_edge_coarse_step_mm", "flyer_tail_root_max_search_mm",
                         "flyer_tail_search_step_mm", "flyer_edge_fit_clearance_mm",
                         "flyer_root_fit_clearance_mm",
                         "flyer_tip_transition_power", "flyer_contact_tail_gap_mm",
                         "flyer_seat_delta_mm",
                         "combination_sagitta_ratio_chord", "combination_width_ratio_young_tip",
                         "combination_end_trim_mm",
                         "mesh_tessellation_mm", "mesh_angular_tolerance_rad"]
    for key in positive_advanced:
        if advanced[key] <= 0:
            raise ValueError(f"advanced.{key}必须大于0")
    for key in ("qiangshan_clearance_mm", "rafter_seat_relief_mm", "old_seat_relief_mm",
                "flyer_edge_inboard_bias_mm",
                "board_surface_overlap_mm", "likou_surface_overlap_mm", "upper_board_surface_overlap_mm"):
        if advanced[key] < 0:
            raise ValueError(f"advanced.{key}不能为负数")
    if advanced["board_surface_overlap_mm"] >= params["board_lower_t"]:
        raise ValueError("advanced.board_surface_overlap_mm必须小于parameters.board_lower_t")
    if advanced["upper_board_surface_overlap_mm"] >= params["board_upper_t"]:
        raise ValueError("advanced.upper_board_surface_overlap_mm必须小于parameters.board_upper_t")
    for key, minimum in (("fan_station_count", 33), ("flyer_width_fiber_stations", 5),
                         ("combination_cross_width_stations", 3), ("combination_u_stations", 9)):
        val = advanced[key]
        if val != int(val) or int(val) < minimum:
            raise ValueError(f"advanced.{key}必须是≥{minimum}的整数")
        advanced[key] = int(val)
    if advanced["fan_station_count"] % 2 == 0 or advanced["flyer_width_fiber_stations"] % 2 == 0:
        raise ValueError("advanced.fan_station_count和flyer_width_fiber_stations必须为奇数，确保包含中心截面")
    if not advanced["combination_back_u_ratio_D"] < advanced["combination_front_u_ratio_D"]:
        raise ValueError("advanced.combination_back_u_ratio_D必须小于combination_front_u_ratio_D")
    if advanced["combination_back_u_ratio_D"] >= 0 or advanced["combination_front_u_ratio_D"] <= 0:
        raise ValueError("组合木端点比例必须分居嫩戗根点u=0两侧")

    included = math.degrees(math.asin(params["U_n"] / params["L_n"]))
    old_axis_angle = math.degrees(math.atan(params["t"] / math.sqrt(2.0)))
    derived = {
        "corner_angle_deg": 90.0,
        "young_plan_run_mm": math.sqrt(params["L_n"] ** 2 - params["U_n"] ** 2),
        "young_rise_angle_deg": included,
        "old_young_control_included_angle_deg": 180.0 - included - old_axis_angle,
        "old_corner_fan_coefficient_mm": params["k_c"] * params["p"],
        "fan_count_rule": "n为满足弧长/n < B的最小奇数等分区间数；内部i/n分点生成n-1根摔网椽，弧端点只作边界",
        "fan_division_count_n": None,
        "fan_plan_sagitta_mm": None,
        "young_sagitta_mm": None,
        "young_sagitta_rule": "派生为min(根车背,梢车背)×0.75",
        "fly_tail_ratio": None,
        "fly_tail_rule": "尖尾由本次生成的下层板上表面触底求得；不强制1.5比值",
        "included_angle_source": "由嫩戗升角asin(U_n/L_n)和老戗方向atan(t/sqrt(2))按实轴方向派生；不作为输入",
    }
    return {"case_id": case_id, "corner_angle_deg": 90.0,
            "parameters": params, "advanced": advanced, "derived": derived}


def load_config(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置 {path}: {exc}") from exc
    return resolve_config(raw)


def write_resolved(config: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
