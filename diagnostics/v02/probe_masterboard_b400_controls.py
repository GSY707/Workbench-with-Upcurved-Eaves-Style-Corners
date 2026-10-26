from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
]

import stage04_core as s4


def intersect_line_polyline(origin, direction, path, label, expected_q):
    ox, oy = map(float, origin)
    ux, uy = map(float, direction)
    hits = []
    for index, (a, b) in enumerate(zip(path[:-1], path[1:])):
        ax, ay = map(float, a)
        dx, dy = float(b[0]) - ax, float(b[1]) - ay
        den = ux * dy - uy * dx
        if abs(den) <= 1e-11:
            continue
        rx, ry = ax - ox, ay - oy
        q = (rx * dy - ry * dx) / den
        f = (rx * uy - ry * ux) / den
        if -1e-9 <= f <= 1.0 + 1e-9:
            point = [ox + q * ux, oy + q * uy]
            hits.append({"segment": index, "q_mm": q, "fraction": f, "point_xy_mm": point})
    if not hits:
        raise ValueError(f"{label}: no line/path intersection")
    unique = []
    for hit in hits:
        if not any(math.dist(hit["point_xy_mm"], old["point_xy_mm"]) <= 1e-5 for old in unique):
            unique.append(hit)
    unique.sort(key=lambda item: abs(item["q_mm"] - float(expected_q)))
    return unique[0], unique


def main():
    source = HERE / "outputs" / "workbench" / "case-389425e820d34287a5c23b3f36aa5a01" / "parameters_resolved.json"
    config = json.loads(source.read_text(encoding="utf-8"))
    config["case_id"] = "probe_masterboard_b400_controls"
    config.pop("derived", None)
    ctx = s4.build_base_and_preflight(config)
    case = ctx["case"]
    inner_path = ctx["inner_path"]
    half_width = float(case["p"]["w_fly"]) / 2.0
    straight = [r for r in ctx["chosen"] if r["family"] == "straight"]
    x_min = min((float(r["S"][0]) - half_width for r in straight),
                default=-float(case["D"]) - float(case["eave_tail_extension"]))
    x_min = min(x_min, float(inner_path[0][0])) - 5.0
    front_path = [(x_min, float(case["e"]) - float(case["p"]["likou_width"]))]
    front_path.extend((float(x), float(y)) for x, y in inner_path)
    outer_path = [(x_min, float(case["e"]) + float(case["p"]["p"])),
                  (-float(case["D"]), float(case["e"]) + float(case["p"]["p"]))]
    outer_path.extend((float(x), float(y)) for x, y in ctx["end_curve"]["points"][1:])
    records = []
    for record in ctx["chosen"]:
        S = record["S"]
        U = record["unit"]
        W = record["width_axis"]
        controls = []
        for fiber_index, v in enumerate(__import__("numpy").linspace(-half_width, half_width, 17)):
            origin = [float(S[0]) + float(W[0]) * float(v),
                      float(S[1]) + float(W[1]) * float(v)]
            hit, all_hits = intersect_line_polyline(
                origin, U, front_path,
                f"{record['family']} {record['index']} v={v:.3f} inner", record["q_I_mm"])
            e_hit, e_all_hits = intersect_line_polyline(
                origin, U, outer_path,
                f"{record['family']} {record['index']} v={v:.3f} outer", record["l_SE_mm"])
            station = s4.find_path_station(hit["point_xy_mm"], front_path)
            e_station = s4.find_path_station(e_hit["point_xy_mm"], outer_path)
            controls.append({"fiber_index": fiber_index, "v_mm": float(v),
                             "q_I_mm": float(hit["q_mm"]),
                             "I_xy_mm": hit["point_xy_mm"],
                             "inner_path_station_mm": float(station),
                             "intersection_count": len(all_hits),
                             "q_E_mm": float(e_hit["q_mm"]),
                             "E_xy_mm": e_hit["point_xy_mm"],
                             "outer_path_station_mm": float(e_station),
                             "outer_intersection_count": len(e_all_hits)})
        stations = [c["inner_path_station_mm"] for c in controls]
        diffs = [b - a for a, b in zip(stations[:-1], stations[1:])]
        records.append({
            "family": record["family"], "index": int(record["index"]),
            "source_rafter_id": record["source_rafter_id"],
            "center_I_path_station_mm": float(s4.find_path_station(record["I"], front_path)),
            "center_E_path_station_mm": float(s4.find_path_station(record["E"], outer_path)),
            "v_station_order_sign": 1 if min(diffs) > 0 else (-1 if max(diffs) < 0 else 0),
            "v_station_differences_mm": diffs,
            "v_station_span_mm": [float(min(stations)), float(max(stations))],
            "controls": controls,
        })
    records.sort(key=lambda item: item["v_station_span_mm"][0])
    gaps = []
    for left, right in zip(records[:-1], records[1:]):
        gap = float(right["v_station_span_mm"][0] - left["v_station_span_mm"][1])
        gaps.append({"left": f"{left['family']}_{left['index']}",
                     "right": f"{right['family']}_{right['index']}",
                     "inner_path_gap_mm": gap})
    result = {
        "case_id": config["case_id"], "source_case_id": source.parent.name,
        "fan_rafter_count": len([r for r in records if r["family"] == "fan"]),
        "straight_rafter_count": len([r for r in records if r["family"] == "straight"]),
        "record_order_by_inner_path_A_B_extent": [f"{r['family']}_{r['index']}" for r in records],
        "strictly_monotonic_within_each_width": all(r["v_station_order_sign"] != 0 for r in records),
        "nonoverlap_in_inner_path_station": all(g["inner_path_gap_mm"] >= -1e-6 for g in gaps),
        "minimum_gap_or_overlap_mm": min((g["inner_path_gap_mm"] for g in gaps), default=None),
        "gaps": gaps, "records": records,
        "front_boundary_path_xy_mm": front_path,
        "outer_boundary_path_xy_mm": outer_path,
    }
    out = HERE / "diagnostics" / "v02" / "flyer_masterboard_order_probe_b400.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"records", "gaps"}},
                     ensure_ascii=False, indent=2), flush=True)
    print(f"diagnostic: {out}", flush=True)


if __name__ == "__main__":
    main()
