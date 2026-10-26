from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
sys.path[:0] = [
    str(ROOT / "资料整理" / "tmp" / "model_deps"),
    str(ROOT / "资料整理" / "tmp" / "section_plot_deps"),
    str(HERE / "engine"),
    str(HERE / "diagnostics" / "v02"),
]

import stage05_core as stage05
from OCP.BRepTools import BRepTools
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Shape
from probe_shared_flyer_masterboard_entities import load_b400_config


def shape_summary(shape: stage05.cq.Shape) -> dict:
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


def plain(value):
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, "tolist"):
        return plain(value.tolist())
    return value


def persist_brep(shape, path: Path) -> dict:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite BRep evidence: {path}")
    if not BRepTools.Write_s(shape.wrapped, str(path)) or not path.is_file():
        raise RuntimeError(f"failed to persist BRep: {path}")
    return {"path": str(path.relative_to(HERE)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size}


def main() -> None:
    run_started = time.perf_counter()
    config, source_case_id = load_b400_config()
    config["case_id"] = "probe_persist_b400_shared_motherboard_members"
    started = time.perf_counter()
    ctx = stage05.build_stage04_base(config)
    stage04_seconds = time.perf_counter() - started
    started = time.perf_counter()
    source_order, motherboard = stage05._build_flyer_masterboard(ctx)
    masterboard_seconds = time.perf_counter() - started

    out_dir = HERE / "diagnostics" / "v02" / "B400_shared_motherboard_members"
    output_json = HERE / "diagnostics" / "v02" / "B400_shared_motherboard_members.json"
    if out_dir.exists() or output_json.exists():
        raise FileExistsError(f"refusing to overwrite prior member evidence: {out_dir} / {output_json}")
    out_dir.mkdir(parents=True)

    production_ids = [record["source_rafter_id"] for record in ctx["chosen"]]
    spatial_ids = [item["record"]["source_rafter_id"] for item in source_order]
    spatial_index = {source_id: index for index, source_id in enumerate(spatial_ids)}
    prior_fan01 = HERE / "diagnostics" / "v02" / "fan01_shared_motherboard_B400.brep"
    if not prior_fan01.is_file():
        raise FileNotFoundError(f"preexisting fan01 BRep is required: {prior_fan01}")
    fan01_copy = out_dir / "fan_rafter_01.brep"
    shutil.copyfile(prior_fan01, fan01_copy)
    restored = TopoDS_Shape()
    if (not BRepTools.Read_s(restored, str(fan01_copy), BRep_Builder()) or restored.IsNull()):
        raise RuntimeError("failed to reload already-built fan_rafter_01 BRep")
    fan01_shape = stage05.cq.Shape(restored)
    fan01_meta = {
        "member_id": "fan_rafter_01", "production_order": production_ids.index("fan_rafter_01"),
        "motherboard_source_order": spatial_index["fan_rafter_01"],
        "family": "fan", "index": 1,
        "brep": {"path": str(fan01_copy.relative_to(HERE)),
                 "sha256": hashlib.sha256(fan01_copy.read_bytes()).hexdigest(),
                 "bytes": fan01_copy.stat().st_size,
                 "reused_from": str(prior_fan01.relative_to(HERE))},
        "shape": shape_summary(fan01_shape),
        "source": "same current shared-motherboard geometry; BRep created before the side checker, no geometry build or cutter repeated",
    }
    members = [fan01_meta]
    progress = {
        "case_id": config["case_id"], "source_case_id": source_case_id,
        "source_case_config": {key: config.get(key) for key in ("D", "e", "p", "B")},
        "stage04_preflight_seconds": stage04_seconds,
        "shared_field_seconds": masterboard_seconds,
        "source_order_production_ids": production_ids,
        "source_order_motherboard_spatial_ids": spatial_ids,
        "masterboard_guard_results": motherboard["guard_results"],
        "motherboard_q_E_side_spans": motherboard["q_E_side_spans"],
        "members": members,
    }
    output_json.write_text(json.dumps(plain(progress), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"member_id": fan01_meta["member_id"],
                      "production_order": fan01_meta["production_order"],
                      "motherboard_source_order": fan01_meta["motherboard_source_order"],
                      "shape": fan01_meta["shape"], "reused": True},
                     ensure_ascii=False), flush=True)

    for production_order, record in enumerate(ctx["chosen"]):
        source_id = record["source_rafter_id"]
        if source_id == "fan_rafter_01":
            continue
        source_item = next(item for item in source_order
                           if item["record"]["source_rafter_id"] == source_id)
        started = time.perf_counter()
        shape, detail = stage05.build_contact_flyer(
            record, ctx["board_sampler"], ctx["case"], ctx["end_curve"],
            ctx["selected_gamma"], masterboard_record=source_item)
        build_seconds = time.perf_counter() - started
        summary = shape_summary(shape)
        if shape.isNull() or not summary["valid"] or summary["solid_count"] != 1 or summary["volume_mm3"] <= 1e-5:
            raise ValueError(f"{source_id} invalid shared-motherboard member: {summary}")

        path = out_dir / f"{source_id}.brep"
        brep = persist_brep(shape, path)
        controls = detail["I_front_edge_fiber_records"]
        q_i = [float(controls[0]["q_front_mm"]), float(controls[-1]["q_front_mm"])]
        q_e = [float(controls[0]["q_E_mm"]), float(controls[-1]["q_E_mm"])]
        item = {
            "member_id": source_id,
            "production_order": production_order,
            "motherboard_source_order": spatial_index[source_id],
            "family": str(record["family"]), "index": int(record["index"]),
            "profile_basis": detail["profile_basis"],
            "build_seconds": build_seconds,
            "shape": summary,
            "brep": brep,
            "q_I_A_B_mm": q_i,
            "q_E_A_B_mm": q_e,
            "q_E_A_B_span_mm": abs(q_e[0] - q_e[1]),
            "cross_width_cell_count": detail["cross_width_cell_count"],
            "band_brep_validity": detail["band_brep_validity"],
            "side_brep_generator_validation_failures": detail.get(
                "side_brep_generator_validation_failures", []),
        }
        if source_id in ("fan_rafter_03", "fan_rafter_05", "eave_rafter_01"):
            item["side_contours"] = detail.get("side_contours", [])
            item["side_brep_generator_checks"] = detail.get("side_brep_generator_checks")
        members.append(item)
        progress["members"] = members
        output_json.write_text(json.dumps(plain(progress), ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps({"member_id": source_id,
                          "production_order": production_order,
                          "motherboard_source_order": spatial_index[source_id],
                          "build_seconds": build_seconds,
                          "shape": summary,
                          "q_E_A_B_mm": q_e,
                          "q_E_A_B_span_mm": abs(q_e[0] - q_e[1]),
                          "brep_path": brep["path"]}, ensure_ascii=False), flush=True)

    if len(members) != len(ctx["chosen"]):
        raise ValueError(f"persisted {len(members)} of {len(ctx['chosen'])} selected members")
    progress["all_selected_members_valid_one_solid"] = all(
        item["shape"]["valid"] and item["shape"]["solid_count"] == 1 for item in members)
    progress["persisted_member_count"] = len(members)
    progress["total_seconds_including_reused_fan01"] = float(time.perf_counter() - run_started)
    output_json.write_text(json.dumps(plain(progress), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"summary": str(output_json),
                      "brep_directory": str(out_dir),
                      "persisted_member_count": len(members),
                      "all_selected_members_valid_one_solid": progress[
                          "all_selected_members_valid_one_solid"],
                      "stage04_preflight_seconds": stage04_seconds,
                      "shared_field_seconds": masterboard_seconds},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
