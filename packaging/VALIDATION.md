# v0.4.1 发布验证

2026-11-03，在本机 Windows x64 完成：

- 后端 HTTP 16 项、网格缓存 6 项、交付 10 项现有检查通过。
- 解包安装到中文路径，清空 Python 环境变量后，内置 Python 和计算依赖导入通过；Python 导入路径均位于安装目录内。
- 首次启动、重复启动复用、带空闲服务的覆盖安装、覆盖后重启、运行服务时卸载通过。卸载后程序和服务移除，外部状态文件与测试标记保留。
- 独立数据目录中的真实 preview API job 成功；一次 angle_120_B400 exact CLI 生成及 STEP 导出通过，STEP 为 33,206,383 字节，模型、排料图及下载文件齐全。
- 安装版包含中文字体、44 个 Python 发行包以及 Microsoft app-local C++ 运行库。

安装包 SHA-256：`654ecec6737ed8d15e70d351355c5734d1ce44edf5dac1b542c6e585f12a1d7e`。

本机完整构建与运行记录位于被忽略的 `release/build-report.json`、`release/primary-verification.json` 和 `release/install-verification/`。检查中修复了安装版输出路径、Windows 进程探测和临时测试夹具的来源记录；没有修改历史生成产物。exact 验证脚本修正文件清单后复核已有成功产物，未重复计算。

这里记录的是软件安装、运行和文件交付验证，不代替构造或加工验收。
