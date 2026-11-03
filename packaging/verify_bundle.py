#!/usr/bin/env python
"""Exercise the staged offline bundle and record a real generation run."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGING_DIR.parent
RELEASE_DIR = PROJECT_ROOT / "release"
BUNDLE_DIR = RELEASE_DIR / "bundle"
APP_DIR = BUNDLE_DIR / "生成器" / "直角戗角"
PYTHON = BUNDLE_DIR / "runtime" / "python.exe"
LAUNCHER = BUNDLE_DIR / "packaging" / "launcher.py"
CONFIG = APP_DIR / "configs" / "angle_120.json"


def request(url: str, method: str = "GET", payload: Any = None,
            timeout: float = 10.0) -> tuple[int, bytes, str]:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def get_json(base: str, route: str) -> dict[str, Any]:
    status, raw, _ = request(base + route)
    if status != 200:
        raise RuntimeError(f"GET {route} returned HTTP {status}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"GET {route} returned a non-object response")
    return value


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_checked(command: list[str], *, cwd: Path, env: dict[str, str],
                 timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout[-16000:]}"
        )
    return result


def wait_job(base: str, job_id: str, timeout: int, mode: str) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        job = get_json(base, f"/api/jobs/{job_id}")["job"]
        status = job.get("status")
        if status != last_status:
            print(f"{mode}: {status} — {job.get('stage', '')}", flush=True)
            last_status = status
        if status == "succeeded":
            if job.get("mode") != mode or not isinstance(job.get("result"), dict):
                raise RuntimeError(f"{mode} job returned inconsistent result: {job}")
            return job
        if status in {"failed", "cancelled", "interrupted"}:
            raise RuntimeError(f"{mode} generation failed: {job.get('error')}\n{job.get('log_tail', '')}")
        time.sleep(3)
    raise TimeoutError(f"{mode} job {job_id} did not finish within {timeout} seconds")


def exercise_job(base: str, config: dict[str, Any], mode: str,
                 timeout: int) -> dict[str, Any]:
    status, raw, _ = request(base + "/api/jobs", method="POST",
                             payload={"config": config, "mode": mode}, timeout=30)
    if status not in (200, 202):
        raise RuntimeError(f"POST /api/jobs returned HTTP {status}")
    submission = json.loads(raw.decode("utf-8"))
    job = submission.get("job")
    if not isinstance(job, dict) or not job.get("id"):
        raise RuntimeError(f"generation submission has no job id: {submission}")
    completed = (job if job.get("status") == "succeeded"
                 else wait_job(base, job["id"], timeout, mode))
    result = completed.get("result")
    if not isinstance(result, dict) or result.get("mode") != mode:
        raise RuntimeError(f"{mode} generation returned the wrong result mode")
    case_id = result.get("id")
    if not isinstance(case_id, str) or not case_id.startswith("case-"):
        raise RuntimeError(f"{mode} generation returned an invalid case id: {case_id}")
    return {"job_id": job["id"], "case_id": case_id, "status": completed["status"],
            "elapsed_seconds": completed.get("elapsed_seconds"),
            "result": result, "reused": submission.get("reused", False)}


def fetch_artifact(base: str, case_id: str, name: str) -> dict[str, Any]:
    status, raw, content_type = request(base + f"/results/{case_id}/{name}", timeout=30)
    if status != 200 or not raw:
        raise RuntimeError(f"static result {name} for {case_id} failed: HTTP {status}")
    return {"path": f"/results/{case_id}/{name}", "bytes": len(raw),
            "content_type": content_type}


def verify(args: argparse.Namespace) -> dict[str, Any]:
    for path in (PYTHON, LAUNCHER, CONFIG):
        if not path.is_file():
            raise FileNotFoundError(f"staged bundle is incomplete: {path}")
    stage_data = args.data_dir.resolve()
    if not stage_data.is_relative_to(RELEASE_DIR.resolve()):
        raise ValueError(f"staging data must be under release/: {stage_data}")
    stage_data.mkdir(parents=True, exist_ok=True)
    port = find_free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["MUGOUZAO_DATA_DIR"] = str(stage_data)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"Start staged workbench on {port}; data: {stage_data}", flush=True)
    launcher_start = run_checked(
        [str(PYTHON), str(LAUNCHER), "--port", str(port), "--data-dir", str(stage_data),
         "--no-browser"], cwd=BUNDLE_DIR, env=env, timeout=180,
        label="launcher start",
    )
    try:
        bootstrap = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                bootstrap = get_json(base, "/api/bootstrap")
                break
            except (OSError, urllib.error.URLError, TimeoutError, ValueError):
                time.sleep(0.5)
        if bootstrap is None:
            raise RuntimeError("staged service did not expose /api/bootstrap")
        if (bootstrap.get("version") != "0.4.1" or
                Path(bootstrap.get("data_directory", "")).resolve() != stage_data or
                Path(bootstrap.get("workspace", "")).resolve() != BUNDLE_DIR.resolve()):
            raise RuntimeError(f"bootstrap identity or data-dir mismatch: {bootstrap}")

        static_assets = []
        for route in ("/", "/ui/index.html", "/ui/app.js", "/ui/styles.css"):
            status, raw, content_type = request(base + route)
            if status != 200 or not raw:
                raise RuntimeError(f"static asset {route} failed: HTTP {status}")
            static_assets.append({"path": route, "bytes": len(raw),
                                  "content_type": content_type})

        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        for mode in ("preview", "exact"):
            status, raw, _ = request(base + "/api/validate", method="POST",
                                     payload={"config": config, "mode": mode}, timeout=60)
            validated = json.loads(raw.decode("utf-8"))
            if status != 200 or validated.get("mode") != mode or validated.get("valid") is not True:
                raise RuntimeError(f"{mode} config validation failed: {validated}")

        preview = exercise_job(base, config, "preview", args.job_timeout)
        preview_assets = [fetch_artifact(base, preview["case_id"], name)
                          for name in ("index.html", "model.html", "model.obj", "config.json",
                                       "validation.json", "vendor/viewer-runtime.js")]
        preview_validation = json.loads(request(
            base + f"/results/{preview['case_id']}/validation.json")[1].decode("utf-8"))
        if (preview_validation.get("mode") != "preview" or
                preview_validation.get("status") != "preview_ready" or
                preview_validation.get("exact_acceptance") != "not_run"):
            raise RuntimeError(f"preview validation record is inconsistent: {preview_validation}")

        step_dir = stage_data / "outputs" / "step-export-angle_120_B400"
        step_command = [
            str(PYTHON), "-X", "utf8", "-u", str(APP_DIR / "generate.py"),
            "--config", str(CONFIG), "--output", str(step_dir), "--mode", "exact", "--step",
        ]
        step_path = step_dir / "model.step"
        step_validation_path = step_dir / "validation.json"
        step_result = None
        reused_step = False
        step_started = time.monotonic()
        if args.reuse_completed_step and step_validation_path.is_file() and step_path.is_file():
            step_validation = json.loads(step_validation_path.read_text(encoding="utf-8"))
            step_record = step_validation.get("step_export")
            if (step_validation.get("mode") == "exact" and
                    step_validation.get("passed") is True and
                    isinstance(step_record, dict) and
                    step_record.get("bytes") == step_path.stat().st_size and
                    step_record.get("sha256") == __import__("hashlib").sha256(
                        step_path.read_bytes()).hexdigest()):
                print("Reuse the completed exact --step output in the staging directory.",
                      flush=True)
                reused_step = True
            else:
                raise RuntimeError("existing exact STEP output is incomplete or failed verification")
        else:
            print("Run CLI exact generation with --step; this independently exercises STEP export.",
                  flush=True)
            step_result = run_checked(step_command, cwd=APP_DIR, env=env,
                                      timeout=args.step_timeout, label="exact STEP export")
        if not step_path.is_file() or step_path.stat().st_size < 1000:
            raise RuntimeError("--step did not create a non-empty model.step")
        if not step_path.read_bytes()[:32].startswith(b"ISO-10303-21"):
            raise RuntimeError("model.step does not contain a STEP exchange-file header")
        step_validation = json.loads(step_validation_path.read_text(encoding="utf-8"))
        step_record = step_validation.get("step_export")
        if (step_validation.get("passed") is not True or
                not isinstance(step_record, dict) or
                step_record.get("bytes") != step_path.stat().st_size):
            raise RuntimeError(f"STEP export record is invalid: {step_record}")
        step_seconds = round(
            float(step_validation.get("runtime_seconds_including_render", time.monotonic() - step_started))
            if reused_step else time.monotonic() - step_started,
            3,
        )
        if reused_step:
            exact_result = {"status": "exact_ready", "mode": "exact",
                            "case_id": step_validation.get("input", {}).get("case_id")}
        else:
            exact_result = json.loads(step_result.stdout.splitlines()[-1])
        if exact_result.get("status") != "exact_ready" or exact_result.get("mode") != "exact":
            raise RuntimeError(f"CLI exact generation returned an inconsistent result: {exact_result}")
        exact_assets = []
        for filename in ("index.html", "model.html", "layout.html", "layout.svg",
                         "layout.png", "model_preview.png", "marking_points.csv",
                         "model.obj", "validation.json", "model.step"):
            path = step_dir / filename
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(f"exact output artifact is missing or empty: {filename}")
            exact_assets.append({"path": str(path.relative_to(stage_data)),
                                 "bytes": path.stat().st_size})
        for filename in ("layout.png", "model_preview.png"):
            contents = (step_dir / filename).read_bytes()
            if not contents.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError(f"exact static image has invalid PNG signature: {filename}")
        if step_validation.get("mode") != "exact" or step_validation.get("passed") is not True:
            raise RuntimeError(f"exact validation record is inconsistent: {step_validation}")

        return {
            "status": "passed",
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "bundle": str(BUNDLE_DIR),
            "python_version": subprocess.run(
                [str(PYTHON), "-c", "import sys; print(sys.version)"], cwd=BUNDLE_DIR,
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
                check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip(),
            "staging_data_directory": str(stage_data),
            "port": port,
            "bootstrap": {"version": bootstrap.get("version"),
                          "data_directory": bootstrap.get("data_directory"),
                          "workspace": bootstrap.get("workspace")},
            "launcher_start_output": launcher_start.stdout.strip(),
            "static_assets": static_assets,
            "preview": {**{key: value for key, value in preview.items() if key != "result"},
                        "validation": {"status": preview_validation.get("status"),
                                       "exact_acceptance": preview_validation.get("exact_acceptance")},
                        "assets": preview_assets},
            "exact": {"status": "passed", "mode": "exact", "case_id": config["case_id"],
                      "elapsed_seconds": step_seconds,
                      "validation": {"passed": step_validation.get("passed"),
                                     "status": step_validation.get("status")},
                      "assets": exact_assets},
            "step_cli": {"status": "passed", "command": step_command,
                         "reused_completed_output": reused_step,
                         "elapsed_seconds": step_seconds,
                         "output_directory": str(step_dir),
                         "model_step_bytes": step_path.stat().st_size,
                         "model_step_sha256": __import__("hashlib").sha256(step_path.read_bytes()).hexdigest(),
                         "validation_record": step_record,
                         "stdout_tail": step_result.stdout[-6000:] if step_result else None},
            "launcher_stop_command": [str(PYTHON), str(LAUNCHER), "--stop",
                                      "--data-dir", str(stage_data)],
        }
    finally:
        stop = subprocess.run(
            [str(PYTHON), str(LAUNCHER), "--stop", "--data-dir", str(stage_data)],
            cwd=str(BUNDLE_DIR), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        print(f"Stop staged workbench: {stop.returncode}; {stop.stdout.strip()}", flush=True)
        if stop.returncode != 0:
            raise RuntimeError(f"could not stop staged workbench safely: {stop.stdout}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=RELEASE_DIR / "staging-data" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--job-timeout", type=int, default=4 * 60 * 60)
    parser.add_argument("--step-timeout", type=int, default=4 * 60 * 60)
    parser.add_argument("--reuse-completed-step", action="store_true",
                        help="reuse an already validated exact STEP result in --data-dir")
    args = parser.parse_args(argv)
    report = RELEASE_DIR / "build-report.json"
    try:
        verification = verify(args)
        payload = json.loads(report.read_text(encoding="utf-8"))
        payload["verification"] = verification
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Verification report updated: {report}", flush=True)
        print(json.dumps({"status": "passed", "preview": verification["preview"]["case_id"],
                          "exact": verification["exact"]["case_id"],
                          "step_bytes": verification["step_cli"]["model_step_bytes"]},
                         ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(f"Bundle verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
