#!/usr/bin/env python
"""Exact bounded XY coverage/overlap check for the v0.2 upper roll-board tiles."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[2]
ROOT = GENERATOR.parents[1]
ENGINE = GENERATOR / "engine"
sys.path[:0] = [str(ROOT / "资料整理" / "tmp" / "model_deps"), str(ENGINE)]

from config import load_config


def load_stage05():
    spec = importlib.util.spec_from_file_location("upperboard_plan_coverage_stage05",
                                                  ENGINE / "stage05_core.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load engine/stage05_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_polygon(points):
    cleaned = []
    for point in points:
        point = (float(point[0]), float(point[1]))
        if not cleaned or ((cleaned[-1][0] - point[0]) ** 2 +
                           (cleaned[-1][1] - point[1]) ** 2) > 1e-14:
            cleaned.append(point)
    if len(cleaned) > 2 and ((cleaned[0][0] - cleaned[-1][0]) ** 2 +
                             (cleaned[0][1] - cleaned[-1][1]) ** 2) <= 1e-14:
        cleaned.pop()
    if len(cleaned) < 3:
        raise ValueError("captured upper-board footprint collapsed below three XY vertices")
    area2 = sum(cleaned[i][0] * cleaned[(i + 1) % len(cleaned)][1] -
                cleaned[(i + 1) % len(cleaned)][0] * cleaned[i][1]
                for i in range(len(cleaned)))
    if abs(area2) <= 1e-8:
        raise ValueError("captured upper-board footprint has near-zero signed area")
    if area2 < 0.0:
        cleaned.reverse()
    return cleaned


def _footprint_from_sections(sections):
    # Solid.makeLoft(..., ruled=True) connects corresponding polygon vertices
    # linearly between stations. Its vertical projection is therefore the
    # polygon bounded by the two lower-rail XY polylines; the two thickness
    # rails share those same XY coordinates.
    rail0 = [(section[0][0], section[0][1]) for section in sections]
    rail1 = [(section[1][0], section[1][1]) for section in sections]
    return _clean_polygon(rail0 + list(reversed(rail1)))


def _face_loop_xy(wire):
    points = [(float(vertex.toTuple()[0]), float(vertex.toTuple()[1]))
              for vertex in wire.Vertices()]
    return _clean_polygon(points)


def _write_svg(path, domain_polygon, board_polygons, hole_polygons):
    all_points = [point for polygon in [domain_polygon] + board_polygons + hole_polygons
                  for point in polygon]
    xmin = min(point[0] for point in all_points)
    xmax = max(point[0] for point in all_points)
    ymin = min(point[1] for point in all_points)
    ymax = max(point[1] for point in all_points)
    width, height = 1400.0, 1000.0
    margin = 35.0
    scale = min((width - 2 * margin) / max(xmax - xmin, 1e-9),
                (height - 2 * margin) / max(ymax - ymin, 1e-9))

    def xy(point):
        return (margin + (float(point[0]) - xmin) * scale,
                height - margin - (float(point[1]) - ymin) * scale)

    def poly_points(polygon):
        return " ".join(f"{x:.3f},{y:.3f}" for x, y in (xy(point) for point in polygon))

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<g transform="translate(0 0)">',
             f'<polygon points="{poly_points(domain_polygon)}" fill="#f0eee8" stroke="#252525" stroke-width="1.5"/>']
    for polygon in board_polygons:
        lines.append(f'<polygon points="{poly_points(polygon)}" fill="#6daac0" fill-opacity="0.08" stroke="#356b7e" stroke-width="0.45"/>')
    for polygon in hole_polygons:
        lines.append(f'<polygon points="{poly_points(polygon)}" fill="#ef2d2d" fill-opacity="0.85" stroke="#9d1010" stroke-width="1.1"/>')
    lines.extend(['</g>',
                  '<text x="35" y="25" font-family="sans-serif" font-size="16">Upper-board XY coverage: red = uncovered Ω</text>',
                  '</svg>'])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    config_path = GENERATOR / "configs" / "default.json"
    output_path = GENERATOR / "diagnostics" / "v02" / "upperboard_plan_coverage_v02.json"
    started = time.perf_counter()
    config = load_config(config_path)
    stage05 = load_stage05()
    ctx = stage05.build_stage04_base(config)
    stage05.add_contact_flyers(ctx)

    captured_sections = []
    loft_sections = stage05.s2.loft_sections

    def capture_sections(sections):
        captured_sections.append([[tuple(float(c) for c in point) for point in section]
                                 for section in sections])
        return loft_sections(sections)

    stage05.s2.loft_sections = capture_sections
    try:
        board_records = stage05.add_upper_rollboards(ctx)
    finally:
        stage05.s2.loft_sections = loft_sections

    if len(captured_sections) != len(board_records):
        raise RuntimeError(f"section capture mismatch: {len(captured_sections)} lofts / "
                           f"{len(board_records)} X-side board records")

    domain_polygon = ctx["upper_board_domain"]["xy_polygon_mm"]
    flyer_shapes = ctx.get("contact_fly_shapes_x", {})
    source_shapes = [flyer_shapes.get(record["part_ids"][0])
                     for record in ctx["contact_fly_records"] if record["family"] == "fan"]
    source_shapes = [shape for shape in source_shapes if shape is not None]
    if len(source_shapes) != sum(record["family"] == "fan" for record in ctx["contact_fly_records"]):
        raise RuntimeError("missing actual flyer BRep for planar clipping extents")
    thickness = float(ctx["case"]["p"]["board_upper_t"])
    zmin = min(float(shape.BoundingBox().zmin) for shape in source_shapes) - 100.0
    zmax = max(float(shape.BoundingBox().zmax) for shape in source_shapes) + thickness + 100.0
    height = zmax - zmin
    domain_prism = stage05.s2.plan_prism(domain_polygon, zmin, zmax)
    details = {int(record["index"]): record for record in ctx["contact_fly_records"]
               if record["family"] == "fan"}
    piece_prisms = []
    per_piece = []
    board_polygons = []
    for record, sections in zip(board_records, captured_sections):
        polygon = _footprint_from_sections(sections)
        prism = stage05.s2.plan_prism(polygon, zmin, zmax)
        clipped = stage05._clip_upperboard_piece(
            prism, [domain_prism], f"projected {record['piece_type']} {record['part_ids'][0]}")
        if clipped is None:
            raise RuntimeError(f"projected footprint disappeared after required clips: {record['part_ids'][0]}")
        piece_prisms.append(clipped)
        clipped_loops = []
        for face in clipped.Faces():
            face_box = face.BoundingBox()
            if (abs(float(face_box.zmax) - float(face_box.zmin)) <= 1e-6 and
                    abs(float(face_box.zmin) - zmax) <= 1e-5):
                wires = [face.outerWire()]
                try:
                    wires.extend(list(face.innerWires()))
                except Exception:
                    pass
                for wire in wires:
                    loop = _face_loop_xy(wire)
                    if len(loop) >= 3:
                        clipped_loops.append(loop)
        if not clipped_loops:
            raise RuntimeError(f"could not recover clipped XY cap loop for {record['part_ids'][0]}")
        board_polygons.extend(clipped_loops)
        per_piece.append({
            "part_id": record["part_ids"][0],
            "piece_type": record["piece_type"],
            "fan_indices": record["fan_indices"],
            "projected_area_mm2": float(clipped.Volume()) / height,
            "clipped_xy_loops_mm": clipped_loops,
            "clipped_to_domain_omega": True,
            "support_q_nonnegative_clip_applied": False,
            "omega_endpoint_extension": record.get("omega_endpoint_extension"),
            "shared_longitudinal_lambda_bounds": record.get("shared_longitudinal_lambda_bounds"),
            "extended_range_minimum_planar_separation_mm": record.get(
                "extended_range_minimum_planar_separation_mm"),
        })

    if not piece_prisms:
        raise RuntimeError("no projected upper-board pieces captured")
    union = piece_prisms[0].fuse(*piece_prisms[1:]).clean()
    if union.isNull() or not union.isValid():
        raise RuntimeError("projected footprint union is null or invalid")
    uncovered = domain_prism.cut(union).clean()
    if uncovered.isNull():
        uncovered_area = 0.0
        uncovered_components = []
        hole_polygons = []
    else:
        uncovered_area = float(uncovered.Volume()) / height
        uncovered_components = []
        hole_polygons = []
        for solid in uncovered.Solids():
            box = solid.BoundingBox()
            cap_faces = [face for face in solid.Faces()
                         if abs(float(face.BoundingBox().zmax) - float(face.BoundingBox().zmin)) <= 1e-6 and
                         min(abs(float(face.BoundingBox().zmin) - zmin),
                             abs(float(face.BoundingBox().zmin) - zmax)) <= 1e-5]
            cap = max(cap_faces, key=lambda face: float(face.Area())) if cap_faces else None
            loops = []
            if cap is not None:
                wires = [cap.outerWire()]
                try:
                    wires.extend(list(cap.innerWires()))
                except Exception:
                    pass
                for wire in wires:
                    loop = _face_loop_xy(wire)
                    if len(loop) >= 3:
                        loops.append(loop)
            if loops:
                hole_polygons.append(loops[0])
            uncovered_components.append({
                "area_mm2": float(solid.Volume()) / height,
                "bbox_xy_mm": [float(box.xmin), float(box.ymin),
                               float(box.xmax), float(box.ymax)],
                "xy_loops_mm": loops,
            })
        uncovered_components.sort(key=lambda item: item["area_mm2"], reverse=True)
    domain_area = float(domain_prism.Volume()) / height
    union_area = float(union.Volume()) / height
    summed_area = sum(float(piece.Volume()) for piece in piece_prisms) / height
    overlap_area = (summed_area - union_area)
    # Exact prism booleans can leave tiny tolerance artifacts near shared seams.
    tolerance_area = 0.05
    report = {
        "case_id": config["case_id"],
        "source_config": str(config_path.resolve()),
        "projection_method": "actual ruled-loft section rails captured from add_upper_rollboards; every exact XY prism clipped only by old Ω; support endpoint rows extend each width-band terminal ruled surface to Ω-band q limits",
        "projection_is_exact_for_ruled_linear_section_interpolation": True,
        "height_used_for_area_recovery_mm": height,
        "domain_omega_area_mm2": domain_area,
        "domain_omega_polygon_xy_mm": [[float(x), float(y)] for x, y in domain_polygon],
        "piece_count_x": len(piece_prisms),
        "piece_area_sum_mm2": summed_area,
        "union_area_mm2": union_area,
        "uncovered_omega_area_mm2": uncovered_area,
        "uncovered_component_count": len(uncovered_components),
        "largest_uncovered_components": uncovered_components[:20],
        "uncovered_xy_loops_mm": [
            {"area_mm2": item["area_mm2"], "loops_xy_mm": item["xy_loops_mm"]}
            for item in uncovered_components],
        "unexpected_overlap_excess_area_mm2": overlap_area,
        "coverage_passed": abs(uncovered_area) <= tolerance_area,
        "overlap_passed": abs(overlap_area) <= tolerance_area,
        "area_tolerance_mm2": tolerance_area,
        "piece_type_counts_x": {kind: sum(record["piece_type"] == kind for record in per_piece)
                                for kind in ("support", "gap", "border")},
        "support_endpoint_extensions": [
            {"part_id": record["part_ids"][0], **record["omega_endpoint_extension"]}
            for record in board_records if record["piece_type"] == "support"],
        "gap_border_endpoint_extensions": [
            {"part_id": record["part_ids"][0],
             "shared_longitudinal_lambda_bounds": record["shared_longitudinal_lambda_bounds"],
             "endpoint_margin_mm": record["endpoint_margin_mm"],
             "bound_source": record["bound_source"],
             "extended_range_minimum_planar_separation_mm":
                 record["extended_range_minimum_planar_separation_mm"]}
            for record in board_records if record["piece_type"] in ("gap", "border")],
        "per_piece_projected_area": per_piece,
        "piece_projected_polygons_xy_mm": [
            {"part_id": record["part_id"], "piece_type": record["piece_type"],
             "loops_xy_mm": [[[float(x), float(y)] for x, y in loop]
                             for loop in record["clipped_xy_loops_mm"]]}
            for record in per_piece],
        "elapsed_seconds": float(time.perf_counter() - started),
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                      allow_nan=False) + "\n", encoding="utf-8")
    svg_path = output_path.with_suffix(".svg")
    _write_svg(svg_path, domain_polygon, board_polygons, hole_polygons)
    print(json.dumps({"output": str(output_path.resolve()),
                      "svg": str(svg_path.resolve()),
                      "domain_area_mm2": domain_area,
                      "piece_count_x": len(piece_prisms),
                      "uncovered_area_mm2": uncovered_area,
                      "overlap_excess_area_mm2": overlap_area,
                      "coverage_passed": report["coverage_passed"],
                      "overlap_passed": report["overlap_passed"],
                      "uncovered_components": len(uncovered_components),
                      "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False), flush=True)
    if not report["coverage_passed"] or not report["overlap_passed"]:
        raise RuntimeError("upper-board projected coverage/overlap check failed; see diagnostic JSON")


if __name__ == "__main__":
    main()
