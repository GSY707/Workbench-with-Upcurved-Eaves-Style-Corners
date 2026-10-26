from __future__ import annotations

import hashlib
import json
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
from OCP.BRepTools import BRepTools


def plain(value):
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, "tolist"):
        return plain(value.tolist())
    return value


def shape_summary(shape):
    box = shape.BoundingBox()
    return {
        "valid": bool(shape.isValid()),
        "solid_count": len(shape.Solids()),
        "volume_mm3": float(shape.Volume()),
        "face_count": len(shape.Faces()),
        "edge_count": len(shape.Edges()),
        "bbox_mm": [float(box.xmin), float(box.ymin), float(box.zmin),
                    float(box.xmax), float(box.ymax), float(box.zmax)],
    }


def write_result(path: Path, result: dict) -> None:
    path.write_text(json.dumps(plain(result), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    config_path = HERE / "configs" / "default.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_case_id = config["case_id"]
    config["case_id"] = "probe_default_shared_motherboard_representatives"

    output_dir = HERE / "diagnostics" / "v02" / "default_shared_motherboard_representatives_retry1"
    output_json = HERE / "diagnostics" / "v02" / "default_shared_motherboard_representatives_retry1.json"
    if output_dir.exists() or output_json.exists():
        raise FileExistsError(f"refusing to overwrite default probe evidence: {output_dir} / {output_json}")
    output_dir.mkdir(parents=True)

    phase = time.perf_counter()
    ctx = stage05.build_stage04_base(config)
    stage04_seconds = time.perf_counter() - phase
    phase = time.perf_counter()
    source_order, motherboard = stage05._build_flyer_masterboard(ctx)
    masterboard_seconds = time.perf_counter() - phase

    fans = [item for item in source_order if item["record"]["family"] == "fan"]
    straight = [item for item in source_order if item["record"]["family"] != "fan"]
    if not fans or not straight:
        raise ValueError(f"default selection lacks representative fan/straight members: fans={len(fans)} straight={len(straight)}")
    middle_fan = fans[(len(fans) - 1) // 2]
    near_corner_fan = fans[-1]
    straight_member = straight[-1]
    selected = []
    seen = set()
    for item in (middle_fan, near_corner_fan, straight_member):
        source_id = item["record"]["source_rafter_id"]
        if source_id not in seen:
            seen.add(source_id)
            selected.append(item)

    result = {
        "probe_case_id": config["case_id"],
        "source_case_id": source_case_id,
        "source_config_path": str(config_path.relative_to(HERE)),
        "source_parameters": plain(config.get("parameters", {})),
        "source_advanced": plain(config.get("advanced", {})),
        "scope": "default stage04 + shared motherboard runtime path guards + middle/near-corner fan and outer straight representative BReps; no likou cut or full generator run",
        "stage04_preflight_seconds": float(stage04_seconds),
        "shared_field_seconds": float(masterboard_seconds),
        "shared_field_guard_results": motherboard["guard_results"],
        "global_member_order": motherboard["member_order_by_shared_boundary"],
        "q_E_side_spans": motherboard["q_E_side_spans"],
        "maximum_q_E_side_span_mm": float(motherboard["maximum_q_E_side_span_mm"]),
        "shared_boundary_paths": motherboard["shared_boundary_paths"],
        "physical_member_band_count": motherboard["physical_member_band_count"],
        "local_field_patch_count": motherboard["local_field_patch_count"],
        "representative_roles": {
            "middle_fan": middle_fan["record"]["source_rafter_id"],
            "near_corner_fan": near_corner_fan["record"]["source_rafter_id"],
            "outer_straight": straight_member["record"]["source_rafter_id"],
        },
        "representative_members": [],
    }
    write_result(output_json, result)

    production_lookup = {item["source_rafter_id"]: index
                         for index, item in enumerate(ctx["chosen"])}
    for item in selected:
        record = item["record"]
        source_id = record["source_rafter_id"]
        production_order = production_lookup[source_id]
        build_started = time.perf_counter()
        shape, detail = stage05.build_contact_flyer(
            record, ctx["board_sampler"], ctx["case"], ctx["end_curve"],
            ctx["selected_gamma"], masterboard_record=item)
        build_seconds = time.perf_counter() - build_started
        summary = shape_summary(shape)
        if shape.isNull() or not summary["valid"] or summary["solid_count"] != 1 or summary["volume_mm3"] <= 1e-5:
            raise ValueError(f"{source_id} invalid default shared-motherboard representative: {summary}")

        brep_path = output_dir / f"{source_id}.brep"
        if brep_path.exists():
            raise FileExistsError(f"refusing to overwrite BRep evidence: {brep_path}")
        if not BRepTools.Write_s(shape.wrapped, str(brep_path)) or not brep_path.is_file():
            raise RuntimeError(f"failed to persist representative BRep: {brep_path}")
        controls = detail["I_front_edge_fiber_records"]
        q_i = [float(controls[0]["q_front_mm"]), float(controls[-1]["q_front_mm"])]
        q_e = [float(controls[0]["q_E_mm"]), float(controls[-1]["q_E_mm"])]
        member = {
            "member_id": source_id,
            "production_order": int(production_order),
            "motherboard_source_order": int(source_order.index(item)),
            "role": next(role for role, chosen_id in result["representative_roles"].items()
                         if chosen_id == source_id),
            "build_seconds": float(build_seconds),
            "shape": summary,
            "q_I_A_B_mm": q_i,
            "q_E_A_B_mm": q_e,
            "q_E_A_B_span_mm": float(abs(q_e[0] - q_e[1])),
            "cross_width_cell_count": int(detail["cross_width_cell_count"]),
            "band_brep_validity": detail["band_brep_validity"],
            "side_brep_generator_validation_failures": detail.get("side_brep_generator_validation_failures", []),
            "brep": {
                "path": str(brep_path.relative_to(HERE)),
                "sha256": hashlib.sha256(brep_path.read_bytes()).hexdigest(),
                "bytes": brep_path.stat().st_size,
            },
        }
        result["representative_members"].append(member)
        write_result(output_json, result)
        print(json.dumps({"member_id": source_id, "role": member["role"],
                          "production_order": production_order,
                          "build_seconds": build_seconds, "shape": summary,
                          "q_E_A_B_mm": q_e, "brep_path": member["brep"]["path"]},
                         ensure_ascii=False), flush=True)

    result["all_masterboard_guards_passed"] = all(
        value is True if isinstance(value, bool) else value is not None and value >= 0
        for value in motherboard["guard_results"].values())
    result["all_representative_members_valid_one_solid"] = all(
        member["shape"]["valid"] and member["shape"]["solid_count"] == 1
        for member in result["representative_members"])
    result["total_seconds"] = float(time.perf_counter() - started)
    write_result(output_json, result)
    print(json.dumps({"summary": str(output_json),
                      "all_masterboard_guards_passed": result["all_masterboard_guards_passed"],
                      "all_representative_members_valid_one_solid": result[
                          "all_representative_members_valid_one_solid"],
                      "stage04_preflight_seconds": stage04_seconds,
                      "shared_field_seconds": masterboard_seconds,
                      "representative_count": len(result["representative_members"]),
                      "total_seconds": result["total_seconds"]},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
