"""Local workbench API, persistent case registry, and serialized CAD job runner.

This module uses only the Python standard library for its HTTP and job plumbing.
The existing generator is imported only for strict config checks and its
lightweight plan-geometry derivation; full CAD generation always runs in a
separate subprocess.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


GENERATOR_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = GENERATOR_DIR.parents[1]
OUTPUTS_DIR = GENERATOR_DIR / "outputs"
UI_DIR = GENERATOR_DIR / "ui"
STATE_DIR = GENERATOR_DIR / "ui_state"
APP_ID = "mu-gouzao-corner-workbench"
API_VERSION = "0.3.0"
MAX_BODY_BYTES = 1024 * 1024
MAX_LOG_CHARS = 16000
MAX_JOBS = 100
STATE_REPLACE_RETRY_WINERRORS = frozenset({5, 32, 33})
STATE_REPLACE_RETRY_SECONDS = 2.0
STATE_REPLACE_INITIAL_DELAY_SECONDS = 0.02
STATE_REPLACE_MAX_DELAY_SECONDS = 0.2
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
JOB_ID_RE = re.compile(r"^job-[0-9a-f]{32}$")
_LOCAL_LOCK_GUARD = threading.Lock()
_LOCAL_LOCKED_PATHS: set[str] = set()

REQUIRED_ARTIFACTS = {
    "index.html", "model.html", "layout.html", "model.obj",
    "marking_points.csv", "layout.svg", "parameters_resolved.json",
    "validation.json", "model_mesh.json", "profiles.json", "layout.json",
}
SEED_CASES = (
    ("default", "默认参数", "default.json"),
    ("size_variant_valid64", "尺寸与间距变化示例", "size_variant_valid64.json"),
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class WorkbenchError(Exception):
    def __init__(self, status: int, error: str, details: list[str] | None = None):
        super().__init__(error)
        self.status = int(status)
        self.error = str(error)
        self.details = list(details or [])


class StateDirectoryLock:
    """Hold an OS-managed exclusive lock for one service per state directory."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = os.path.normcase(str(self.path))
        with _LOCAL_LOCK_GUARD:
            if self._key in _LOCAL_LOCKED_PATHS:
                raise RuntimeError(
                    f"该工作台数据目录已有服务实例运行，拒绝并发写入：{self.path.parent}")
            _LOCAL_LOCKED_PATHS.add(self._key)
        try:
            self._stream = self.path.open("a+b")
            if self.path.stat().st_size == 0:
                self._stream.write(b"\0")
                self._stream.flush()
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream = getattr(self, "_stream", None)
            if stream is not None:
                stream.close()
            with _LOCAL_LOCK_GUARD:
                _LOCAL_LOCKED_PATHS.discard(self._key)
            raise RuntimeError(
                f"该工作台数据目录已有服务实例运行，拒绝并发写入：{self.path.parent}") from exc

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            stream.close()
            with _LOCAL_LOCK_GUARD:
                _LOCAL_LOCKED_PATHS.discard(self._key)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def core_source_files(generator_dir: Path = GENERATOR_DIR) -> list[Path]:
    """Files whose changes can affect generated geometry or its delivered case."""
    root = Path(generator_dir).resolve()
    paths = [root / "generate.py", root / "parameter_schema.json",
             root / "render_delivery.py", root / "viewer.js", root / "viewer.css",
             root / "viewer.template.html", root / "layout.js", root / "layout.template.html",
             root / "vendor" / "viewer-runtime.js"]
    paths.extend(sorted((root / "engine").glob("*.py")))
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("生成核心文件缺失，无法建立计算身份：" + ", ".join(missing))
    return paths


def source_fingerprint(generator_dir: Path = GENERATOR_DIR) -> str:
    root = Path(generator_dir).resolve()
    digest = hashlib.sha256()
    for path in core_source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content_hash = hashlib.sha256(path.read_bytes()).digest()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(content_hash)
    return digest.hexdigest()


def normalize_identity_config(normalized: dict[str, Any]) -> dict[str, Any]:
    """Drop display-only case_id and derived values from geometric identity."""
    return {
        "corner_angle_deg": normalized["corner_angle_deg"],
        "parameters": normalized["parameters"],
        "advanced": normalized["advanced"],
    }


def calculation_key(normalized: dict[str, Any], fingerprint: str) -> str:
    identity = {"config": normalize_identity_config(normalized),
                "source_fingerprint": fingerprint}
    return hashlib.sha256(_stable_json(identity)).hexdigest()


def _json_file_hashes(output_dir: Path) -> dict[str, str]:
    manifest = load_json(output_dir / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("manifest.json格式无效")
    if manifest.get("file_count") != len(manifest["files"]):
        raise ValueError("manifest文件数量与清单不匹配")
    hashes: dict[str, str] = {}
    output_root = output_dir.resolve()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("manifest含无效文件条目")
        name = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("manifest含无效相对路径")
        rel = PurePosixPath(name)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
            raise ValueError("manifest含越界相对路径")
        target = output_root.joinpath(*rel.parts).resolve()
        try:
            target.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("manifest文件解析到输出目录之外") from exc
        if not target.is_file():
            raise ValueError(f"manifest文件不存在：{name}")
        if entry.get("bytes") != target.stat().st_size:
            raise ValueError(f"manifest文件长度不匹配：{name}")
        actual = sha256_file(target)
        if not isinstance(digest, str) or digest != actual:
            raise ValueError(f"manifest哈希不匹配：{name}")
        if name in hashes:
            raise ValueError(f"manifest文件重复：{name}")
        hashes[name] = actual
    if not REQUIRED_ARTIFACTS.issubset(hashes):
        missing = sorted(REQUIRED_ARTIFACTS - set(hashes))
        raise ValueError("输出缺少必需文件：" + ", ".join(missing))
    return hashes


def _safe_output_dir(outputs_dir: Path, output_rel: str) -> Path:
    rel = PurePosixPath(output_rel)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError("登记的输出相对路径无效")
    root = Path(outputs_dir).resolve()
    out = root.joinpath(*rel.parts).resolve()
    try:
        out.relative_to(root)
    except ValueError as exc:
        raise ValueError("登记的输出目录越界") from exc
    return out


class WorkbenchBackend:
    def __init__(self, *, generator_dir: Path = GENERATOR_DIR,
                 workspace_root: Path | None = None, outputs_dir: Path | None = None,
                 ui_dir: Path | None = None, state_dir: Path | None = None,
                 source_fingerprint_provider: Callable[[], str] | None = None,
                 command_builder: Callable[[Path, Path], list[str]] | None = None,
                 seed_existing: bool = True):
        self.generator_dir = Path(generator_dir).resolve()
        self.workspace_root = Path(workspace_root or self.generator_dir.parents[1]).resolve()
        self.outputs_dir = Path(outputs_dir or self.generator_dir / "outputs").resolve()
        self.ui_dir = Path(ui_dir or self.generator_dir / "ui").resolve()
        self.state_dir = Path(state_dir or self.generator_dir / "ui_state").resolve()
        self.state_path = self.state_dir / "workbench_state.json"
        self.job_config_dir = self.state_dir / "job_configs"
        self.source_fingerprint_provider = source_fingerprint_provider or (
            lambda: source_fingerprint(self.generator_dir))
        self.command_builder = command_builder or self._build_generator_command
        self.lock = threading.RLock()
        self.math_lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_requested: set[str] = set()
        self._closed = False
        self._config_module = self._load_config_module()
        self._generation_module = None
        self._stage05_module = None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.job_config_dir.mkdir(parents=True, exist_ok=True)
        self._instance_lock = StateDirectoryLock(self.state_dir / ".workbench.lock")
        if self.state_path.exists():
            try:
                self._load_state()
                self._mark_orphaned_jobs_interrupted()
            except Exception:
                self._instance_lock.close()
                raise
        else:
            try:
                self.state = {"version": 1, "cases": [], "jobs": [],
                              "initialized_at": utc_now(), "seeded_at": None,
                              "seed_source_fingerprint": None}
                if seed_existing:
                    self._seed_existing_once()
                self._save_state()
            except Exception:
                self._instance_lock.close()
                raise

    def _load_config_module(self):
        path = self.generator_dir / "engine" / "config.py"
        spec = importlib.util.spec_from_file_location("workbench_parametric_config", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载参数校验器")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _load_generation_helpers(self):
        if self._generation_module is not None:
            return self._generation_module, self._stage05_module
        model_deps = self.workspace_root / "资料整理" / "tmp" / "model_deps"
        plot_deps = self.workspace_root / "资料整理" / "tmp" / "section_plot_deps"
        for path in (str(self.generator_dir / "engine"), str(plot_deps), str(model_deps)):
            if path not in sys.path:
                sys.path.insert(0, path)
        spec = importlib.util.spec_from_file_location(
            "workbench_generation_helpers", self.generator_dir / "generate.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载生成器的快速几何派生函数")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stage05 = module.load_stage05()
        self._generation_module, self._stage05_module = module, stage05
        return module, stage05

    def _derive_geometry(self, normalized: dict[str, Any]) -> dict[str, Any]:
        with self.math_lock:
            module, stage05 = self._load_generation_helpers()
            case, arc, young_sagitta = module.derived_geometry_values(normalized, stage05)
            division_count_n = int(case["fan_surface"]["n"])
            module.update_resolved_geometry_derivations(normalized, arc, young_sagitta, division_count_n)
            return normalized["derived"]

    def validate(self, config: Any) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        try:
            normalized = self._config_module.resolve_config(config)
            derived = self._derive_geometry(normalized)
            fingerprint = self.source_fingerprint_provider()
            normalized_config = {
                "case_id": normalized["case_id"],
                "corner_angle_deg": normalized["corner_angle_deg"],
                "parameters": normalized["parameters"],
                "advanced": normalized["advanced"],
            }
            key = calculation_key(normalized, fingerprint)
            response = {"valid": True, "normalized_config": normalized_config,
                        "calculation_key": key, "derived": dict(derived), "warnings": []}
            return response, normalized, key, fingerprint
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise WorkbenchError(400, str(exc), [str(exc)]) from exc
        except Exception as exc:
            raise WorkbenchError(500, "参数快速派生失败", [f"{type(exc).__name__}: {exc}"]) from exc

    def bootstrap(self) -> dict[str, Any]:
        schema = load_json(self.generator_dir / "parameter_schema.json")
        default_raw = load_json(self.generator_dir / "configs" / "default.json")
        examples = []
        for example_id, title, name in (
                ("default", "默认参数", "default.json"),
                ("size_variant_valid64", "尺寸与间距变化示例", "size_variant_valid64.json"),
                ("angle_60", "60° 对称角部参数", "angle_60.json"),
                ("angle_120", "120° 对称角部参数", "angle_120.json")):
            if not (self.generator_dir / "configs" / name).is_file():
                continue
            config = load_json(self.generator_dir / "configs" / name)
            examples.append({"id": example_id, "title": title, "config": config})
        defaults, _, _, _ = self.validate(default_raw)
        with self.lock:
            active = self._active_job_locked()
            active_public = self._public_job(active) if active is not None else None
        return {"version": API_VERSION, "app_id": APP_ID,
                "workspace": str(self.workspace_root), "schema": schema,
                "defaults": defaults["normalized_config"], "examples": examples,
                "cases": self.list_cases(), "active_job": active_public,
                "reference_runtime_seconds": [780, 960]}

    def list_cases(self) -> list[dict[str, Any]]:
        with self.lock:
            records = list(self.state["cases"])
        result = []
        for record in records:
            if not record.get("validated"):
                continue
            try:
                self._verify_case_record(record, check_hashes=True)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            result.append(self._public_case(record))
        return result

    def _seed_existing_once(self) -> None:
        """Register verified handoff outputs with their recorded source identity."""
        try:
            catalog = load_json(self.generator_dir / "configs" / "seed_catalog.json")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            catalog = None
        if (not isinstance(catalog, dict) or catalog.get("version") != 1 or
                not isinstance(catalog.get("cases"), list)):
            catalog = None

        catalog_entries: dict[str, dict[str, Any]] = {}
        if catalog is not None:
            for entry in catalog["cases"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    catalog_entries = {}
                    break
                if entry["id"] in catalog_entries:
                    catalog_entries = {}
                    break
                catalog_entries[entry["id"]] = entry
            if set(catalog_entries) != {case_id for case_id, _, _ in SEED_CASES}:
                catalog_entries = {}

        seeded = []
        for case_id, title, config_file in SEED_CASES:
            entry = catalog_entries.get(case_id)
            if not isinstance(entry, dict):
                continue
            output_dir = self.outputs_dir / case_id
            try:
                if (entry.get("title") != title or entry.get("config_file") != config_file or
                        entry.get("output_rel") != case_id):
                    continue
                fingerprint = entry.get("source_fingerprint")
                expected_key = entry.get("calculation_key")
                expected_hashes = entry.get("artifact_hashes")
                if (not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint) or
                        not isinstance(expected_key, str) or not SHA256_RE.fullmatch(expected_key) or
                        not isinstance(expected_hashes, dict) or not expected_hashes or
                        any(not isinstance(name, str) or not isinstance(digest, str) or
                            not SHA256_RE.fullmatch(digest)
                            for name, digest in expected_hashes.items())):
                    continue
                raw = load_json(self.generator_dir / "configs" / config_file)
                _, normalized, _, _ = self.validate(raw)
                key = calculation_key(normalized, fingerprint)
                if key != expected_key:
                    continue
                record = self._case_record_from_output(
                    case_id=case_id, title=title, raw_config=raw, normalized=normalized,
                    key=key, fingerprint=fingerprint, output_rel=case_id,
                    elapsed_seconds=self._saved_elapsed(output_dir))
                if record["artifact_hashes"] != expected_hashes:
                    continue
                seeded.append(record)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                # Keep a valid first-run installation usable if one optional seed is
                # absent or damaged. It is never silently re-registered later.
                continue
        self.state["cases"] = seeded
        self.state["seeded_at"] = utc_now()
        source_fingerprints = {record["source_fingerprint"] for record in seeded}
        self.state["seed_source_fingerprint"] = (
            next(iter(source_fingerprints)) if len(source_fingerprints) == 1 else None)

    @staticmethod
    def _saved_elapsed(output_dir: Path) -> float | None:
        try:
            value = load_json(output_dir / "validation.json").get("runtime_seconds_before_render")
            return float(value) if value is not None else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _load_state(self) -> None:
        try:
            state = load_json(self.state_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"工作台状态文件无法读取；为避免重登记旧结果，未自动重置：{self.state_path}: {exc}") from exc
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("cases"), list) or not isinstance(state.get("jobs"), list):
            raise RuntimeError(f"工作台状态结构无效；为避免重登记旧结果，未自动重置：{self.state_path}")
        self.state = state

    def _mark_orphaned_jobs_interrupted(self) -> None:
        changed = False
        now = time.time()
        for job in self.state["jobs"]:
            if job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job["stage"] = "服务已重启，生成任务中断"
                job["finished_at"] = utc_now()
                started = job.get("_started_epoch")
                if isinstance(started, (int, float)):
                    job["elapsed_seconds"] = max(0.0, round(now - float(started), 3))
                job["error"] = "服务在生成期间停止；未自动重启 CAD 任务"
                self._append_log(job, "workbench: service restarted; task marked interrupted")
                changed = True
        if changed:
            self._save_state()

    def _save_state(self) -> None:
        self.state["jobs"] = self.state["jobs"][-MAX_JOBS:]
        payload = json_bytes(self.state)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_dir / f"workbench_state.{uuid.uuid4().hex}.tmp"
        with tmp.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + STATE_REPLACE_RETRY_SECONDS
        delay = STATE_REPLACE_INITIAL_DELAY_SECONDS
        while True:
            try:
                os.replace(tmp, self.state_path)
                return
            except OSError as exc:
                if (os.name != "nt" or
                        getattr(exc, "winerror", None) not in STATE_REPLACE_RETRY_WINERRORS):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, STATE_REPLACE_MAX_DELAY_SECONDS)

    def _case_record_from_output(self, *, case_id: str, title: str,
                                 raw_config: dict[str, Any], normalized: dict[str, Any],
                                 key: str, fingerprint: str, output_rel: str,
                                 elapsed_seconds: float | None) -> dict[str, Any]:
        output_dir = _safe_output_dir(self.outputs_dir, output_rel)
        hashes = _json_file_hashes(output_dir)
        manifest = load_json(output_dir / "manifest.json")
        resolved = load_json(output_dir / "parameters_resolved.json")
        validation = load_json(output_dir / "validation.json")
        mesh = load_json(output_dir / "model_mesh.json")
        profiles_doc = load_json(output_dir / "profiles.json")
        layout_doc = load_json(output_dir / "layout.json")
        if manifest.get("case_id") != normalized["case_id"]:
            raise ValueError("manifest案例标识与配置不一致")
        if resolved.get("case_id") != normalized["case_id"]:
            raise ValueError("保存的参数与配置案例标识不一致")
        if resolved.get("corner_angle_deg") != normalized["corner_angle_deg"]:
            raise ValueError("保存的建筑平面内角与本次配置不一致")
        if mesh.get("metadata", {}).get("corner_angle_deg", 90.0) != normalized["corner_angle_deg"]:
            raise ValueError("模型网格的建筑平面内角与本次配置不一致")
        if resolved.get("parameters") != normalized["parameters"] or resolved.get("advanced") != normalized["advanced"]:
            raise ValueError("保存的解析参数与本次配置不一致")
        if validation.get("passed") is not True:
            raise ValueError("几何验证未通过")
        expected = validation.get("input_geometry", {}).get("fan_count_expected_per_side")
        actual = validation.get("input_geometry", {}).get("fan_count_actual_per_side")
        division_count_n = validation.get("input_geometry", {}).get("fan_division_count_n")
        resolved_division_count_n = resolved.get("derived", {}).get("fan_division_count_n")
        profiles = profiles_doc.get("profiles")
        parts = mesh.get("parts")
        pieces = layout_doc.get("layout", {}).get("pieces")
        if not isinstance(profiles, list) or not isinstance(parts, list) or not isinstance(pieces, list):
            raise ValueError("模型、排料或验证数据结构不完整")
        if (not isinstance(division_count_n, int) or division_count_n < 3 or division_count_n % 2 == 0 or
                division_count_n != resolved_division_count_n or expected != division_count_n - 1):
            raise ValueError("弧等分区间n、派生计数与n−1根内部摔网椽语义不一致")
        if expected != actual or actual != len(profiles) or len(pieces) != len(profiles):
            raise ValueError("根数、模型与排料记录不一致")
        if profiles_doc.get("source_mesh_sha256") != sha256_file(output_dir / "model_mesh.json"):
            raise ValueError("profiles来源网格哈希不匹配")
        model_ids = {str(part.get("id")) for part in parts}
        for piece in pieces:
            ids = piece.get("model_ids")
            if not isinstance(ids, list) or len(ids) != 2 or not set(map(str, ids)).issubset(model_ids):
                raise ValueError("排料模板未映射到模型两侧构件编号")
        retain = layout_doc.get("layout", {}).get("retain_ratio_percent")
        board = layout_doc.get("layout", {})
        metrics = {
            "templates_per_side": len(profiles),
            "parts_count": len(parts),
            "board_width_mm": board.get("board_width_x_mm"),
            "board_height_mm": board.get("board_height_y_mm"),
            "board_thickness_mm": board.get("board_thickness_mm"),
            "utilization_percent": retain,
            "translation_max_error_mm": layout_doc.get("translation", {}).get("max_abs_error_mm"),
        }
        return {
            "id": case_id, "title": str(title), "config": raw_config,
            "calculation_key": key, "validated": True, "created_at": utc_now(),
            "elapsed_seconds": elapsed_seconds, "metrics": metrics,
            "urls": self._case_urls(case_id), "source_fingerprint": fingerprint,
            "output_rel": PurePosixPath(output_rel).as_posix(), "artifact_hashes": hashes,
        }

    @staticmethod
    def _case_urls(case_id: str) -> dict[str, str]:
        base = f"/results/{case_id}"
        return {"model": f"{base}/model.html", "layout": f"{base}/layout.html",
                "index": f"{base}/index.html", "obj": f"{base}/model.obj",
                "points": f"{base}/marking_points.csv", "svg": f"{base}/layout.svg",
                "config": f"{base}/config.json", "validation": f"{base}/validation.json"}

    @staticmethod
    def _public_case(record: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "title", "config", "calculation_key", "validated",
                "created_at", "elapsed_seconds", "metrics", "urls")
        return {key: record.get(key) for key in keys}

    def _verify_case_record(self, record: dict[str, Any], *, check_hashes: bool) -> Path:
        case_id = record.get("id")
        output_rel = record.get("output_rel")
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise ValueError("案例登记ID无效")
        if not isinstance(output_rel, str):
            raise ValueError("案例未登记输出目录")
        output_dir = _safe_output_dir(self.outputs_dir, output_rel)
        if check_hashes:
            hashes = _json_file_hashes(output_dir)
            if hashes != record.get("artifact_hashes"):
                raise ValueError("案例输出文件与登记哈希不一致")
        validation = load_json(output_dir / "validation.json")
        if validation.get("passed") is not True or not record.get("validated"):
            raise ValueError("案例验证状态不再有效")
        return output_dir

    def _find_cached_case(self, key: str, fingerprint: str) -> dict[str, Any] | None:
        with self.lock:
            candidates = list(reversed(self.state["cases"]))
        for record in candidates:
            if record.get("calculation_key") != key or record.get("source_fingerprint") != fingerprint:
                continue
            try:
                self._verify_case_record(record, check_hashes=True)
                return record
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return None

    def submit_job(self, config: Any) -> tuple[dict[str, Any], bool]:
        validated, normalized, key, fingerprint = self.validate(config)
        cached = self._find_cached_case(key, fingerprint)
        if cached is not None:
            now = utc_now()
            job = {"id": "job-" + uuid.uuid4().hex, "status": "succeeded",
                   "stage": "载入已验证的缓存结果", "started_at": now,
                   "finished_at": now, "elapsed_seconds": 0.0,
                   "case_id": cached["id"], "result": self._public_case(cached),
                   "log_tail": "参数和生成核心指纹匹配，已检查文件完整性并复用缓存结果。",
                   "error": None, "_started_epoch": time.time(),
                   "_finished_epoch": time.time(), "_calculation_key": key,
                   "_source_fingerprint": fingerprint}
            with self.lock:
                self.state["jobs"].append(job)
                self._save_state()
            return {"job": self._public_job(job), "reused": True}, True

        with self.lock:
            if self._active_job_locked() is not None:
                raise WorkbenchError(409, "已有生成任务运行中；请等待完成或取消当前任务")
            job_id = "job-" + uuid.uuid4().hex
            case_id = "case-" + uuid.uuid4().hex
            output_rel = f"workbench/{case_id}"
            output_dir = _safe_output_dir(self.outputs_dir, output_rel)
            output_dir.mkdir(parents=True, exist_ok=False)
            config_path = self.job_config_dir / f"{job_id}.json"
            config_path.write_bytes(json_bytes(config))
            now_epoch = time.time()
            job = {"id": job_id, "status": "queued", "stage": "排队等待启动",
                   "started_at": None, "finished_at": None,
                   "elapsed_seconds": None, "case_id": case_id, "result": None,
                   "log_tail": "", "error": None, "_started_epoch": None,
                   "_finished_epoch": None, "_created_epoch": now_epoch,
                   "_calculation_key": key, "_source_fingerprint": fingerprint,
                   "_output_rel": output_rel, "_config_path": str(config_path),
                   "_normalized_config": validated["normalized_config"],
                   "_raw_config": config}
            self.state["jobs"].append(job)
            thread = threading.Thread(target=self._run_job,
                                      args=(job_id, output_dir, config_path, normalized, key, fingerprint),
                                      name=f"workbench-{job_id[-8:]}", daemon=True)
            self._threads[job_id] = thread
            self._save_state()
            thread.start()
            return {"job": self._public_job(job), "reused": False}, False

    def _build_generator_command(self, config_path: Path, output_path: Path) -> list[str]:
        return [sys.executable, "-X", "utf8", "-u", str(self.generator_dir / "generate.py"),
                "--config", str(config_path), "--output", str(output_path)]

    def _run_job(self, job_id: str, output_dir: Path, config_path: Path,
                 normalized: dict[str, Any], key: str, fingerprint: str) -> None:
        process: subprocess.Popen[str] | None = None
        skip_start = False
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None or job.get("status") != "queued" or self._closed:
                skip_start = True
            else:
                started = time.time()
                job["status"] = "running"
                job["stage"] = "启动生成子进程"
                job["started_at"] = utc_now()
                job["_started_epoch"] = started
                self._save_state()
        if skip_start:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass
            with self.lock:
                self._threads.pop(job_id, None)
                self._save_state()
            return
        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(
                self.command_builder(config_path, output_dir), cwd=str(self.generator_dir),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            with self.lock:
                self._processes[job_id] = process
                cancel_now = job_id in self._cancel_requested or self._closed
            if cancel_now:
                self._terminate_process(process)
            if process.stdout is not None:
                for line in process.stdout:
                    self._receive_log_line(job_id, line.rstrip("\r\n"))
            return_code = process.wait()
            with self.lock:
                job = self._find_job_locked(job_id)
                if job is None or job.get("status") in ("interrupted", "cancelled"):
                    return
                if job_id in self._cancel_requested:
                    self._finish_job_locked(job, "cancelled", "生成任务已取消",
                                            error="任务已取消；其未完成输出不会作为案例提供")
                    return
            if return_code != 0:
                tail = self._last_error_lines(job_id)
                message = f"生成进程退出码 {return_code}"
                if tail:
                    message += "：" + tail[-1800:]
                with self.lock:
                    job = self._find_job_locked(job_id)
                    if job and job.get("status") not in ("interrupted", "cancelled"):
                        self._finish_job_locked(job, "failed", "生成失败", error=message)
                return
            current_fingerprint = self.source_fingerprint_provider()
            if current_fingerprint != fingerprint:
                raise ValueError("生成期间核心源码发生变化；本次结果不登记为当前核心缓存")
            output_rel = self._job_output_rel(job_id)
            raw_config = self._job_raw_config(job_id)
            case_id = self._job_case_id(job_id)
            title = str(normalized["case_id"])
            elapsed = self._job_elapsed(job_id)
            record = self._case_record_from_output(
                case_id=case_id, title=title, raw_config=raw_config,
                normalized=normalized, key=key, fingerprint=fingerprint,
                output_rel=output_rel, elapsed_seconds=elapsed)
            with self.lock:
                job = self._find_job_locked(job_id)
                if job is None or job.get("status") in ("interrupted", "cancelled"):
                    return
                # A success result is appended as a new independent case. Existing
                # cases are immutable and are never replaced on failure or cancel.
                self.state["cases"].append(record)
                job["result"] = self._public_case(record)
                self._finish_job_locked(job, "succeeded", "生成完成")
        except Exception as exc:
            with self.lock:
                job = self._find_job_locked(job_id)
                if job and job.get("status") not in ("interrupted", "cancelled"):
                    self._finish_job_locked(job, "failed", "生成失败", error=f"{type(exc).__name__}: {exc}")
        finally:
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                with self.lock:
                    self._processes.pop(job_id, None)
            with self.lock:
                self._cancel_requested.discard(job_id)
                self._threads.pop(job_id, None)
                self._save_state()
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _job_output_rel(self, job_id: str) -> str:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                raise ValueError("生成任务记录不存在")
            return str(job["_output_rel"])

    def _finish_job_locked(self, job: dict[str, Any], status: str, stage: str,
                            *, error: str | None = None) -> None:
        finished = time.time()
        started = job.get("_started_epoch")
        job["status"] = status
        job["stage"] = stage
        job["finished_at"] = utc_now()
        job["_finished_epoch"] = finished
        job["elapsed_seconds"] = (max(0.0, round(finished - float(started), 3))
                                  if isinstance(started, (int, float)) else 0.0)
        job["error"] = error
        self._save_state()

    def _job_raw_config(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                raise ValueError("生成任务记录不存在")
            return job["_raw_config"]

    def _job_case_id(self, job_id: str) -> str:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                raise ValueError("生成任务记录不存在")
            return str(job["case_id"])

    def _job_elapsed(self, job_id: str) -> float:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                return 0.0
            started = job.get("_started_epoch")
            return max(0.0, round(time.time() - float(started), 3)) if started else 0.0

    def _receive_log_line(self, job_id: str, line: str) -> None:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                return
            self._append_log(job, line)
            stage = self._stage_from_log(line)
            if stage:
                job["stage"] = stage
            self._save_state()

    @staticmethod
    def _append_log(job: dict[str, Any], line: str) -> None:
        old = job.get("log_tail", "")
        joined = (old + "\n" + line).strip() if old else line
        job["log_tail"] = joined[-MAX_LOG_CHARS:]

    @staticmethod
    def _stage_from_log(line: str) -> str | None:
        lower = line.lower()
        mappings = (
            ("generator: validated", "参数已校验，准备几何计算"),
            ("stage04: stage02 framing rebuild start", "建立基础屋架与戗角几何"),
            ("stage04: stage02 framing rebuild complete", "基础屋架完成，生成嫩戗与里口"),
            ("stage04: young corner", "生成嫩戗、弯里口与板面"),
            ("stage04: board mesh sampled", "采样板面并求解各根飞椽姿态"),
            ("stage04: preflight complete", "姿态预检完成，开始构建飞椽实体"),
            ("stage05: dynamic stage04 base start", "建立参数化基础构件"),
            ("stage05: dynamic stage04 base ready", "基础构件完成，准备飞椽实体"),
            ("stage05: flyer brep build start", "构建立脚与正身飞椽实体"),
            ("stage05: flyer ", "构建立脚与正身飞椽实体"),
            ("generator: standing/straight flyer solids start", "构建立脚与正身飞椽实体"),
            ("generator: likou seat refit start", "逐根重切飞椽里口承口"),
            ("stage05: likou notch cutters start", "逐根生成里口切槽"),
            ("stage05: likou cutter ", "逐根生成里口切槽"),
            ("stage05: likou x boolean start", "切削 X 侧里口承口"),
            ("stage05: likou y boolean start", "切削 Y 侧里口承口"),
            ("stage05: likou x boolean complete", "X 侧里口承口完成"),
            ("stage05: likou y boolean complete", "Y 侧里口承口完成"),
            ("generator: upper roll-board rebuild start", "重建上层卷戗板"),
            ("generator: combination timber rebuild start", "放样并切削组合木"),
            ("stage05: combination stock loft", "放样组合木料坯"),
            ("stage05: subtracting old/young breps", "按老戗与嫩戗实体切削组合木"),
            ("stage05: combination brep cut complete", "组合木切削完成"),
            ("generator: geometry complete", "实体完成，导出模型网格"),
            ("generator: mesh/obj complete", "模型网格完成，提取飞椽中心截面"),
            ("generator: profiles ready", "中心截面完成，计算单列排料"),
            ("generator: layout and spline calculations complete", "排料与样条误差计算完成"),
            ("generator: offline viewer rendering start", "生成离线模型与排料查看页"),
            ("generator: offline viewer rendering complete", "离线查看页生成完成"),
        )
        for needle, stage in mappings:
            if needle in lower:
                return stage
        return None

    def _last_error_lines(self, job_id: str) -> str:
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                return ""
            lines = [line.strip() for line in str(job.get("log_tail", "")).splitlines()
                     if line.strip()]
        selected = [line for line in lines if "traceback" in line.lower() or
                    "error" in line.lower() or "exception" in line.lower()]
        return "\n".join((selected or lines)[-8:])

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
        except OSError:
            pass

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.fullmatch(job_id):
            raise WorkbenchError(404, "未找到该生成任务")
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                raise WorkbenchError(404, "未找到该生成任务")
            if job.get("status") not in ("queued", "running"):
                return self._public_job(job)
            self._cancel_requested.add(job_id)
            job["stage"] = "正在取消生成任务"
            self._append_log(job, "workbench: cancellation requested")
            process = self._processes.get(job_id)
            self._save_state()
        if process is not None:
            self._terminate_process(process)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.fullmatch(job_id):
            raise WorkbenchError(404, "未找到该生成任务")
        with self.lock:
            job = self._find_job_locked(job_id)
            if job is None:
                raise WorkbenchError(404, "未找到该生成任务")
            return self._public_job(job)

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "status", "stage", "started_at", "finished_at",
                "elapsed_seconds", "case_id", "result", "log_tail", "error")
        public = {key: job.get(key) for key in keys}
        if public["status"] in ("queued", "running"):
            started = job.get("_started_epoch")
            if isinstance(started, (int, float)):
                public["elapsed_seconds"] = max(0.0, round(time.time() - float(started), 3))
        return public

    def _find_job_locked(self, job_id: str) -> dict[str, Any] | None:
        return next((job for job in reversed(self.state["jobs"]) if job.get("id") == job_id), None)

    def _active_job_locked(self) -> dict[str, Any] | None:
        return next((job for job in reversed(self.state["jobs"])
                     if job.get("status") in ("queued", "running")), None)

    def result_file(self, case_id: str, relative_name: str) -> tuple[bytes, str, str]:
        if not CASE_ID_RE.fullmatch(case_id):
            raise WorkbenchError(404, "未找到该结果")
        with self.lock:
            record = next((case for case in self.state["cases"] if case.get("id") == case_id), None)
            if record is None or not record.get("validated"):
                raise WorkbenchError(404, "未找到已验证的结果")
            output_rel = record.get("output_rel")
            hashes = dict(record.get("artifact_hashes", {}))
            raw_config = record.get("config")
        if relative_name == "config.json":
            return json_bytes(raw_config), "application/json; charset=utf-8", 'attachment; filename="config.json"'
        rel = PurePosixPath(relative_name)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
            raise WorkbenchError(404, "结果文件路径无效")
        canonical = rel.as_posix()
        if canonical not in hashes:
            raise WorkbenchError(404, "该文件未登记为此案例的输出")
        try:
            output_dir = _safe_output_dir(self.outputs_dir, output_rel)
            target = output_dir.joinpath(*rel.parts).resolve()
            target.relative_to(output_dir)
            if not target.is_file():
                raise FileNotFoundError(canonical)
            data = target.read_bytes()
        except (OSError, ValueError) as exc:
            raise WorkbenchError(404, "结果文件不存在或路径无效") from exc
        mime, _ = mimetypes.guess_type(target.name)
        if target.suffix.lower() in (".json", ".html", ".js", ".css", ".csv", ".svg", ".obj"):
            mime = f"{mime or 'application/octet-stream'}; charset=utf-8"
        return data, mime or "application/octet-stream", ""

    def close(self) -> None:
        with self.lock:
            self._closed = True
            active = self._active_job_locked()
            process = self._processes.get(active["id"]) if active else None
            threads = list(self._threads.values())
            if active:
                active["status"] = "interrupted"
                active["stage"] = "服务关闭，任务中断"
                active["finished_at"] = utc_now()
                started = active.get("_started_epoch")
                if isinstance(started, (int, float)):
                    active["elapsed_seconds"] = max(0.0, round(time.time() - float(started), 3))
                active["error"] = "服务关闭；未自动重启 CAD 任务"
                self._append_log(active, "workbench: server shutdown; task marked interrupted")
                self._save_state()
        if process is not None:
            self._terminate_process(process)
        current = threading.current_thread()
        for thread in threads:
            if thread is not current:
                thread.join(timeout=5.0)
        if not any(thread.is_alive() for thread in threads):
            self._instance_lock.close()


def _safe_static_path(root: Path, url_path: str) -> Path:
    decoded = unquote(url_path)
    if "\x00" in decoded or "\\" in decoded:
        raise WorkbenchError(404, "静态资源路径无效")
    rel = PurePosixPath(decoded.lstrip("/"))
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise WorkbenchError(404, "静态资源路径无效")
    resolved_root = root.resolve()
    target = resolved_root.joinpath(*rel.parts).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise WorkbenchError(404, "静态资源路径无效") from exc
    if not target.is_file():
        raise WorkbenchError(404, "未找到静态资源")
    return target


def create_server(backend: WorkbenchBackend, host: str = "127.0.0.1", port: int = 8765):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if host != "127.0.0.1":
        raise ValueError("本地工作台只能绑定127.0.0.1")

    class WorkbenchHTTPServer(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

    class Handler(BaseHTTPRequestHandler):
        server_version = f"MuGouzaoWorkbench/{API_VERSION}"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            # Use only a terse local service log; request contents may contain user data.
            sys.stderr.write("workbench-http: " + (format % args) + "\n")

        def _json_response(self, status: int, payload: Any, head_only: bool = False) -> None:
            self._send_bytes(status, json_bytes(payload), "application/json; charset=utf-8",
                             head_only=head_only)

        def _send_bytes(self, status: int, data: bytes, content_type: str,
                        disposition: str = "", head_only: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def _error(self, status: int, error: str, details: list[str] | None = None,
                   head_only: bool = False) -> None:
            payload = {"error": error, "details": list(details or [])}
            self._send_bytes(status, json_bytes(payload), "application/json; charset=utf-8",
                             head_only=head_only)

        def _host_allowed(self) -> bool:
            host_header = self.headers.get("Host", "").lower()
            server_port = int(self.server.server_address[1])
            return host_header in (f"127.0.0.1:{server_port}", f"localhost:{server_port}")

        def _write_origin_allowed(self) -> bool:
            if not self._host_allowed():
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
            if fetch_site == "cross-site":
                return False
            origin = self.headers.get("Origin")
            if origin is None:
                return fetch_site in ("", "same-origin")
            if origin == "null":
                return False
            parsed = urlsplit(origin)
            return (parsed.scheme == "http" and
                    parsed.netloc.lower() == self.headers.get("Host", "").lower() and
                    parsed.path in ("", "/") and not parsed.query and not parsed.fragment)

        def _read_json_body(self) -> Any:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise WorkbenchError(415, "请求必须使用 application/json")
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise WorkbenchError(400, "Content-Length无效") from exc
            if length < 0 or length > MAX_BODY_BYTES:
                raise WorkbenchError(413, "请求内容长度无效或超过限制")
            try:
                body = self.rfile.read(length)
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkbenchError(400, "请求不是有效的UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise WorkbenchError(400, "请求JSON顶层必须是对象")
            return value

        def _dispatch_get(self, head_only: bool = False) -> None:
            path = urlsplit(self.path).path
            if not self._host_allowed():
                self._error(403, "仅允许通过本地回环地址访问", head_only=head_only)
                return
            try:
                if path == "/" or path == "/ui" or path.startswith("/ui/"):
                    if path == "/ui":
                        self.send_response(301)
                        self.send_header("Location", "/ui/")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if path == "/":
                        candidate = backend.ui_dir / "index.html"
                        if not candidate.is_file():
                            fallback = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                                        "<title>本地工作台</title><body><p>工作台后端已启动，等待前端文件 "
                                        "ui/index.html。</p></body></html>").encode("utf-8")
                            self._send_bytes(503, fallback, "text/html; charset=utf-8", head_only=head_only)
                            return
                        target = candidate.resolve()
                        target.relative_to(backend.ui_dir.resolve())
                    else:
                        suffix = path[len("/ui/"):]
                        if not suffix:
                            suffix = "index.html"
                        elif suffix.endswith("/"):
                            suffix += "index.html"
                        target = _safe_static_path(backend.ui_dir, suffix)
                    mime, _ = mimetypes.guess_type(target.name)
                    content_type = f"{mime or 'application/octet-stream'}; charset=utf-8" if target.suffix.lower() in (".html", ".css", ".js", ".json", ".svg") else (mime or "application/octet-stream")
                    self._send_bytes(200, target.read_bytes(), content_type, head_only=head_only)
                    return
                if path.startswith("/results/"):
                    segments = path.split("/")
                    if len(segments) < 4:
                        raise WorkbenchError(404, "未找到该结果文件")
                    case_id = unquote(segments[2])
                    if "/" in case_id or "\\" in case_id:
                        raise WorkbenchError(404, "结果案例ID无效")
                    relative = unquote("/".join(segments[3:]))
                    data, mime, disposition = backend.result_file(case_id, relative)
                    self._send_bytes(200, data, mime, disposition, head_only=head_only)
                    return
                if path == "/api/bootstrap":
                    self._json_response(200, backend.bootstrap(), head_only=head_only)
                    return
                if path == "/api/cases":
                    self._json_response(200, {"cases": backend.list_cases()}, head_only=head_only)
                    return
                if path.startswith("/api/jobs/"):
                    job_id = unquote(path[len("/api/jobs/"):])
                    if "/" in job_id or "\\" in job_id:
                        raise WorkbenchError(404, "未找到该生成任务")
                    self._json_response(200, {"job": backend.get_job(job_id)}, head_only=head_only)
                    return
                raise WorkbenchError(404, "未找到该接口")
            except WorkbenchError as exc:
                self._error(exc.status, exc.error, exc.details, head_only=head_only)
            except (OSError, ValueError, TypeError) as exc:
                self._error(500, "读取本地结果失败", [str(exc)], head_only=head_only)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler method
            self._dispatch_get()

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler method
            self._dispatch_get(head_only=True)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler method
            if not self._write_origin_allowed():
                self._error(403, "已拒绝跨站写请求")
                return
            path = urlsplit(self.path).path
            try:
                body = self._read_json_body()
                if path == "/api/validate":
                    if set(body) != {"config"}:
                        raise WorkbenchError(400, "校验请求只允许config字段")
                    if "config" not in body:
                        raise WorkbenchError(400, "请求缺少config字段")
                    response, _, _, _ = backend.validate(body["config"])
                    self._json_response(200, response)
                    return
                if path == "/api/jobs":
                    if set(body) != {"config"}:
                        raise WorkbenchError(400, "生成请求只允许config字段")
                    if "config" not in body:
                        raise WorkbenchError(400, "请求缺少config字段")
                    response, _ = backend.submit_job(body["config"])
                    status = 200 if response["reused"] else 202
                    self._json_response(status, response)
                    return
                match = re.fullmatch(r"/api/jobs/([^/]+)/cancel", path)
                if match:
                    if body:
                        raise WorkbenchError(400, "取消请求必须使用空对象")
                    self._json_response(200, {"job": backend.cancel_job(unquote(match.group(1)))})
                    return
                raise WorkbenchError(404, "未找到该接口")
            except WorkbenchError as exc:
                self._error(exc.status, exc.error, exc.details)
            except (ValueError, TypeError) as exc:
                self._error(400, str(exc), [str(exc)])

        def do_OPTIONS(self) -> None:  # noqa: N802 - no CORS preflight is allowed
            self._error(405, "不提供跨站访问")

        def do_PUT(self) -> None:  # noqa: N802
            self._error(405, "仅支持GET和POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._error(405, "仅支持GET和POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._error(405, "仅支持GET和POST")

    return WorkbenchHTTPServer((host, int(port)), Handler)


def serve(port: int = 8765) -> None:
    if not (1 <= int(port) <= 65535):
        raise ValueError("端口必须在1到65535之间")
    backend = WorkbenchBackend()
    try:
        server = create_server(backend, port=int(port))
    except Exception:
        backend.close()
        raise
    print(f"workbench: app_id={APP_ID} workspace={backend.workspace_root}", flush=True)
    print(f"workbench: listening on http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        backend.close()

