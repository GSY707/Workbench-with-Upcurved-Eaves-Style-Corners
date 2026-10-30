"""登记本次已完成输出；服务须先停止，不启动 CAD，不重写输出身份。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

GENERATOR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(GENERATOR))
from workbench_backend import WorkbenchBackend


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, nargs="+")
    args = parser.parse_args()
    diag = Path(__file__).resolve().parent
    state_path = GENERATOR / "ui_state/workbench_state.json"
    before = read_json(state_path)
    if any(j.get("status") in ("queued", "running") for j in before["jobs"]):
        raise RuntimeError("仍有活动任务，拒绝登记或改变其状态")
    before_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
    backup = diag / f"workbench_state.before_registration.{before_hash[:12]}.json"
    if not backup.exists():
        shutil.copy2(state_path, backup)
    backend = WorkbenchBackend(seed_existing=False)
    added = []
    try:
        staged = []
        for summary_path in args.summary:
            summary = read_json(summary_path.resolve())
            if summary.get("return_code") != 0 or summary.get("timed_out"):
                raise RuntimeError(f"不是成功完成的输出：{summary_path}")
            output = Path(summary["output_dir"]).resolve()
            output_rel = output.relative_to((GENERATOR / "outputs").resolve()).as_posix()
            resolved = read_json(output / "parameters_resolved.json")
            raw = {key: resolved[key] for key in ("case_id", "corner_angle_deg", "parameters", "advanced")}
            mode = summary["mode"]
            response, normalized, key, fingerprint = backend.validate(raw, mode=mode)
            if summary["source_identity"]["production"]["sha256"] != fingerprint:
                raise RuntimeError(f"当前源码与实测来源不同，拒绝重新标识：{summary_path}")
            case_id = "case-v04-" + summary_path.name.removesuffix(".summary.json").replace("_", "-")
            mode_name = "预览" if mode == "preview" else "精确"
            title = f"v0.4 {raw['corner_angle_deg']:g}° B={raw['parameters']['B']:g} {mode_name}"
            record = backend._case_record_from_output(
                case_id=case_id, title=title, raw_config=response["normalized_config"],
                normalized=normalized, key=key, fingerprint=fingerprint, output_rel=output_rel,
                elapsed_seconds=summary["wall_seconds_startup_to_exit"], mode=mode,
                require_current_source=True,
            )
            existing = next((c for c in backend.state["cases"] if c["id"] == case_id), None)
            if existing:
                if any(existing.get(k) != record.get(k) for k in ("output_rel", "source_fingerprint", "calculation_key", "mode", "artifact_hashes")):
                    raise RuntimeError(f"既有记录身份不一致，拒绝覆盖：{case_id}")
            else:
                staged.append(record)
            added.append({"id": case_id, "mode": mode, "title": title,
                          "output_rel": output_rel, "source_fingerprint": fingerprint,
                          "already_registered": existing is not None})
        backend.state["cases"].extend(staged)
        assert backend.state["cases"][:len(before["cases"])] == before["cases"], "原历史记录被改变"
        backend._save_state()
    finally:
        backend.close()
    report = {"status": "passed", "state_backup": str(backup), "state_before_sha256": before_hash,
              "original_cases_preserved": len(before["cases"]), "cases": added,
              "cad_started": False, "outputs_modified": False}
    (diag / "registration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
