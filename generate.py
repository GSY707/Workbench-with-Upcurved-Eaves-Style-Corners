#!/usr/bin/env python
"""Generate a symmetric convex corner model and its flyer layout."""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENGINE = HERE / "engine"
MODEL_DEPS = ROOT / "资料整理" / "tmp" / "model_deps"
PLOT_DEPS = ROOT / "资料整理" / "tmp" / "section_plot_deps"
sys.path[:0] = [str(MODEL_DEPS), str(PLOT_DEPS), str(ENGINE)]

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import minimize_scalar

from config import load_config, write_resolved


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _geometry_pipeline_source(source_text):
    tree = ast.parse(source_text)
    generate_node = next((node for node in tree.body
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                          node.name == "generate"), None)
    if generate_node is None:
        raise RuntimeError("无法读取generate()中的几何构造入口")
    loader_nodes = []
    combination_nodes = []
    for node in ast.walk(generate_node):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            target_names = {target.id for target in targets
                            if isinstance(target, ast.Name)}
            value = getattr(node, "value", None)
            if ("stage05" in target_names and isinstance(value, ast.Call) and
                    isinstance(value.func, ast.Name) and value.func.id == "load_stage05"):
                loader_nodes.append(node)
            if "combination_info" in target_names:
                combination_nodes.append(node)
    loader_nodes.sort(key=lambda node: (node.lineno, node.col_offset))
    geometry_ends = [node for node in combination_nodes
                     if loader_nodes and node.lineno > loader_nodes[0].lineno]
    if not loader_nodes or not geometry_ends:
        raise RuntimeError("无法提取stage05几何构造分支，拒绝建立不完整的几何身份")
    source_lines = source_text.splitlines()
    start_line = loader_nodes[0].lineno
    end_line = max(node.end_lineno for node in geometry_ends)
    # The stage05 load is inside the geometry-rebuild branch, while preview and
    # exact now assign combination_info in separate arms of a nested mode test.
    # Hash the complete source span so both production geometry paths are part
    # of checkpoint identity.
    return "\n".join(source_lines[start_line - 1:end_line])


def _identity_digest(entries):
    digest = hashlib.sha256()
    for entry in entries:
        relative_bytes = entry["path"].encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(bytes.fromhex(entry["sha256"]))
    return digest.hexdigest()


def canonical_json_sha256(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def production_source_identity():
    """Use the workbench's full core-source set for provenance, not recovery gating."""
    paths = [HERE / "generate.py", HERE / "parameter_schema.json",
             HERE / "render_delivery.py", HERE / "viewer.js", HERE / "viewer.css",
             HERE / "viewer.template.html", HERE / "layout.js", HERE / "layout.template.html",
             HERE / "vendor" / "viewer-runtime.js"]
    paths.extend(sorted(ENGINE.glob("*.py")))
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("生产源码文件缺失，无法记录检查点来源：" + ", ".join(map(str, missing)))
    entries = [{"path": path.relative_to(HERE).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in paths]
    return {"sha256": _identity_digest(entries), "files": entries}


def geometry_source_identity():
    """Fingerprint geometry producers while excluding export/report/viewer implementation."""
    source_text = (HERE / "generate.py").read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    helper_names = ("load_stage05", "derived_geometry_values",
                    "update_resolved_geometry_derivations")
    helper_nodes = {node.name: node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                    node.name in helper_names}
    if set(helper_nodes) != set(helper_names):
        raise RuntimeError("几何派生helper缺失，无法建立几何源码身份")
    entries = []
    for path in sorted(ENGINE.glob("*.py")):
        entries.append({"path": path.relative_to(HERE).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for name in helper_names:
        segment = ast.get_source_segment(source_text, helper_nodes[name]) or ""
        entries.append({"path": f"generate.py::{name}",
                        "sha256": hashlib.sha256(segment.encode("utf-8")).hexdigest()})
    pipeline = _geometry_pipeline_source(source_text)
    entries.append({"path": "generate.py::stage05_geometry_pipeline",
                    "sha256": hashlib.sha256(pipeline.encode("utf-8")).hexdigest()})
    entries.sort(key=lambda entry: entry["path"])
    return {"sha256": _identity_digest(entries), "files": entries}


def geometry_config_identity(config):
    return {"corner_angle_deg": config["corner_angle_deg"],
            "parameters": config["parameters"],
            # These tessellation settings feed the stage04 board/likou surface
            # sampler and stage05 combination-top sampler, so they are inputs
            # to the BRep geometry, not only final display-mesh settings.
            "advanced": config["advanced"],
            # Recovery recomputes these from the current normalized inputs and
            # geometry source before searching for a checkpoint.
            "derived": config["derived"]}


def json_dump(data, path: Path) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")


def checked_generation_mode(mode):
    if mode not in ("preview", "exact"):
        raise ValueError("生成模式必须是 preview 或 exact")
    return mode


def write_resolved_mode(config, output_path, mode, source_identity=None):
    write_resolved(config, output_path)
    resolved_path = Path(output_path)
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["mode"] = checked_generation_mode(mode)
    if source_identity is not None:
        resolved["source_identity"] = source_identity
    json_dump(resolved, resolved_path)


DISPLAY_ONLY_SOURCE_FILES = frozenset({
    "render_delivery.py", "viewer.js", "viewer.css", "viewer.template.html",
    "layout.js", "layout.template.html", "vendor/viewer-runtime.js",
})


def current_source_identity_record():
    production = production_source_identity()
    geometry = geometry_source_identity()
    return {"production": production, "geometry": geometry}


def _verified_identity_section(section, label):
    if not isinstance(section, dict):
        raise ValueError(f"保存的{label}source identity缺失")
    digest, files = section.get("sha256"), section.get("files")
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(ch not in "0123456789abcdef" for ch in digest) or
            not isinstance(files, list) or not files):
        raise ValueError(f"保存的{label}source identity格式无效")
    normalized = []
    seen = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError(f"保存的{label}source identity含无效文件记录")
        path, file_hash = entry.get("path"), entry.get("sha256")
        if (not isinstance(path, str) or not path or path in seen or
                not isinstance(file_hash, str) or len(file_hash) != 64 or
                any(ch not in "0123456789abcdef" for ch in file_hash)):
            raise ValueError(f"保存的{label}source identity含无效路径或哈希")
        seen.add(path)
        normalized.append({"path": path, "sha256": file_hash})
    if _identity_digest(normalized) != digest:
        raise ValueError(f"保存的{label}source identity摘要不匹配其文件清单")
    return {"sha256": digest, "files": normalized}


def _verified_source_identity(record):
    if not isinstance(record, dict):
        raise ValueError("输出没有保存的source identity，不能补写当前源码身份")
    return {"production": _verified_identity_section(record.get("production"), "production "),
            "geometry": _verified_identity_section(record.get("geometry"), "geometry ")}


def _verify_source_identity_chain(documents, current_identity):
    saved = [_verified_source_identity(document.get("source_identity"))
             for document in documents if document is not None]
    if not saved:
        raise ValueError("输出没有可信source identity，拒绝finalize-existing")
    identity = saved[0]
    if any(item != identity for item in saved[1:]):
        raise ValueError("resolved、mesh、validation或manifest中的source identity不一致")
    if identity["geometry"]["sha256"] != current_identity["geometry"]["sha256"]:
        raise ValueError("输出geometry source identity与当前几何源码不一致，拒绝finalize-existing")
    if identity["production"]["sha256"] != current_identity["production"]["sha256"]:
        old_files = {item["path"]: item["sha256"] for item in identity["production"]["files"]}
        new_files = {item["path"]: item["sha256"] for item in current_identity["production"]["files"]}
        changed = {path for path in old_files.keys() | new_files.keys()
                   if old_files.get(path) != new_files.get(path)}
        if not changed or not changed.issubset(DISPLAY_ONLY_SOURCE_FILES):
            raise ValueError("输出production source与当前源码差异不局限于展示文件，拒绝finalize-existing")
    return identity


def verify_saved_manifest(output_dir, manifest, case_id, mode):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("已有manifest.json格式无效，拒绝finalize-existing")
    if manifest.get("case_id") != case_id or manifest.get("mode", "exact") != mode:
        raise ValueError("已有manifest案例或生成模式不匹配，拒绝finalize-existing")
    if (not manifest["files"] or
            manifest.get("file_count") != len(manifest["files"])):
        raise ValueError("已有manifest文件计数不匹配，拒绝finalize-existing")
    root = Path(output_dir).resolve()
    seen = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("已有manifest含无效文件记录")
        name, expected_hash = entry.get("path"), entry.get("sha256")
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("已有manifest含无效相对路径")
        rel = PurePosixPath(name)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts) or name in seen:
            raise ValueError("已有manifest含越界或重复相对路径")
        seen.add(name)
        target = root.joinpath(*rel.parts).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("已有manifest路径解析到输出目录之外") from exc
        if (not target.is_file() or entry.get("bytes") != target.stat().st_size or
                not isinstance(expected_hash, str) or sha256(target) != expected_hash):
            raise ValueError(f"已有manifest文件内容或哈希不匹配：{name}")
    return manifest


def load_stage05():
    spec = importlib.util.spec_from_file_location("parametric_stage05_core", ENGINE / "stage05_core.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 engine/stage05_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def derived_geometry_values(config, stage05):
    """Return lightweight geometric derivations without building any BReps."""
    case = stage05.s2.load_case(config)
    stage05.s2.setup_fan_surface(case)
    arc = case["fan_surface"]["arc_info"]
    p = config["parameters"]
    root_t = min(1.0, max(0.0, math.dist(case["old_start"], case["old_root"]) /
                             case["old_length_plan"]))
    ridge_taper = 1.0 - (1.0 - float(p["old_tail_scale"])) * stage05.s2.smooth(root_t)
    root_ridge = float(case["old_ridge_height_root"]) * ridge_taper * float(p["young_root_scale"])
    tip_ridge = root_ridge * float(p["young_tip_scale"])
    young_sagitta = min(root_ridge, tip_ridge) * 0.75
    return case, arc, float(young_sagitta)


def update_resolved_geometry_derivations(config, arc, young_sagitta, fan_division_count_n):
    n = int(fan_division_count_n)
    if n < 3 or n % 2 == 0:
        raise ValueError(f"摔网弧等分区间数n必须是大于等于3的奇数；当前n={n}")
    config["derived"]["fan_division_count_n"] = n
    config["derived"]["fan_count"] = n - 1
    config["derived"]["fan_spacing_mm"] = float(arc["arc_length_mm"] / n)
    config["derived"]["fan_spacing_rule"] = (
        "n为满足弧长/n < B的最小奇数等分区间数；摔网取内部i/n分点，弧端边界椽归正身")
    config["derived"]["fan_plan_sagitta_mm"] = float(arc["derived_sagitta_mm"])
    config["derived"]["fan_plan_sagitta_reference_mm"] = float(arc["nominal_f_plan_reference_mm"])
    config["derived"]["young_sagitta_mm"] = float(young_sagitta)


def signed_area(points):
    return 0.5 * sum(points[i][0] * points[(i + 1) % len(points)][1]
                     - points[i][1] * points[(i + 1) % len(points)][0]
                     for i in range(len(points)))


def cross2(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def line_intersection(p1, p2, p3, p4):
    a = p2 - p1
    b = p4 - p3
    den = cross2(a, b)
    scale = max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1.0)
    if abs(den) <= 1e-12 * scale:
        raise ValueError("立脚飞椽中心截面梯形化时，底边和延长边平行或数值退化")
    return p1 + (cross2(p3 - p1, b) / den) * a


def center_fiber(records, field, label):
    found = [r for r in records if abs(float(r.get("v_mm", math.inf))) <= 1e-9]
    if len(found) != 1:
        raise ValueError(f"{label}中心截面需恰有一个v=0样点，实际{len(found)}个")
    return found[0]


def make_profiles(fly_records):
    fan_records = sorted((r for r in fly_records if r["family"] == "fan"), key=lambda r: int(r["index"]))
    if len(fan_records) != len({int(r["index"]) for r in fan_records}) or not fan_records:
        raise ValueError("动态立脚飞椽记录为空或编号重复")
    profiles = []
    for rec in fan_records:
        index = int(rec["index"])
        front = center_fiber(rec["I_front_edge_fiber_records"], "v_mm", f"F{index:02d}.I")
        root = center_fiber(rec["root_fiber_records"], "v_mm", f"F{index:02d}.R")
        qi = float(front["q_front_mm"])
        hi = float(front["H_front_mm"])
        qr = float(root["q_root_mm"])
        length = float(rec["l_SE_mm"])
        width = float(rec["width_mm"])
        e_bottom_z = float(rec["E_bottom_center_mm"])
        e_depth = float(rec["E_depth_world_Z_mm"])
        d_i = float(rec["dI_world_Z_mm"])
        i_bottom = np.array([qi, hi], dtype=float)
        e_bottom = np.array([length, e_bottom_z], dtype=float)
        e_top = np.array([length, e_bottom_z + e_depth], dtype=float)
        i_top_source = np.array([qi, hi + d_i], dtype=float)

        source_top_slope = (e_top[1] - i_top_source[1]) / (e_top[0] - i_top_source[0])
        r_source = np.array([qr, i_top_source[1] + source_top_slope * (qr - qi)], dtype=float)
        bottom_slope = (e_bottom[1] - i_bottom[1]) / (e_bottom[0] - i_bottom[0])
        i_top_new = np.array([qi, e_top[1] + bottom_slope * (qi - length)], dtype=float)
        r_new = line_intersection(i_bottom, r_source, i_top_new, e_top)

        def at_q(a, b, q):
            if abs(float(b[0] - a[0])) <= 1e-12:
                raise ValueError(f"{rec['family']} {index}: centerline edge has zero longitudinal span")
            return float(a[1] + (b[1] - a[1]) * (q - a[0]) / (b[0] - a[0]))

        s_bottom = np.array([0.0, at_q(i_bottom, e_bottom, 0.0)])
        s_top_source = np.array([0.0, at_q(i_top_source, e_top, 0.0)])
        s_top_new = np.array([0.0, at_q(i_top_new, e_top, 0.0)])
        source_points = {"R": r_source.tolist(), "I_bottom": i_bottom.tolist(),
                         "I_top": i_top_source.tolist(), "S_bottom": s_bottom.tolist(),
                         "S_top": s_top_source.tolist(), "E_bottom": e_bottom.tolist(),
                         "E_top": e_top.tolist()}
        points = {"R": r_new.tolist(), "I_bottom": i_bottom.tolist(),
                  "I_top": i_top_new.tolist(), "S_bottom": s_bottom.tolist(),
                  "S_top": s_top_new.tolist(), "E_bottom": e_bottom.tolist(),
                  "E_top": e_top.tolist()}
        source_polygon = [source_points[k] for k in ("R", "I_bottom", "E_bottom", "E_top")]
        polygon = [points[k] for k in ("R", "I_bottom", "E_bottom", "E_top")]
        area = abs(signed_area(polygon))
        source_area = abs(signed_area(source_polygon))
        if not math.isfinite(area) or area <= 0.0:
            raise ValueError(f"{rec['family']} {index}: 梯形面积不是有限正值 ({area})")
        delta_r = r_new - r_source
        profile = {
            "id": f"F{index:02d}", "index": index, "width_mm": width,
            "profile_basis": "centerline_approximation",
            "area_mm2": area, "polygon_qz_mm": polygon, "points_qz_mm": points,
            "source_polygon_qz_mm": source_polygon, "source_points_qz_mm": source_points,
            "model_ids": [f"flying_fan_x_{index:02d}", f"flying_fan_y_{index:02d}"],
            "adjustments": {"I_top_delta_z_mm": float(i_top_new[1] - i_top_source[1]),
                            "S_top_delta_z_mm": float(s_top_new[1] - s_top_source[1]),
                            "R_delta_q_mm": float(delta_r[0]), "R_delta_z_mm": float(delta_r[1]),
                            "R_displacement_mm": float(np.linalg.norm(delta_r)),
                            "area_delta_mm2": float(area - source_area)},
            "source_values": {"q_front_mm": qi, "H_front_mm": hi, "q_root_mm": qr,
                              "q_R_center_mm": float(rec["q_R_center_mm"]), "l_SE_mm": length,
                              "width_mm": width, "dI_world_Z_mm": d_i,
                              "E_bottom_center_mm": e_bottom_z, "E_depth_world_Z_mm": e_depth,
                                  "side_contours_A_B_are_not_used_for_layout": True,
                              "fiber_v_mm": float(front["v_mm"]),
                              "root_fiber_v_mm": float(root["v_mm"])},
        }
        profiles.append(profile)
    return profiles


def fit_natural_cubic(xs, ys, labels):
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if len(xs) != len(ys) or len(xs) < 2 or np.any(np.diff(xs) <= 0.0):
        raise ValueError("自然三次样条需要至少两个严格递增的节点")
    spline = CubicSpline(xs, ys, bc_type="natural", extrapolate=True)
    node_error = np.asarray(spline(xs), dtype=float) - ys
    entry = {
        "type": "natural_cubic_spline_C2",
        "knots_x_mm": xs.tolist(), "knots_y_mm": ys.tolist(),
        "segments": [{"x0_mm": float(xs[i]), "x1_mm": float(xs[i + 1]),
                      "coeffs_descending_in_dx_mm": [float(v) for v in spline.c[:, i]]}
                     for i in range(len(xs) - 1)],
        "errors": {"max_abs_mm": float(np.max(np.abs(node_error))),
                   "rms_mm": float(np.sqrt(np.mean(node_error ** 2))),
                   "max_error_signed_mm": float(node_error[int(np.argmax(np.abs(node_error)))])},
        "node_ids": list(labels),
        "extrapolation": "端段三次多项式延长；允许端段延长，拟合误差仅报告",
    }
    return spline, entry


def best_translation(d_spline, x_d, x_c, y_c, board_width):
    x_d = np.asarray(x_d, dtype=float)
    x_c = np.asarray(x_c, dtype=float)
    y_c = np.asarray(y_c, dtype=float)
    low, high = -float(board_width), 2.0 * float(board_width)

    def at(dx):
        r0 = y_c - np.asarray(d_spline(x_c - float(dx)), dtype=float)
        dy = float((np.max(r0) + np.min(r0)) / 2.0)
        errors = dy - r0
        return {"dx_mm": float(dx), "dy_mm": dy,
                "max_abs_error_mm": float(np.max(np.abs(errors))),
                "rms_error_mm": float(np.sqrt(np.mean(errors ** 2))),
                "error_mm_by_root": errors.tolist(),
                "residual0_C_minus_D_mm_by_root": r0.tolist()}

    grid = np.linspace(low, high, 1201)
    scores = np.empty(len(grid), dtype=float)
    for i, dx in enumerate(grid):
        scores[i] = at(float(dx))["max_abs_error_mm"]
    local = np.flatnonzero((scores[1:-1] <= scores[:-2]) & (scores[1:-1] <= scores[2:])) + 1
    candidates = [(float(scores[0]), float(grid[0])), (float(scores[-1]), float(grid[-1]))]
    for idx in local:
        refined = minimize_scalar(lambda dx: at(dx)["max_abs_error_mm"],
                                  bounds=(float(grid[idx - 1]), float(grid[idx + 1])),
                                  method="bounded", options={"xatol": 1e-10, "maxiter": 300})
        candidates.append((float(refined.fun), float(refined.x)))
    kink_candidates = np.unique((x_c[:, None] - x_d[None, :]).ravel())
    for dx in kink_candidates[(kink_candidates >= low) & (kink_candidates <= high)]:
        candidates.append((at(float(dx))["max_abs_error_mm"], float(dx)))
    _, dx = min(candidates, key=lambda item: item[0])
    result = at(dx)
    query = x_c - result["dx_mm"]
    result.update({"method": "natural_cubic", "objective": "minimax C-point vertical y error",
                   "search_bounds_dx_mm": [low, high],
                   "extension_left_mm": float(max(0.0, x_d[0] - float(np.min(query)))),
                   "extension_right_mm": float(max(0.0, float(np.max(query)) - x_d[-1])),
                   "optimizer_grid_points": int(len(grid)),
                   "refined_local_minima_count": int(len(local)),
                   "translated_sample_kinks_considered": int(len(kink_candidates))})
    return result


def make_layout(profiles):
    local = []
    for p in profiles:
        pts = p["points_qz_mm"]
        a = np.asarray(pts["R"], dtype=float)
        b = np.asarray(pts["I_bottom"], dtype=float)
        c = np.asarray(pts["E_bottom"], dtype=float)
        d = np.asarray(pts["E_top"], dtype=float)
        u = c - b
        u_norm = float(np.linalg.norm(u))
        if u_norm <= 1e-10:
            raise ValueError(f"{p['id']}: 排料弦向轴长度为零")
        u /= u_norm
        ex = np.array([u[1], -u[0]], dtype=float)
        transformed = {}
        for key, point in (("A", a), ("B", b), ("C", c), ("D", d)):
            delta = point - a
            transformed[key] = [float(np.dot(delta, ex)), float(np.dot(delta, u))]
        width = transformed["B"][0]
        if width <= 0.0:
            raise ValueError(f"{p['id']}: 竖直长边排料宽度必须为正，实际{width:g}mm")
        local.append({"id": p["id"], "index": p["index"], "width_mm": width,
                      **transformed, "model_ids": p["model_ids"], "input_area_mm2": p["area_mm2"],
                      "axis_u_qz": u.tolist(), "axis_x_qz": ex.tolist(),
                      "alignment_residuals_mm": {"A_x": transformed["A"][0], "D_x": transformed["D"][0],
                                                 "B_x_minus_w": transformed["B"][0] - width,
                                                 "C_x_minus_w": transformed["C"][0] - width}})

    xlines = [0.0]
    for piece in local:
        xlines.append(xlines[-1] + piece["width_mm"])
    board_width = float(xlines[-1])
    xlines_arr = np.asarray(xlines, dtype=float)
    xs_for = {"b": xlines_arr[1:], "c": xlines_arr[1:], "d": xlines_arr[:-1]}
    ys_for = {"b": np.asarray([p["B"][1] for p in local]),
              "c": np.asarray([p["C"][1] for p in local]),
              "d": np.asarray([p["D"][1] for p in local])}
    labels = {key: [f"{p['id']}.{key.upper()}" for p in local] for key in "bcd"}
    fits, splines = {}, {}
    curve_nodes = {}
    for key in "bcd":
        splines[key], fit = fit_natural_cubic(xs_for[key], ys_for[key], labels[key])
        fits[key] = {"natural_cubic": fit}
        curve_nodes[key] = [{"id": labels[key][i], "x_mm": float(xs_for[key][i]),
                             "y_mm": float(ys_for[key][i])} for i in range(len(local))]

    pieces = []
    area_sum = 0.0
    for i, p in enumerate(local):
        x0, x1 = float(xlines[i]), float(xlines[i + 1])
        coords = {"A": [x0, 0.0], "B": [x1, float(p["B"][1])],
                  "C": [x1, float(p["C"][1])], "D": [x0, float(p["D"][1])]}
        area = abs(signed_area([coords[k] for k in "ABCD"]))
        if not math.isfinite(area) or area <= 0.0:
            raise ValueError(f"{p['id']}: 排料轮廓面积必须为有限正值，实际{area}")
        if coords["C"][1] <= coords["B"][1] or coords["D"][1] <= coords["A"][1]:
            raise ValueError(f"{p['id']}: 右侧C需高于B且左侧D需高于A")
        pieces.append({"id": p["id"], "index": p["index"], "x_left_mm": x0,
                       "x_right_mm": x1, "width_x_mm": p["width_mm"], **coords,
                       "area_mm2": area, "model_ids": p["model_ids"]})
        area_sum += area
    board_height = max(max(p["B"][1], p["C"][1], p["D"][1]) for p in local)
    if board_height <= 0.0:
        raise ValueError("排料板材高度必须为正")
    try:
        translation = best_translation(splines["d"], xs_for["d"], xs_for["c"], ys_for["c"], board_width)
        translation["available"] = True
    except Exception as exc:
        translation = {"available": False, "method": "natural_cubic", "dx_mm": None, "dy_mm": None,
                       "max_abs_error_mm": None, "rms_error_mm": None,
                       "error": f"{type(exc).__name__}: {exc}",
                       "reason": "translation fit is diagnostic-only; exact layout corners remain authoritative"}
    fits["d"]["natural_cubic"]["translation_to_c_minimax"] = translation
    retained_ratio = 100.0 * area_sum / (board_width * board_height)
    return {
        "schema": "tip_ground_bcd_fit_results_v1",
        "input": {"profile_field": "profiles[].points_qz_mm", "coordinate_units": "mm",
                  "profile_count": len(profiles), "mirror_sets": 2},
        "method": {
            "transform": "u=normalize(E_bottom-I_bottom); Xaxis=(u_z,-u_q); Yaxis=u; A=R is origin",
            "global_layout": "Ai=(x[i-1],0), Bi=(x[i],b(x[i])), Ci=(x[i],c(x[i])), Di=(x[i-1],d(x[i-1])); x[i]=sum(width[1..i])",
            "curve_domains": {"b": "x1..xn (right竖线)", "c": "x1..xn (right竖线)", "d": "x0..x(n-1) (left竖线)"},
            "interpolant": "自然三次C2样条；每段系数按dx=x-x0降幂排列；端段可延长",
            "outline_rule": "排料切割轮廓以每根实际梯形的A/B/C/D角点连接直线为准；样条只作定位与误差报告",
            "translation_rule": "c_hat(x)=d_nat(x-dx)+dy；在[-板宽,2*板宽]内作minimax搜索，误差为各原C竖线处的y差，不反馈三维几何",
            "saw_kerf_mm": 0.0,
        },
        "layout": {
            "board_width_x_mm": board_width, "board_height_y_mm": float(board_height),
            "board_thickness_mm": float(profiles[0]["width_mm"]),
            "blank_envelope_area_mm2": float(board_width * board_height),
            "blank_envelope_volume_mm3": float(board_width * board_height * profiles[0]["width_mm"]),
            "x_verticals_mm": [float(x) for x in xlines],
            "retained_profile_area_mm2": float(area_sum),
            "retained_volume_mm3": float(area_sum * profiles[0]["width_mm"]),
            "retain_ratio_percent": float(retained_ratio),
            "reference_profile_area_mm2_from_input": float(sum(p["area_mm2"] for p in profiles)),
            "area_preservation_delta_vs_input_mm2": float(area_sum - sum(p["area_mm2"] for p in profiles)),
            "pieces": pieces, "source_profiles_local": local,
        },
        "curve_nodes": curve_nodes,
        "fits": fits,
        "outline_methods": {"natural_cubic": {"all_piece_areas_positive": True,
                                                 "piece_count": len(pieces),
                                                 "curve_fit_errors_are_diagnostic_only": True}},
        "translation": translation,
    }


def checkpoint_fly_records(fly_records):
    """Keep only the exact center-fiber and report fields needed after mesh export."""
    fields = ("family", "index", "role", "is_boundary", "profile_basis", "source_rafter_id", "tail_length_from_S_center_mm",
              "l_SE_mm", "width_mm", "E_bottom_center_mm", "E_depth_world_Z_mm",
              "dI_world_Z_mm", "q_R_center_mm", "side_contours",
              "dI_A_minus_B_mm", "E_depth_A_minus_B_mm")
    packed = []
    for record in fly_records:
        if record.get("family") != "fan":
            continue
        item = {key: record[key] for key in fields if key in record}
        for field, value_key, extra_keys in (
                ("I_front_edge_fiber_records", None, ("v_mm", "q_front_mm", "H_front_mm")),
                ("root_fiber_records", None, ("v_mm", "q_root_mm"))):
            center = [sample for sample in record.get(field, [])
                      if abs(float(sample.get("v_mm", math.inf))) <= 1e-9]
            if len(center) != 1:
                raise ValueError(f"几何检查点无法保存{record.get('source_rafter_id')}的{field}中心纤维")
            item[field] = [{key: float(center[0][key]) for key in extra_keys}]
        packed.append(item)
    return packed


def save_geometry_checkpoint(parts, control, config, fly_records, notch_records,
                             board_records, combination_info, ctx, phase_timings,
                             runtime_seconds_before_checkpoint, output_dir, *, mode="exact"):
    """Persist the completed geometry only as a same-job mesh-failure recovery point."""
    from OCP.BRepTools import BRepTools

    checkpoint_dir = output_dir / "geometry_checkpoint"
    temporary_dir = output_dir / ".geometry_checkpoint.tmp"
    checkpoint_started = time.perf_counter()
    if checkpoint_dir.exists():
        raise ValueError(f"几何检查点已存在且未被本次运行恢复：{checkpoint_dir}")
    if temporary_dir.exists():
        shutil.rmtree(temporary_dir)
    temporary_dir.mkdir(parents=True)
    part_entries = []
    try:
        if not parts:
            raise ValueError("几何检查点没有可保存的构件")
        seen_ids = set()
        for index, part in enumerate(parts):
            part_id = part.get("id")
            if not isinstance(part_id, str) or not part_id or part_id in seen_ids:
                raise ValueError(f"几何检查点构件ID缺失或重复：{part_id!r}")
            seen_ids.add(part_id)
            if not isinstance(part.get("name"), str) or not isinstance(part.get("group"), str):
                raise ValueError(f"几何检查点构件{part_id}缺少name/group元数据")
            shape = part.get("shape")
            if shape is None:
                raise ValueError(f"几何检查点构件{part_id}缺少BRep shape")
            if shape.isNull():
                raise ValueError(f"几何检查点构件{part_id}是空BRep")
            volume = float(shape.Volume())
            filename = f"part-{index:04d}.brep"
            path = temporary_dir / filename
            if not BRepTools.Write_s(shape.wrapped, str(path)) or not path.is_file():
                raise ValueError(f"无法写入构件{part_id}的几何检查点BRep")
            part_entries.append({"id": part_id, "name": part["name"], "group": part["group"],
                                 "volume_mm3": volume, "brep": filename, "sha256": sha256(path)})
        context = {
            "case": {"fan_geometry": ctx["case"]["fan_geometry"],
                     "eave_rafters_geometry": ctx["case"]["eave_rafters_geometry"]},
            "upper_board_domain": ctx.get("upper_board_domain"),
            "selection_rule": ctx["selection_rule"],
            "likou_notch_performance": ctx.get("likou_notch_performance"),
        }
        document = {
            "schema": "v02_mesh_failure_geometry_checkpoint_v2",
            "state": "complete_geometry_before_mesh_export",
            "mode": mode,
            "created_unix_seconds": time.time(),
            "config_identity": geometry_config_identity(config),
            "config_identity_sha256": canonical_json_sha256(geometry_config_identity(config)),
            "checkpoint_case_id": config["case_id"],
            "resolved_config": config,
            "production_source_identity": production_source_identity(),
            "geometry_source_identity": geometry_source_identity(),
            "post_geometry_artifact_policy": (
                "profiles.json and layout.json are regenerated from saved center-fiber records "
                "with the current make_profiles/make_layout implementations and current case config"),
            "geometry_runtime_seconds_before_checkpoint": float(runtime_seconds_before_checkpoint),
            "geometry_phase_timings_seconds": phase_timings,
            "parts": part_entries,
            "control": control,
            "fly_records": checkpoint_fly_records(fly_records),
            "notch_records": notch_records,
            "board_records": board_records,
            "combination_info": combination_info,
            "context": context,
        }
        document["geometry_phase_timings_seconds"] = {
            **phase_timings,
            "geometry_checkpoint_write": float(time.perf_counter() - checkpoint_started),
        }
        json_dump(document, temporary_dir / "checkpoint.json")
        temporary_dir.replace(checkpoint_dir)
    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        raise
    return checkpoint_dir, float(time.perf_counter() - checkpoint_started)


def restore_geometry_checkpoint(checkpoint_path, config, *, mode="exact"):
    """Load a checkpoint only when both normalized inputs and geometry sources match."""
    import cadquery as cq
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    checkpoint_path = Path(checkpoint_path)
    document_path = checkpoint_path / "checkpoint.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    if document.get("schema") != "v02_mesh_failure_geometry_checkpoint_v2":
        raise ValueError(f"不支持的几何检查点schema：{document.get('schema')}")
    if document.get("state") != "complete_geometry_before_mesh_export":
        raise ValueError("几何检查点尚未完成，拒绝恢复")
    if document.get("mode", "exact") != mode:
        raise ValueError("几何检查点生成模式与本次请求不一致，拒绝恢复")
    if document.get("config_identity") != geometry_config_identity(config):
        raise ValueError("几何检查点参数或重算后的derived与本次配置不一致，拒绝恢复")
    checkpoint_config = document.get("resolved_config")
    checkpoint_case_id = document.get("checkpoint_case_id")
    if (not isinstance(checkpoint_config, dict) or
            checkpoint_case_id != checkpoint_config.get("case_id")):
        raise ValueError("几何检查点原始case_id与resolved_config不一致，拒绝恢复")
    current_sources = geometry_source_identity()
    if document.get("geometry_source_identity") != current_sources:
        raise ValueError("几何检查点源码指纹与当前生成器不一致，拒绝恢复")
    checkpoint_production_sources = document.get("production_source_identity")
    if (not isinstance(checkpoint_production_sources, dict) or
            not isinstance(checkpoint_production_sources.get("sha256"), str)):
        raise ValueError("几何检查点缺少完整生产源码指纹，拒绝恢复")
    part_entries = document.get("parts")
    if not isinstance(part_entries, list) or not part_entries:
        raise ValueError("几何检查点没有构件清单")
    parts = []
    seen_ids = set()
    for entry in part_entries:
        part_id = entry.get("id")
        if (not isinstance(part_id, str) or not part_id or part_id in seen_ids or
                not isinstance(entry.get("name"), str) or not isinstance(entry.get("group"), str)):
            raise ValueError(f"几何检查点构件清单包含缺失或重复元数据：{part_id!r}")
        seen_ids.add(part_id)
        filename = entry.get("brep")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("几何检查点包含不安全的BRep路径")
        brep_path = checkpoint_path / filename
        if not brep_path.is_file() or sha256(brep_path) != entry.get("sha256"):
            raise ValueError(f"几何检查点BRep缺失或哈希错误：{filename}")
        restored = TopoDS_Shape()
        if not BRepTools.Read_s(restored, str(brep_path), BRep_Builder()) or restored.IsNull():
            raise ValueError(f"几何检查点BRep无法读取：{filename}")
        shape = cq.Shape.cast(restored)
        volume = float(shape.Volume())
        expected_volume = entry.get("volume_mm3")
        if (isinstance(expected_volume, bool) or not isinstance(expected_volume, (int, float)) or
                not math.isfinite(float(expected_volume)) or not math.isfinite(volume) or
                not math.isclose(volume, float(expected_volume), rel_tol=1e-10, abs_tol=1e-7)):
            raise ValueError(f"几何检查点构件{part_id}的恢复体积与清单不一致")
        parts.append({"id": part_id, "name": entry["name"], "group": entry["group"],
                      "shape": shape})
    if len(parts) != len(part_entries):
        raise ValueError("几何检查点构件恢复数量不一致")
    return {"config": config, "parts": parts,
            "control": document["control"], "fly_records": document["fly_records"],
            "notch_records": document["notch_records"], "board_records": document["board_records"],
            "combination_info": document["combination_info"], "context": document["context"],
            "checkpoint_case_id": checkpoint_case_id,
            "production_source_identity": checkpoint_production_sources,
            "geometry_source_identity": document["geometry_source_identity"],
            "recovery_production_source_identity": production_source_identity(),
            "recovery_geometry_source_identity": current_sources,
            "config_identity_sha256": document.get(
                "config_identity_sha256", canonical_json_sha256(document["config_identity"])),
            "geometry_phase_timings_seconds": document.get("geometry_phase_timings_seconds", {}),
            "geometry_runtime_seconds_before_checkpoint": document.get(
                "geometry_runtime_seconds_before_checkpoint"),
            "checkpoint_path": str(checkpoint_path.resolve())}


def find_geometry_checkpoint(config, output_dir, *, mode="exact"):
    """Find a completed mesh-failure checkpoint with identical geometry inputs/sources."""
    output_dir = Path(output_dir).resolve()
    workbench_dir = HERE / "outputs" / "workbench"
    candidates = []
    local = output_dir / "geometry_checkpoint" / "checkpoint.json"
    if local.is_file():
        candidates.append(local)
    if workbench_dir.is_dir():
        candidates.extend(path for path in workbench_dir.glob("case-*/geometry_checkpoint/checkpoint.json")
                          if path.resolve() != local.resolve())
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    expected_config = geometry_config_identity(config)
    expected_sources = geometry_source_identity()
    for document_path in candidates:
        try:
            document = json.loads(document_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"generator: ignoring unreadable mesh checkpoint {document_path}: {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        if (document.get("schema") != "v02_mesh_failure_geometry_checkpoint_v2" or
                document.get("state") != "complete_geometry_before_mesh_export" or
                document.get("mode", "exact") != mode or
                document.get("config_identity") != expected_config):
            continue
        if document.get("geometry_source_identity") != expected_sources:
            print(f"generator: matching-config mesh checkpoint has a different geometry source; rebuilding CAD",
                  flush=True)
            continue
        return restore_geometry_checkpoint(document_path.parent, config, mode=mode)
    return None


def absolute_face_mesh(shape, tolerance, angular, requested_upper_limit=None,
                       *, clean_before_mesh=True):
    """Mesh in absolute mode; reject known failures before copying mesh arrays."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_Orientation
    from OCP.TopLoc import TopLoc_Location

    request_limit = float(tolerance if requested_upper_limit is None else requested_upper_limit)
    if clean_before_mesh:
        BRepTools.Clean_s(shape.wrapped)
    mesher = BRepMesh_IncrementalMesh(shape.wrapped, tolerance, False, angular)
    try:
        target_guard_passed = bool(BRepTools.Triangulation_s(shape.wrapped, tolerance))
        target_guard_error = None
    except Exception as exc:
        target_guard_passed = False
        target_guard_error = f"{type(exc).__name__}: {exc}"
    if math.isclose(request_limit, float(tolerance), rel_tol=0.0, abs_tol=0.0):
        request_guard_passed = target_guard_passed
        request_guard_error = target_guard_error
    else:
        try:
            request_guard_passed = bool(BRepTools.Triangulation_s(shape.wrapped, request_limit))
            request_guard_error = None
        except Exception as exc:
            request_guard_passed = False
            request_guard_error = f"{type(exc).__name__}: {exc}"

    faces = list(shape.Faces())
    quick_face_rows = []
    quick_nodes = quick_triangles = 0
    quick_failure_reasons = []
    for face_index, face in enumerate(faces, start=1):
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face.wrapped, location)
        info = {"face_index": face_index, "triangulation_present": triangulation is not None,
                "node_count": 0, "triangle_count": 0, "deflection_mm": None,
                "within_linear_limit": False, "boundary_edge_count": len(face.Edges()),
                "boundary_edge_diagnostic": "not_needed_for_early_mesh_rejection"}
        if triangulation is None:
            quick_failure_reasons.append("one_or_more_faces_have_no_usable_triangulation")
        else:
            node_count = int(triangulation.NbNodes())
            triangle_count_face = int(triangulation.NbTriangles())
            deflection = float(triangulation.Deflection())
            within_limit = math.isfinite(deflection) and deflection <= request_limit + 1e-9
            info.update({"node_count": node_count, "triangle_count": triangle_count_face,
                         "deflection_mm": deflection, "within_linear_limit": within_limit})
            quick_nodes += node_count
            quick_triangles += triangle_count_face
            if node_count < 3 or triangle_count_face < 1:
                quick_failure_reasons.append("one_or_more_faces_have_no_positive_triangle_count")
            if not within_limit:
                quick_failure_reasons.append("one_or_more_faces_exceed_requested_linear_deflection")
        quick_face_rows.append(info)
    if not faces:
        quick_failure_reasons.append("shape_has_no_faces")
    # A false global guard can be explained by degenerate edges, so that case
    # still reaches the full edge classification below. Face completeness and
    # the explicit absolute upper limit, however, cannot be recovered by more
    # node extraction at this target.
    quick_failure_reasons = list(dict.fromkeys(quick_failure_reasons))
    if quick_failure_reasons:
        report = {"requested_linear_deflection_mm": request_limit,
                  "requested_linear_deflection_upper_limit_mm": request_limit,
                  "linear_deflection_interpretation": (
                      "OCCT triangulation.Deflection kernel report; not an independent distance measurement"),
                  "mesher_linear_deflection_target_mm": float(tolerance),
                  "angular_tolerance_rad": float(angular),
                  "mesher_is_done": bool(mesher.IsDone()),
                  "mesher_status_flags": int(mesher.GetStatusFlags()),
                  "triangulation_guard_passed": request_guard_passed,
                  "triangulation_guard_at_requested_upper_limit_passed": request_guard_passed,
                  "triangulation_guard_at_mesher_target_passed": target_guard_passed,
                  "triangulation_guard_error": request_guard_error,
                  "triangulation_guard_at_mesher_target_error": target_guard_error,
                  "face_count": len(faces),
                  "all_faces_complete": all(row["triangulation_present"] for row in quick_face_rows),
                  "all_faces_have_positive_triangles": all(
                      row["node_count"] >= 3 and row["triangle_count"] > 0 for row in quick_face_rows),
                  "all_faces_within_linear_limit": all(row["within_linear_limit"] for row in quick_face_rows),
                  "all_faces_within_mesher_target": None,
                  "all_triangle_indices_valid": None,
                  "triangle_index_validation_performed": False,
                  "face_node_count_total": quick_nodes,
                  "face_triangle_count_total": quick_triangles,
                  "extracted_vertex_count": 0, "extracted_triangle_count": 0,
                  "mesh_extraction_performed": False,
                  "mesh_ready": False, "rejection_reasons": quick_failure_reasons,
                  "missing_nondegenerate_edge_polygons": [],
                  "missing_degenerate_edge_polygons": [],
                  "missing_unclassified_edge_polygons": [],
                  "faces": quick_face_rows}
        return [], [], report, False

    vertex_rows, triangle_rows = [], []
    face_rows = []
    missing_nondegenerate, missing_degenerate, missing_unknown = [], [], []
    all_faces_complete = True
    all_faces_within_limit = True
    all_indices_valid = True
    for face_index, face in enumerate(faces, start=1):
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face.wrapped, location)
        face_info = {"face_index": face_index, "triangulation_present": triangulation is not None,
                     "node_count": 0, "triangle_count": 0, "deflection_mm": None,
                     "within_linear_limit": False, "boundary_edge_count": len(face.Edges()),
                     "boundary_edges_missing_polygon": []}
        if triangulation is None:
            all_faces_complete = False
            face_rows.append(face_info)
            continue
        node_count = int(triangulation.NbNodes())
        face_triangles = int(triangulation.NbTriangles())
        face_deflection = float(triangulation.Deflection())
        within_limit = math.isfinite(face_deflection) and face_deflection <= request_limit + 1e-9
        within_target = math.isfinite(face_deflection) and face_deflection <= tolerance + 1e-9
        face_info.update({"node_count": node_count, "triangle_count": face_triangles,
                          "deflection_mm": face_deflection,
                          "within_linear_limit": within_limit,
                          "within_mesher_target": within_target})
        if node_count < 3 or face_triangles < 1:
            all_faces_complete = False
        if not within_limit:
            all_faces_within_limit = False
        offset = len(vertex_rows)
        transform = location.Transformation()
        for node_index in range(1, node_count + 1):
            point = triangulation.Node(node_index).Transformed(transform)
            vertex_rows.append([float(point.X()), float(point.Y()), float(point.Z())])
        reversed_face = face.wrapped.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
        for triangle in triangulation.Triangles():
            indices = (int(triangle.Value(1)), int(triangle.Value(2)), int(triangle.Value(3)))
            if any(index < 1 or index > node_count for index in indices):
                all_indices_valid = False
                continue
            if reversed_face:
                indices = (indices[0], indices[2], indices[1])
            triangle_rows.append(tuple(offset + index - 1 for index in indices))
        for edge_index, edge in enumerate(face.Edges(), start=1):
            missing = False
            try:
                polygon = BRep_Tool.PolygonOnTriangulation_s(edge.wrapped, triangulation, location)
                missing = polygon is None or (hasattr(polygon, "IsNull") and polygon.IsNull())
            except Exception as exc:
                missing = True
                face_info.setdefault("boundary_edge_check_errors", []).append(
                    {"edge_index": edge_index, "error": f"{type(exc).__name__}: {exc}"})
            if not missing:
                continue
            try:
                degenerate = bool(BRep_Tool.Degenerated_s(edge.wrapped))
            except Exception:
                degenerate = None
            try:
                edge_length = float(edge.Length())
            except Exception:
                edge_length = None
            issue = {"edge_index": edge_index, "degenerate": degenerate,
                     "length_mm": edge_length}
            face_info["boundary_edges_missing_polygon"].append(issue)
            if degenerate is True:
                missing_degenerate.append({"face_index": face_index, **issue})
            elif degenerate is False:
                missing_nondegenerate.append({"face_index": face_index, **issue})
            else:
                missing_unknown.append({"face_index": face_index, **issue})
        face_rows.append(face_info)

    all_faces_have_positive_triangles = all(
        face["triangulation_present"] and face["node_count"] >= 3 and face["triangle_count"] > 0
        for face in face_rows)
    all_faces_within_target = all(face["within_mesher_target"] for face in face_rows)
    rejection_reasons = []
    if not face_rows:
        rejection_reasons.append("shape_has_no_faces")
    if not all_faces_complete:
        rejection_reasons.append("one_or_more_faces_have_no_usable_triangulation")
    if not all_faces_have_positive_triangles:
        rejection_reasons.append("one_or_more_faces_have_no_positive_triangle_count")
    if not all_faces_within_limit:
        rejection_reasons.append("one_or_more_faces_exceed_requested_linear_deflection")
    if not all_indices_valid:
        rejection_reasons.append("triangle_indices_out_of_range")
    if not triangle_rows:
        rejection_reasons.append("mesh_has_no_triangles")
    if missing_nondegenerate:
        rejection_reasons.append("nondegenerate_boundary_edge_polygon_missing")
    if missing_unknown:
        rejection_reasons.append("boundary_edge_degeneracy_unclassified")
    if not request_guard_passed and not missing_degenerate:
        rejection_reasons.append("triangulation_guard_false_without_degenerate_edge_explanation")
    accepted = (bool(face_rows) and all_faces_complete and all_faces_have_positive_triangles and
                all_faces_within_limit and all_indices_valid and bool(triangle_rows) and
                not missing_nondegenerate and not missing_unknown and
                (request_guard_passed or bool(missing_degenerate)))
    guard_false_explanation = None
    if not request_guard_passed:
        guard_false_explanation = (
            "every face has a usable triangle mesh within the requested upper deflection; all missing "
            "edge polygons are classified as degenerate"
            if (missing_degenerate and not missing_nondegenerate and not missing_unknown and
                all_faces_complete and all_faces_within_limit and all_indices_valid and
                bool(triangle_rows)) else
            "inspect per-face deflection and missing-edge classifications; acceptance is denied "
            "unless all non-edge mesh checks pass and every missing edge is degenerate")
    if not all_faces_within_target:
        guard_false_explanation = (
            (guard_false_explanation + "; " if guard_false_explanation else "") +
            "the internal mesher target was not met on every face, but the original requested "
            "upper limit remains the acceptance threshold")
    report = {"requested_linear_deflection_mm": request_limit,
              "requested_linear_deflection_upper_limit_mm": request_limit,
              "linear_deflection_interpretation": (
                  "OCCT triangulation.Deflection kernel report; not an independent distance measurement"),
              "mesher_linear_deflection_target_mm": float(tolerance),
              "angular_tolerance_rad": float(angular),
              "mesher_is_done": bool(mesher.IsDone()),
              "mesher_status_flags": int(mesher.GetStatusFlags()),
              "triangulation_guard_passed": request_guard_passed,
              "triangulation_guard_at_requested_upper_limit_passed": request_guard_passed,
              "triangulation_guard_at_mesher_target_passed": target_guard_passed,
              "triangulation_guard_error": request_guard_error,
              "triangulation_guard_at_mesher_target_error": target_guard_error,
              "face_count": len(face_rows),
              "all_faces_complete": all_faces_complete,
              "all_faces_have_positive_triangles": all_faces_have_positive_triangles,
              "all_faces_within_linear_limit": all_faces_within_limit,
              "all_faces_within_mesher_target": all_faces_within_target,
              "all_triangle_indices_valid": all_indices_valid,
              "triangle_index_validation_performed": True,
              "face_node_count_total": sum(int(face["node_count"]) for face in face_rows),
              "face_triangle_count_total": sum(int(face["triangle_count"]) for face in face_rows),
              "extracted_vertex_count": len(vertex_rows),
              "extracted_triangle_count": len(triangle_rows),
              "mesh_extraction_performed": True,
              "mesh_ready": bool(accepted),
              "rejection_reasons": rejection_reasons,
              "triangulation_guard_false_explanation": guard_false_explanation,
              "missing_nondegenerate_edge_polygons": missing_nondegenerate,
              "missing_degenerate_edge_polygons": missing_degenerate,
              "missing_unclassified_edge_polygons": missing_unknown,
              "faces": face_rows}
    return vertex_rows, triangle_rows, report, accepted


MESH_CACHE_SCHEMA = "absolute_face_mesh_cache_v1"
MESH_CACHE_ALGORITHM = "occt_absolute_face_mesh_two_phase_incremental_v2_cached_fine_start"


def _stable_json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _mesh_environment_identity():
    from importlib.metadata import PackageNotFoundError, version
    import OCP
    versions = {}
    for package in ("cadquery", "cadquery-ocp"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "unknown"
    return {"python": sys.version.split()[0], "packages": versions,
            "ocp_module_version": str(getattr(OCP, "__version__", "unknown"))}


def _mesh_cache_source_sha256():
    # This source file contains the meshing checks and OBJ triangle indexing.
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _shape_brep_sha256(shape, cache_dir):
    """Hash a cleaned BRep serialization; never use names or bounding boxes as identity."""
    from OCP.BRepTools import BRepTools
    from OCP.TopTools import TopTools_FormatVersion
    cache_dir.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix="brep-", suffix=".brep", dir=cache_dir)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        if not BRepTools.Write_s(shape.wrapped, str(temp_path), False, False,
                                 TopTools_FormatVersion.TopTools_FormatVersion_VERSION_1):
            raise OSError("OCCT could not serialize the BRep for mesh-cache identity")
        return sha256(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _mesh_cache_key(identity):
    return hashlib.sha256(_stable_json_bytes(identity)).hexdigest()


def _load_mesh_cache(path, cache_key, identity):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
        payload = document["payload"]
        if (document.get("schema") != MESH_CACHE_SCHEMA or
                document.get("cache_key") != cache_key or
                document.get("identity") != identity or
                document.get("payload_sha256") != hashlib.sha256(_stable_json_bytes(payload)).hexdigest()):
            return None
        vertices, triangles, report = payload["vertices"], payload["triangles"], payload["report"]
        if (not isinstance(vertices, list) or not vertices or
                not isinstance(triangles, list) or not triangles or
                not isinstance(report, dict) or report.get("mesh_ready") is not True or
                report.get("mesh_extraction_performed") is not True):
            return None
        if any(not isinstance(row, list) or len(row) != 3 or
               any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in row)
               for row in vertices):
            return None
        vertex_count = len(vertices)
        if any(not isinstance(row, list) or len(row) != 3 or
               any(type(index) is not int or index < 0 or index >= vertex_count for index in row)
               for row in triangles):
            return None
        if (report.get("triangle_index_validation_performed") is not True or
                report.get("all_triangle_indices_valid") is not True or
                report.get("all_faces_complete") is not True or
                report.get("all_faces_have_positive_triangles") is not True or
                report.get("all_faces_within_linear_limit") is not True or
                report.get("missing_nondegenerate_edge_polygons") != [] or
                report.get("missing_unclassified_edge_polygons") != [] or
                report.get("rejection_reasons") != []):
            return None
        faces = report.get("faces")
        if (not isinstance(faces, list) or not faces or
                len(faces) != report.get("face_count") or
                sum(int(face.get("node_count", -1)) for face in faces) != len(vertices) or
                sum(int(face.get("triangle_count", -1)) for face in faces) != len(triangles)):
            return None
        return vertices, [tuple(row) for row in triangles], report
    except (OSError, EOFError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _write_mesh_cache(path, cache_key, identity, vertices, triangles, report):
    payload = {"vertices": vertices,
               "triangles": [list(map(int, row)) for row in triangles],
               "report": report}
    document = {"schema": MESH_CACHE_SCHEMA, "cache_key": cache_key,
                "identity": identity,
                "payload_sha256": hashlib.sha256(_stable_json_bytes(payload)).hexdigest(),
                "payload": payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp.gz", dir=path.parent)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with gzip.open(temp_path, "wt", encoding="utf-8", compresslevel=6) as stream:
            json.dump(document, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def export_mesh(parts, control, config, output_dir, *, mode="exact", mesh_cache_dir=None):
    mesh_parts, obj_lines = [], ["# Parametric timber construction; units mm"]
    obj_offset = 1
    triangle_count = 0
    tolerance = float(config["advanced"]["mesh_tessellation_mm"])
    angular = float(config["advanced"]["mesh_angular_tolerance_rad"])
    remesh_seconds = 0.0
    cache_dir = (Path(mesh_cache_dir) if mesh_cache_dir is not None else HERE / "outputs" / ".mesh_cache")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_available = True
    except OSError:
        cache_available = False
    source_sha = _mesh_cache_source_sha256()
    environment = _mesh_environment_identity()
    cache_hits = cache_misses = cache_invalid = cache_writes = cache_unavailable = 0
    # Mesher targets may need to be smaller than the requested upper bound on
    # strongly trimmed/ruled faces. Keep retries bounded and acceptance fixed;
    # a B400 lower-board BRep required 1/32 of its 2 mm limit to meet it.
    retry_targets = [tolerance / factor for factor in (1, 2, 4, 8, 16, 32)]
    diagnostics = {"schema": "absolute_mesh_export_diagnostics_v3",
                   "case_id": config["case_id"], "mode": mode,
                   "requested_linear_deflection_mm": tolerance,
                   "requested_linear_deflection_upper_limit_mm": tolerance,
                   "mesher_attempt_targets_mm": retry_targets,
                   "angular_tolerance_rad": angular,
                   "retry_tolerances_mm": retry_targets,
                   "mesh_cache": {"schema": MESH_CACHE_SCHEMA, "algorithm": MESH_CACHE_ALGORITHM,
                                  "source_sha256": source_sha, "environment": environment,
                                  "directory": str(cache_dir.resolve()),
                                  "cache_available": cache_available},
                   "parts": []}
    effective_targets = {}
    for part in parts:
        shape = part["shape"]
        mesh_started = time.perf_counter()
        part_report = {"id": part["id"], "name": part["name"], "group": part["group"],
                       "attempts": [], "accepted_tolerance_mm": None,
                       "brep_geometry_sha256": None, "mesh_cache": "unavailable",
                       "mesh_cache_candidate_scan_count": 0,
                       "mesh_cache_start_tolerance_mm": None,
                       "skipped_coarser_retry_tolerances_mm": [],
                       "invalid_mesh_cache_candidate_tolerances_mm": []}
        chosen_vertices = chosen_triangles = None
        last_report = None
        brep_sha = None
        cleaned_once = False
        try:
            from OCP.BRepTools import BRepTools
            # One explicit clean per part. OCCT receives the existing mesh
            # on progressively finer retries and incrementally refines it.
            BRepTools.Clean_s(shape.wrapped)
            cleaned_once = True
        except Exception as exc:
            part_report["mesh_clean_error"] = f"{type(exc).__name__}: {exc}"
        if cache_available and cleaned_once:
            try:
                brep_sha = _shape_brep_sha256(shape, cache_dir)
                part_report["brep_geometry_sha256"] = brep_sha
                part_report["mesh_cache"] = "miss"
            except Exception as exc:
                cache_unavailable += 1
                part_report["mesh_cache"] = "identity_unavailable"
                part_report["mesh_cache_error"] = f"{type(exc).__name__}: {exc}"
        else:
            cache_unavailable += 1

        def cache_identity_for(target):
            return {"brep_sha256": brep_sha,
                    "requested_linear_deflection_mm": tolerance,
                    "mesher_target_mm": float(target),
                    "angular_tolerance_rad": angular,
                    "algorithm": MESH_CACHE_ALGORITHM,
                    "algorithm_source_sha256": source_sha, "environment": environment}

        invalid_cache_paths = set()
        cached_start = None
        if brep_sha is not None:
            # A successful finer mesh is sufficient for this unchanged BRep
            # and request. Find the coarsest previously validated target before
            # asking OCCT to repeat known-failing coarser attempts.
            for target_index, candidate_tolerance in enumerate(diagnostics["retry_tolerances_mm"]):
                candidate_identity = cache_identity_for(candidate_tolerance)
                candidate_key = _mesh_cache_key(candidate_identity)
                candidate_path = cache_dir / f"{candidate_key}.json.gz"
                if not candidate_path.is_file():
                    continue
                part_report["mesh_cache_candidate_scan_count"] += 1
                candidate_mesh = _load_mesh_cache(candidate_path, candidate_key,
                                                   candidate_identity)
                if candidate_mesh is None:
                    invalid_cache_paths.add(candidate_path)
                    part_report["invalid_mesh_cache_candidate_tolerances_mm"].append(
                        float(candidate_tolerance))
                    cache_invalid += 1
                    continue
                cached_start = (target_index, float(candidate_tolerance), candidate_key,
                                candidate_identity, candidate_path, candidate_mesh)
                break

        if cached_start is not None:
            start_index = cached_start[0]
            part_report["mesh_cache_start_tolerance_mm"] = cached_start[1]
            part_report["skipped_coarser_retry_tolerances_mm"] = [
                float(target) for target in diagnostics["retry_tolerances_mm"][:start_index]]
        else:
            start_index = 0

        for target_index, attempt_tolerance in enumerate(
                diagnostics["retry_tolerances_mm"][start_index:], start=start_index):
            identity = {"brep_sha256": brep_sha, "requested_linear_deflection_mm": tolerance,
                        "mesher_target_mm": float(attempt_tolerance),
                        "angular_tolerance_rad": angular,
                        "algorithm": MESH_CACHE_ALGORITHM,
                        "algorithm_source_sha256": source_sha, "environment": environment}
            cache_key = _mesh_cache_key(identity) if brep_sha else None
            cache_path = cache_dir / f"{cache_key}.json.gz" if cache_key else None
            if cached_start is not None and target_index == cached_start[0]:
                cached = cached_start[5]
            else:
                cached = _load_mesh_cache(cache_path, cache_key, identity) if cache_path else None
            if cached is not None:
                vertices, triangles, report = cached
                report = {**report, "mesh_cache_hit": True}
                accepted = True
                cache_hits += 1
                part_report["mesh_cache"] = "hit"
            else:
                if cache_path and cache_path.exists():
                    if cache_path not in invalid_cache_paths:
                        cache_invalid += 1
                elif cache_path:
                    cache_misses += 1
                mesh_started = time.perf_counter()
                try:
                    vertices, triangles, report, accepted = absolute_face_mesh(
                        shape, float(attempt_tolerance), angular,
                        requested_upper_limit=tolerance, clean_before_mesh=not cleaned_once)
                except Exception as exc:
                    report = {"requested_linear_deflection_mm": float(attempt_tolerance),
                              "requested_linear_deflection_upper_limit_mm": tolerance,
                              "mesher_linear_deflection_target_mm": float(attempt_tolerance),
                              "angular_tolerance_rad": angular,
                              "error": f"{type(exc).__name__}: {exc}", "mesh_ready": False,
                              "mesher_is_done": None, "mesher_status_flags": None,
                              "mesh_extraction_performed": False, "faces": []}
                    accepted = False
                    vertices, triangles = [], []
                remesh_seconds += time.perf_counter() - mesh_started
                if accepted and cache_path:
                    try:
                        _write_mesh_cache(cache_path, cache_key, identity,
                                          vertices, triangles, report)
                        cache_writes += 1
                        part_report["mesh_cache"] = "written"
                    except OSError as exc:
                        cache_unavailable += 1
                        part_report["mesh_cache_error"] = f"{type(exc).__name__}: {exc}"
                elif not cache_path:
                    report = {**report, "mesh_cache_hit": False}
            part_report["attempts"].append(report)
            last_report = report
            if accepted:
                chosen_vertices, chosen_triangles = vertices, triangles
                part_report["accepted_tolerance_mm"] = float(attempt_tolerance)
                break
        diagnostics["parts"].append(part_report)
        if chosen_vertices is None or chosen_triangles is None:
            diagnostics["failed_part_id"] = part["id"]
            diagnostics["failed_part_summary"] = {
                "attempted_tolerances_mm": diagnostics["retry_tolerances_mm"],
                "last_attempt": last_report,
            }
            json_dump(diagnostics, output_dir / "mesh_export_diagnostics.json")
            raise ValueError(
                f"构件{part['id']}绝对网格在{diagnostics['retry_tolerances_mm']}mm内无法满足逐面三角网和边界离散要求")
        effective_targets[part["id"]] = part_report["accepted_tolerance_mm"]
        vertex_rows = chosen_vertices
        tri_rows = chosen_triangles
        mesh_parts.append({"id": part["id"], "name": part["name"], "group": part["group"],
                           "vertices": [v for row in vertex_rows for v in row],
                           "triangles": [i for row in tri_rows for i in row]})
        obj_lines.append(f"o {part['id']}")
        obj_lines.extend(f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in vertex_rows)
        obj_lines.extend("f %d %d %d" % tuple(obj_offset + i for i in tri) for tri in tri_rows)
        obj_offset += len(vertex_rows)
        triangle_count += len(tri_rows)
    diagnostics["summary"] = {
        "parts": len(parts),
        "mesh_cache_hit_attempt_count": cache_hits,
        "mesh_cache_miss_attempt_count": cache_misses,
        "mesh_cache_invalid_entry_count": cache_invalid,
        "mesh_cache_written_attempt_count": cache_writes,
        "mesh_cache_unavailable_part_count": cache_unavailable,
        "parts_with_cached_fine_start": sum(
            part.get("mesh_cache_start_tolerance_mm") is not None
            for part in diagnostics["parts"]),
        "skipped_coarser_retry_attempt_count": sum(
            len(part.get("skipped_coarser_retry_tolerances_mm", []))
            for part in diagnostics["parts"]),
        "mesh_cache_candidate_scan_count": sum(
            part.get("mesh_cache_candidate_scan_count", 0)
            for part in diagnostics["parts"]),
        "parts_cleaned_once_before_mesh": len(parts) if cache_available else 0,
        "mesher_not_done_count": sum(
            any(attempt.get("mesher_is_done") is False for attempt in part["attempts"])
            for part in diagnostics["parts"]),
        "nonzero_mesher_status_attempt_count": sum(
            attempt.get("mesher_status_flags") not in (None, 0)
            for part in diagnostics["parts"] for attempt in part["attempts"]),
        "triangulation_guard_false_attempt_count": sum(
            attempt.get("triangulation_guard_passed") is False
            for part in diagnostics["parts"] for attempt in part["attempts"]),
        "mesher_target_not_met_but_within_requested_limit_attempt_count": sum(
            attempt.get("all_faces_within_mesher_target") is False and
            attempt.get("all_faces_within_linear_limit") is True
            for part in diagnostics["parts"] for attempt in part["attempts"]),
        "parts_requiring_finer_deflection": sum(
            len(part["attempts"]) > 1 for part in diagnostics["parts"]),
        "parts_with_missing_degenerate_edge_polygons": sum(
            any(attempt.get("missing_degenerate_edge_polygons")
                for attempt in part["attempts"])
            for part in diagnostics["parts"]),
        "parts_with_missing_nondegenerate_edge_polygons": sum(
            any(attempt.get("missing_nondegenerate_edge_polygons")
                for attempt in part["attempts"])
            for part in diagnostics["parts"]),
        "accepted_tolerance_mm_by_part": effective_targets,
        "triangle_count": triangle_count,
    }
    json_dump(diagnostics, output_dir / "mesh_export_diagnostics.json")
    max_actual_deflection = max(
        (face["deflection_mm"] for part in diagnostics["parts"]
         for attempt in part["attempts"][-1:] for face in attempt.get("faces", [])
         if face.get("deflection_mm") is not None), default=0.0)
    mesh = {"metadata": {"units": "mm", "up_axis": "Z", "case_id": config["case_id"],
                         "mode": mode,
                         "preview_status": "preview_only" if mode == "preview" else None,
                         "stage": 5, "stage_name": "symmetric parametric corner construction",
                         "corner_angle_deg": float(config["corner_angle_deg"]),
                         "mesh_tessellation_mm": tolerance,
                         "mesh_requested_linear_deflection_mm": tolerance,
                         "mesh_linear_deflection_mm": tolerance,
                         "mesh_effective_max_face_deflection_mm": float(max_actual_deflection),
                         "mesh_effective_min_part_target_mm": float(min(effective_targets.values())),
                         "mesh_effective_target_mm_by_part": effective_targets,
                         "mesh_retry_tolerances_mm": diagnostics["retry_tolerances_mm"],
                         "mesh_deflection_check_basis": (
                             "OCCT triangulation.Deflection kernel report; not an independent distance measurement"),
                         "mesh_diagnostic_summary": diagnostics["summary"],
                         "mesh_relative_mode": False,
                         "mesh_angular_tolerance_rad": angular,
                         "mesh_cache_rule": "geometry BRep SHA256 + requested/attempt deflection + angular tolerance + generator source + OCCT/Python environment; absolute validated meshes only; reuse the coarsest cached accepted target before remeshing; otherwise one Clean_s per part then incremental finer attempts; direct face node/triangle extraction",
                         "mesh_absolute_remesh_seconds": float(remesh_seconds),
                         "mesh_cache_clean_part_count": int(len(parts) if cache_available else 0),
                         "mesh_cache_summary": diagnostics["summary"],
                         "mesh_diagnostics_file": "mesh_export_diagnostics.json",
                         "scope": "参数重建第五版范围；角部左右镜像；排料仅含立脚飞椽单侧模板×2；正身飞椽仅进3D"},
            "parts": mesh_parts, "control_lines": control}
    mesh_path = output_dir / "model_mesh.json"
    json_dump(mesh, mesh_path)
    (output_dir / "model.obj").write_text("\n".join(obj_lines) + "\n", encoding="utf-8")
    return mesh, triangle_count


def make_profiles_json(profiles, config, mesh_sha):
    return {"schema": "parametric_flyer_profiles_v1",
            "units": {"coordinates": "mm", "area": "mm^2", "width": "mm"},
            "case_id": config["case_id"], "source_mesh_sha256": mesh_sha,
            "profile_basis": "centerline_approximation",
            "method": {"name": "center-fiber idealized trapezoid",
                       "profile_basis": "centerline_approximation",
                       "coordinate_frame": "q is longitudinal distance from S toward E; z is world Z",
                       "construction": "选取本次每根立脚飞椽的中心纤维v=0；保留I底—E底及完整E端，将梢部顶边与底边平行并通过E顶，再与I底—原触底R底边相交。三维A/B侧面独立轮廓不作全宽展开或合并进单列排料。",
                       "source": "profiles.json polygon_qz_mm and points_qz_mm derive from the v=0 center fiber fields in stage05 flyer records",
                       "scope": "单侧所有立脚飞椽；每根中心截面梯形化，不代表全宽展开；正身飞椽不进入排料；单侧模板制作2套镜像件",
                       "allowance": "无锯缝余量"},
            "profiles": profiles}


def generation_record(config, parts, mesh, profiles, layout, fly_records, fan_division_count_n):
    """Record that artifact generation finished; geometric acceptance is visual and remains pending."""
    division_count = int(fan_division_count_n)
    expected_fan_count = division_count - 1
    actual_fan_count = sum(record.get("family") == "fan" for record in fly_records)
    mesh_parts = list(mesh.get("parts", []))
    triangle_count = sum(len(part.get("triangles", [])) // 3 for part in mesh_parts)
    return {
        "schema": "v02_generation_record_v1",
        # Workbench completion currently relies on this legacy field. It means
        # the generator wrote its deliverables, not that the model was approved.
        "passed": True,
        "assessment_basis": "generation_complete_visual_review_pending",
        "model_geometry_checks_run": False,
        "visual_review": {
            "status": "pending",
            "required": True,
            "note": "本记录只表示生成与导出流程完成；模型形体等待网页阴影/三角面检查。",
        },
        "input_geometry": {
            "case_id": config["case_id"],
            "corner_angle_deg": float(config["corner_angle_deg"]),
            "fan_division_count_n": division_count,
            "fan_arc_interval_length_mm": float(config["derived"]["fan_spacing_mm"]),
            "fan_count_expected_per_side": int(expected_fan_count),
            "fan_count_actual_per_side": int(actual_fan_count),
            "fan_count_single_side": int(actual_fan_count),
            "fan_count_both_sides": 2 * int(actual_fan_count),
        },
        "entities": {
            "count": len(parts),
            "geometry_assessment": "not_run",
            "items": [{"id": part["id"], "group": part["group"]} for part in parts],
        },
        "mesh_export": {
            "completed": True,
            "part_count": len(mesh_parts),
            "triangle_count": int(triangle_count),
            "linear_deflection_mm": mesh.get("metadata", {}).get("mesh_linear_deflection_mm"),
            "relative_mode": mesh.get("metadata", {}).get("mesh_relative_mode"),
        },
        "layout_output": {
            "profile_count": len(profiles),
            "piece_count": len(layout.get("layout", {}).get("pieces", [])),
            "board_thickness_mm": layout.get("layout", {}).get("board_thickness_mm"),
        },
        "collision_checks": {"status": "not_run"},
    }


def build_manifest(case_dir, case_id, *, mode="exact", source_identity=None,
                   presentation_source_identity=None):
    files = []
    for path in sorted(case_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": str(path.relative_to(case_dir)).replace("\\", "/"),
                          "bytes": path.stat().st_size, "sha256": sha256(path)})
    manifest = {"case_id": case_id, "mode": checked_generation_mode(mode),
                "file_count": len(files), "files": files}
    if source_identity is not None:
        manifest["source_identity"] = source_identity
    if presentation_source_identity is not None:
        manifest["presentation_source_identity"] = presentation_source_identity
    return manifest


def remove_stale_step(case_dir: Path) -> None:
    """A reused case directory must not advertise an unrequested older STEP."""
    step_path = Path(case_dir) / "model.step"
    if step_path.is_file():
        step_path.unlink()


def finalize_existing_case(config_path: Path, output_path: Path, make_viewer=True):
    """Finish reports/views from geometry exports after a post-export interruption."""
    config = load_config(config_path)
    output_dir = Path(output_path)
    if not output_dir.is_absolute():
        output_dir = HERE / output_dir
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to((HERE / "outputs").resolve())
    except ValueError as exc:
        raise ValueError(f"恢复输出路径必须位于生成器outputs目录内：{output_dir}") from exc
    expected = ("model_mesh.json", "model.obj", "profiles.json", "layout.json", "parameters_resolved.json")
    missing = [name for name in expected if not (output_dir / name).is_file()]
    if missing:
        raise ValueError(f"无法从现有导出恢复；缺少文件：{', '.join(missing)}")
    resolved_existing = json.loads((output_dir / "parameters_resolved.json").read_text(encoding="utf-8"))
    mesh = json.loads((output_dir / "model_mesh.json").read_text(encoding="utf-8"))
    validation_path = output_dir / "validation.json"
    manifest_path = output_dir / "manifest.json"
    validation_existing = (json.loads(validation_path.read_text(encoding="utf-8"))
                           if validation_path.is_file() else None)
    manifest_existing = (json.loads(manifest_path.read_text(encoding="utf-8"))
                         if manifest_path.is_file() else None)
    mesh_metadata = mesh.get("metadata", {})
    recorded_modes = [resolved_existing.get("mode", "exact"),
                      mesh_metadata.get("mode", "exact")]
    if validation_existing is not None:
        recorded_modes.append(validation_existing.get("mode", "exact"))
    if manifest_existing is not None:
        recorded_modes.append(manifest_existing.get("mode", "exact"))
    if any(recorded_mode != "exact" for recorded_mode in recorded_modes):
        raise ValueError("恢复拒绝：已有参数、网格、验证或manifest不是同一exact模式")
    current_identity = current_source_identity_record()
    saved_source_identity = _verify_source_identity_chain(
        [resolved_existing, {"source_identity": mesh_metadata.get("source_identity")},
         validation_existing, manifest_existing], current_identity)
    same_inputs = (resolved_existing.get("case_id") == config["case_id"] and
                   resolved_existing.get("corner_angle_deg") == config["corner_angle_deg"] and
                   resolved_existing.get("parameters") == config["parameters"] and
                   resolved_existing.get("advanced") == config["advanced"])
    if not same_inputs:
        raise ValueError("恢复拒绝：parameters_resolved.json与所给配置的case_id、参数或advanced不完全一致；"
                         "为避免把不同配置与旧网格混配，请先检查输出目录身份")
    if manifest_existing is not None:
        verify_saved_manifest(output_dir, manifest_existing, config["case_id"], "exact")
    if validation_existing is not None:
        validation_input = validation_existing.get("input", {})
        if (validation_existing.get("mode", "exact") != "exact" or
                validation_input.get("case_id", config["case_id"]) != config["case_id"] or
                validation_input.get("corner_angle_deg", config["corner_angle_deg"]) != config["corner_angle_deg"] or
                validation_input.get("parameters", config["parameters"]) != config["parameters"] or
                validation_input.get("advanced", config["advanced"]) != config["advanced"]):
            raise ValueError("恢复拒绝：已有validation的模式或参数与本次配置不一致")
    profile_doc = json.loads((output_dir / "profiles.json").read_text(encoding="utf-8"))
    layout = json.loads((output_dir / "layout.json").read_text(encoding="utf-8"))
    if (mesh.get("metadata", {}).get("case_id") != config["case_id"] or
            profile_doc.get("case_id") != config["case_id"] or
            layout.get("input", {}).get("case_id") != config["case_id"]):
        raise ValueError("恢复拒绝：mesh/profiles/layout的case_id与配置不一致")
    model_mesh_hash = sha256(output_dir / "model_mesh.json")
    if profile_doc.get("source_mesh_sha256") != model_mesh_hash:
        raise ValueError("恢复拒绝：profiles.json引用的model_mesh哈希与当前网格不一致")
    profile_ids = [profile.get("id") for profile in profile_doc.get("profiles", [])]
    layout_piece_ids = [piece.get("id") for piece in layout.get("layout", {}).get("pieces", [])]
    if (not profile_ids or len(profile_ids) != len(set(profile_ids)) or
            layout_piece_ids != profile_ids or
            layout.get("input", {}).get("profile_count") != len(profile_ids)):
        raise ValueError("恢复拒绝：profiles与layout的编号或数量不一致")
    profiles = profile_doc["profiles"]
    mesh_parts = mesh.get("parts")
    if not isinstance(mesh_parts, list) or not mesh_parts:
        raise ValueError("恢复拒绝：model_mesh.json没有可加载的构件清单")
    mesh_ids = [part.get("id") for part in mesh_parts if isinstance(part, dict)]
    if len(mesh_ids) != len(mesh_parts) or any(not isinstance(part_id, str) or not part_id for part_id in mesh_ids):
        raise ValueError("恢复拒绝：model_mesh.json构件编号缺失")
    expected_fly_ids = {part_id for profile in profiles for part_id in profile["model_ids"]}
    actual_fly_ids = {part_id for part_id in mesh_ids
                      if part_id.startswith(("flying_fan_x_", "flying_fan_y_"))}
    if expected_fly_ids != actual_fly_ids:
        raise ValueError("恢复拒绝：立脚飞椽profiles的model_ids与model_mesh构件编号不一致")
    board_t = float(layout["layout"]["board_thickness_mm"])
    translation = layout.get("translation") or {}
    triangle_count = sum(len(part.get("triangles", [])) // 3 for part in mesh_parts)

    expected_fan_division_count_n = None
    expected_fan_count = None
    fan_geometry = {"fan_division_count_n": None,
                    "fan_rafter_count": None,
                    "actual_fan_rafter_count": len(profiles),
                    "mirrored_count": len(profiles),
                    "total_count": 2 * len(profiles),
                    "n_definition": "n为最小奇数等分区间数，A=L弧/n<B；内部i/n分点生成n-1根摔网椽，端点只作边界"}
    stage05 = load_stage05()
    geometric_case, arc, young_sagitta = derived_geometry_values(config, stage05)
    expected_fan_division_count_n = int(geometric_case["fan_surface"]["n"])
    expected_fan_count = expected_fan_division_count_n - 1
    update_resolved_geometry_derivations(config, arc, young_sagitta, expected_fan_division_count_n)
    if len(profiles) != expected_fan_count:
        raise ValueError(f"恢复输出的立脚模板数{len(profiles)}与当前分档规则n−1={expected_fan_count}不一致")
    fan_geometry.update({"fan_division_count_n": expected_fan_division_count_n,
                         "fan_rafter_count": expected_fan_count,
                         "expected_fan_rafter_count": expected_fan_count,
                         "fan_count_matches_spacing_rule": len(profiles) == expected_fan_count,
                         "arc_length_mm": float(arc["arc_length_mm"]),
                         "arc_length_over_n_mm": float(arc["arc_length_mm"] / expected_fan_division_count_n),
                         "derived_sagitta_mm": float(arc["derived_sagitta_mm"]),
                         "spacing_limit_B_mm": float(config["parameters"]["B"])})

    entities = {"count": len(mesh_parts), "geometry_assessment": "not_run",
                "items": [{"id": part["id"], "group": part.get("group")}
                          for part in mesh_parts]}
    layout_check = {"profile_count": len(profiles), "piece_count": len(layout["layout"]["pieces"]),
                    "fan_division_count_n": expected_fan_division_count_n,
                    "fan_count_expected_per_side": expected_fan_count,
                    "fan_count_actual_per_side": len(profiles),
                    "model_ids_match_dynamic_flyer_ids": expected_fly_ids == actual_fly_ids,
                    "profile_model_ids": sorted(expected_fly_ids),
                    "actual_3d_flyer_ids": sorted(actual_fly_ids),
                    "board_thickness_mm": board_t,
                    "board_thickness_source_parameter_mm": float(config["parameters"]["w_fly"]),
                    "retain_ratio_percent": float(layout["layout"]["retain_ratio_percent"]),
                    "translation_available": bool(translation.get("available", False)),
                    "translation_error_max_mm": translation.get("max_abs_error_mm"),
                    "translation_error_rms_mm": translation.get("rms_error_mm")}
    validation = {"schema": "v02_generation_record_v1", "mode": "exact",
                  "source_identity": saved_source_identity,
                  "validation_scope": "exact_generation_delivery",
                  "assessment_basis": "generation_complete_visual_review_pending",
                  "model_geometry_checks_run": False,
                  "visual_review": {"status": "pending", "required": True,
                                    "note": "现有网格、排料及构件编号已关联；形体等待网页阴影/三角面检查。"},
                  "input_geometry": {"case_id": config["case_id"],
                                     "corner_angle_deg": float(config["corner_angle_deg"]),
                                      "fan_division_count_n": expected_fan_division_count_n,
                                      "fan_count_expected_per_side": expected_fan_count,
                                      "fan_count_actual_per_side": len(profiles),
                                      "fan_count_single_side": len(profiles),
                                      "fan_count_both_sides": 2 * len(profiles)},
                  "entities": entities,
                  "mesh_export": {"completed": True, "part_count": len(mesh_parts),
                                  "triangle_count": int(triangle_count)},
                  "layout": layout_check,
                  "fan_layout": fan_geometry,
                  "straight_rafters": {"eave_rafter_count_both_sides": sum(p.get("group") == "eave_rafters" for p in mesh_parts)},
                  "collision_checks": {"full_pairwise_common_volume": "not run",
                                       "full_pairwise_face_area": "not run",
                                        "scope": "未做模型几何验收"},
                   "input": {"case_id": config["case_id"], "corner_angle_deg": config["corner_angle_deg"],
                            "parameters": config["parameters"], "advanced": config["advanced"],
                            "derived": config["derived"]},
                  "recovery": {"mode": "finalized from saved model_mesh.json, profiles.json and layout.json",
                               "geometry_rebuilt": False,
                                "reason": "保留现有网格、OBJ、中心截面与排料文件；补写生成状态/展示输出，不重建CAD几何。"},
                  # Legacy backend completion flag: this records delivery completion only.
                  "passed": True}
    # All mode, input, source, manifest, linkage, and count checks are complete.
    # Only now may recovery rewrite derived records or remove a stale STEP.
    write_resolved_mode(config, output_dir / "parameters_resolved.json", "exact",
                        source_identity=saved_source_identity)
    json_dump(validation, validation_path)
    render_report = None
    if make_viewer:
        try:
            renderer_spec = importlib.util.spec_from_file_location("parametric_delivery_renderer", HERE / "render_delivery.py")
            if renderer_spec is None or renderer_spec.loader is None:
                raise RuntimeError("无法加载生成器render_delivery.py")
            renderer = importlib.util.module_from_spec(renderer_spec)
            renderer_spec.loader.exec_module(renderer)
            render_report = renderer.render_case(output_dir)
        except Exception as exc:
            render_report = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    remove_stale_step(output_dir)
    presentation_identity = (current_identity["production"]
                             if current_identity["production"]["sha256"] !=
                             saved_source_identity["production"]["sha256"] else None)
    json_dump(build_manifest(output_dir, config["case_id"], mode="exact",
                             source_identity=saved_source_identity,
                             presentation_source_identity=presentation_identity), manifest_path)
    return {"case_id": config["case_id"], "output_dir": str(output_dir),
            "parts": len(mesh_parts), "fan_templates_per_side": len(profiles),
            "layout_board_mm": [layout["layout"]["board_width_x_mm"],
                                layout["layout"]["board_height_y_mm"], board_t],
            "retain_ratio_percent": layout["layout"]["retain_ratio_percent"],
            "translation_error_max_mm": translation.get("max_abs_error_mm"),
            "translation_error_rms_mm": translation.get("rms_error_mm"),
            "mesh_triangles": triangle_count, "render": render_report,
            "elapsed_seconds": None, "recovered": True, "passed": True,
            "fan_division_count_n": expected_fan_division_count_n,
            "fan_count_expected_per_side": expected_fan_count,
            "fan_count_actual_per_side": len(profiles)}


def generate(config_path: Path, output_path: Path, make_viewer=True, export_step=False, *,
             mode="exact", mesh_cache_dir=None):
    mode = checked_generation_mode(mode)
    if mode == "preview" and export_step:
        raise ValueError("预览模式不生成STEP；请使用精确生成")
    start = time.perf_counter()
    phase_timings = {}
    phase_started = time.perf_counter()
    config = load_config(config_path)
    source_identity = current_source_identity_record()
    phase_timings["config_validation_and_initial_write"] = time.perf_counter() - phase_started
    print(f"generator: validated case={config['case_id']} angle={config['corner_angle_deg']:g}deg; starting geometry", flush=True)
    phase_started = time.perf_counter()
    identity_stage05 = load_stage05()
    identity_case, identity_arc, identity_young_sagitta = derived_geometry_values(
        config, identity_stage05)
    update_resolved_geometry_derivations(
        config, identity_arc, identity_young_sagitta,
        int(identity_case["fan_surface"]["n"]))
    phase_timings["lightweight_geometry_identity_derivation"] = time.perf_counter() - phase_started
    generator_output_root = (HERE / "outputs").resolve()
    output_dir = Path(output_path)
    if not output_dir.is_absolute():
        output_dir = HERE / output_dir
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(generator_output_root)
    except ValueError as exc:
        raise ValueError(f"输出路径必须位于生成器outputs目录内，避免覆盖旧模型或研究探针：{output_dir}") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    if mode == "preview":
        exact_only_artifacts = ("layout.html", "layout.json", "layout.png", "layout.svg",
                                "marking_points.csv", "profiles.json", "model.step")
        present = [name for name in exact_only_artifacts if (output_dir / name).exists()]
        if present:
            raise ValueError("预览输出目录包含先前精确生成文件；请指定新的独立输出目录：" + ", ".join(present))
    write_resolved_mode(config, output_dir / "parameters_resolved.json", mode,
                        source_identity=source_identity)

    recovery_started = time.perf_counter()
    geometry_recovery = find_geometry_checkpoint(config, output_dir, mode=mode)
    if geometry_recovery is not None:
        previous_phase_timings = geometry_recovery.get("geometry_phase_timings_seconds", {})
        for name in ("stage04_base_and_preflight", "stage05_flyer_build",
                     "stage05_likou_cut_and_mirror", "stage05_upper_board_rebuild",
                     "stage05_combination_rebuild", "geometry_total_after_stage04",
                     "geometry_checkpoint_write"):
            if name in previous_phase_timings:
                phase_timings[name] = float(previous_phase_timings[name])
        phase_timings["geometry_checkpoint_restore"] = time.perf_counter() - recovery_started
        recovered_context = geometry_recovery["context"]
        ctx = {"parts": geometry_recovery["parts"], "control": geometry_recovery["control"],
               "case": recovered_context["case"],
               "upper_board_domain": recovered_context.get("upper_board_domain"),
               "selection_rule": recovered_context["selection_rule"],
               "likou_notch_performance": recovered_context.get("likou_notch_performance")}
        fly_records = geometry_recovery["fly_records"]
        notch_records = geometry_recovery["notch_records"]
        board_records = geometry_recovery["board_records"]
        combination_info = geometry_recovery["combination_info"]
        write_resolved_mode(config, output_dir / "parameters_resolved.json", mode,
                            source_identity=source_identity)
        recovered_checkpoint = geometry_recovery
        geometry_recovery = {
            "mode": "exact geometry-source and normalized-config checkpoint",
            "source_checkpoint": recovered_checkpoint["checkpoint_path"],
            "checkpoint_case_id": recovered_checkpoint["checkpoint_case_id"],
            "current_case_id": config["case_id"],
            "config_identity_sha256": recovered_checkpoint["config_identity_sha256"],
            "checkpoint_production_source_sha256": recovered_checkpoint[
                "production_source_identity"]["sha256"],
            "recovery_production_source_sha256": recovered_checkpoint[
                "recovery_production_source_identity"]["sha256"],
            "checkpoint_geometry_source_sha256": recovered_checkpoint[
                "geometry_source_identity"]["sha256"],
            "recovery_geometry_source_sha256": recovered_checkpoint[
                "recovery_geometry_source_identity"]["sha256"],
            "post_geometry_artifacts_regenerated_from_current_algorithms": True,
            "prior_geometry_runtime_seconds": recovered_checkpoint.get(
                "geometry_runtime_seconds_before_checkpoint"),
            "geometry_rebuilt": False}
        print(f"generator: restored {len(ctx['parts'])} verified BReps from geometry checkpoint; skipping CAD rebuild",
              flush=True)
    else:
        phase_started = time.perf_counter()
        stage05 = load_stage05()
        ctx = stage05.build_stage04_base(config)
        update_resolved_geometry_derivations(
            config, ctx["case"]["fan_geometry"]["arc"],
            ctx["young_info"]["effective_carback_sagitta_mm"],
            ctx["case"]["fan_geometry"]["n"])
        write_resolved_mode(config, output_dir / "parameters_resolved.json", mode,
                            source_identity=source_identity)
        phase_timings["stage04_base_and_preflight"] = time.perf_counter() - phase_started
        print(f"generator: {mode} flyer geometry start", flush=True)
        phase_started = time.perf_counter()
        fly_records = (stage05.add_preview_contact_flyers(ctx) if mode == "preview"
                       else stage05.add_contact_flyers(ctx))
        phase_timings["stage05_flyer_build"] = time.perf_counter() - phase_started
        if mode == "preview":
            notch_records = []
            board_records = []
            phase_timings["stage05_likou_cut_and_mirror"] = 0.0
            phase_timings["stage05_upper_board_rebuild"] = 0.0
            print("generator: preview combination stock blank start", flush=True)
            phase_started = time.perf_counter()
            combination_info = stage05.add_preview_combination_blank(ctx)
            phase_timings["stage05_combination_rebuild"] = time.perf_counter() - phase_started
        else:
            print("generator: likou seat refit start", flush=True)
            phase_started = time.perf_counter()
            notch_records = stage05.cut_contact_likou_notches(ctx)
            phase_timings["stage05_likou_cut_and_mirror"] = time.perf_counter() - phase_started
            print("generator: upper roll-board rebuild start", flush=True)
            phase_started = time.perf_counter()
            board_records = stage05.add_upper_rollboards(ctx)
            phase_timings["stage05_upper_board_rebuild"] = time.perf_counter() - phase_started
            print("generator: combination timber rebuild start", flush=True)
            phase_started = time.perf_counter()
            combination_info = stage05.add_combination(ctx)
            phase_timings["stage05_combination_rebuild"] = time.perf_counter() - phase_started
        phase_timings["geometry_total_after_stage04"] = sum(
            phase_timings[name] for name in ("stage05_flyer_build", "stage05_likou_cut_and_mirror",
                                             "stage05_upper_board_rebuild", "stage05_combination_rebuild"))
        print(f"generator: geometry complete ({len(ctx['parts'])} parts)", flush=True)
        if not fly_records:
            raise ValueError("当前参数下没有生成飞椽")
        if mode == "exact":
            print("generator: saving mesh-failure checkpoint", flush=True)
            checkpoint_path, checkpoint_seconds = save_geometry_checkpoint(
                ctx["parts"], ctx["control"], config, fly_records, notch_records,
                board_records, combination_info, ctx, phase_timings,
                time.perf_counter() - start, output_dir, mode=mode)
            phase_timings["geometry_checkpoint_write"] = checkpoint_seconds
            print(f"generator: mesh-failure checkpoint ready ({checkpoint_path}; {checkpoint_seconds:.3f}s)",
                  flush=True)
        geometry_recovery = None
    print(f"generator: geometry complete ({len(ctx['parts'])} parts); exporting mesh", flush=True)
    if not fly_records:
        raise ValueError("当前参数下没有生成飞椽")

    phase_started = time.perf_counter()
    try:
        mesh, triangles = export_mesh(ctx["parts"], ctx["control"], config, output_dir,
                                      mode=mode, mesh_cache_dir=mesh_cache_dir)
    except Exception:
        diagnostics_path = output_dir / "mesh_export_diagnostics.json"
        if diagnostics_path.is_file() and mode == "exact":
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            checkpoint_source = (geometry_recovery["source_checkpoint"]
                                 if geometry_recovery is not None
                                 else str((output_dir / "geometry_checkpoint").resolve()))
            checkpoint_dir = Path(checkpoint_source)
            if checkpoint_dir.name == "checkpoint.json":
                checkpoint_dir = checkpoint_dir.parent
            checkpoint_manifest = checkpoint_dir / "checkpoint.json"
            checkpoint_parts = []
            if checkpoint_manifest.is_file():
                checkpoint_doc = json.loads(checkpoint_manifest.read_text(encoding="utf-8"))
                for entry in checkpoint_doc.get("parts", []):
                    if entry.get("id") == diagnostics.get("failed_part_id"):
                        checkpoint_parts.append({"id": entry["id"], "name": entry["name"],
                                                 "group": entry["group"], "brep": entry["brep"],
                                                 "volume_mm3": entry.get("volume_mm3"),
                                                 "sha256": entry["sha256"]})
            diagnostics["geometry_brep_checkpoint"] = {
                "path": str(checkpoint_dir.resolve()),
                "checkpoint_manifest": str(checkpoint_manifest.resolve()),
                "checkpoint_case_id": checkpoint_doc.get("checkpoint_case_id")
                    if checkpoint_manifest.is_file() else None,
                "current_case_id": config["case_id"],
                "config_identity_sha256": checkpoint_doc.get("config_identity_sha256")
                    if checkpoint_manifest.is_file() else None,
                "checkpoint_production_source_sha256": checkpoint_doc.get(
                    "production_source_identity", {}).get("sha256")
                    if checkpoint_manifest.is_file() else None,
                "checkpoint_geometry_source_sha256": checkpoint_doc.get(
                    "geometry_source_identity", {}).get("sha256")
                    if checkpoint_manifest.is_file() else None,
                "recovery_production_source_sha256": (
                    geometry_recovery.get("recovery_production_source_sha256")
                    if geometry_recovery is not None else production_source_identity()["sha256"]),
                "recovery_geometry_source_sha256": (
                    geometry_recovery.get("recovery_geometry_source_sha256")
                    if geometry_recovery is not None else geometry_source_identity()["sha256"]),
                "failed_part": checkpoint_parts[0] if checkpoint_parts else None,
            }
            json_dump(diagnostics, diagnostics_path)
        raise
    phase_timings["mesh_remesh_and_export"] = time.perf_counter() - phase_started
    phase_timings["mesh_absolute_remesh_only"] = float(mesh["metadata"]["mesh_absolute_remesh_seconds"])
    mesh["metadata"]["source_identity"] = source_identity
    json_dump(mesh, output_dir / "model_mesh.json")
    local_checkpoint = output_dir / "geometry_checkpoint"
    if mode == "exact" and local_checkpoint.is_dir():
        shutil.rmtree(local_checkpoint)
    mesh_path = output_dir / "model_mesh.json"
    mesh_hash = sha256(mesh_path)
    if mode == "preview":
        mesh_parts = mesh.get("parts", [])
        triangle_total = sum(len(part.get("triangles", [])) // 3 for part in mesh_parts)
        flyer_records = list(ctx.get("contact_fly_records", []))
        fan_count = sum(record.get("family") == "fan" for record in ctx.get("chosen", []))
        expected_fans = int(ctx["case"]["fan_geometry"]["fan_rafter_count"])
        if fan_count != expected_fans:
            raise ValueError(f"preview flyer count mismatch: expected={expected_fans}, actual={fan_count}")
        entity_items = []
        for part in ctx["parts"]:
            shape = part["shape"]
            entity_items.append({"id": part["id"], "group": part["group"],
                                 "brep_face_count": len(shape.Faces()),
                                 "solid_count": len(shape.Solids()),
                                 "is_valid": bool(shape.isValid()),
                                 "volume_mm3": float(shape.Volume())})
        validation = {
            "schema": "workbench_generation_record_v1", "mode": "preview",
            "source_identity": source_identity,
            "status": "preview_ready", "validation_scope": "preview_geometry_and_mesh_delivery",
            "exact_acceptance": "not_run", "passed": False,
            "assessment_basis": "preview_delivery_complete_exact_geometry_not_run",
            "model_geometry_checks_run": False,
            "input": {"case_id": config["case_id"],
                      "corner_angle_deg": config["corner_angle_deg"],
                      "parameters": config["parameters"], "advanced": config["advanced"],
                      "derived": config["derived"]},
            "input_geometry": {"fan_count_expected_per_side": expected_fans,
                               "fan_count_actual_per_side": fan_count,
                               "fan_count_both_sides": 2 * fan_count},
            "entities": {"count": len(entity_items),
                         "geometry_assessment": "preview_validity_checked_exact_acceptance_not_run",
                         "items": entity_items},
            "mesh_export": {"completed": True, "part_count": len(mesh_parts),
                            "triangle_count": triangle_total,
                            "linear_deflection_mm": mesh["metadata"]["mesh_linear_deflection_mm"],
                            "relative_mode": False,
                            "cache": mesh["metadata"]["mesh_cache_summary"]},
            "preview": {
                "status": "preview_ready", "exact_generation_available": False,
                "likou_notches_cut": False, "upper_rollboard_generated": False,
                "flyers": flyer_records,
                "flyer_geometry_rule": "Each flyer uses the v=0 center R/I/E profile rigidly extruded along its true 3D width vector; A/B match by construction.",
                "combination": combination_info,
                "combination_old_young_boolean_cuts_run": False,
                "combination_horizontal_split_run": False,
                "layout_generated": False, "step_generated": False,
                "warnings": [
                    "预览飞椽未保留全宽扭曲与两侧独立轮廓。",
                    "里口保留原坯，未切槽；未生成上层卷戗板。",
                    "组合木为未与老戗/嫩戗布尔切配、未上下拆分的原坯。",
                    "排料、放样 CSV 与 STEP 未生成；精确实体验收未运行。"],
            },
            "phase_timings_seconds": phase_timings,
            "runtime_seconds_before_render": float(time.perf_counter() - start),
            "runtime_seconds_including_render": None,
            "collision_checks": {"status": "not_run"},
        }
        json_dump(validation, output_dir / "validation.json")
        config_source = Path(config_path)
        json_dump(json.loads(config_source.read_text(encoding="utf-8-sig")),
                  output_dir / "config.json")
        render_report = None
        if make_viewer:
            print("generator: offline preview viewer rendering start", flush=True)
            phase_started = time.perf_counter()
            renderer_spec = importlib.util.spec_from_file_location(
                "parametric_preview_delivery_renderer", HERE / "render_delivery.py")
            if renderer_spec is None or renderer_spec.loader is None:
                raise RuntimeError("无法加载render_delivery.py")
            renderer = importlib.util.module_from_spec(renderer_spec)
            renderer_spec.loader.exec_module(renderer)
            render_report = renderer.render_preview_case(output_dir)
            phase_timings["rendering"] = time.perf_counter() - phase_started
        validation["runtime_seconds_including_render"] = float(time.perf_counter() - start)
        validation["phase_timings_seconds"] = phase_timings
        validation["render"] = render_report
        json_dump(validation, output_dir / "validation.json")
        json_dump(build_manifest(output_dir, config["case_id"], mode=mode,
                                 source_identity=source_identity),
                  output_dir / "manifest.json")
        print(f"generator: preview ready ({len(mesh_parts)} parts, {triangle_total} triangles)", flush=True)
        return {"case_id": config["case_id"], "mode": mode, "status": "preview_ready",
                "output_dir": str(output_dir), "parts": len(mesh_parts),
                "flyer_templates_per_side": fan_count, "mesh_triangles": triangle_total,
                "render": render_report, "phase_timings_seconds": phase_timings,
                "elapsed_seconds": float(time.perf_counter() - start)}
    print(f"generator: mesh/OBJ complete ({triangles} triangles); extracting profiles", flush=True)
    phase_started = time.perf_counter()
    profiles = make_profiles(fly_records)
    json_dump(make_profiles_json(profiles, config, mesh_hash), output_dir / "profiles.json")
    phase_timings["profiles"] = time.perf_counter() - phase_started
    print(f"generator: profiles ready ({len(profiles)} standing templates); computing layout", flush=True)
    phase_started = time.perf_counter()
    layout = make_layout(profiles)
    print("generator: layout and spline calculations complete", flush=True)
    layout["input"]["case_id"] = config["case_id"]
    layout["layout"]["board_thickness_mm"] = float(config["parameters"]["w_fly"])
    layout["layout"]["blank_envelope_volume_mm3"] = float(layout["layout"]["blank_envelope_area_mm2"] * config["parameters"]["w_fly"])
    layout["layout"]["retained_volume_mm3"] = float(layout["layout"]["retained_profile_area_mm2"] * config["parameters"]["w_fly"])
    layout["profile_basis"] = "centerline_approximation"
    layout["profile_basis_source"] = "profiles.json取立脚飞椽v=0中心纤维轮廓；A/B三维侧面不展开到排料"
    json_dump(layout, output_dir / "layout.json")
    phase_timings["layout_and_json"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    validation = generation_record(
        config, ctx["parts"], mesh, profiles, layout, fly_records,
        ctx["case"]["fan_geometry"]["n"])
    validation["mode"] = "exact"
    validation["source_identity"] = source_identity
    validation["validation_scope"] = "exact_generation_delivery"
    validation["exact_acceptance"] = "generation_complete_visual_review_pending"
    validation["input"] = {"case_id": config["case_id"], "corner_angle_deg": config["corner_angle_deg"],
                            "parameters": config["parameters"], "advanced": config["advanced"],
                            "derived": config["derived"]}
    validation["fan_layout"] = ctx["case"]["fan_geometry"]
    validation["straight_rafters"] = ctx["case"]["eave_rafters_geometry"]
    validation["profile_basis"] = {
        "name": "centerline_approximation",
        "source": "profiles.json uses the v=0 center fiber of each standing flyer",
        "scope": "A/B side surfaces remain independently defined in 3D and are not unfolded into separate layout profiles",
    }
    validation["mesh_export"].update({
        "linear_deflection_mm": float(mesh["metadata"]["mesh_linear_deflection_mm"]),
        "requested_linear_deflection_mm": float(mesh["metadata"]["mesh_requested_linear_deflection_mm"]),
        "effective_max_face_deflection_mm": float(mesh["metadata"]["mesh_effective_max_face_deflection_mm"]),
        "effective_min_part_target_mm": float(mesh["metadata"]["mesh_effective_min_part_target_mm"]),
        "relative_mode": bool(mesh["metadata"]["mesh_relative_mode"]),
        "angular_tolerance_rad": float(mesh["metadata"]["mesh_angular_tolerance_rad"]),
        "cache_rule": mesh["metadata"]["mesh_cache_rule"],
        "absolute_remesh_seconds": float(mesh["metadata"]["mesh_absolute_remesh_seconds"]),
        "cleaned_part_count": int(mesh["metadata"]["mesh_cache_clean_part_count"]),
        "diagnostic_file": mesh["metadata"]["mesh_diagnostics_file"],
        "diagnostic_summary": mesh["metadata"]["mesh_diagnostic_summary"],
    })
    if geometry_recovery is not None:
        validation["geometry_checkpoint_recovery"] = geometry_recovery
    validation["stage05"] = {"likou_notch_records": notch_records,
                             "flyer_masterboard": ctx.get("flyer_masterboard"),
                             "qiangshan_profile_adjustment": ctx.get("case", {}).get(
                                 "qiangshan_profile_adjustment"),
                             "upper_board_records": board_records,
                             "upper_board_domain": ctx.get("upper_board_domain"),
                             "upper_board_piece_summary": {
                                 "piece_count_per_wing": len(board_records),
                                 "piece_count_both_wings": 2 * len(board_records),
                                 "piece_type_count_x": {
                                     kind: sum(item.get("piece_type") == kind for item in board_records)
                                     for kind in ("continuous_wing",)},
                                 "underface_vertical_gap_mm": float(config["advanced"]["upper_board_surface_overlap_mm"]),
                                 "thickness_rule": "每翼连续曲面用全局纵向/横向切向求法向等效厚度偏置",
                                 "coverage_rule": "基于真摔网飞椽与边界正身飞椽共享顶面轨、椽间控制点和两端半档构造单一光滑曲面，保持实际S/E边界且每翼一件",
                             },
                             "combination": combination_info,
                             "selection_rule": ctx["selection_rule"],
                             "fan_side_profile_rule": (
                                 "A/B edges are extracted from the prebuilt shared piecewise-ruled motherboard M "
                                 "through each member's W_i band; at each member station q both sides use the same T(q), "
                                 "while their bottom and top elevations may differ. Each side intersects the shared front "
                                 "and tip boundaries independently, so q_E may differ across the width. Longitudinal "
                                 "generators stay straight. Likou cutters reuse W_i and the same I-to-E lower surface."),
                             "fan_side_contours": [
                                 {"source_rafter_id": r["source_rafter_id"],
                                  "index": r["index"], "profile_basis": r["profile_basis"],
                                  "thickness_profile": r.get("thickness_profile"),
                                  "sides": r["side_contours"]}
                                 for r in fly_records if r["family"] == "fan"],
                             "likou_notch_performance": ctx.get("likou_notch_performance"),
                             "stage03_mesh_sha256": None,
                             "full_pairwise_collision_scan": "not run"}
    phase_timings["generation_record"] = time.perf_counter() - phase_started
    validation["phase_timings_seconds"] = phase_timings
    validation["runtime_seconds_before_render"] = float(time.perf_counter() - start)
    if export_step:
        cq = stage05.cq
        step_path = output_dir / "model.step"
        phase_started = time.perf_counter()
        cq.exporters.export(cq.Compound.makeCompound([p["shape"] for p in ctx["parts"]]),
                            str(step_path), exportType="STEP")
        phase_timings["step_export"] = time.perf_counter() - phase_started
        validation["step_export"] = {"path": "model.step", "bytes": step_path.stat().st_size,
                                      "sha256": sha256(step_path), "roundtrip_readback": "not run"}
    json_dump(validation, output_dir / "validation.json")
    render_report = None
    if make_viewer:
        print("generator: offline viewer rendering start", flush=True)
        phase_started = time.perf_counter()
        renderer_spec = importlib.util.spec_from_file_location("parametric_delivery_renderer", HERE / "render_delivery.py")
        if renderer_spec is None or renderer_spec.loader is None:
            raise RuntimeError("无法加载生成器render_delivery.py")
        renderer = importlib.util.module_from_spec(renderer_spec)
        renderer_spec.loader.exec_module(renderer)
        try:
            render_report = renderer.render_case(output_dir)
            print("generator: offline viewer rendering complete", flush=True)
        except Exception as exc:
            render_report = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"generator: viewer rendering unavailable; core outputs retained: {render_report['error']}",
                  flush=True)
        phase_timings["rendering"] = time.perf_counter() - phase_started
    if not export_step:
        remove_stale_step(output_dir)
    validation["phase_timings_seconds"] = phase_timings
    validation["runtime_seconds_including_render"] = float(time.perf_counter() - start)
    validation["render"] = render_report
    json_dump(validation, output_dir / "validation.json")
    json_dump(build_manifest(output_dir, config["case_id"], mode=mode,
                             source_identity=source_identity), output_dir / "manifest.json")
    return {"case_id": config["case_id"], "mode": mode, "status": "exact_ready",
            "output_dir": str(output_dir),
            "parts": len(ctx["parts"]), "fan_templates_per_side": len(profiles),
            "standing_flyer_3d_count_both_sides": 2 * len(profiles),
            "layout_board_mm": [layout["layout"]["board_width_x_mm"],
                                layout["layout"]["board_height_y_mm"], layout["layout"]["board_thickness_mm"]],
            "retain_ratio_percent": layout["layout"]["retain_ratio_percent"],
            "translation_error_max_mm": layout["translation"]["max_abs_error_mm"],
            "translation_error_rms_mm": layout["translation"]["rms_error_mm"],
            "mesh_triangles": triangles, "render": render_report,
            "phase_timings_seconds": phase_timings,
            "elapsed_seconds": float(time.perf_counter() - start)}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/default.json"),
                        help="配置JSON；相对路径以本生成器目录为基准")
    parser.add_argument("--output", type=Path, default=Path("outputs/default"),
                        help="独立输出目录；相对路径以本生成器目录为基准，且必须在outputs/内")
    parser.add_argument("--mode", choices=("preview", "exact"), default="exact",
                        help="几何生成模式；preview为快速预览，exact为精确生成（默认）")
    parser.add_argument("--mesh-cache-dir", type=Path,
                        help="绝对网格缓存目录；只复用BRep哈希、精度、源码与内核版本完全匹配的网格")
    parser.add_argument("--no-render", action="store_true", help="只生成几何与计算数据，不生成HTML和PNG")
    parser.add_argument("--step", action="store_true", help="额外导出STEP（不执行STEP回读）")
    parser.add_argument("--finalize-existing", action="store_true",
                        help="从已有mesh/profiles/layout补写验证与展示，不重新生成CAD几何")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else HERE / args.config
    try:
        if args.finalize_existing:
            if args.mode != "exact":
                raise ValueError("--finalize-existing仅支持exact模式")
            result = finalize_existing_case(config_path, args.output, make_viewer=not args.no_render)
        else:
            result = generate(config_path, args.output, make_viewer=not args.no_render,
                              export_step=args.step, mode=args.mode,
                              mesh_cache_dir=args.mesh_cache_dir)
    except Exception as exc:
        parser.exit(2, f"生成失败：{type(exc).__name__}: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    if args.finalize_existing and result.get("passed") is False:
        parser.exit(2, "生成失败：恢复后的产物未通过完整性检查；validation.json与展示文件已保留诊断。\n")


if __name__ == "__main__":
    main()
