from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "资料整理" / "tmp" / "model_deps"), str(HERE / "engine")]

import stage05_core as stage05


def shape_state(shape):
    return {"valid": bool(shape.isValid()), "solids": len(shape.Solids()),
            "volume_mm3": float(shape.Volume()), "faces": len(shape.Faces()),
            "edges": len(shape.Edges()), "vertices": len(shape.Vertices())}


def make_loft(sections):
    wires = [stage05.s2.polygon_wire(section) for section in sections]
    return stage05.cq.Solid.makeLoft(wires, ruled=True)


def planarity(points):
    values = stage05.np.asarray(points, dtype=float)
    centered = values - values.mean(axis=0)
    _, singular, vh = stage05.np.linalg.svd(centered, full_matrices=False)
    residuals = stage05.np.abs(centered @ vh[-1])
    return float(residuals.max()), float(stage05.np.sqrt(stage05.np.mean(residuals ** 2)))


def analyzer_statuses(shape):
    from OCP.BRepCheck import BRepCheck_Analyzer
    analyzer = BRepCheck_Analyzer(shape.wrapped)
    statuses = []
    result = analyzer.Result(shape.wrapped)
    values = result.Status()
    for value in values:
        statuses.append(str(getattr(value, "name", value)))
    return {"analyzer_is_valid": bool(analyzer.IsValid()), "shape_statuses": statuses}


def main():
    source = HERE / "diagnostics" / "v02" / "masterboard_first_production_loft_diagnostic_b400.json"
    probe = json.loads(source.read_text(encoding="utf-8"))
    full_sections = probe["loft_captures"][0]["sections"]
    rail_count = len(full_sections[0]["points"]) // 2
    bands = rail_count - 1
    sections_by_band = [[] for _ in range(bands)]
    plane_residuals_by_band = [[] for _ in range(bands)]
    for entry in full_sections:
        points = entry["points"]
        bottom = points[:rail_count]
        top = list(reversed(points[rail_count:]))
        for index in range(bands):
            section = [bottom[index], bottom[index + 1], top[index + 1], top[index]]
            sections_by_band[index].append(section)
            plane_residuals_by_band[index].append(planarity(section))

    band_shapes = []
    for index, sections in enumerate(sections_by_band):
        phase_states = {}
        for phase, phase_sections in (("tail_R_to_I", sections[:13]),
                                      ("outer_I_to_E", sections[12:])):
            phase_shape = make_loft(phase_sections)
            phase_states[phase] = shape_state(phase_shape)
        shape = make_loft(sections)
        band_shapes.append(shape)
        band_states = shape_state(shape)
        band_states.update({
            "band_index": index,
            "v_mm": [float(full_sections[0]["points"][index][1]),
                     float(full_sections[0]["points"][index + 1][1])],
            "max_section_plane_residual_mm": max(pair[0] for pair in plane_residuals_by_band[index]),
            "rms_section_plane_residual_mm_max": max(pair[1] for pair in plane_residuals_by_band[index]),
            "phase_shapes": phase_states,
        })

    fusion_states = []
    fused = band_shapes[0]
    for index, shape in enumerate(band_shapes[1:], start=1):
        try:
            fused = fused.fuse(shape).clean()
            fusion_states.append({"through_band_index": index, **shape_state(fused)})
        except Exception as exc:
            fusion_states.append({"through_band_index": index, "error": repr(exc)})
            break

    full_shape = make_loft([entry["points"] for entry in full_sections])
    result = {
        "source_diagnostic": source.name,
        "member": probe["member"],
        "production_order_index_zero_based": probe["production_order_index_zero_based"],
        "motherboard_spatial_order_index_zero_based": probe["motherboard_spatial_order_index_zero_based"],
        "production_order_source_rafter_ids": probe["production_order_source_rafter_ids"],
        "motherboard_spatial_order_source_rafter_ids": probe["motherboard_spatial_order_source_rafter_ids"],
        "representation": "one production strip loft per adjacent pair of the 17 shared width rails, then fuse",
        "section_point_order": "bottom rails v-low to v-high, then top rails v-high to v-low",
        "rail_count": rail_count,
        "band_count": bands,
        "full_width_shape": {**shape_state(full_shape), **analyzer_statuses(full_shape)},
        "band_shapes": [dict(state, band_index=index) for index, state in enumerate(
            [{**shape_state(shape), "max_section_plane_residual_mm": max(pair[0] for pair in plane_residuals_by_band[index]),
              "phase_shapes": {"tail_R_to_I": shape_state(make_loft(sections_by_band[index][:13])),
                               "outer_I_to_E": shape_state(make_loft(sections_by_band[index][12:]))}}
             for index, shape in enumerate(band_shapes)])],
        "fused_band_shapes_by_step": fusion_states,
    }
    output = HERE / "diagnostics" / "v02" / "masterboard_first_band_lofts_b400.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "band_shapes"},
                     ensure_ascii=False, indent=2), flush=True)
    print(json.dumps({"band_shape_summary": [
        {key: item[key] for key in ("band_index", "valid", "solids", "volume_mm3", "max_section_plane_residual_mm")}
        for item in result["band_shapes"]]}, ensure_ascii=False, indent=2), flush=True)
    print(f"diagnostic: {output}", flush=True)


if __name__ == "__main__":
    main()
