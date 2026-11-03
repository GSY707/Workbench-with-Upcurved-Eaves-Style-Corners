# Windows 离线安装包

当前版本：**0.4.1，Windows x64**。安装包包含 CPython 3.12、锁定的 CadQuery/OCP 与绘图依赖、Microsoft app-local C++ 运行库和离线查看器资源；构建不调用 pip。

## 启动与停止

安装后使用开始菜单或桌面上的 **戗角生成器** 启动。服务在后台运行，浏览器打开本机工作台。关闭浏览器不会停止服务。

默认数据目录是 `%LOCALAPPDATA%\MuGouzao`，其中包含 `outputs`、`ui_state`、日志和启动记录。日志位于 `%LOCALAPPDATA%\MuGouzao\logs`。

`MuGouzao.cmd` 支持以下命令：

```text
MuGouzao.cmd [--port 18765] [--data-dir PATH] [--no-browser]
MuGouzao.cmd --stop [--data-dir PATH]
```

默认使用端口 8765；端口被占用时自动选择可用端口。传入 `--port` 时使用指定端口。`--data-dir` 可把数据与默认目录隔离，`--no-browser` 只启动后台服务。

停止服务请运行开始菜单中的 **停止戗角生成器**，或执行 `MuGouzao.cmd --stop`。停止前会确认进程属于本安装，且没有仍在生成的任务。安装升级和卸载使用同一检查；用户数据不会随卸载删除。

## 随包许可证

CPython、44 个 Python 发行包、Noto Sans CJK SC 和 Microsoft Visual C++ Runtime 的许可证及发行元数据位于安装目录 `THIRD_PARTY_NOTICES`。运行时与依赖版本见 `APP-VERSION.json`、`DEPENDENCIES.json` 和 `requirements.lock`。

## 构建

构建机需要 Windows x64、Python 3.9 或更高版本、Inno Setup 6（`ISCC.exe`）和与锁定版本一致的 Microsoft VC++ Runtime CAB。构建脚本通过 HTTPS 下载并校验官方 CPython embeddable 和 VC++ Redist；CadQuery 等 Python 包从已有 `model_deps` 目录复制，不使用 pip。构建过程要求系统 PowerShell 5.1、.NET Framework C# 编译器，以及能访问 Python.org、Microsoft 下载站点的网络。

在生成器项目目录运行：

```powershell
python packaging\build.py --deps-source "C:\Users\24408\Documents\ChatGPT\木构造\资料整理\tmp\model_deps"
```

省略 `--deps-source` 时使用默认的 `木构造\资料整理\tmp\model_deps`。可用 `--iscc "C:\Path\ISCC.exe"` 指定 Inno Setup 编译器，或传 `--skip-installer` 只生成 bundle 并检查嵌入式 Python。脚本会比对 `requirements.lock`，校验下载哈希、签名、运行时 DLL 版本和依赖导入，并输出：

- `release\bundle\`：完整离线应用目录。
- `release\dist\MuGouzao-Setup-0.4.1-win64.exe`：可安装包。
- `release\dist\SHA256SUMS.txt`：安装包 SHA-256。
- `release\build-report.json`：构建输入、源码清单、依赖/许可证清单、校验结果和安装包哈希。
- `release\inno-build.log`：Inno Setup 编译日志。

构建后可对独立数据目录运行真实验证：

```powershell
python packaging\verify_bundle.py --data-dir release\staging-data\manual
```

该命令以新端口启动 bundle，检查 API 与静态资源，生成一个 preview job，并运行一次 `angle_120.json` 的 exact `--step` CLI 流程；产物写入给定目录下的 `outputs`，结果附加到 `release\build-report.json`。exact 和 STEP 导出耗时随机器性能变化。
