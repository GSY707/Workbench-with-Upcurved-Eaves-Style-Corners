#!/usr/bin/env python
"""Bounded, non-exporting profile of the production 120-degree geometry path."""
from __future__ import annotations

import cProfile
import datetime as dt
import hashlib
import inspect
import json
import os
import pstats
import sys
import time
import traceback
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
GENERATOR_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(GENERATOR_DIR))

import generate  # noqa: E402


CONFIG_PATH = GENERATOR_DIR / "configs" / "angle_120.json"
PROFILE_PATH = OUT_DIR / "geometry.prof"
SUMMARY_PATH = OUT_DIR / "profile_summary.json"
PROGRESS_PATH = OUT_DIR / "progress.json"
TOP_PATH = OUT_DIR / "profile_top.txt"
PROBE_LIMIT_SECONDS = 350.0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_cad_timers(stage05) -> tuple[list[dict], list[tuple[type, str, object]]]:
    """Wrap CadQuery public API boundaries, preserving their descriptors."""
    counters: list[dict] = []
    originals: list[tuple[type, str, object]] = []
    targets = [
        (stage05.cq.Shape, "cut"),
        (stage05.cq.Shape, "fuse"),
        (stage05.cq.Shape, "intersect"),
        (stage05.cq.Shape, "clean"),
        (stage05.cq.Shape, "tessellate"),
        (stage05.cq.Solid, "makeLoft"),
        (stage05.cq.Solid, "makeSolid"),
        (stage05.cq.Face, "makeRuledSurface"),
        (stage05.cq.Wire, "makePolygon"),
    ]
    for owner, method_name in targets:
        descriptor = inspect.getattr_static(owner, method_name)
        counter = {"operation": f"{owner.__name__}.{method_name}",
                   "calls": 0, "wall_seconds": 0.0, "failures": 0}
        counters.append(counter)
        originals.append((owner, method_name, descriptor))
        if isinstance(descriptor, classmethod):
            original = descriptor.__func__

            def wrapped(cls, *args, __original=original, __counter=counter, **kwargs):
                started = time.perf_counter()
                __counter["calls"] += 1
                try:
                    return __original(cls, *args, **kwargs)
                except BaseException:
                    __counter["failures"] += 1
                    raise
                finally:
                    __counter["wall_seconds"] += time.perf_counter() - started

            setattr(owner, method_name, classmethod(wrapped))
        elif isinstance(descriptor, staticmethod):
            original = descriptor.__func__

            def wrapped(*args, __original=original, __counter=counter, **kwargs):
                started = time.perf_counter()
                __counter["calls"] += 1
                try:
                    return __original(*args, **kwargs)
                except BaseException:
                    __counter["failures"] += 1
                    raise
                finally:
                    __counter["wall_seconds"] += time.perf_counter() - started

            setattr(owner, method_name, staticmethod(wrapped))
        else:
            original = descriptor

            def wrapped(self, *args, __original=original, __counter=counter, **kwargs):
                started = time.perf_counter()
                __counter["calls"] += 1
                try:
                    return __original(self, *args, **kwargs)
                except BaseException:
                    __counter["failures"] += 1
                    raise
                finally:
                    __counter["wall_seconds"] += time.perf_counter() - started

            setattr(owner, method_name, wrapped)
    return counters, originals


def restore_cad_methods(originals: list[tuple[type, str, object]]) -> None:
    for owner, method_name, descriptor in reversed(originals):
        setattr(owner, method_name, descriptor)


def snapshot_counters(counters: list[dict]) -> list[dict]:
    return [{**item, "wall_seconds": round(float(item["wall_seconds"]), 6)}
            for item in counters]


def cprofile_functions(profiler: cProfile.Profile, limit: int = 60) -> list[dict]:
    stats = pstats.Stats(profiler)
    rows = []
    ordered = sorted(stats.stats.items(), key=lambda pair: pair[1][3], reverse=True)
    for (filename, lineno, function), (primitive, calls, self_seconds,
                                       cumulative_seconds, _callers) in ordered[:limit]:
        rows.append({
            "file": filename,
            "line": int(lineno),
            "function": function,
            "primitive_calls": int(primitive),
            "calls": int(calls),
            "self_seconds": round(float(self_seconds), 6),
            "cumulative_seconds": round(float(cumulative_seconds), 6),
        })
    return rows


def write_profile_text(profiler: cProfile.Profile) -> None:
    stats = pstats.Stats(profiler, stream=open(TOP_PATH, "w", encoding="utf-8"))
    stats.strip_dirs().sort_stats("cumulative").print_stats(80)
    stats = pstats.Stats(profiler, stream=open(TOP_PATH, "a", encoding="utf-8"))
    stats.strip_dirs().sort_stats("tottime").print_stats(40)


def main() -> int:
    started_utc = utc_now()
    probe_started = time.perf_counter()
    geometry_started = None
    stages: list[dict] = []
    profiler = cProfile.Profile()
    cad_counters: list[dict] = []
    cad_originals: list[tuple[type, str, object]] = []
    ctx = None
    stage05 = None
    status = "running"
    error = None
    source_identity = None

    try:
        if not CONFIG_PATH.is_file():
            raise FileNotFoundError(f"missing representative config: {CONFIG_PATH}")
        config = generate.load_config(CONFIG_PATH)
        if float(config["corner_angle_deg"]) != 120.0 or float(config["parameters"]["B"]) != 400.0:
            raise RuntimeError("representative config must be angle=120 and B=400")

        stage05 = generate.load_stage05()
        identity_case, identity_arc, identity_young_sagitta = generate.derived_geometry_values(
            config, stage05)
        generate.update_resolved_geometry_derivations(
            config, identity_arc, identity_young_sagitta,
            int(identity_case["fan_surface"]["n"]))
        source_identity = generate.geometry_source_identity()
        tracked_files = [GENERATOR_DIR / name for name in (
            "generate.py", "engine/stage02_core.py", "engine/stage03_core.py",
            "engine/stage04_core.py", "engine/stage05_core.py", "engine/config.py")]
        cad_counters, cad_originals = install_cad_timers(stage05)

        atomic_json(PROGRESS_PATH, {
            "status": "ready", "started_utc": started_utc,
            "config": {"path": str(CONFIG_PATH), "case_id": config["case_id"],
                       "corner_angle_deg": config["corner_angle_deg"],
                       "B_mm": config["parameters"]["B"],
                       "fan_division_count_n": config["derived"]["fan_division_count_n"],
                       "fan_count": config["derived"]["fan_count"]},
            "geometry_source_identity_sha256": source_identity["sha256"],
            "tracked_sha256": {p.relative_to(GENERATOR_DIR).as_posix(): sha256(p)
                               for p in tracked_files},
            "completed_stages": stages,
        })

        profiler.enable()
        geometry_started = time.perf_counter()

        def record_stage(name, function):
            stage_started = time.perf_counter()
            before_counts = snapshot_counters(cad_counters)
            result = function()
            duration = time.perf_counter() - stage_started
            stage = {
                "name": name,
                "wall_seconds": round(duration, 6),
                "elapsed_geometry_seconds": round(time.perf_counter() - geometry_started, 6),
                "parts_after": len(ctx["parts"]) if ctx else None,
                "cad_ops_before": before_counts,
                "cad_ops_after": snapshot_counters(cad_counters),
            }
            if name == "stage04_base_and_preflight":
                stage["chosen_records"] = len(ctx.get("chosen", []))
                stage["chosen_by_family"] = {
                    family: sum(r["family"] == family for r in ctx.get("chosen", []))
                    for family in ("fan", "straight")}
                stage["stage03_mesh_parts"] = len(ctx.get("stage03_mesh", {}).get("parts", []))
            elif name == "stage05_flyer_build":
                stage["fly_record_count"] = len(result)
                stage["width_ruled_band_count_total"] = sum(
                    int(r.get("width_ruled_band_count", 0)) for r in result)
                stage["fiber_count_total"] = sum(
                    len(r.get("I_front_edge_fiber_records", [])) for r in result)
            elif name == "stage05_likou_cut_and_mirror":
                stage["notch_performance"] = ctx.get("likou_notch_performance")
            elif name == "stage05_upper_board_rebuild":
                stage["upper_board_piece_count"] = len(result)
                stage["upper_board_details"] = [
                    {key: piece.get(key) for key in (
                        "longitudinal_section_count", "width_control_point_count_per_section",
                        "board_piece_count_per_wing", "normal_offset_vertical_range_mm")}
                    for piece in result]
            elif name == "stage05_combination_rebuild":
                stage["combination_info"] = result
            stages.append(stage)
            atomic_json(PROGRESS_PATH, {
                "status": "running", "started_utc": started_utc,
                "elapsed_geometry_seconds": round(time.perf_counter() - geometry_started, 6),
                "completed_stages": stages,
            })
            print(f"PROBE_STAGE {name} {duration:.3f}s", flush=True)
            if time.perf_counter() - geometry_started > PROBE_LIMIT_SECONDS:
                raise TimeoutError(f"geometry probe exceeded internal {PROBE_LIMIT_SECONDS:g}s limit")
            return result

        def build_base():
            nonlocal ctx
            ctx = stage05.build_stage04_base(config)
            return ctx

        ctx = record_stage("stage04_base_and_preflight", build_base)
        if ctx is None:
            raise RuntimeError("stage04 base did not return its context")
        generate.update_resolved_geometry_derivations(
            config, ctx["case"]["fan_geometry"]["arc"],
            ctx["young_info"]["effective_carback_sagitta_mm"],
            ctx["case"]["fan_geometry"]["n"])
        fly_records = record_stage("stage05_flyer_build", lambda: stage05.add_contact_flyers(ctx))
        record_stage("stage05_likou_cut_and_mirror", lambda: stage05.cut_contact_likou_notches(ctx))
        record_stage("stage05_upper_board_rebuild", lambda: stage05.add_upper_rollboards(ctx))
        record_stage("stage05_combination_rebuild", lambda: stage05.add_combination(ctx))
        if not fly_records:
            raise RuntimeError("stage05 produced no flyer records")
        status = "completed"
    except BaseException as exc:  # Record partial evidence before returning a nonzero exit.
        status = "timed_out" if isinstance(exc, TimeoutError) else "failed"
        error = {"type": type(exc).__name__, "message": str(exc),
                 "traceback": traceback.format_exc()}
        print(f"PROBE_ERROR {type(exc).__name__}: {exc}", flush=True)
    finally:
        profiler.disable()
        if cad_originals:
            restore_cad_methods(cad_originals)
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(str(PROFILE_PATH))
        write_profile_text(profiler)
        total_geometry_seconds = (time.perf_counter() - geometry_started
                                  if geometry_started is not None else 0.0)
        summary = {
            "status": status,
            "started_utc": started_utc,
            "finished_utc": utc_now(),
            "probe_process_wall_seconds": round(time.perf_counter() - probe_started, 6),
            "geometry_wall_seconds": round(total_geometry_seconds, 6),
            "internal_geometry_limit_seconds": PROBE_LIMIT_SECONDS,
            "scope": "build_stage04_base -> add_contact_flyers -> cut_contact_likou_notches -> add_upper_rollboards -> add_combination; no mesh export, BRep checkpoint, rendering, or saved full model",
            "config": {"path": str(CONFIG_PATH), "sha256": sha256(CONFIG_PATH),
                       "normalized": config if "config" in locals() else None},
            "geometry_source_identity_sha256": source_identity["sha256"] if source_identity else None,
            "tracked_sha256": ({p.relative_to(GENERATOR_DIR).as_posix(): sha256(p)
                                for p in tracked_files}
                               if "tracked_files" in locals() else {}),
            "probe_source_sha256": sha256(Path(__file__)),
            "stages": stages,
            "cad_operation_wall_counters": snapshot_counters(cad_counters),
            "cprofile_top_by_cumulative": cprofile_functions(profiler, 80),
            "cprofile_total_calls": pstats.Stats(profiler).total_calls,
            "cprofile_primitive_calls": pstats.Stats(profiler).prim_calls,
            "cprofile_note": "cProfile observes Python call frames only; native OCP time runs inside CadQuery Python wrappers and appears as their self-time. Timed CadQuery API wrappers provide wall-time samples for selected native-heavy calls.",
            "error": error,
        }
        atomic_json(SUMMARY_PATH, summary)
        atomic_json(PROGRESS_PATH, {
            "status": status,
            "started_utc": started_utc,
            "finished_utc": summary["finished_utc"],
            "elapsed_geometry_seconds": summary["geometry_wall_seconds"],
            "completed_stages": stages,
            "error": error,
        })
        print(f"PROBE_DONE status={status} geometry={total_geometry_seconds:.3f}s", flush=True)
    return 0 if status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
