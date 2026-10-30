"""Collect saved-run timings and runtime capabilities; never build CAD geometry."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATOR = HERE.parents[1]
spec = importlib.util.spec_from_file_location("performance_baseline_generator", GENERATOR / "generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def read(relative):
    return json.loads((GENERATOR / relative).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect():
    import cadquery
    import OCP
    from OCP.OSD import OSD_Parallel, OSD_ThreadPool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    pool = OSD_ThreadPool.DefaultPool_s()
    identity = generator.production_source_identity()
    runtime = {
        "python": sys.version,
        "cadquery": cadquery.__version__,
        "ocp": OCP.__version__,
        "occt_thread_pool_size": pool.NbThreads(),
        "occt_default_threads_to_launch": pool.NbDefaultThreadsToLaunch(),
        "occt_uses_builtin_threads": OSD_Parallel.ToUseOcctThreads_s(),
        "mesher_constructor_doc": BRepMesh_IncrementalMesh.__init__.__doc__,
    }
    cases = []
    configurations = [
        ("60", "outputs/angle_60_B400_run06", "diagnostics/non_right_angle/angle_60_B400_run06_cli_provenance.json"),
        ("120", "outputs/angle_120_B400_run01", "diagnostics/non_right_angle/angle_120_B400_run01_cli_provenance.json"),
        ("90_previous_version", "outputs/workbench/case-b0257e3b87884f2a88137a283f3a38da", None),
    ]
    for label, relative, provenance_path in configurations:
        validation = read(f"{relative}/validation.json")
        mesh = read(f"{relative}/model_mesh.json")
        mesh_diagnostics = read(f"{relative}/mesh_export_diagnostics.json")
        phases = validation["phase_timings_seconds"]
        provenance = read(provenance_path) if provenance_path else None
        # The older workbench value is its published start-to-success wall time.
        total = provenance["elapsed_seconds"] if provenance else 410.497
        flyers = phases["stage05_flyer_build"]
        combination = phases["stage05_combination_rebuild"]
        notch = phases["stage05_likou_cut_and_mirror"]
        export = phases["mesh_remesh_and_export"]
        hot = flyers + combination + notch
        triangles_by_group = Counter()
        for part in mesh["parts"]:
            triangles_by_group[part["group"]] += len(part["triangles"]) // 3
        exclusive = {k: v for k, v in phases.items()
                     if k not in {"geometry_total_after_stage04", "mesh_absolute_remesh_only"}}
        row = {
            "label": label,
            "output_path": relative,
            "total_wall_seconds": total,
            "total_wall_basis": "external CLI process including startup; no rendering" if provenance else "saved workbench job start-to-success including rendering",
            "internal_runtime_including_render": validation["runtime_seconds_including_render"],
            "phase_timings_seconds_original": phases,
            "exclusive_phase_sum_seconds": sum(exclusive.values()),
            "wall_minus_exclusive_phase_sum_seconds": total - sum(exclusive.values()),
            "phase_percent_of_wall": {k: v / total * 100 for k, v in exclusive.items()},
            "three_hot_phases_percent": hot / total * 100,
            "entity_count": len(mesh["parts"]),
            "triangle_count": sum(triangles_by_group.values()),
            "triangles_by_group": dict(triangles_by_group),
            "mesh_attempt_histogram": dict(Counter(len(p["attempts"]) for p in mesh_diagnostics["parts"])),
            "mesh_total_attempts": sum(len(p["attempts"]) for p in mesh_diagnostics["parts"]),
            "mesh_retry_part_count": sum(len(p["attempts"]) > 1 for p in mesh_diagnostics["parts"]),
            "likou_performance": validation["stage05"]["likou_notch_performance"],
            "flyer_physical_member_band_count": validation["stage05"]["flyer_masterboard"]["physical_member_band_count"],
            "flyer_local_field_patch_count": validation["stage05"]["flyer_masterboard"]["local_field_patch_count"],
            "source_identity_matches_current": identity["sha256"] == provenance["source_identity_sha256"] if provenance else None,
            "historical_source_files_match_current": {e["path"]: digest(GENERATOR / e["path"]) == e["sha256"] for e in provenance["source_files"]} if provenance else None,
            "source_artifact_sha256": {name: digest(GENERATOR / relative / name)
                                       for name in ["validation.json", "model_mesh.json", "mesh_export_diagnostics.json"]},
            "conditional_estimates_not_measurements": {
                "flyer_and_combination_halved_wall_seconds": total - (flyers + combination) / 2,
                "flyer_and_combination_halved_total_speedup": total / (total - (flyers + combination) / 2),
                "flyer_only_4x_ideal_wall_seconds": total - flyers * .75,
                "flyer_only_4x_ideal_total_speedup": total / (total - flyers * .75),
                "mesh_export_2x_wall_saving_seconds": export / 2,
                "mesh_export_2x_total_time_reduction_percent": export / 2 / total * 100,
                "mesh_export_zero_cost_impossible_upper_bound_percent": export / total * 100,
                "remove_second_side_boolean_gross_saving_seconds": validation["stage05"]["likou_notch_performance"]["y_boolean_seconds"],
                "three_hot_phases_halved_wall_seconds": total - hot / 2,
                "three_hot_phases_halved_total_speedup": total / (total - hot / 2),
                "combination_only_parameter_change_ideal_wall_seconds": total - phases["stage04_base_and_preflight"] - flyers - notch - phases["stage05_upper_board_rebuild"],
                "combination_only_parameter_change_ideal_total_speedup": total / (total - phases["stage04_base_and_preflight"] - flyers - notch - phases["stage05_upper_board_rebuild"]),
            },
        }
        cases.append(row)
    return {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Saved successful run statistics and runtime introspection only; no CAD generation or speedup experiment.",
        "source_identity": identity,
        "runtime": runtime,
        "hardware_observed_2026_10_29": {
            "cpu": "13th Gen Intel(R) Core(TM) i9-13900HX",
            "physical_cores": 24,
            "logical_processors": 32,
            "physical_memory_bytes": 34081574912,
            "gpu": "NVIDIA GeForce RTX 4070 Laptop GPU",
            "gpu_memory_mib": 8188,
            "driver": "610.74",
            "evidence": "Root Get-CimInstance Win32_Processor/Win32_ComputerSystem and nvidia-smi observations in this task; not re-queried by this collector.",
        },
        "cases": cases,
        "accounting_notes": [
            "geometry_total_after_stage04 is the sum of four stage05 fields, not another stage.",
            "mesh_absolute_remesh_only is nested within mesh_remesh_and_export; its timer also covers face extraction and guards, not only native mesher computation.",
            "likou mirror_pair_check_seconds spans booleans and checks; do not sum it with x/y boolean fields.",
            "Different angles change geometry and part counts; rows are not optimization A/B comparisons.",
            "All speedup scenarios are conditional arithmetic, ignore added scheduling/validation/serialization costs, and are not measured optimized performance.",
        ],
    }


if __name__ == "__main__":
    result = collect()
    destination = HERE / "baseline_analysis.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), "runtime": {k: v for k, v in result["runtime"].items() if k != "mesher_constructor_doc"},
                      "cases": [{k: r[k] for k in ["label", "total_wall_seconds", "three_hot_phases_percent", "mesh_total_attempts", "source_identity_matches_current", "conditional_estimates_not_measurements"]} for r in result["cases"]]}, ensure_ascii=False, indent=2))
