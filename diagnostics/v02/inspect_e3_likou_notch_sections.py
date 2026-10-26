#!/usr/bin/env python
"""Inspect the existing e3 workbench mesh at likely circled straight-fly seats."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[2]
ROOT = GENERATOR.parents[1]
sys.path[:0] = [str(ROOT / "资料整理" / "tmp" / "model_deps"), str(GENERATOR / "engine")]

from config import load_config


def load_stage05():
    spec = importlib.util.spec_from_file_location("inspect_e3_stage05", GENERATOR / "engine" / "stage05_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MeshTopSampler:
    """Vertical ray queries against an exported part's actual triangle mesh."""

    def __init__(self, part):
        import numpy as np
        flat = np.asarray(part["vertices"], dtype=float).reshape((-1, 3))
        self.tri = flat[np.asarray(part["triangles"], dtype=int).reshape((-1, 3))]
        self.xy = self.tri[:, :, :2]
        self.z = self.tri[:, :, 2]
        self.min_xy = self.xy.min(axis=1)
        self.max_xy = self.xy.max(axis=1)
        self.valid = np.abs(
            (self.xy[:, 1, 1] - self.xy[:, 2, 1]) * (self.xy[:, 0, 0] - self.xy[:, 2, 0])
            + (self.xy[:, 2, 0] - self.xy[:, 1, 0]) * (self.xy[:, 0, 1] - self.xy[:, 2, 1])
        ) > 1e-12

    def z_hits(self, x, y):
        import numpy as np
        cand = np.flatnonzero(self.valid & (self.min_xy[:, 0] - 1e-7 <= x) & (x <= self.max_xy[:, 0] + 1e-7)
                              & (self.min_xy[:, 1] - 1e-7 <= y) & (y <= self.max_xy[:, 1] + 1e-7))
        if not len(cand):
            return []
        p = self.xy[cand]
        den = ((p[:, 1, 1] - p[:, 2, 1]) * (p[:, 0, 0] - p[:, 2, 0])
               + (p[:, 2, 0] - p[:, 1, 0]) * (p[:, 0, 1] - p[:, 2, 1]))
        w0 = ((p[:, 1, 1] - p[:, 2, 1]) * (x - p[:, 2, 0])
              + (p[:, 2, 0] - p[:, 1, 0]) * (y - p[:, 2, 1])) / den
        w1 = ((p[:, 2, 1] - p[:, 0, 1]) * (x - p[:, 2, 0])
              + (p[:, 0, 0] - p[:, 2, 0]) * (y - p[:, 2, 1])) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7)
        return (w0[inside] * self.z[cand[inside], 0]
                + w1[inside] * self.z[cand[inside], 1]
                + w2[inside] * self.z[cand[inside], 2]).tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=GENERATOR / "configs" / "size_variant_valid64.json")
    parser.add_argument("--mesh", type=Path, default=GENERATOR / "outputs/workbench/case-e3b47d1b9f6a4beda5ed00e4f28ed3e7/model_mesh.json")
    parser.add_argument("--output", type=Path, default=GENERATOR / "diagnostics/v02/e3_likou_notch_sections.json")
    parser.add_argument("--brep-check", action="store_true", help="verify residual mesh locations against a targeted exact BRep cut")
    args = parser.parse_args()
    started = time.perf_counter()
    config = load_config(args.config)
    stage05 = load_stage05()
    ctx = stage05.build_stage04_base(config)
    mesh = json.loads(args.mesh.read_text(encoding="utf-8"))
    mesh_part = next(item for item in mesh["parts"] if item["id"] == "likou_x")
    sampler = MeshTopSampler(mesh_part)
    target = next(part["shape"] for part in ctx["parts"] if part["id"] == "likou_x")
    records = [r for r in ctx["chosen"] if r["family"] == "straight" and int(r["index"]) in {1, 2, 3}]
    if len(records) != 3:
        raise RuntimeError(f"expected three straight x flyers, got {len(records)}")

    reports = []
    exact_records = []
    for record in records:
        print(f"section check: straight flyer {record['index']:02d}", flush=True)
        _, detail = stage05.build_contact_flyer(record, ctx["board_sampler"], ctx["case"])
        edges = detail["I_front_edge_fiber_records"]
        v_knots = [float(e["v_mm"]) for e in edges]
        q_fronts = [float(e["q_front_mm"]) for e in edges]
        h_fronts = [float(e["H_front_mm"]) for e in edges]
        half_width = float(ctx["case"]["p"]["w_fly"]) / 2.0
        # Dense 1mm q sections include the cutter boundary, I(v) breakpoints,
        # and the full 17-fiber spacing; this is an actual-mesh section audit.
        q0 = min(q_fronts) - 0.05
        qs = sorted(set([q0, 0.0] + [float(q) for q in stage05.np.arange(q0, 0.0, 1.0)]
                        + [float(q) for q in q_fronts]))
        vs = sorted(set([float(v) for v in stage05.np.linspace(-half_width, half_width, 33)] + v_knots))
        residuals = []
        missed = 0
        for q in qs:
            for v in vs:
                qi = float(stage05.np.interp(v, v_knots, q_fronts))
                if q < qi:
                    floor = stage05.board_top_at(record, ctx["board_sampler"], q, v)
                    if floor is None:
                        missed += 1
                        continue
                else:
                    edge = {"q_front_mm": qi, "H_front_mm": float(stage05.np.interp(v, v_knots, h_fronts))}
                    floor = stage05.bottom_outer_line(record, edge, q, v, detail["E_bottom_center_mm"])
                x, y = stage05.xy_at_q(record, q, v)
                hits = sampler.z_hits(x, y)
                if not hits:
                    continue
                top = max(hits)
                excess = float(top - floor)
                if excess > 0.75:
                    residuals.append({"q_mm": q, "v_mm": v, "x_mm": x, "y_mm": y,
                                      "likou_top_z_mm": float(top), "seat_floor_z_mm": float(floor),
                                      "material_above_floor_mm": excess})
        reports.append({
            "flyer_id": f"straight_flying_x_{int(record['index']):02d}",
            "candidate_likou_target_id": "likou_x",
            "q_interval_mm": [float(q0), 0.0],
            "q_spacing_mm": 1.0,
            "width_section_count": len(vs),
            "actual_mesh_triangle_count": len(sampler.tri),
            "above_floor_section_hits_over_0_75mm": len(residuals),
            "maximum_material_above_floor_mm": max((r["material_above_floor_mm"] for r in residuals), default=0.0),
            "largest_residual_sections": sorted(residuals, key=lambda r: r["material_above_floor_mm"], reverse=True)[:12],
            "missing_board_queries": missed,
            "edge_bias_inboard_mm": float(detail["I_front_edge_bias_inboard_mm"]),
            "piecewise_I_edge_fit_mm": float(detail["piecewise_edge_inboard_fit_mm"]),
        })
        exact_records.append((record, detail, residuals))

    exact_checks = None
    if args.brep_check:
        print("section check: exact BRep cutter coverage at the observed residual sections", flush=True)
        cutters = [stage05.contact_notch_cutter(record, detail, ctx["board_sampler"], target)
                   for record, detail, _ in exact_records]
        exact_checks = []
        for report, cutter, (record, detail, residuals) in zip(reports, cutters, exact_records):
            by_q = {}
            for item in residuals:
                old = by_q.get(item["q_mm"])
                if old is None or item["material_above_floor_mm"] > old["material_above_floor_mm"]:
                    by_q[item["q_mm"]] = item
            candidates = sorted(by_q.values(), key=lambda r: r["q_mm"])
            point_checks = []
            for item in candidates:
                upper = min(item["likou_top_z_mm"], item["seat_floor_z_mm"] + 40.0)
                z_values = [float(item["seat_floor_z_mm"] + offset)
                            for offset in (1.0, 5.0, 15.0, 30.0, 39.0)
                            if item["seat_floor_z_mm"] + offset < upper]
                inside_cutter_z = []
                inside_target_z = []
                for z in z_values:
                    point = stage05.cq.Vector(item["x_mm"], item["y_mm"], float(z))
                    if cutter.isInside(point, 0.05):
                        inside_cutter_z.append(float(z))
                    if target.isInside(point, 0.05):
                        inside_target_z.append(float(z))
                point_checks.append({"q_mm": item["q_mm"], "v_mm": item["v_mm"],
                                     "material_above_floor_mm": item["material_above_floor_mm"],
                                     "exact_cutter_points_covering_expected_removed_region": len(inside_cutter_z),
                                     "exact_uncut_target_points_inside": len(inside_target_z),
                                     "tested_heights_above_floor_mm": [z-item["seat_floor_z_mm"] for z in z_values]})
            exact_checks.append({"flyer_id": report["flyer_id"], "sampled_points": len(point_checks),
                                 "sections_with_any_exact_cutter_coverage": sum(p["exact_cutter_points_covering_expected_removed_region"] > 0 for p in point_checks),
                                 "sections_with_no_exact_cutter_coverage": sum(p["exact_cutter_points_covering_expected_removed_region"] == 0 for p in point_checks),
                                 "sections_with_exact_target_and_cutter_overlap": sum(p["exact_cutter_points_covering_expected_removed_region"] > 0 and p["exact_uncut_target_points_inside"] > 0 for p in point_checks),
                                 "checks": point_checks})
    report = {
        "case_id": mesh["metadata"]["case_id"],
        "source_mesh": str(args.mesh.resolve()),
        "source_config": str(args.config.resolve()),
        "method": "1mm q sections x 33 width positions; vertical intersections against actual exported likou_x triangles, compared with the corresponding reconstructed old notch-floor surface; positive excess means remaining likou above the seat floor.",
        "candidate_interpretation": "The three blue-circled tabs align visually with the three straight-eave flyer/likou crossings; these part pairs are tested directly.",
        "checks": reports,
        "targeted_exact_cutter_checks": exact_checks,
        "elapsed_seconds": time.perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "elapsed_seconds": report["elapsed_seconds"],
                      "residual_counts": [r["above_floor_section_hits_over_0_75mm"] for r in reports],
                      "exact_cutter_uncovered_section_counts": None if exact_checks is None else
                      [r["sections_with_no_exact_cutter_coverage"] for r in exact_checks],
                      "exact_target_cutter_overlap_sections": None if exact_checks is None else
                      [r["sections_with_exact_target_and_cutter_overlap"] for r in exact_checks]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
