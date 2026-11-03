#!/usr/bin/env python
"""Build the self-contained Windows x64 installer without installing Python packages."""
from __future__ import annotations

import argparse
import base64
import email
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGING_DIR.parent
RELEASE_DIR = PROJECT_ROOT / "release"
BUNDLE_DIR = RELEASE_DIR / "bundle"
DIST_DIR = RELEASE_DIR / "dist"
APP_DIR = BUNDLE_DIR / "生成器" / "直角戗角"
DEPS_SOURCE = PROJECT_ROOT.parents[1] / "资料整理" / "tmp" / "model_deps"
DEPS_DIR = BUNDLE_DIR / "资料整理" / "tmp" / "model_deps"
RUNTIME_DIR = BUNDLE_DIR / "runtime"
PYTHON_CLI = RUNTIME_DIR / "python.exe"
PYTHON_GUI = RUNTIME_DIR / "pythonw.exe"
NOTICES_DIR = BUNDLE_DIR / "THIRD_PARTY_NOTICES"
LOCK_PATH = PACKAGING_DIR / "runtime-lock.json"
PACKAGE_LOCK_PATH = PACKAGING_DIR / "requirements.lock"
APP_VERSION = "0.4.1"
INSTALLER_NAME = f"MuGouzao-Setup-{APP_VERSION}-win64.exe"
EXCLUDED_SOURCE_DIRS = {
    ".git", ".pytest_cache", "__pycache__", "design", "diagnostics", "node_modules",
    "packaging",
    "outputs", "release", "tests", "ui_state", "venv", ".venv",
}
NOTICE_NAME_TOKENS = ("license", "licence", "notice", "copying", "copyright")
VC_RUNTIME_DLLS = (
    "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "vcamp140.dll",
    "vccorlib140.dll", "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
)
LICENSE_FALLBACKS = {
    "cadquery-ocp": ("cadquery_ocp/LICENSE",),
    "cadquery-ocp-proxy": ("cadquery_ocp/LICENSE",),
    "casadi": ("casadi/include/licenses/casadi/LICENSE/LICENSE.txt",),
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"期望JSON对象：{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=True)
    if not resolved.is_relative_to(base):
        raise ValueError(f"路径超出允许目录：{resolved}")
    return resolved


def safe_remove(path: Path, root: Path) -> None:
    target = safe_relative(path, root)
    if target == root.resolve(strict=True):
        raise ValueError("拒绝删除根目录")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def run(command: list[str], *, cwd: Path | None = None,
        timeout: int | None = None, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if result.returncode != 0:
        excerpt = result.stdout[-12000:]
        raise RuntimeError(f"命令失败({result.returncode}): {' '.join(command)}\n{excerpt}")
    if not quiet and result.stdout.strip():
        print(result.stdout.rstrip())
    return result


def download_locked(url: str, target: Path, expected_sha256: str) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        actual = sha256_file(target)
        if actual != expected_sha256:
            raise ValueError(f"缓存校验失败：{target}\n期望 {expected_sha256}\n实际 {actual}")
        return {"path": str(target), "bytes": target.stat().st_size, "sha256": actual,
                "source": url, "cache_hit": True}

    request = urllib.request.Request(url, headers={"User-Agent": "MuGouzao-Packager/0.4.1"})
    digest = hashlib.sha256()
    count = 0
    temporary = target.with_suffix(target.suffix + ".download")
    print(f"下载 {url}")
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
        total = response.headers.get("Content-Length")
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            stream.write(block)
            digest.update(block)
            count += len(block)
            print(f"  {count:,}" + (f" / {int(total):,} bytes" if total else " bytes"), flush=True)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"下载校验失败：{target}\n期望 {expected_sha256}\n实际 {actual}")
    os.replace(temporary, target)
    return {"path": str(target), "bytes": count, "sha256": actual,
            "source": url, "cache_hit": False}


def package_lock() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in PACKAGE_LOCK_PATH.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        if "==" not in clean:
            raise ValueError(f"依赖锁格式错误：{clean}")
        name, version = clean.split("==", 1)
        key = name.lower().replace("_", "-").replace(".", "-")
        if key in result:
            raise ValueError(f"依赖锁中重复项目：{name}")
        result[key] = version
    return result


def distribution_metadata(source: Path) -> list[dict[str, Any]]:
    locked = package_lock()
    actual: dict[str, dict[str, Any]] = {}
    for info_dir in sorted(source.glob("*.dist-info")):
        metadata_path = info_dir / "METADATA"
        if not metadata_path.is_file():
            continue
        metadata = email.message_from_bytes(metadata_path.read_bytes())
        name = str(metadata.get("Name", "")).strip()
        version = str(metadata.get("Version", "")).strip()
        key = name.lower().replace("_", "-").replace(".", "-")
        if not name or not version:
            raise ValueError(f"发行包元数据缺少名称或版本：{metadata_path}")
        if key in actual:
            raise ValueError(f"依赖目录中发行包重复：{name}")
        actual[key] = {
            "name": name,
            "version": version,
            "normalized_name": key,
            "license_expression": str(metadata.get("License-Expression", "")).strip(),
            "license_field": str(metadata.get("License", "")).strip(),
            "license_classifiers": [
                value for value in (metadata.get_all("Classifier") or [])
                if value.startswith("License ::")
            ],
            "metadata_directory": info_dir.name,
            "metadata_path": metadata_path,
            "dist_info_directory": info_dir,
        }
    if actual != {key: value for key, value in actual.items() if key in locked}:
        extra = sorted(set(actual) - set(locked))
        if extra:
            raise ValueError(f"requirements.lock未包含这些发行包：{extra}")
    missing = sorted(set(locked) - set(actual))
    if missing:
        raise ValueError(f"当前模型依赖缺少requirements.lock项目：{missing}")
    mismatched = [
        f"{actual[name]['name']}=={actual[name]['version']} (locked {locked[name]})"
        for name in locked if actual[name]["version"] != locked[name]
    ]
    if mismatched:
        raise ValueError("模型依赖版本与requirements.lock不一致：\n" + "\n".join(mismatched))
    if set(actual) != set(locked):
        raise ValueError("模型依赖与requirements.lock不完全一致。")
    return sorted(actual.values(), key=lambda row: row["name"].casefold())


def ignored_entries(_: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name in {"__pycache__", ".pytest_cache"} or name.endswith((".pyc", ".pyo"))
    }


def source_ignore(_: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name in EXCLUDED_SOURCE_DIRS or name in {"__pycache__", ".pytest_cache"} or
        name.endswith((".pyc", ".pyo"))
    }


def file_manifest(root: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        rows.append({"path": path.relative_to(root).as_posix(), "bytes": size,
                     "sha256": sha256_file(path)})
    return rows, total


def copy_source() -> dict[str, Any]:
    print("[1/7] 复制生成器源码")
    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT, APP_DIR, ignore=source_ignore, symlinks=False)
    font_source = PACKAGING_DIR / "vendor" / "noto-cjk" / "NotoSansCJKsc-Regular.otf"
    font_license = PACKAGING_DIR / "vendor" / "noto-cjk" / "LICENSE"
    app_font = APP_DIR / "resources" / "fonts" / "NotoSansCJKsc-Regular.otf"
    app_font.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(font_source, app_font)
    manifest, total = file_manifest(APP_DIR)
    write_json(BUNDLE_DIR / "SOURCE-MANIFEST.json", {
        "app_version": APP_VERSION,
        "source_files": len(manifest),
        "source_bytes": total,
        "source_tree_sha256": hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "files": manifest,
    })
    return {"files": len(manifest), "bytes": total, "font_sha256": sha256_file(app_font),
            "font_license_sha256": sha256_file(font_license)}


def collect_license_files(dep_source: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    print("[3/7] 收集许可证与NOTICE")
    manifest: list[dict[str, Any]] = []
    for row in rows:
        info_dir = row["dist_info_directory"]
        slug = row["normalized_name"]
        target_dir = NOTICES_DIR / "python-packages" / slug
        target_dir.mkdir(parents=True, exist_ok=True)
        metadata_target = target_dir / "METADATA"
        shutil.copy2(row["metadata_path"], metadata_target)
        copies: list[tuple[Path, str]] = [(row["metadata_path"], "METADATA")]

        candidates = [
            path for path in info_dir.rglob("*")
            if path.is_file() and any(token in path.name.lower() for token in NOTICE_NAME_TOKENS)
        ]
        for source in candidates:
            relative = source.relative_to(info_dir).as_posix()
            destination = target_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copies.append((source, relative))

        if not any(relative != "METADATA" for _, relative in copies):
            for relative in LICENSE_FALLBACKS.get(slug, ()):
                source = dep_source / Path(relative)
                if source.is_file():
                    destination_name = f"UPSTREAM-{source.name}"
                    shutil.copy2(source, target_dir / destination_name)
                    copies.append((source, destination_name))

        if not any(relative != "METADATA" for _, relative in copies):
            license_field = row.get("license_field", "")
            if len(license_field) >= 300:
                destination = target_dir / "LICENSE-FROM-METADATA.txt"
                destination.write_text(license_field.rstrip() + "\n", encoding="utf-8")
                copies.append((row["metadata_path"], destination.name))

        if not any(relative != "METADATA" for _, relative in copies):
            expression = row.get("license_expression") or row.get("license_field") or ""
            raise ValueError(
                f"发行包 {row['name']}=={row['version']} 没有可附带的许可证文件；"
                f"元数据许可证字段为：{expression[:180]}"
            )

        manifest.append({
            "name": row["name"], "version": row["version"],
            "license_expression": row.get("license_expression", ""),
            "license_field": row.get("license_field", ""),
            "license_classifiers": row.get("license_classifiers", []),
            "notice_directory": str(target_dir.relative_to(BUNDLE_DIR).as_posix()),
            "included_files": [relative for _, relative in copies],
        })
    return manifest


def powershell_json(script: str) -> Any:
    powershell = os.environ.get("SystemRoot", r"C:\Windows") + (
        r"\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                  "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                 timeout=30, quiet=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "PowerShell JSON输出无法解析；返回值为 " + repr(result.stdout[:2000])
        ) from exc


def authenticode_facts(paths: list[Path]) -> list[dict[str, Any]]:
    values = json.dumps([str(path.resolve()) for path in paths], ensure_ascii=False)
    encoded_json = base64.b64encode(values.encode("utf-8")).decode("ascii")
    script = (
        "$ProgressPreference='SilentlyContinue'; "
        "Add-Type -TypeDefinition @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "public static class MuGouzaoWinTrust {\n"
        "  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]\n"
        "  public struct FileInfo { public uint cbStruct; public string path; public IntPtr file; public IntPtr subject; }\n"
        "  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]\n"
        "  public struct TrustData { public uint cbStruct; public IntPtr policy; public IntPtr sip; public uint ui; public uint revoke; public uint choice; public IntPtr file; public uint state; public IntPtr stateData; public string url; public uint flags; public uint context; public IntPtr signatureSettings; }\n"
        "  [DllImport(\"wintrust.dll\", ExactSpelling=true, PreserveSig=true)]\n"
        "  private static extern int WinVerifyTrust(IntPtr hwnd, ref Guid action, ref TrustData data);\n"
        "  public static int Verify(string path) {\n"
        "    FileInfo fi = new FileInfo(); fi.cbStruct = (uint)Marshal.SizeOf(typeof(FileInfo)); fi.path = path;\n"
        "    IntPtr p = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(FileInfo))); Marshal.StructureToPtr(fi, p, false);\n"
        "    TrustData td = new TrustData(); td.cbStruct = (uint)Marshal.SizeOf(typeof(TrustData)); td.ui = 2; td.revoke = 0; td.choice = 1; td.file = p; td.state = 0; td.flags = 0x1000;\n"
        "    Guid action = new Guid(\"00AAC56B-CD44-11d0-8CC2-00C04FC295EE\");\n"
        "    try { return WinVerifyTrust(new IntPtr(-1), ref action, ref td); } finally { Marshal.DestroyStructure(p, typeof(FileInfo)); Marshal.FreeHGlobal(p); }\n"
        "  }\n"
        "}\n"
        "'@ -ErrorAction Stop; "
        "$paths = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('"
        + encoded_json + "')) | ConvertFrom-Json; "
        "$rows = foreach ($path in $paths) { $trust = [MuGouzaoWinTrust]::Verify($path); "
        "$cert = [System.Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile($path); "
        "$info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($path); "
        "$status = if ($trust -eq 0) { 'Valid' } else { 'NotTrusted' }; "
        "[PSCustomObject]@{ Path=$path; Status=$status; TrustCode=$trust; "
        "Signer=$cert.Subject; FileVersion=$info.FileVersion } }; "
        "$rows | ConvertTo-Json -Compress"
    )
    result = powershell_json(script)
    if isinstance(result, dict):
        return [result]
    if not isinstance(result, list):
        raise RuntimeError("无法读取运行时DLL签名信息。")
    return result


def locate_vc_cab(version: str) -> Path:
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    cache = program_data / "Package Cache"
    folder_version = version.rsplit(".", 1)[0]
    matches = sorted(cache.glob(
        f"*v{folder_version}/packages/vcRuntimeMinimum_amd64/cab1.cab"
    ))
    if not matches:
        raise FileNotFoundError(
            f"找不到版本{version}的Microsoft CRT CAB。请先在构建机安装相同版本的官方"
            "VC++ Redistributable，或将该版本的vcRuntimeMinimum_amd64/cab1.cab放入"
            "packaging/.cache/vc-runtime/。"
        )
    return matches[-1]


def stage_vc_runtime(lock: dict[str, Any]) -> dict[str, Any]:
    print("[4/7] 提取并校验Microsoft app-local CRT")
    version = lock["vc_redist"]["version"]
    cab = locate_vc_cab(version)
    expand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "expand.exe"
    extraction_root = PACKAGING_DIR / ".cache" / f"vc-runtime-extract-{version}"
    safe_remove(extraction_root, PACKAGING_DIR / ".cache") if extraction_root.exists() else None
    extraction_root.mkdir(parents=True, exist_ok=True)
    result = run([str(expand), "-F:*", str(cab), str(extraction_root)], timeout=120,
                 quiet=True)
    if "Expanding Files Complete" not in result.stdout:
        raise RuntimeError(f"VC runtime CAB提取失败：{result.stdout[-2000:]}")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    copies: list[Path] = []
    for name in VC_RUNTIME_DLLS:
        source = extraction_root / f"{name}_amd64"
        if not source.is_file():
            raise FileNotFoundError(f"官方CRT CAB缺少 {name}")
        target = RUNTIME_DIR / name
        shutil.copy2(source, target)
        copies.append(target)
    facts = authenticode_facts(copies)
    version_prefix = version.rsplit(".", 1)[0]
    for row in facts:
        if (row.get("Status") != "Valid" or
                "Microsoft Corporation" not in str(row.get("Signer", ""))):
            raise ValueError(f"Microsoft CRT签名校验失败：{row}")
        if not str(row.get("FileVersion", "")).startswith(version_prefix):
            raise ValueError(f"Microsoft CRT版本与锁不一致：{row}")
    return {
        "version": version,
        "source_cab": str(cab),
        "source_cab_sha256": sha256_file(cab),
        "files": [
            {"name": path.name, "bytes": path.stat().st_size,
             "sha256": sha256_file(path),
             "file_version": facts[index].get("FileVersion"),
             "signer": facts[index].get("Signer")}
            for index, path in enumerate(copies)
        ],
    }


def find_iscc(explicit: str | None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"找不到Inno Setup编译器：{candidate}")
        return candidate
    for executable in ("ISCC.exe", "iscc.exe"):
        found = shutil.which(executable)
        if found:
            return Path(found).resolve()
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) /
        "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("找不到ISCC.exe；安装Inno Setup 6后重试。")


def git_state() -> dict[str, Any]:
    try:
        revision = run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                       timeout=10, quiet=True).stdout.strip()
        status = run(["git", "status", "--porcelain", "--untracked-files=all"],
                     cwd=PROJECT_ROOT, timeout=10, quiet=True).stdout.splitlines()
        return {"revision": revision, "dirty": bool(status), "dirty_files": status}
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return {"revision": None, "dirty": None, "dirty_files": []}


def tree_stats(root: Path) -> dict[str, Any]:
    files = 0
    total = 0
    for directory, _, names in os.walk(root):
        for name in names:
            path = Path(directory) / name
            try:
                total += path.stat().st_size
                files += 1
            except OSError:
                pass
    return {"files": files, "bytes": total}


def build(args: argparse.Namespace) -> None:
    start = time.monotonic()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lock = load_json(LOCK_PATH)
    deps_source = (Path(args.deps_source).expanduser().resolve()
                   if args.deps_source else DEPS_SOURCE.resolve())
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if BUNDLE_DIR.exists():
        safe_remove(BUNDLE_DIR, RELEASE_DIR)
    installer_path = DIST_DIR / INSTALLER_NAME
    if installer_path.exists():
        safe_remove(installer_path, DIST_DIR)

    print("[1/7] 校验依赖版本与打包输入")
    if not deps_source.is_dir():
        raise FileNotFoundError(f"找不到已有model_deps目录：{deps_source}")
    distributions = distribution_metadata(deps_source)
    font = PACKAGING_DIR / "vendor" / "noto-cjk" / "NotoSansCJKsc-Regular.otf"
    font_license = PACKAGING_DIR / "vendor" / "noto-cjk" / "LICENSE"
    if sha256_file(font) != lock["font"]["sha256"]:
        raise ValueError("Noto CJK字体SHA-256与runtime-lock.json不匹配。")
    if sha256_file(font_license) != lock["font"]["license_sha256"]:
        raise ValueError("Noto CJK许可证SHA-256与runtime-lock.json不匹配。")

    cache = PACKAGING_DIR / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    python_zip = cache / "python-3.12.10-embeddable-amd64.zip"
    python_info = download_locked(lock["python_embed"]["url"], python_zip,
                                  lock["python_embed"]["sha256"])
    vc_redist = cache / f"vc_redist-{lock['vc_redist']['version']}.x64.exe"
    redist_info = download_locked(lock["vc_redist"]["url"], vc_redist,
                                  lock["vc_redist"]["sha256"])
    license_docx = PACKAGING_DIR / "vendor" / "microsoft" / \
        "Visual-C-Runtime-2015-2022-License.docx"
    if sha256_file(license_docx) != lock["vc_redist"]["license_sha256"]:
        raise ValueError("Microsoft VC Runtime license文件与runtime-lock.json不匹配。")
    redist_signature = authenticode_facts([vc_redist])[0]
    if (redist_signature.get("Status") != "Valid" or
            "Microsoft Corporation" not in str(redist_signature.get("Signer", "")) or
            redist_signature.get("FileVersion") != lock["vc_redist"]["version"]):
        raise ValueError(f"Microsoft VC Redist签名或版本校验失败：{redist_signature}")

    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    source_info = copy_source()

    print("[2/7] 复制现有model_deps，不调用pip")
    DEPS_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(deps_source, DEPS_DIR, ignore=ignored_entries, symlinks=False)
    lock_target = BUNDLE_DIR / "requirements.lock"
    shutil.copy2(PACKAGE_LOCK_PATH, lock_target)
    shutil.copy2(PACKAGE_LOCK_PATH, DEPS_DIR / "requirements.lock")
    deps_stats = tree_stats(DEPS_DIR)

    print("[3/7] 解压官方CPython embeddable")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(RUNTIME_DIR)
    required_runtime_files = ("python.exe", "pythonw.exe", "python312.dll",
                              "python312.zip", "python312._pth", "LICENSE.txt")
    for filename in required_runtime_files:
        if not (RUNTIME_DIR / filename).is_file():
            raise FileNotFoundError(f"官方CPython embeddable缺少{filename}")
    # Embedded Python only accepts the literal `import site` line in _pth.
    # sitecustomize adds the Unicode bundle paths using Python's UTF-8 source
    # decoding, independent of the host Windows ANSI code page.
    pth = "python312.zip\n.\nimport site\n"
    (RUNTIME_DIR / "python312._pth").write_text(pth, encoding="ascii")
    (RUNTIME_DIR / "sitecustomize.py").write_text(
        "import os\nimport sys\n"
        "_runtime = os.path.dirname(sys.executable)\n"
        "for _relative in (r'..\\资料整理\\tmp\\model_deps', "
        "r'..\\生成器\\直角戗角', r'..\\生成器\\直角戗角\\engine'):\n"
        "    _path = os.path.abspath(os.path.join(_runtime, _relative))\n"
        "    if _path not in sys.path:\n"
        "        sys.path.insert(0, _path)\n",
        encoding="utf-8",
    )

    vc_runtime = stage_vc_runtime(lock)
    shutil.copy2(license_docx, RUNTIME_DIR / license_docx.name)
    (RUNTIME_DIR / "PYTHON-RUNTIME.txt").write_text(
        f"CPython {lock['python_embed']['version']} x64 embeddable\n"
        f"Source: {lock['python_embed']['url']}\n"
        f"SHA-256: {lock['python_embed']['sha256']}\n"
        "The Python Software Foundation license is included as LICENSE.txt.\n",
        encoding="utf-8",
    )

    cmd_path = BUNDLE_DIR / "MuGouzao.cmd"
    cmd_path.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "\"%~dp0runtime\\python.exe\" \"%~dp0packaging\\launcher.py\" %*\r\n"
        "exit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )
    packaging_copy = BUNDLE_DIR / "packaging"
    packaging_copy.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGING_DIR / "launcher.py", packaging_copy / "launcher.py")
    shutil.copy2(PACKAGING_DIR / "README.md", packaging_copy / "README.md")

    package_license_info = collect_license_files(DEPS_DIR, distributions)
    notices_noto = NOTICES_DIR / "Noto-Sans-CJK-SC"
    notices_noto.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGING_DIR / "vendor" / "noto-cjk" / "LICENSE", notices_noto / "LICENSE")
    shutil.copy2(PACKAGING_DIR / "vendor" / "noto-cjk" / "UPSTREAM.md", notices_noto / "UPSTREAM.md")
    notices_microsoft = NOTICES_DIR / "Microsoft-Visual-C-Runtime"
    notices_microsoft.mkdir(parents=True, exist_ok=True)
    shutil.copy2(license_docx, notices_microsoft / license_docx.name)
    shutil.copy2(PACKAGING_DIR / "vendor" / "microsoft" / "README.md",
                 notices_microsoft / "README.md")
    python_notices = NOTICES_DIR / "Python"
    python_notices.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RUNTIME_DIR / "LICENSE.txt", python_notices / "LICENSE.txt")
    (NOTICES_DIR / "README.md").write_text(
        "# Third-party notices\n\n"
        "Python package licenses and metadata are under `python-packages/`; "
        "CPython is under `Python/`; Noto CJK and Microsoft VC Runtime notices "
        "are included in their named folders.\n",
        encoding="utf-8",
    )
    dependencies = {
        "schema": "mugouzao-offline-dependencies-v1",
        "app_version": APP_VERSION,
        "architecture": "win64",
        "python": {
            "version": lock["python_embed"]["version"],
            "source": lock["python_embed"]["url"],
            "archive_sha256": lock["python_embed"]["sha256"],
            "license_file": "THIRD_PARTY_NOTICES/Python/LICENSE.txt",
        },
        "microsoft_vc_runtime": vc_runtime,
        "packages": package_license_info,
        "package_count": len(package_license_info),
        "install_method": "Copied from the existing workspace model_deps; no pip install or dependency download.",
    }
    write_json(BUNDLE_DIR / "DEPENDENCIES.json", dependencies)
    write_json(BUNDLE_DIR / "APP-VERSION.json", {
        "name": "MuGouzao",
        "version": APP_VERSION,
        "api_version": APP_VERSION,
        "architecture": "win64",
        "python_version": lock["python_embed"]["version"],
    })

    runtime_command = [str(PYTHON_CLI), "-c",
                       "import sys; assert sys.version_info[:2] == (3, 12); "
                       "print(sys.version); print(sys.executable)"]
    embedded_check = run(runtime_command, cwd=BUNDLE_DIR, timeout=30, quiet=True)
    import_command = [str(PYTHON_CLI), "-c",
                      "import cadquery, OCP, numpy, scipy, matplotlib, ezdxf; "
                      "print('cadquery', cadquery.__version__); "
                      "print('numpy', numpy.__version__); "
                      "print('scipy', scipy.__version__)"]
    imports = run(import_command, cwd=BUNDLE_DIR, timeout=180, quiet=True)

    git = git_state()
    bundle_stats = tree_stats(BUNDLE_DIR)
    report: dict[str, Any] = {
        "schema": "mugouzao-windows-installer-build-v1",
        "app_version": APP_VERSION,
        "built_at": stamp,
        "builder": {"python": sys.version, "platform": sys.platform,
                    "repository_revision": git["revision"], "repository_dirty": git["dirty"],
                    "dirty_files": git["dirty_files"]},
        "inputs": {
            "python_embeddable": python_info,
            "vc_redist": {**redist_info, "signature": redist_signature},
            "model_deps_source": str(deps_source),
            "locked_package_count": len(distributions),
            "font": {"path": "resources/fonts/NotoSansCJKsc-Regular.otf",
                     "sha256": source_info["font_sha256"], "bytes": font.stat().st_size},
        },
        "source_bundle": {
            "directory": str(BUNDLE_DIR),
            "source_files": source_info["files"],
            "source_bytes": source_info["bytes"],
            "dependency_files": deps_stats["files"],
            "dependency_bytes": deps_stats["bytes"],
            "tree_files": bundle_stats["files"],
            "tree_bytes": bundle_stats["bytes"],
        },
        "embedded_runtime_smoke_check": {
            "command": runtime_command,
            "output": embedded_check.stdout.strip(),
            "import_command": import_command,
            "import_output": imports.stdout.strip(),
        },
        "installer": None,
        "verification": None,
        "elapsed_seconds": None,
    }

    if not args.skip_installer:
        print("[7/7] 编译Inno Setup安装包")
        compiler = find_iscc(args.iscc)
        inno_log = RELEASE_DIR / "inno-build.log"
        command = [
            str(compiler),
            f"/DAppVersion={APP_VERSION}",
            f"/DBundleDir={BUNDLE_DIR}",
            f"/DOutputDir={DIST_DIR}",
            str(PACKAGING_DIR / "MuGouzao.iss"),
        ]
        try:
            result = subprocess.run(
                command, cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=args.inno_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Inno Setup编译超过{args.inno_timeout}秒。") from exc
        inno_log.write_text(result.stdout, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(
                f"Inno Setup编译失败({result.returncode})，日志：{inno_log}\n"
                f"{result.stdout[-12000:]}"
            )
        if not installer_path.is_file():
            raise FileNotFoundError(f"Inno Setup未生成预期安装包：{installer_path}")
        report["installer"] = {
            "path": str(installer_path),
            "bytes": installer_path.stat().st_size,
            "sha256": sha256_file(installer_path),
            "compiler": str(compiler),
            "log": str(inno_log),
        }
        checksum_file = DIST_DIR / "SHA256SUMS.txt"
        checksum_file.write_text(
            f"{report['installer']['sha256']}  {installer_path.name}\n",
            encoding="ascii",
        )
        report["release_checksum_file"] = {
            "path": str(checksum_file),
            "bytes": checksum_file.stat().st_size,
            "sha256": sha256_file(checksum_file),
            "contains": [installer_path.name],
        }

    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    write_json(RELEASE_DIR / "build-report.json", report)
    print(f"Build report: {RELEASE_DIR / 'build-report.json'}")
    if report["installer"]:
        print(f"Installer: {installer_path} ({report['installer']['bytes']:,} bytes)")
    print(f"Bundle: {bundle_stats['files']:,} files, {bundle_stats['bytes']:,} bytes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", help="Inno Setup ISCC.exe path; auto-detected by default")
    parser.add_argument("--deps-source", type=Path,
                        help="existing model_deps directory to copy; never pip-installs packages")
    parser.add_argument("--skip-installer", action="store_true",
                        help="stage bundle and verify embedded Python without compiling setup")
    parser.add_argument("--inno-timeout", type=int, default=4 * 60 * 60,
                        help="Inno Setup compile timeout in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        build(parse_args(argv))
    except Exception as exc:
        print(f"构建失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
