from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


GENERATOR = Path(__file__).resolve().parents[2]
DIAGNOSTICS = Path(__file__).resolve().parent
CHECKPOINT = DIAGNOSTICS / "exact_120_cachefix_geometry_checkpoint"
CACHE = DIAGNOSTICS / "mesh_cache" / "preview_120_cachefix"
OUTPUT = GENERATOR / "outputs" / "workbench_preview_implementation_20261030" / "exact_120_cachehit_export_probe"
SUMMARY = DIAGNOSTICS / "exact_120_cachehit_export_probe.summary.json"
STDOUT = DIAGNOSTICS / "exact_120_cachehit_export_probe.stdout.log"
STDERR = DIAGNOSTICS / "exact_120_cachehit_export_probe.stderr.log"
TIMEOUT_SECONDS = 45


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, document) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def worker() -> dict:
    sys.path.insert(0, str(GENERATOR))
    import generate

    config = generate.load_config(GENERATOR / "configs" / "angle_120.json")
    stage05 = generate.load_stage05()
    case, arc, young_sagitta = generate.derived_geometry_values(config, stage05)
    generate.update_resolved_geometry_derivations(
        config, arc, young_sagitta, int(case["fan_surface"]["n"]))

    started = time.perf_counter()
    restored = generate.restore_geometry_checkpoint(CHECKPOINT, config, mode="exact")
    restore_seconds = time.perf_counter() - started
    OUTPUT.mkdir(parents=True)
    mesh_started = time.perf_counter()
    mesh, triangle_count = generate.export_mesh(
        restored["parts"], restored["control"], config, OUTPUT,
        mode="exact", mesh_cache_dir=CACHE)
    export_seconds = time.perf_counter() - mesh_started
    diagnostic = json.loads((OUTPUT / "mesh_export_diagnostics.json").read_text(encoding="utf-8"))
    return {"case_id": "angle_120_B400", "mode": "exact", "parts": len(mesh["parts"]),
            "triangle_count": triangle_count,
            "checkpoint_restore_seconds": restore_seconds,
            "mesh_export_wall_seconds": export_seconds,
            "mesh_absolute_remesh_seconds": mesh["metadata"]["mesh_absolute_remesh_seconds"],
            "cache_summary": diagnostic["summary"],
            "accepted_tolerance_mm_by_part": diagnostic["summary"]["accepted_tolerance_mm_by_part"],
            "output_dir": str(OUTPUT.resolve()),
            "checkpoint_dir": str(CHECKPOINT.resolve()),
            "cache_dir": str(CACHE.resolve()),
            "source_identity": generate.current_source_identity_record()}


def main() -> int:
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(), ensure_ascii=False, allow_nan=False))
        return 0

    if not CHECKPOINT.is_dir() or not (CHECKPOINT / "checkpoint.json").is_file():
        raise SystemExit(f"Verified checkpoint is missing: {CHECKPOINT}")
    if not CACHE.is_dir() or not any(CACHE.glob("*.json.gz")):
        raise SystemExit(f"Preseeded mesh cache is missing: {CACHE}")
    if OUTPUT.exists() or SUMMARY.exists():
        raise SystemExit(f"Refusing to overwrite an existing probe result: {OUTPUT}")
    started_at = utc_now()
    start = time.perf_counter()
    command = [sys.executable, "-X", "utf8", "-u", str(Path(__file__).resolve()), "--worker"]
    try:
        result = subprocess.run(command, cwd=GENERATOR, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        result = subprocess.CompletedProcess(command, 124,
                                             stdout=exc.stdout or "",
                                             stderr=exc.stderr or "")
        timed_out = True
    wall = time.perf_counter() - start
    STDOUT.write_text(result.stdout or "", encoding="utf-8")
    STDERR.write_text(result.stderr or "", encoding="utf-8")
    summary = {"started_at": started_at, "finished_at": utc_now(),
               "wall_seconds": wall, "timeout_seconds": TIMEOUT_SECONDS,
               "timed_out": timed_out, "return_code": result.returncode,
               "command": command, "stdout_log": str(STDOUT.resolve()),
               "stderr_log": str(STDERR.resolve())}
    if result.returncode == 0:
        payload = json.loads((result.stdout or "{}").splitlines()[-1])
        summary.update(payload)
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return 0 if result.returncode == 0 and not timed_out else 2


if __name__ == "__main__":
    raise SystemExit(main())
