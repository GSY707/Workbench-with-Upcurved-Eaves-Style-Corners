#!/usr/bin/env python
"""One-case preview probe: center-fiber flyers, control-driven boards, uncut combo blank."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

PROCESS_ENTRY_PERF = time.perf_counter()
PROCESS_STARTED_UTC = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
OUT_DIR = Path(__file__).resolve().parent
GENERATOR_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(GENERATOR_DIR))

import generate  # noqa: E402


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def shape_faces(shape) -> int:
    return len(shape.Faces())


def profile_side_and_extrude(stage05, record: dict, control: dict, root: dict,
                             full_width_mm: float) -> tuple[object, dict]:
    """Build a prismatic member from the production center fiber's R/I/E profile."""
    center_v = float(control["v_mm"])
    if abs(center_v) > 1e-8:
        raise ValueError(f"preview center fiber is not v=0: {center_v}")
    width_axis = [float(value) for value in record["width_axis"]]
    width_slope_z = float(record.get("g", 0.0))
    width_vec = [width_axis[0] * full_width_mm,
                 width_axis[1] * full_width_mm,
                 width_slope_z * full_width_mm]
    start_shift = [-0.5 * value for value in width_vec]

    q_r = float(root["q_root_mm"])
    q_i = float(control["q_front_mm"])
    q_e = float(control["q_E_mm"])
    station_rows = [
        {"label": "R", "q_mm": q_r,
         "bottom_z_mm": float(root["R_bottom_z_mm"]),
         "top_z_mm": float(root["R_top_z_mm"])},
        {"label": "I", "q_mm": q_i,
         "bottom_z_mm": float(control["H_front_mm"]),
         "top_z_mm": float(control["I_upper_z_mm"])},
        {"label": "E", "q_mm": q_e,
         "bottom_z_mm": float(control["E_bottom_z_mm"]),
         "top_z_mm": float(control["E_top_z_mm"])},
    ]
    if not q_r < q_i < q_e:
        raise ValueError(f"preview R/I/E order is invalid: {q_r}, {q_i}, {q_e}")

    points_center = []
    for row in station_rows:
        x, y = stage05.xy_at_q(record, row["q_mm"], 0.0)
        points_center.append([float(x), float(y), row["bottom_z_mm"]])
    for row in reversed(station_rows):
        x, y = stage05.xy_at_q(record, row["q_mm"], 0.0)
        points_center.append([float(x), float(y), row["top_z_mm"]])

    points_a = [[p[0] + start_shift[0], p[1] + start_shift[1], p[2] + start_shift[2]]
                for p in points_center]
    cq = stage05.cq
    wire_points = [cq.Vector(*point) for point in points_a]
    wire = cq.Wire.makePolygon(wire_points + [wire_points[0]], close=False)
    shape = cq.Solid.extrudeLinear(wire, [], cq.Vector(*width_vec))
    if shape.isNull() or not shape.isValid() or len(shape.Solids()) != 1:
        raise ValueError(
            f"preview flyer {record['family']} {record['index']} is invalid or multi-solid")
    volume = float(shape.Volume())
    if not volume > 1e-5:
        raise ValueError(f"preview flyer {record['family']} {record['index']} has nonpositive volume")
    face_count = shape_faces(shape)
    if face_count > 20:
        raise ValueError(
            f"preview flyer {record['family']} {record['index']} has {face_count} BRep faces (>20 target)")
    return shape, {
        "center_profile_rie_bottom_then_top_mm": points_center,
        "a_side_profile_mm": points_a,
        "width_vector_3d_mm": width_vec,
        "full_width_mm": float(full_width_mm),
        "same_a_b_side_profile": True,
        "same_profile_reason": "The B-side section is the same six-vertex center profile translated by the full rigid extrusion vector.",
        "station_rows": station_rows,
        "brep_face_count": face_count,
        "volume_mm3": volume,
    }


def lightweight_contact_detail(item: dict) -> dict:
    """Expose masterboard fibers to the roll-board builder without making flyer BReps."""
    record = item["record"]
    controls = item["controls"]
    roots = item["roots"]
    center_index = len(controls) // 2
    center = controls[center_index]
    return {
        "family": str(record["family"]),
        "index": int(record["index"]),
        "role": str(record.get("role", "fan" if record["family"] == "fan" else "regular")),
        "is_boundary": bool(record.get("is_boundary", False)),
        "source_rafter_id": str(record["source_rafter_id"]),
        "S": [float(value) for value in record["S"]],
        "E": [float(value) for value in record["E"]],
        "unit": [float(value) for value in record["unit"]],
        "width_axis": [float(value) for value in record["width_axis"]],
        "g": float(record.get("g", 0.0)),
        "l_SE_mm": float(record["l_SE_mm"]),
        "width_knot_indices": list(range(len(controls))),
        "I_front_edge_fiber_records": controls,
        "root_fiber_records": roots,
        "q_I_center_mm": float(item["center_q_I_mm"]),
        "q_E_center_mm": float(item["center_q_E_mm"]),
        "q_S_center_mm": float(item["center_q_S_mm"]),
        "dI_world_Z_mm": float(item["center_dI_mm"]),
        "H_I_center_mm": float(item["center_H_I_mm"]),
        "E_bottom_center_mm": float(item["center_E_bottom_mm"]),
        "E_depth_world_Z_mm": float(center["E_depth_world_Z_mm"]),
        "E_top_center_z_mm": float(item["center_E_top_mm"]),
    }


def part_metadata(parts: list[dict]) -> list[dict]:
    return [{"id": part["id"], "name": part["name"], "group": part["group"],
             "brep_face_count": shape_faces(part["shape"])}
            for part in parts]


def run_case(config_path: Path, output_dir: Path) -> int:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.json"
    summary_path = output_dir / "summary.json"
    run_started = time.perf_counter()
    run_started_utc = utc_now()
    phases: list[dict] = []
    state = {"status": "running", "case_id": None, "phases": phases,
             "mesh_attempts": [], "current_phase": "configuration"}
    error = None
    context = None
    mesh_ready_elapsed = None

    def checkpoint(status: str = "running", **extra) -> None:
        atomic_json(progress_path, {
            "status": status,
            "case_id": state.get("case_id"),
            "started_utc": run_started_utc,
            "elapsed_process_seconds": round(time.perf_counter() - PROCESS_ENTRY_PERF, 6),
            "elapsed_case_seconds": round(time.perf_counter() - run_started, 6),
            "current_phase": state.get("current_phase"),
            "phases": phases,
            "mesh_attempts": state["mesh_attempts"],
            "base_stage": state.get("base_stage"),
            "upper_rollboard": state.get("upper_rollboard"),
            "combination_preview": state.get("combination_preview"),
            "preview_rafter_count": len(state.get("flyers", [])),
            **extra,
        })

    def phase(name: str, fn):
        state["current_phase"] = name
        started = time.perf_counter()
        checkpoint()
        result = fn()
        row = {"name": name, "wall_seconds": round(time.perf_counter() - started, 6)}
        phases.append(row)
        checkpoint()
        print(f"PREVIEW_PHASE {state.get('case_id')} {name} {row['wall_seconds']:.3f}s", flush=True)
        return result

    try:
        config_path = Path(config_path).resolve()
        config = generate.load_config(config_path)
        state["case_id"] = config["case_id"]
        stage05 = generate.load_stage05()
        identity_case, identity_arc, identity_young_sagitta = generate.derived_geometry_values(
            config, stage05)
        generate.update_resolved_geometry_derivations(
            config, identity_arc, identity_young_sagitta,
            int(identity_case["fan_surface"]["n"]))
        prod_identity = generate.production_source_identity()
        geom_identity = generate.geometry_source_identity()
        config_sha = sha256(config_path)
        checkpoint("running", angle_deg=config["corner_angle_deg"], B_mm=config["parameters"]["B"],
                   config_sha256=config_sha,
                   production_source_identity_sha256=prod_identity["sha256"],
                   geometry_source_identity_sha256=geom_identity["sha256"])

        context = phase("stage04_base_and_preflight",
                        lambda: stage05.build_stage04_base(config))
        base_parts = list(context["parts"])
        base_part_ids = {part["id"] for part in base_parts}
        fan_expected = int(context["case"]["fan_geometry"]["fan_rafter_count"])
        fan_actual = sum(record["family"] == "fan" for record in context.get("chosen", []))
        if fan_actual != fan_expected:
            raise ValueError(f"missing fans: expected={fan_expected}, actual={fan_actual}")
        generate.update_resolved_geometry_derivations(
            config, context["case"]["fan_geometry"]["arc"],
            context["young_info"]["effective_carback_sagitta_mm"],
            context["case"]["fan_geometry"]["n"])
        state["base_stage"] = {
            "parts": len(base_parts), "parts_by_group": dict(Counter(p["group"] for p in base_parts)),
            "chosen_fly_records_per_side": len(context["chosen"]),
            "chosen_by_family_per_side": dict(Counter(r["family"] for r in context["chosen"])),
            "expected_fan_rafter_count_per_side": fan_expected,
            "actual_fan_rafter_count_per_side": fan_actual,
            "raw_likou_ids_preserved": sorted(pid for pid in base_part_ids if pid.startswith("likou_")),
            "lower_board_ids_preserved": sorted(pid for pid in base_part_ids if "board" in pid),
        }
        checkpoint("running")

        def build_controls():
            source_order, motherboard = stage05._build_flyer_masterboard(context)
            context["flyer_masterboard"] = motherboard
            return source_order, motherboard

        source_order, motherboard = phase("shared_flyer_control_field", build_controls)
        selected_ids = {str(item["record"]["source_rafter_id"]) for item in source_order}
        expected_ids = {str(record["source_rafter_id"]) for record in context["chosen"]}
        if selected_ids != expected_ids:
            raise ValueError("masterboard controls do not match the complete chosen-flyer identity set")

        preview_rows = []

        def build_preview_flyers():
            for item in source_order:
                record = item["record"]
                center_index = len(item["controls"]) // 2
                control, root = item["controls"][center_index], item["roots"][center_index]
                width = float(context["case"]["p"]["w_fly"])
                shape_x, preview_info = profile_side_and_extrude(
                    stage05, record, control, root, width)
                family, index = str(record["family"]), int(record["index"])
                if family == "fan":
                    pid_x = f"flying_fan_x_{index:02d}"
                    group = stage05.s4.WING_GROUP
                    name_x = f"预览立脚飞椽基准翼-{index:02d}（中心纵剖等宽挤出）"
                else:
                    pid_x = f"straight_flying_x_{index:02d}"
                    group = stage05.s4.STRAIGHT_GROUP
                    name_x = f"预览正身飞椽基准翼-{index:02d}（中心纵剖等宽挤出）"
                pid_y = pid_x.replace("_x_", "_y_")
                stage05.add(context["parts"], pid_x, name_x, group, shape_x)
                shape_y = stage05.s2.mirror_shape(context["case"], shape_x)
                stage05.add(context["parts"], pid_y, name_x.replace("基准翼", "镜像翼"), group, shape_y)
                preview_rows.append({
                    "source_rafter_id": str(record["source_rafter_id"]),
                    "family": family, "index": index, "part_ids": [pid_x, pid_y],
                    "width_mm": width,
                    "center_fiber_v_mm": float(control["v_mm"]),
                    "preview_only_profile_rule": "one shared R-I-E center-fiber side profile extruded along the actual 3D width direction",
                    "ab_side_same_geometry_guaranteed_by_rigid_extrusion": True,
                    "side_profile": preview_info,
                    "part_face_counts": {pid_x: shape_faces(shape_x), pid_y: shape_faces(shape_y)},
                    "part_volumes_mm3": {pid_x: float(shape_x.Volume()), pid_y: float(shape_y.Volume())},
                    "valid_single_solid": {pid_x: bool(shape_x.isValid() and len(shape_x.Solids()) == 1),
                                            pid_y: bool(shape_y.isValid() and len(shape_y.Solids()) == 1)},
                })
                checkpoint("running")
            return preview_rows

        phase("preview_flyer_extrusion", build_preview_flyers)
        state["flyers"] = preview_rows
        checkpoint("running")
        context["contact_fly_records"] = [lightweight_contact_detail(item) for item in source_order]

        board_result = {"status": "not_attempted", "parts_before": None, "parts_after": None,
                        "brep_face_counts": {}}

        def build_control_driven_upper_board():
            before = len(context["parts"])
            try:
                records = stage05.add_upper_rollboards(context)
                created = context["parts"][before:]
                board_result.update({
                    "status": "included_from_control_data",
                    "piece_record_count": len(records),
                    "part_ids": [part["id"] for part in created],
                    "parts_before": before,
                    "parts_after": len(context["parts"]),
                    "brep_face_counts": {part["id"]: shape_faces(part["shape"]) for part in created},
                    "parts": [{"id": part["id"], "group": part["group"],
                               "volume_mm3": float(part["shape"].Volume()),
                               "valid_single_solid": bool(part["shape"].isValid() and
                                                           len(part["shape"].Solids()) == 1)}
                              for part in created],
                    "construction_input": "17-fiber control/root records from this run; no exact flyer solids were built",
                })
                return records
            except Exception as exc:
                del context["parts"][before:]
                board_result.update({"status": "not_included_after_control_build_error",
                                     "parts_before": before,
                                     "parts_after": len(context["parts"]),
                                     "error": f"{type(exc).__name__}: {exc}",
                                     "traceback": traceback.format_exc()})
                print(f"PREVIEW_UPPER_BOARD_SKIPPED {type(exc).__name__}: {exc}", flush=True)
                return []

        phase("upper_rollboard_from_controls", build_control_driven_upper_board)
        state["upper_rollboard"] = board_result
        checkpoint("running")

        combo_skip = {"blank_shape": None, "cut_calls_bypassed": 0,
                      "intersect_calls_bypassed": 0}
        original_cut = stage05.cq.Shape.cut
        original_intersect = stage05.cq.Shape.intersect

        def skip_combination_cut(self, *to_cut, **kwargs):
            combo_skip["cut_calls_bypassed"] += 1
            if combo_skip["blank_shape"] is None:
                combo_skip["blank_shape"] = self
            return self

        def skip_combination_intersect(self, *clips, **kwargs):
            combo_skip["intersect_calls_bypassed"] += 1
            return self

        def build_uncut_combination_blank():
            stage05.cq.Shape.cut = skip_combination_cut
            stage05.cq.Shape.intersect = skip_combination_intersect
            try:
                # Reuse the production sample/circle/loft formula, while a local no-op
                # intercept preserves the lofted blank before old/young subtraction.
                stage05.make_combination_blank(context)
            finally:
                stage05.cq.Shape.cut = original_cut
                stage05.cq.Shape.intersect = original_intersect
            blank = combo_skip["blank_shape"]
            if blank is None:
                raise RuntimeError("production combination path did not reach its pre-Boolean blank")
            stage05.add(context["parts"], "combination_preview_blank",
                        "预览组合木料坯（未切老戗/嫩戗，未分菱角木/扁担木）",
                        "combination_preview_blank", blank)
            if not blank.isValid() or len(blank.Solids()) != 1 or float(blank.Volume()) <= 1e-4:
                raise ValueError("uncut combination preview blank failed BRep validity/volume checks")
            return {
                "status": "included_uncut_single_blank",
                "part_id": "combination_preview_blank",
                "brep_face_count": shape_faces(blank),
                "volume_mm3": float(blank.Volume()),
                "valid_single_solid": True,
                "old_young_boolean_cuts_run": False,
                "horizontal_split_run": False,
                "bypassed_production_calls": {
                    "cut_calls_bypassed": combo_skip["cut_calls_bypassed"],
                    "intersect_calls_bypassed": combo_skip["intersect_calls_bypassed"],
                    "pre_boolean_blank_captured": True,
                },
                "geometry_source": "The existing make_combination_blank(ctx) sampling, arc and loft formula was reused; local Shape.cut/intersect no-ops captured the lofted blank and skipped all subtraction and split Booleans.",
            }

        combo_preview = phase("combination_uncut_blank_loft", build_uncut_combination_blank)
        state["combination_preview"] = combo_preview
        checkpoint("running")

        before_mesh_parts = part_metadata(context["parts"])
        shape_to_part = {id(part["shape"]): {"id": part["id"], "group": part["group"]}
                         for part in context["parts"]}
        mesh_timing_by_part: dict[str, dict] = defaultdict(
            lambda: {"attempts": [], "wall_seconds": 0.0, "accepted_triangles": None})
        original_absolute_face_mesh = generate.absolute_face_mesh

        def timed_absolute_face_mesh(shape, tolerance, angular, requested_upper_limit=None):
            started = time.perf_counter()
            identity = shape_to_part.get(id(shape), {"id": "unmapped_shape", "group": "unknown"})
            success = False
            result = None
            caught = None
            try:
                result = original_absolute_face_mesh(
                    shape, tolerance, angular, requested_upper_limit=requested_upper_limit)
                success = bool(result[3])
                return result
            except BaseException as exc:
                caught = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                elapsed = time.perf_counter() - started
                row = mesh_timing_by_part[identity["id"]]
                attempt = {"tolerance_mm": float(tolerance),
                           "requested_upper_limit_mm": (None if requested_upper_limit is None
                                                        else float(requested_upper_limit)),
                           "wall_seconds": round(elapsed, 6), "accepted": success,
                           "error": caught}
                if result is not None:
                    report = result[2]
                    attempt["triangle_count"] = len(result[1])
                    attempt["face_count"] = len(report.get("faces", []))
                    attempt["all_faces_within_linear_limit"] = report.get("all_faces_within_linear_limit")
                    if success:
                        row["accepted_triangles"] = len(result[1])
                row["attempts"].append(attempt)
                row["wall_seconds"] += elapsed
                state["mesh_attempts"] = [
                    {"part_id": part_id, **details}
                    for part_id, details in mesh_timing_by_part.items()]
                checkpoint()

        state["current_phase"] = "absolute_mesh_export"
        mesh_started = time.perf_counter()
        checkpoint()
        generate.absolute_face_mesh = timed_absolute_face_mesh
        try:
            mesh, triangle_count = generate.export_mesh(
                context["parts"], context["control"], config, output_dir)
        finally:
            generate.absolute_face_mesh = original_absolute_face_mesh
        mesh_ready_elapsed = time.perf_counter() - PROCESS_ENTRY_PERF
        case_ready_elapsed = time.perf_counter() - run_started
        mesh_export_seconds = time.perf_counter() - mesh_started
        phases.append({"name": "absolute_mesh_export", "wall_seconds": round(mesh_export_seconds, 6)})
        print(f"PREVIEW_PHASE {state['case_id']} absolute_mesh_export {mesh_export_seconds:.3f}s", flush=True)

        mesh_triangles_by_id = {
            part["id"]: len(part["triangles"]) // 3 for part in mesh.get("parts", [])}
        group_triangles = defaultdict(int)
        id_to_group = {part["id"]: part["group"] for part in context["parts"]}
        for part_id, count in mesh_triangles_by_id.items():
            group_triangles[id_to_group.get(part_id, "unknown")] += int(count)
        final_part_rows = []
        for part in before_mesh_parts:
            part_id = part["id"]
            timing = mesh_timing_by_part.get(part_id, {})
            final_part_rows.append({
                **part,
                "mesh_triangle_count": mesh_triangles_by_id.get(part_id),
                "mesh_wall_seconds": round(float(timing.get("wall_seconds", 0.0)), 6),
                "mesh_attempts": timing.get("attempts", []),
            })
        mesh_diag = json.loads((output_dir / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
        status = "completed"
        total_wall = time.perf_counter() - PROCESS_ENTRY_PERF
        summary = {
            "status": status,
            "started_utc": PROCESS_STARTED_UTC,
            "case_started_utc": run_started_utc,
            "finished_utc": utc_now(),
            "python_process_cold_start_to_mesh_ready_seconds": round(mesh_ready_elapsed, 6),
            "case_work_to_mesh_ready_seconds": round(case_ready_elapsed, 6),
            "mesh_ready_wall_seconds_from_process_entry": round(mesh_ready_elapsed, 6),
            "total_process_wall_seconds": round(total_wall, 6),
            "config": {"path": str(config_path), "sha256": config_sha, "normalized": config},
            "production_source_identity_sha256": prod_identity["sha256"],
            "production_source_file_count": len(prod_identity["files"]),
            "geometry_source_identity_sha256": geom_identity["sha256"],
            "probe_source_sha256": sha256(Path(__file__)),
            "probe_source_path": str(Path(__file__).resolve()),
            "preview_scope": {
                "stage04_recomputed_from_current_config": True,
                "missing_fan_guard": "passed",
                "expected_fan_rafter_count_per_side": fan_expected,
                "actual_fan_rafter_count_per_side": fan_actual,
                "selected_preview_rafter_count_per_side": len(source_order),
                "selected_preview_rafter_count_both_sides": 2 * len(source_order),
                "stage04_base_and_raw_likou_preserved": True,
                "likou_notches_cut": False,
                "flyer_model": "one center-fiber R/I/E side profile rigidly extruded along the actual 3D width direction; exact A/B face profile is identical by construction",
                "full_width_flyer_fit_preserved": False,
                "upper_rollboard": board_result,
                "combination_blank": combo_preview,
                "combination_split_into_lingjiao_biandan": False,
                "combination_old_young_cut": False,
                "not_an_exact_geometry_acceptance": True,
            },
            "phases": phases,
            "base_stage": state["base_stage"],
            "flyers": preview_rows,
            "parts_before_mesh": final_part_rows,
            "mesh_export": {
                "used_original_generate_export_mesh": True,
                "absolute_face_mesh_guard_preserved": True,
                "mesh_tessellation_mm": config["advanced"]["mesh_tessellation_mm"],
                "mesh_angular_tolerance_rad": config["advanced"]["mesh_angular_tolerance_rad"],
                "mesh_ready": True,
                "model_mesh_path": str(output_dir / "model_mesh.json"),
                "model_obj_path": str(output_dir / "model.obj"),
                "diagnostics_path": str(output_dir / "mesh_export_diagnostics.json"),
                "part_count": len(mesh.get("parts", [])),
                "brep_face_count_total": sum(part["brep_face_count"] for part in before_mesh_parts),
                "triangle_count_total": int(triangle_count),
                "triangle_count_by_group": dict(group_triangles),
                "mesh_wall_seconds_by_group": {
                    group: round(sum(float(mesh_timing_by_part.get(part["id"], {}).get("wall_seconds", 0.0))
                                     for part in context["parts"] if part["group"] == group), 6)
                    for group in sorted({part["group"] for part in context["parts"]})},
                "export_mesh_wall_seconds": round(mesh_export_seconds, 6),
                "native_mesh_guard_summary": mesh_diag.get("summary"),
            },
            "limitations": [
                "The preview intentionally uses one v=0 profile for both A/B sides and therefore loses the full-width curvature and fit.",
                "Likou notches and old/young combination cuts are omitted; the combination member is the uncut stock blank and remains one BRep.",
                "The control-derived upper rollboard is included when its builder accepts the freshly computed rails; it does not validate contact with the simplified flyers.",
                "This diagnostic measures geometry and original mesh export only, not viewer page generation, startup UI, rendering, or product integration.",
            ],
            "error": None,
        }
        atomic_json(summary_path, summary)
        state["status"] = status
        state["current_phase"] = "complete"
        checkpoint(status)
        print(f"PREVIEW_DONE {state['case_id']} cold_to_mesh={mesh_ready_elapsed:.3f}s "
              f"parts={len(context['parts'])} triangles={triangle_count}", flush=True)
        return 0
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc),
                 "traceback": traceback.format_exc()}
        status = "failed"
        print(f"PREVIEW_ERROR {state.get('case_id')} {type(exc).__name__}: {exc}", flush=True)
        summary = {
            "status": status,
            "started_utc": PROCESS_STARTED_UTC,
            "case_started_utc": run_started_utc,
            "finished_utc": utc_now(),
            "python_process_elapsed_seconds": round(time.perf_counter() - PROCESS_ENTRY_PERF, 6),
            "config_path": str(Path(config_path).resolve()) if "config_path" in locals() else None,
            "probe_source_sha256": sha256(Path(__file__)),
            "phases": phases,
            "base_stage": state.get("base_stage"),
            "upper_rollboard": state.get("upper_rollboard"),
            "combination_preview": state.get("combination_preview"),
            "mesh_attempts": state.get("mesh_attempts"),
            "error": error,
        }
        atomic_json(summary_path, summary)
        state["status"] = status
        state["current_phase"] = "failed"
        checkpoint(status, error=error)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return run_case(args.config, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
