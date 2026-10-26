from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
]

import stage05_core as stage05


def summarize_sections(sections):
    result = []
    for index, section in enumerate(sections):
        pts = stage05.np.asarray(section, dtype=float)
        centered = pts - pts.mean(axis=0)
        _, singular, vh = stage05.np.linalg.svd(centered, full_matrices=False)
        normal = vh[-1]
        plane_residual = stage05.np.abs(centered @ normal)
        edges = stage05.np.roll(pts, -1, axis=0) - pts
        result.append({
            "index": index,
            "point_count": int(len(pts)),
            "min_edge_mm": float(stage05.np.min(stage05.np.linalg.norm(edges, axis=1))),
            "max_edge_mm": float(stage05.np.max(stage05.np.linalg.norm(edges, axis=1))),
            "best_fit_plane_max_residual_mm": float(plane_residual.max()),
            "best_fit_plane_rms_residual_mm": float(stage05.np.sqrt(stage05.np.mean(plane_residual ** 2))),
            "plane_singular_values": [float(value) for value in singular],
            "points": [[float(value) for value in point] for point in pts],
        })
    return result


def shape_summary(shape):
    faces = shape.Faces()
    return {
        "is_null": bool(shape.isNull()),
        "is_valid": bool(shape.isValid()),
        "solids": len(shape.Solids()),
        "volume_mm3": float(shape.Volume()),
        "faces": len(faces),
        "edges": len(shape.Edges()),
        "vertices": len(shape.Vertices()),
        "invalid_faces": [index for index, face in enumerate(faces) if not face.isValid()],
        "bbox": [float(value) for value in shape.BoundingBox().toTuple()] if hasattr(shape.BoundingBox(), "toTuple") else None,
    }


def build_raw(sections):
    wires = [stage05.s2.polygon_wire(section) for section in sections]
    shape = stage05.cq.Solid.makeLoft(wires, ruled=True)
    return shape


def main():
    source = HERE / "outputs" / "workbench" / "case-389425e820d34287a5c23b3f36aa5a01" / "parameters_resolved.json"
    config = json.loads(source.read_text(encoding="utf-8"))
    config.pop("derived", None)
    config["case_id"] = "probe_masterboard_first_loft_diagnostic"
    started = time.perf_counter()
    ctx = stage05.build_stage04_base(config)
    base_seconds = time.perf_counter() - started
    started = time.perf_counter()
    source_order, motherboard = stage05._build_flyer_masterboard(ctx)
    field_seconds = time.perf_counter() - started
    record = ctx["chosen"][0]
    item = next(value for value in source_order
                if value["record"]["source_rafter_id"] == record["source_rafter_id"])
    production_order_ids = [entry["source_rafter_id"] for entry in ctx["chosen"]]
    motherboard_order_ids = [entry["record"]["source_rafter_id"] for entry in source_order]
    captures = []
    original_loft_sections = stage05.s2.loft_sections

    def capturing_loft(sections):
        raw = build_raw(sections)
        phase_results = {}
        if len(captures) == 0 and len(sections) == 25:
            phase_results["tail_R_to_I"] = shape_summary(build_raw(sections[:13]))
            phase_results["outer_I_to_E"] = shape_summary(build_raw(sections[12:]))
            try:
                tail = build_raw(sections[:13])
                outer = build_raw(sections[12:])
                fused = tail.fuse(outer).clean()
                phase_results["fuse_at_shared_I"] = shape_summary(fused)
            except Exception as exc:
                phase_results["fuse_at_shared_I_error"] = repr(exc)
        captures.append({
            "section_count": len(sections), "full_shape": shape_summary(raw),
            "phase_shapes": phase_results,
            "sections": summarize_sections(sections) if len(captures) == 0 else None,
        })
        if not raw.isValid() or len(raw.Solids()) != 1 or raw.Volume() <= 1e-5:
            raise ValueError(f"diagnostic loft invalid: valid={raw.isValid()} solids={len(raw.Solids())} volume={raw.Volume()}")
        return raw

    stage05.s2.loft_sections = capturing_loft
    error = None
    try:
        stage05.build_contact_flyer(record, ctx["board_sampler"], ctx["case"],
                                    ctx["end_curve"], ctx["selected_gamma"],
                                    masterboard_record=item)
    except Exception as exc:
        error = repr(exc)
    finally:
        stage05.s2.loft_sections = original_loft_sections

    output = HERE / "diagnostics" / "v02" / "masterboard_first_production_loft_diagnostic_b400.json"
    result = {
        "case_id": config["case_id"], "source_case_id": source.parent.name,
        "member": {"source_rafter_id": record["source_rafter_id"],
                   "family": record["family"], "index": int(record["index"])},
        "production_order_index_zero_based": int(production_order_ids.index(record["source_rafter_id"])),
        "motherboard_spatial_order_index_zero_based": int(motherboard_order_ids.index(record["source_rafter_id"])),
        "production_order_source_rafter_ids": production_order_ids,
        "motherboard_spatial_order_source_rafter_ids": motherboard_order_ids,
        "stage04_preflight_seconds": base_seconds,
        "masterboard_field_seconds": field_seconds,
        "member_build_error": error,
        "loft_captures": captures,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "loft_captures"},
                     ensure_ascii=False, indent=2), flush=True)
    print(f"diagnostic: {output}", flush=True)
    if captures:
        print(json.dumps({"first_loft": {key: value for key, value in captures[0].items() if key != "sections"}},
                         ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
