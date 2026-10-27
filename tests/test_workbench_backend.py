from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workbench_backend import (  # noqa: E402
    APP_ID,
    API_VERSION,
    STATE_REPLACE_INITIAL_DELAY_SECONDS,
    STATE_REPLACE_MAX_DELAY_SECONDS,
    STATE_REPLACE_RETRY_SECONDS,
    WorkbenchBackend,
    calculation_key,
    create_server,
    load_json,
)
from _workbench_fixtures import default_config, fake_command_builder, make_seed_outputs  # noqa: E402


def seed_source_fingerprint():
    return hashlib.sha256(b"workbench-synthetic-seed-source-v1").hexdigest()


class WorkbenchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workbench-test-")
        self.temp_path = Path(self.temp.name)
        self._start_backend()

    def tearDown(self):
        self._stop_server()
        self.temp.cleanup()

    def _start_backend(self, *, seed_existing=False, outputs_dir=None,
                       source_fingerprint_provider=None, command_builder=None,
                       state_dir=None):
        seed_catalog = None
        if seed_existing:
            if not hasattr(self, "seed_fixtures"):
                self.seed_fixtures = make_seed_outputs(ROOT, self.temp_path, seed_source_fingerprint())
            outputs_dir, seed_catalog = self.seed_fixtures

        def fixture_json(path):
            if seed_catalog is not None and Path(path) == ROOT / "configs" / "seed_catalog.json":
                return seed_catalog
            return load_json(path)

        with mock.patch("workbench_backend.load_json", side_effect=fixture_json):
            self.backend = WorkbenchBackend(
                generator_dir=ROOT,
                workspace_root=ROOT.parents[1],
                outputs_dir=outputs_dir or self.temp_path / "outputs",
                ui_dir=ROOT / "ui",
                state_dir=state_dir or self.temp_path / "state",
                source_fingerprint_provider=source_fingerprint_provider or (lambda: "test-source-v1"),
                command_builder=command_builder,
                seed_existing=seed_existing,
            )
        self.server = create_server(self.backend, port=0)
        self.server_thread = threading.Thread(target=self.server.serve_forever,
                                              kwargs={"poll_interval": 0.02}, daemon=True)
        self.server_thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _stop_server(self):
        if getattr(self, "server", None) is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=3)
            self.server = None
        if getattr(self, "backend", None) is not None:
            self.backend.close()
            self.backend = None

    def request(self, path, payload=None, *, origin=True, host=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            if origin:
                headers["Origin"] = self.base
        if host is not None:
            headers["Host"] = host
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                mime = response.headers.get_content_type()
                if mime == "application/json":
                    return response.status, json.loads(body.decode("utf-8")), response.headers
                return response.status, body, response.headers
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = body
            return exc.code, payload, exc.headers

    def get_default_config(self):
        return default_config(ROOT)

    def post_config(self, config):
        status, payload, _ = self.request("/api/validate", {"config": config})
        self.assertEqual(status, 200, payload)
        return payload

    def test_angle_changes_configuration_and_cache_identity(self):
        keys = []
        for angle in (60.0, 90.0, 120.0):
            config = self.get_default_config()
            config["corner_angle_deg"] = angle
            result = self.post_config(config)
            self.assertEqual(result["normalized_config"]["corner_angle_deg"], angle)
            self.assertEqual(result["derived"]["corner_angle_deg"], angle)
            keys.append(result["calculation_key"])
        self.assertEqual(len(set(keys)), 3)
        for angle in (0, 180, -1, True, "120"):
            config["corner_angle_deg"] = angle
            status, _, _ = self.request("/api/validate", {"config": config})
            self.assertEqual(status, 400, angle)

    def test_non_right_angle_result_cannot_be_registered_as_another_angle(self):
        self._stop_server()
        self._start_backend(command_builder=fake_command_builder(ROOT, lambda _: "success"))
        config = self.get_default_config()
        config["corner_angle_deg"] = 120
        status, start = self.request("/api/jobs", {"config": config})[:2]
        self.assertEqual(status, 202)
        job = self.wait_job(start["job"]["id"], {"succeeded", "failed"})
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["result"]["config"]["corner_angle_deg"], 120)
        record = self.backend.state["cases"][-1]
        wrong_config = copy.deepcopy(config)
        wrong_config["corner_angle_deg"] = 90
        _, normalized, key, fingerprint = self.backend.validate(wrong_config)
        with self.assertRaisesRegex(ValueError, "建筑平面内角"):
            self.backend._case_record_from_output(
                case_id=record["id"], title=record["title"], raw_config=wrong_config,
                normalized=normalized, key=key, fingerprint=fingerprint,
                output_rel=record["output_rel"], elapsed_seconds=0)

    def wait_job(self, job_id, terminal, timeout=8.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            status, payload, _ = self.request(f"/api/jobs/{job_id}")
            self.assertEqual(status, 200, payload)
            last = payload["job"]
            if last["status"] in terminal:
                return last
            time.sleep(0.04)
        self.fail(f"任务未在{timeout}s内进入终态：{last}")

    @staticmethod
    def _temporary_windows_replace_error(code):
        error = PermissionError("temporary Windows state-file lock")
        error.winerror = code
        return error

    @unittest.skipUnless(os.name == "nt", "Windows state-file replacement behavior")
    def test_save_state_retries_transient_windows_replace_error(self):
        state_path = self.backend.state_path
        previous_bytes = state_path.read_bytes()
        self.backend.state["retry_test_marker"] = "written after retry"
        real_replace = os.replace
        attempts = []
        delays = []

        def replace_after_transient_error(source, destination):
            attempts.append((Path(source), Path(destination)))
            if len(attempts) == 1:
                self.assertEqual(Path(destination).read_bytes(), previous_bytes)
                self.assertTrue(Path(source).is_file())
                raise self._temporary_windows_replace_error(5)
            return real_replace(source, destination)

        with mock.patch("workbench_backend.os.replace", side_effect=replace_after_transient_error), \
                mock.patch("workbench_backend.time.sleep", side_effect=delays.append):
            self.backend._save_state()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(delays, [STATE_REPLACE_INITIAL_DELAY_SECONDS])
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["retry_test_marker"], "written after retry")
        self.assertFalse(list(state_path.parent.glob("workbench_state.*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows state-file replacement behavior")
    def test_save_state_retry_exhaustion_preserves_previous_state_and_temp_file(self):
        state_path = self.backend.state_path
        previous_bytes = state_path.read_bytes()
        self.backend.state["retry_test_marker"] = "pending write"
        attempts = []
        delays = []
        now = [100.0]

        def fake_monotonic():
            return now[0]

        def fake_sleep(delay):
            delays.append(delay)
            now[0] += delay

        def always_locked(source, destination):
            source, destination = Path(source), Path(destination)
            attempts.append(source)
            self.assertEqual(destination.read_bytes(), previous_bytes)
            self.assertTrue(source.is_file())
            raise self._temporary_windows_replace_error(5)

        with mock.patch("workbench_backend.os.replace", side_effect=always_locked), \
                mock.patch("workbench_backend.time.monotonic", side_effect=fake_monotonic), \
                mock.patch("workbench_backend.time.sleep", side_effect=fake_sleep):
            with self.assertRaises(PermissionError):
                self.backend._save_state()

        self.assertGreater(len(attempts), 1)
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(state_path.read_bytes(), previous_bytes)
        self.assertAlmostEqual(sum(delays), STATE_REPLACE_RETRY_SECONDS)
        self.assertEqual(delays[0], STATE_REPLACE_INITIAL_DELAY_SECONDS)
        self.assertLessEqual(max(delays), STATE_REPLACE_MAX_DELAY_SECONDS)
        self.assertTrue(attempts[0].is_file())
        temporary_state = json.loads(attempts[0].read_text(encoding="utf-8"))
        self.assertEqual(temporary_state["retry_test_marker"], "pending write")

    @unittest.skipUnless(os.name == "nt", "Windows state-file replacement behavior")
    def test_save_state_does_not_retry_unrecognized_replace_error(self):
        state_path = self.backend.state_path
        previous_bytes = state_path.read_bytes()
        self.backend.state["retry_test_marker"] = "failed write"
        attempts = []
        delays = []

        def fail_with_unrecognized_error(source, destination):
            attempts.append(Path(source))
            error = PermissionError("permanent replacement failure")
            error.winerror = 1234
            raise error

        with mock.patch("workbench_backend.os.replace", side_effect=fail_with_unrecognized_error), \
                mock.patch("workbench_backend.time.sleep", side_effect=delays.append):
            with self.assertRaises(PermissionError):
                self.backend._save_state()

        self.assertEqual(len(attempts), 1)
        self.assertEqual(delays, [])
        self.assertEqual(state_path.read_bytes(), previous_bytes)
        self.assertTrue(attempts[0].is_file())

    def test_seeded_cases_validate_reuse_and_serve_registered_files(self):
        # Registry/cache tests use bounded synthetic deliveries in temporary directories.
        self._stop_server()
        seed_state = self.temp_path / "seed-state"
        self._start_backend(seed_existing=True, outputs_dir=ROOT / "outputs",
                            state_dir=seed_state,
                            source_fingerprint_provider=seed_source_fingerprint)
        status, bootstrap, _ = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(API_VERSION, "0.3.0")
        self.assertEqual(bootstrap["version"], API_VERSION)
        self.assertEqual(bootstrap["app_id"], APP_ID)
        self.assertEqual(Path(bootstrap["workspace"]).resolve(), ROOT.parents[1].resolve())
        self.assertTrue({"default", "size_variant_valid64"} <=
                        {item["id"] for item in bootstrap["examples"]})
        cases = {case["id"]: case for case in bootstrap["cases"]}
        self.assertEqual(set(cases), {"default", "size_variant_valid64"})

        for case_id in ("default", "size_variant_valid64"):
            case = cases[case_id]
            renamed = copy.deepcopy(case["config"])
            renamed["case_id"] = "renamed-for-cache-test"
            status, validation, _ = self.request("/api/validate", {"config": renamed})
            self.assertEqual(status, 200, validation)
            self.assertEqual(validation["calculation_key"], case["calculation_key"])
            status, submitted, _ = self.request("/api/jobs", {"config": renamed})
            self.assertEqual(status, 200, submitted)
            self.assertTrue(submitted["reused"])
            self.assertEqual(submitted["job"]["status"], "succeeded")
            self.assertEqual(submitted["job"]["result"]["id"], case_id)
            for name in ("model", "layout", "index", "obj", "points", "svg", "config", "validation"):
                asset_status, body, _ = self.request(case["urls"][name])
                self.assertEqual(asset_status, 200, (case_id, name, body))
                self.assertTrue(body)

        root_status, root_body, _ = self.request("/")
        self.assertEqual(root_status, 200)
        self.assertIn(b"<!doctype html", root_body.lower())
        ui_status, ui_body, _ = self.request("/ui/app.js")
        self.assertEqual(ui_status, 200)
        self.assertGreater(len(ui_body), 1000)

    def test_invalid_config_csrf_and_path_traversal_are_rejected(self):
        config = self.get_default_config()
        config["parameters"]["unknown_workbench_key"] = 1
        status, invalid, _ = self.request("/api/validate", {"config": config})
        self.assertEqual(status, 400)
        self.assertIn("未知", invalid["error"])

        config = self.get_default_config()
        status, forged_path, _ = self.request(
            "/api/jobs", {"config": config, "output_path": "../../outside"})
        self.assertEqual(status, 400)
        self.assertIn("只允许config", forged_path["error"])

        data = json.dumps({"config": config}, ensure_ascii=False).encode("utf-8")
        csrf_request = urllib.request.Request(
            self.base + "/api/validate", data=data,
            headers={"Content-Type": "application/json", "Origin": "http://attacker.invalid"})
        try:
            with urllib.request.urlopen(csrf_request, timeout=5) as response:
                status, csrf = response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status, csrf = exc.code, json.loads(exc.read().decode("utf-8"))
        self.assertEqual(status, 403)
        self.assertIn("跨站", csrf["error"])

        try:
            status, spoofed, _ = self.request(
                "/api/validate", {"config": config}, host="attacker.invalid")
        except (ConnectionAbortedError, ConnectionResetError):
            # Windows may abort the loopback response after rejecting the
            # forged Host header; no request was accepted by the workbench.
            status, spoofed = 403, {"error": "forged Host rejected"}
        self.assertEqual(status, 403)

        status, traversal, _ = self.request("/results/default/..%2F..%2FREADME.md")
        self.assertEqual(status, 404)

    def test_success_then_failure_keeps_only_valid_case(self):
        builder = fake_command_builder(ROOT, lambda d: "fail" if d == 1201.0 else "success")
        self._stop_server()
        self._start_backend(command_builder=builder)
        config = self.get_default_config()
        config["case_id"] = "fake_success"
        status, submitted, _ = self.request("/api/jobs", {"config": config})
        self.assertEqual(status, 202)
        self.assertFalse(submitted["reused"])
        succeeded = self.wait_job(submitted["job"]["id"], {"succeeded", "failed"})
        self.assertEqual(succeeded["status"], "succeeded", succeeded)
        self.assertTrue(succeeded["result"]["validated"])
        self.assertEqual(succeeded["stage"], "生成完成")
        self.assertIn("offline viewer rendering complete", succeeded["log_tail"])

        failed_config = copy.deepcopy(config)
        failed_config["case_id"] = "fake_failure"
        failed_config["parameters"]["D"] = 1201.0
        status, failed_start, _ = self.request("/api/jobs", {"config": failed_config})
        self.assertEqual(status, 202)
        failed = self.wait_job(failed_start["job"]["id"], {"succeeded", "failed"})
        self.assertEqual(failed["status"], "failed", failed)
        status, cases, _ = self.request("/api/cases")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in cases["cases"]], [succeeded["result"]["id"]])

    def test_single_active_job_busy_response_and_cancel(self):
        builder = fake_command_builder(ROOT, lambda d: "sleep" if d == 1202.0 else "success")
        self._stop_server()
        self._start_backend(command_builder=builder)
        config = self.get_default_config()
        config["case_id"] = "fake_sleep"
        config["parameters"]["D"] = 1202.0
        status, submitted, _ = self.request("/api/jobs", {"config": config})
        self.assertEqual(status, 202)
        job_id = submitted["job"]["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.backend.get_job(job_id)
            if job["stage"] == "采样板面并求解各根飞椽姿态":
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "running", job)
        self.assertEqual(job["stage"], "采样板面并求解各根飞椽姿态", job)

        busy_config = self.get_default_config()
        busy_config["parameters"]["D"] = 1203.0
        status, busy, _ = self.request("/api/jobs", {"config": busy_config})
        self.assertEqual(status, 409, busy)

        status, cancel, _ = self.request(f"/api/jobs/{job_id}/cancel", {})
        self.assertEqual(status, 200, cancel)
        cancelled = self.wait_job(job_id, {"cancelled", "failed", "succeeded"})
        self.assertEqual(cancelled["status"], "cancelled", cancelled)
        self.assertEqual(self.backend.list_cases(), [])

    def test_source_change_never_reseeds_and_state_directory_is_exclusive(self):
        self._stop_server()
        outputs = ROOT / "outputs"
        source_a = seed_source_fingerprint
        source_b = lambda: "source-after-v02-change"
        seed_state = self.temp_path / "seed-state"
        self._start_backend(seed_existing=True, outputs_dir=outputs,
                            source_fingerprint_provider=source_a, state_dir=seed_state)
        seeded_at = self.backend.state["seeded_at"]
        self.assertEqual(len(self.backend.list_cases()), 2)
        self.assertEqual(self.backend.state["seed_source_fingerprint"], source_a())
        baseline = self.backend.validate(self.get_default_config())
        self.assertIsNotNone(self.backend._find_cached_case(baseline[2], baseline[3]))
        with self.assertRaisesRegex(RuntimeError, "已有服务实例"):
            WorkbenchBackend(generator_dir=ROOT, outputs_dir=outputs,
                             state_dir=seed_state,
                             source_fingerprint_provider=source_b)

        self._stop_server()
        self._start_backend(seed_existing=True, outputs_dir=outputs,
                            source_fingerprint_provider=source_b,
                            state_dir=seed_state)
        self.assertEqual(self.backend.state["seeded_at"], seeded_at)
        self.assertEqual(self.backend.state["seed_source_fingerprint"], source_a())
        self.assertEqual(len(self.backend.list_cases()), 2)
        default = self.get_default_config()
        validation, normalized, key, fingerprint = self.backend.validate(default)
        self.assertEqual(fingerprint, "source-after-v02-change")
        self.assertIsNone(self.backend._find_cached_case(key, fingerprint))

    def test_fresh_state_keeps_seed_identity_and_does_not_reuse_it_for_changed_source(self):
        self._stop_server()
        current_source = "source-after-v02-change"
        self._start_backend(seed_existing=True, outputs_dir=ROOT / "outputs",
                            source_fingerprint_provider=lambda: current_source,
                            state_dir=self.temp_path / "fresh-v02-state")

        old_source = seed_source_fingerprint()
        records = {record["id"]: record for record in self.backend.state["cases"]}
        self.assertEqual(set(records), {"default", "size_variant_valid64"})
        self.assertEqual(self.backend.state["seed_source_fingerprint"], old_source)
        for record in records.values():
            self.assertEqual(record["source_fingerprint"], old_source)
            normalized = self.backend._config_module.resolve_config(record["config"])
            self.assertEqual(record["calculation_key"], calculation_key(normalized, old_source))

        _, _, current_key, fingerprint = self.backend.validate(self.get_default_config())
        self.assertEqual(fingerprint, current_source)
        self.assertNotEqual(records["default"]["calculation_key"], current_key)
        self.assertIsNone(self.backend._find_cached_case(current_key, fingerprint))

    def test_instance_lock_rejects_a_second_process(self):
        state_dir = self.temp_path / "cross-process-state"
        self._stop_server()
        self._start_backend(state_dir=state_dir)
        child_code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "from workbench_backend import WorkbenchBackend; "
            f"b=WorkbenchBackend(generator_dir=Path({str(ROOT)!r}), "
            f"state_dir=Path({str(state_dir)!r}), seed_existing=False); "
            "b.close(); print('UNEXPECTEDLY_ACQUIRED')"
        )
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", child_code],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("已有服务实例", result.stderr)

    def test_restart_marks_saved_running_job_interrupted(self):
        state_dir = self.temp_path / "restart-state"
        state_dir.mkdir()
        (state_dir / "workbench_state.json").write_text(json.dumps({
            "version": 1,
            "cases": [],
            "jobs": [{"id": "job-" + "a" * 32, "status": "running",
                      "stage": "实体处理中", "_started_epoch": time.time() - 10,
                      "log_tail": "stage05: combination stock loft"}],
            "initialized_at": "before-restart",
            "seeded_at": "seeded-before-restart",
            "seed_source_fingerprint": "old-source",
        }), encoding="utf-8")
        self._stop_server()
        self._start_backend(state_dir=state_dir)
        job = self.backend.get_job("job-" + "a" * 32)
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["stage"], "服务已重启，生成任务中断")
        self.assertIn("未自动重启 CAD", job["error"])
        self.assertEqual(self.backend.bootstrap()["active_job"], None)


if __name__ == "__main__":
    unittest.main()
