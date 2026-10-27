#!/usr/bin/env python
"""Run one angle CLI case while preserving its exact source identity and log."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
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

spec = importlib.util.spec_from_file_location("angle_cli_generate", HERE / "generate.py")
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = HERE / config_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = HERE / output_path
    output_path = output_path.resolve()
    output_root = (HERE / "outputs").resolve()
    try:
        output_path.relative_to(output_root)
    except ValueError as exc:
        raise SystemExit(f"output must be under {output_root}") from exc
    if output_path.exists():
        raise SystemExit(f"refusing to reuse existing output path: {output_path}")
    config = generate.load_config(config_path)
    stem = output_path.name
    log_path = DIAG / f"{stem}_cli.log"
    provenance_path = DIAG / f"{stem}_cli_provenance.json"
    if log_path.exists() or provenance_path.exists():
        raise SystemExit(f"refusing to overwrite existing diagnostics for {stem}")

    started_at = datetime.now(timezone.utc).isoformat()
    clock_start = time.perf_counter()
    source_before = generate.production_source_identity()
    command = [sys.executable, "-X", "utf8", str(HERE / "generate.py"),
               "--config", str(config_path), "--output", str(output_path), "--no-render"]
    print(f"CLI_PROVENANCE_STARTED case={config['case_id']} output={output_path}", flush=True)
    print(f"CLI_SOURCE_SHA256={source_before['sha256']}", flush=True)
    print(f"CLI_START_UTC={started_at}", flush=True)
    with log_path.open("w", encoding="utf-8", newline="") as log:
        process = subprocess.Popen(command, cwd=HERE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        exit_code = process.wait()
    elapsed = time.perf_counter() - clock_start
    ended_at = datetime.now(timezone.utc).isoformat()
    source_after = generate.production_source_identity()
    provenance = {
        "case_id": config["case_id"],
        "corner_angle_deg": float(config["corner_angle_deg"]),
        "config_path": config_path.relative_to(HERE).as_posix(),
        "output_path": output_path.relative_to(HERE).as_posix(),
        "cli_args": ["--config", config_path.relative_to(HERE).as_posix(),
                     "--output", output_path.relative_to(HERE).as_posix(), "--no-render"],
        "source_identity_sha256": source_before["sha256"],
        "source_files": source_before["files"],
        "identity_after_cli_sha256": source_after["sha256"],
        "identity_unchanged": source_after["sha256"] == source_before["sha256"],
        "cli_process_start_utc": started_at,
        "cli_process_end_utc": ended_at,
        "elapsed_seconds": elapsed,
        "exit_code": int(exit_code),
        "cli_log_path": log_path.relative_to(HERE).as_posix(),
    }
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(f"CLI_PROVENANCE_END exit={exit_code} elapsed_seconds={elapsed:.3f} "
          f"identity_unchanged={provenance['identity_unchanged']}", flush=True)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
