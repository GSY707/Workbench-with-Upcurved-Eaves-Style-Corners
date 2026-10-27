#!/usr/bin/env python
"""Run only the 90-degree B400 stage04 geometry preflight."""
from __future__ import annotations

import importlib.util
import json
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

import config as config_module

spec = importlib.util.spec_from_file_location("angle_90_preflight_generate", HERE / "generate.py")
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)


def main():
    out = DIAG / "angle_90_B400_preflight_result.json"
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing preflight record: {out}")
    started = datetime.now(timezone.utc).isoformat()
    clock_start = time.perf_counter()
    source_before = generate.production_source_identity()
    config = config_module.resolve_config({
        "case_id": "angle_90_B400_preflight",
        "corner_angle_deg": 90.0,
        "parameters": {"B": 400.0},
    })
    stage05 = generate.load_stage05()
    ctx = stage05.s4.build_base_and_preflight(config)
    source_after = generate.production_source_identity()
    if source_before["sha256"] != source_after["sha256"]:
        raise RuntimeError("production source changed during 90-degree preflight")
    expected = int(ctx["case"]["fan_geometry"]["fan_rafter_count"])
    actual = sum(record.get("family") == "fan" for record in ctx.get("chosen", []))
    report = {
        "case_id": config["case_id"],
        "corner_angle_deg": float(config["corner_angle_deg"]),
        "B_mm": float(config["parameters"]["B"]),
        "source_identity_sha256": source_before["sha256"],
        "source_identity_unchanged": True,
        "source_files": source_before["files"],
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - clock_start,
        "scope": "stage04 build_base_and_preflight only; not full CLI or visual/model acceptance",
        "base_part_count": len(ctx.get("parts", [])),
        "expected_fan_count_per_side": expected,
        "selected_fan_count_per_side": actual,
        "selected_record_count": len(ctx.get("chosen", [])),
        "pose_failures": ctx.get("pose_failures") or [],
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                   encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "source_identity_sha256", "elapsed_seconds", "base_part_count",
        "expected_fan_count_per_side", "selected_fan_count_per_side",
        "selected_record_count", "pose_failures")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
