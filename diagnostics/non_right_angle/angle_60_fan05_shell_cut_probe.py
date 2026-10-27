#!/usr/bin/env python
"""Build the source-defined fan-05 slot shell and cut only the original likou_x."""
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

spec = importlib.util.spec_from_file_location("angle_probe_generate", HERE / "generate.py")
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)


def main():
    started = datetime.now(timezone.utc).isoformat()
    clock_start = time.perf_counter()
    source_before = generate.production_source_identity()
    config = generate.load_config(HERE / "configs" / "angle_60.json")
    stage05 = generate.load_stage05()
    ctx = stage05.build_stage04_base(config)
    source_order, _ = stage05._build_flyer_masterboard(ctx)
    record = next(r for r in ctx["chosen"]
                  if r["family"] == "fan" and int(r["index"]) == 5)
    source = next(item for item in source_order
                  if item["record"]["source_rafter_id"] == record["source_rafter_id"])
    detail = {
        "I_front_edge_fiber_records": source["controls"],
        "E_bottom_center_mm": source["center_E_bottom_mm"],
    }
    target = next(p["shape"] for p in ctx["parts"] if p["id"] == "likou_x")
    cutter = stage05.contact_notch_cutter(record, detail, ctx["board_sampler"], target)
    cutter_bbox = cutter.BoundingBox()
    cutter_metrics = {
        "valid": bool(cutter.isValid()),
        "solid_count": len(cutter.Solids()),
        "volume_mm3": float(cutter.Volume()),
        "face_count": len(cutter.Faces()),
        "shell_count": len(cutter.Shells()),
        "bbox_mm": [float(cutter_bbox.xmin), float(cutter_bbox.ymin),
                    float(cutter_bbox.zmin), float(cutter_bbox.xmax),
                    float(cutter_bbox.ymax), float(cutter_bbox.zmax)],
        "expected_face_count": len(detail["I_front_edge_fiber_records"]) + 4,
    }

    # Exactly one local boolean against the original Stage-04 likou_x.
    cut = target.cut(cutter).clean()
    components = []
    for index, solid in enumerate(cut.Solids()):
        box = solid.BoundingBox()
        components.append({
            "index": index,
            "volume_mm3": float(solid.Volume()),
            "bbox_mm": [float(box.xmin), float(box.ymin), float(box.zmin),
                        float(box.xmax), float(box.ymax), float(box.zmax)],
        })
    source_after = generate.production_source_identity()
    if source_after["sha256"] != source_before["sha256"]:
        raise RuntimeError("production source changed during local slot-shell cut probe")
    report = {
        "case_id": config["case_id"],
        "corner_angle_deg": config["corner_angle_deg"],
        "source_identity_sha256": source_before["sha256"],
        "source_identity_unchanged": True,
        "source_files": source_before["files"],
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - clock_start,
        "scope": "one fan05 source-defined slot cutter against original stage04 likou_x; one boolean only",
        "fiber_count": len(detail["I_front_edge_fiber_records"]),
        "bottom_face_count": len(detail["I_front_edge_fiber_records"]) - 1,
        "cutter": cutter_metrics,
        "likou_before": {"valid": bool(target.isValid()),
                         "solid_count": len(target.Solids()),
                         "volume_mm3": float(target.Volume())},
        "likou_after": {"valid": bool(cut.isValid()),
                        "solid_count": len(cut.Solids()),
                        "volume_mm3": float(cut.Volume()),
                        "removed_volume_mm3": float(target.Volume()) - float(cut.Volume())},
        "remaining_components": components,
    }
    out = DIAG / "angle_60_fan05_shell_cut_probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                   encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "source_identity_sha256", "elapsed_seconds", "fiber_count", "cutter",
        "likou_before", "likou_after")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
