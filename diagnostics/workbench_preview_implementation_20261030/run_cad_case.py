from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


GENERATOR = Path(__file__).resolve().parents[2]
DIAGNOSTICS = Path(__file__).resolve().parent
OUTPUTS = GENERATOR / "outputs" / "workbench_preview_implementation_20261030"
CONFIGS = {
    "preview_60": ("configs/angle_60.json", "preview", 180),
    "preview_90": ("configs/default.json", "preview", 180),
    "preview_120": ("configs/angle_120.json", "preview", 180),
    "exact_120": ("configs/angle_120.json", "exact", 480),
    "preview_90_B400": ("diagnostics/preview_performance_20261029/angle_90_B400.json",
                        "preview", 90),
    "preview_120_cachefix": ("configs/angle_120.json", "preview", 180),
    "exact_120_cachefix": ("configs/angle_120.json", "exact", 360),
}
CACHE_IDS = {
    "exact_120": "preview_120",
    "exact_120_cachefix": "preview_120_cachefix",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def summarize(case_id: str, mode: str, output_dir: Path, cache_dir: Path,
              started_at: str, wall_seconds: float, return_code: int | None,
              timed_out: bool, stdout_path: Path, stderr_path: Path) -> dict:
    record = {"case_id": case_id, "mode": mode, "started_at": started_at,
              "wall_seconds_startup_to_exit": wall_seconds,
              "return_code": return_code, "timed_out": timed_out,
              "output_dir": str(output_dir.resolve()),
              "cache_dir": str(cache_dir.resolve()),
              "stdout_log": str(stdout_path.resolve()),
              "stderr_log": str(stderr_path.resolve()),
              "cache_entry_count": len(list(cache_dir.glob("*.json.gz")))
                  if cache_dir.is_dir() else 0}
    validation_path = output_dir / "validation.json"
    mesh_path = output_dir / "model_mesh.json"
    resolved_path = output_dir / "parameters_resolved.json"
    if validation_path.is_file() and mesh_path.is_file() and resolved_path.is_file():
        validation = load_json(validation_path)
        mesh = load_json(mesh_path)
        resolved = load_json(resolved_path)
        metadata = mesh.get("metadata", {})
        validation_mesh = validation.get("mesh_export", {})
        summary = metadata.get("mesh_cache_summary") or validation_mesh.get("diagnostic_summary") or {}
        fan_entities = [item for item in validation.get("entities", {}).get("items", [])
                        if str(item.get("id", "")).startswith("flying_fan_")]
        record.update({
            "result_status": validation.get("status", "exact" if validation.get("passed") else "unknown"),
            "validated": validation.get("passed"),
            "exact_acceptance": validation.get("exact_acceptance"),
            "validation_scope": validation.get("validation_scope"),
            "parts": len(mesh.get("parts", [])),
            "triangle_count": sum(len(part.get("triangles", [])) // 3 for part in mesh.get("parts", [])),
            "mesh_deflection_requested_mm": metadata.get("mesh_requested_linear_deflection_mm"),
            "mesh_deflection_reported_max_mm": metadata.get("mesh_effective_max_face_deflection_mm"),
            "mesh_wall_seconds": metadata.get("mesh_absolute_remesh_seconds"),
            "mesh_export_phase_seconds": (validation.get("phase_timings_seconds") or {}).get(
                "mesh_remesh_and_export"),
            "startup_to_mesh_delivery_seconds": validation.get("runtime_seconds_before_render"),
            "generator_runtime_including_render_seconds": validation.get(
                "runtime_seconds_including_render"),
            "render_seconds": (validation.get("phase_timings_seconds") or {}).get("rendering", 0.0),
            "phase_timings_seconds": validation.get("phase_timings_seconds"),
            "cache_summary": summary,
            "source_identity": resolved.get("source_identity"),
            "flyer_face_counts": [item.get("brep_face_count") for item in fan_entities],
            "flyer_face_count_values": sorted({item.get("brep_face_count") for item in fan_entities}),
        })
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file():
        record["manifest_mode"] = load_json(manifest_path).get("mode", "exact")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", choices=tuple(CONFIGS))
    args = parser.parse_args()

    config_rel, mode, timeout_seconds = CONFIGS[args.case_id]
    output_dir = OUTPUTS / args.case_id
    cache_id = CACHE_IDS.get(args.case_id, args.case_id)
    cache_dir = DIAGNOSTICS / "mesh_cache" / cache_id
    stdout_path = DIAGNOSTICS / f"{args.case_id}.stdout.log"
    stderr_path = DIAGNOSTICS / f"{args.case_id}.stderr.log"
    summary_path = DIAGNOSTICS / f"{args.case_id}.summary.json"
    progress_path = DIAGNOSTICS / f"{args.case_id}.progress.json"
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {output_dir}")
    if mode == "preview" and cache_dir.exists() and any(cache_dir.iterdir()):
        raise SystemExit(f"Refusing to call a non-empty cache cold: {cache_dir}")
    output_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-X", "utf8", "-u", str(GENERATOR / "generate.py"),
               "--config", str(GENERATOR / config_rel), "--output", str(output_dir),
               "--mode", mode, "--mesh-cache-dir", str(cache_dir)]
    started_at = utc_now()
    start = time.perf_counter()
    write_json(progress_path, {"case_id": args.case_id, "mode": mode,
                               "status": "running", "started_at": started_at,
                               "timeout_seconds": timeout_seconds, "command": command,
                               "output_dir": str(output_dir.resolve()),
                               "cache_dir": str(cache_dir.resolve())})
    return_code = None
    timed_out = False
    with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, \
            stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
        process = subprocess.Popen(command, cwd=GENERATOR, stdout=stdout, stderr=stderr)
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            return_code = process.wait()
    wall_seconds = time.perf_counter() - start
    result = summarize(args.case_id, mode, output_dir, cache_dir, started_at,
                       wall_seconds, return_code, timed_out, stdout_path, stderr_path)
    result["finished_at"] = utc_now()
    result["command"] = command
    write_json(summary_path, result)
    write_json(progress_path, {"case_id": args.case_id, "mode": mode,
                               "status": "timed_out" if timed_out else
                                        "succeeded" if return_code == 0 else "failed",
                               "started_at": started_at, "finished_at": result["finished_at"],
                               "wall_seconds": wall_seconds,
                               "return_code": return_code,
                               "summary": str(summary_path.resolve())})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if return_code == 0 and not timed_out else 2


if __name__ == "__main__":
    raise SystemExit(main())
