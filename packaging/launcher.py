#!/usr/bin/env python
"""Start or stop the private, local-only MuGouzao workbench service."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from ctypes import wintypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any


APP_ID = "mu-gouzao-corner-workbench"
APP_VERSION = "0.4.1"
DEFAULT_PORT = 8765
READY_TIMEOUT_SECONDS = 120
APP_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_DIR = APP_ROOT / "生成器" / "直角戗角"
SERVER_SCRIPT = GENERATOR_DIR / "workbench_server.py"
RUNTIME_DIR = APP_ROOT / "runtime"
PYTHON_GUI = RUNTIME_DIR / "pythonw.exe"
PYTHON_CLI = RUNTIME_DIR / "python.exe"
RECORD_NAME = "launcher.json"
LOCK_NAME = ".launcher.lock"


def canonical(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.expandvars(str(path))))


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "MuGouzao"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="启动或停止木构造戗角生成器；本地服务仅监听本机。"
    )
    parser.add_argument("--port", type=int, help="本地端口；默认8765，占用时自动选择空闲端口")
    parser.add_argument("--data-dir", type=Path,
                        help="数据目录；默认 %%LOCALAPPDATA%%\\MuGouzao")
    parser.add_argument("--no-browser", action="store_true", help="启动服务但不打开浏览器")
    parser.add_argument("--stop", action="store_true", help="停止本安装启动的空闲服务")
    args = parser.parse_args(argv)
    if args.port is not None and not (1 <= args.port <= 65535):
        parser.error("--port必须在1到65535之间")
    if args.stop and args.port is not None:
        parser.error("--stop不能与--port一起使用")
    return args


class LaunchLock:
    """Serialize starts and stops for one data directory."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self.stream.close()
                raise RuntimeError("此数据目录已有启动或停止操作正在进行。") from exc
        else:
            import fcntl
            try:
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self.stream.close()
                raise RuntimeError("此数据目录已有启动或停止操作正在进行。") from exc

    def close(self) -> None:
        if self.stream.closed:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_UN)
        finally:
            self.stream.close()

    def __enter__(self) -> "LaunchLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def record_path(data_dir: Path) -> Path:
    return data_dir / RECORD_NAME


def load_record(data_dir: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(record_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def save_record(data_dir: Path, value: dict[str, Any]) -> None:
    path = record_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def remove_record(data_dir: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        record_path(data_dir).unlink()


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def process_details(pid: int) -> tuple[str, str] | None:
    if os.name != "nt":
        return None
    powershell = os.environ.get("SystemRoot", r"C:\Windows") + (
        r"\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    script = (
        "$p=Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId = "
        + str(int(pid))
        + "'; if ($p) { [Console]::OutputEncoding = New-Object "
        "System.Text.UTF8Encoding; [Console]::WriteLine($p.ExecutablePath); "
        "[Console]::WriteLine($p.CommandLine) }"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = completed.stdout.splitlines()
    if completed.returncode != 0 or len(lines) < 2 or not lines[0].strip():
        return None
    return lines[0].strip(), lines[1].strip()


def expected_process(record: dict[str, Any], data_dir: Path) -> bool:
    try:
        pid = int(record["pid"])
        port = int(record["port"])
    except (KeyError, TypeError, ValueError):
        return False
    if not process_exists(pid):
        return False
    details = process_details(pid)
    if details is None:
        return False
    executable, command_line = details
    if canonical(executable) != canonical(PYTHON_GUI):
        return False
    command = command_line.replace("/", "\\").casefold()
    expected_script = str(SERVER_SCRIPT).replace("/", "\\").casefold()
    return (expected_script in command and f"--port {port}" in command and
            canonical(record.get("data_dir", "")) == canonical(data_dir) and
            canonical(record.get("workspace", "")) == canonical(APP_ROOT))


def fetch_json(port: int, route: str, timeout: float = 2.0) -> dict[str, Any] | None:
    url = f"http://127.0.0.1:{int(port)}{route}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def is_our_service(port: int, data_dir: Path) -> dict[str, Any] | None:
    payload = fetch_json(port, "/api/bootstrap")
    if not payload:
        return None
    if (payload.get("app_id") != APP_ID or payload.get("version") != APP_VERSION or
            canonical(payload.get("workspace", "")) != canonical(APP_ROOT) or
            canonical(payload.get("data_directory", "")) != canonical(data_dir)):
        return None
    return payload


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
        return True


def log_dir(data_dir: Path) -> Path:
    path = data_dir / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tail_log(path: Path, limit: int = 8000) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - limit))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def make_record(pid: int, port: int, data_dir: Path, log_file: Path) -> dict[str, Any]:
    return {
        "app_id": APP_ID,
        "app_version": APP_VERSION,
        "pid": int(pid),
        "port": int(port),
        "workspace": str(APP_ROOT),
        "generator_directory": str(GENERATOR_DIR),
        "server_script": str(SERVER_SCRIPT),
        "python_executable": str(PYTHON_GUI),
        "data_dir": str(data_dir),
        "log_file": str(log_file),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def existing_record_service(data_dir: Path) -> dict[str, Any] | None:
    record = load_record(data_dir)
    if not record or not expected_process(record, data_dir):
        return None
    try:
        port = int(record["port"])
    except (KeyError, TypeError, ValueError):
        return None
    return record if is_our_service(port, data_dir) is not None else None


def command_line_matches(record: dict[str, Any], data_dir: Path) -> bool:
    return expected_process(record, data_dir) and (
        is_our_service(int(record["port"]), data_dir) is not None
    )


def stop_service(data_dir: Path) -> int:
    record = load_record(data_dir)
    if record is None:
        for port in (DEFAULT_PORT,):
            if is_our_service(port, data_dir) is not None:
                print("服务正在运行，但启动记录无法确认所属进程；未停止服务。")
                return 4
        print("没有本安装启动的后台服务。")
        return 0

    try:
        port = int(record["port"])
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        print("启动记录不完整；未停止任何进程。")
        return 4

    if (canonical(record.get("workspace", "")) != canonical(APP_ROOT) or
            canonical(record.get("data_dir", "")) != canonical(data_dir)):
        print("启动记录不属于此安装或数据目录；未停止任何进程。")
        return 4

    if not process_exists(pid):
        if is_our_service(port, data_dir) is not None:
            print("端口上的服务无法与启动记录关联；未停止任何进程。")
            return 4
        remove_record(data_dir)
        print("已清理过期的启动记录。")
        return 0

    if not command_line_matches(record, data_dir):
        print("进程或服务身份与此安装不符；未停止任何进程。")
        return 4

    bootstrap = is_our_service(port, data_dir)
    active = bootstrap.get("active_job") if bootstrap else None
    if isinstance(active, dict) and active.get("status") in {"queued", "running"}:
        print("当前仍有生成任务。任务完成后再停止或卸载。")
        return 3

    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    try:
        killed = subprocess.run(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("无法确认服务已停止；未移除启动记录。")
        return 4
    if killed.returncode != 0 and process_exists(pid):
        print("停止服务失败；未移除启动记录。")
        return 4

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and process_exists(pid):
        time.sleep(0.25)
    if process_exists(pid):
        print("服务仍在运行；未移除启动记录。")
        return 4
    remove_record(data_dir)
    print(f"已停止本安装的服务（端口 {port}）。")
    return 0


def start_service(data_dir: Path, requested_port: int | None, no_browser: bool) -> int:
    if not PYTHON_GUI.is_file() or not PYTHON_CLI.is_file():
        raise FileNotFoundError(f"缺少内置 Python 运行环境：{RUNTIME_DIR}")
    if not SERVER_SCRIPT.is_file():
        raise FileNotFoundError(f"找不到工作台服务：{SERVER_SCRIPT}")
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / LOCK_NAME
    with LaunchLock(lock_path):
        current = existing_record_service(data_dir)
        if current:
            port = int(current["port"])
            url = f"http://127.0.0.1:{port}/"
            print(f"已复用此安装和数据目录的服务：{url}")
            print(f"服务日志：{current.get('log_file', '')}")
            if not no_browser:
                webbrowser.open(url, new=2)
            return 0

        preferred = requested_port if requested_port is not None else DEFAULT_PORT
        if is_our_service(preferred, data_dir) is not None:
            url = f"http://127.0.0.1:{preferred}/"
            print(f"已复用此安装和数据目录的服务：{url}")
            if not no_browser:
                webbrowser.open(url, new=2)
            return 0

        if port_is_available(preferred):
            port = preferred
        elif requested_port is not None:
            raise RuntimeError(f"端口 {preferred} 已被占用；请用 --port 指定其他端口。")
        else:
            port = free_port()
            print(f"默认端口 {DEFAULT_PORT} 已被占用，改用空闲端口 {port}。")

        logs = log_dir(data_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = logs / f"server-{stamp}-{port}.log"
        env = os.environ.copy()
        env["MUGOUZAO_DATA_DIR"] = str(data_dir)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [str(PYTHON_GUI), "-X", "utf8", "-u", str(SERVER_SCRIPT),
               "--port", str(port)]
        startupinfo = None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        with log_file.open("ab") as log_stream:
            process = subprocess.Popen(
                cmd, cwd=str(GENERATOR_DIR), env=env,
                stdin=subprocess.DEVNULL, stdout=log_stream, stderr=subprocess.STDOUT,
                creationflags=creationflags, startupinfo=startupinfo,
                close_fds=True,
            )
            record = make_record(process.pid, port, data_dir, log_file)
            save_record(data_dir, record)

        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if is_our_service(port, data_dir) is not None:
                url = f"http://127.0.0.1:{port}/"
                print(f"戗角生成器已启动：{url}")
                print(f"数据目录：{data_dir}")
                print(f"服务日志：{log_file}")
                print("停止服务：MuGouzao.cmd --stop")
                if not no_browser:
                    webbrowser.open(url, new=2)
                return 0
            process.poll()
            if process.returncode is not None:
                detail = tail_log(log_file)
                remove_record(data_dir)
                raise RuntimeError(
                    f"工作台服务启动失败（退出码 {process.returncode}）。\n"
                    f"日志：{log_file}\n{detail}"
                )
            time.sleep(0.5)

        raise TimeoutError(
            f"工作台在{READY_TIMEOUT_SECONDS}秒内未就绪；服务进程仍在运行。\n"
            f"日志：{log_file}"
        )


def write_fatal_log(data_dir: Path, error: BaseException) -> None:
    try:
        path = log_dir(data_dir) / "launcher-errors.log"
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] {type(error).__name__}: {error}\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    raw_data = args.data_dir or os.environ.get("MUGOUZAO_DATA_DIR")
    data_dir = Path(raw_data).expanduser().resolve() if raw_data else default_data_dir().resolve()
    try:
        if args.stop:
            data_dir.mkdir(parents=True, exist_ok=True)
            with LaunchLock(data_dir / LOCK_NAME):
                return stop_service(data_dir)
        return start_service(data_dir, args.port, args.no_browser)
    except Exception as exc:
        write_fatal_log(data_dir, exc)
        print(f"木构造戗角生成器启动失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
