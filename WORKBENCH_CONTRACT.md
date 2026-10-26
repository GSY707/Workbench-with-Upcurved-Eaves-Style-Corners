# 本地工作台 v0.2 实施接口

目标：在同一页编辑参数、异步生成精确三维模型、查看立脚飞椽排料和下载结果。v0.2 的几何范围见 [升级约定](UPGRADE_V02.md)，性能记录见 [速度说明](PERFORMANCE.md)。默认不在每次按键时启动计算。

## 服务与路径

- Python 标准库服务 `workbench_server.py`，只绑定 `127.0.0.1`，默认端口 8765；`--port` 可改端口。
- `/` 为 `ui/index.html`，`/ui/` 静态界面；`/results/<case-id>/...` 仅映射登记过的 outputs 内结果，禁止目录穿越。
- 所有 API JSON 用 UTF-8；失败形如 `{error: "中文说明", details: [...]}`，校验失败 400，忙碌 409。拒绝跨站写请求，不允许客户端提供运行命令或输出路径。
- 用户界面语言中文。任何用户字符串作为文本显示，不拼接成可执行 HTML。

## API

`GET /api/bootstrap` 返回 `{app_id:"mu-gouzao-corner-workbench", workspace, version:"0.2.0", schema, defaults, examples:[{id,title,config}], cases:[Case], active_job:Job|null, reference_runtime_seconds}`。workspace 为当前项目绝对路径；启动器匹配服务身份、工作区和版本后才复用端口。参考耗时以本机已完成任务为依据。

`POST /api/validate` 输入 `{config}`，返回 `{valid:true, normalized_config, calculation_key, derived, warnings:[]}`。仅配置与可快速推导的数据检查，不进行完整 CAD 或宣称实体已通过。未知字段和非有限数拒绝。错误返回 400。

`GET /api/cases` 返回 `{cases:[Case]}`。只列完整验证通过的结果，不把失败候选当示例。

`POST /api/jobs` 输入 `{config}`，先校验；如果相同计算身份的成功结果可复用，返回 `{job:Job, reused:true}`；否则启动独立输出的单个子进程并返回 `{job:Job,reused:false}`。同时最多一个运行任务，忙碌 409。不覆盖旧输出。

`GET /api/jobs/<id>` 返回 `{job:Job}`；`POST /api/jobs/<id>/cancel` 输入 `{}`，返回 `{job:Job}`。刷新页面仍可恢复观察。服务重启时将遗留运行记录标为 interrupted，不自动重启 CAD。

Case 字段：`id,title,config,calculation_key,validated,created_at,elapsed_seconds,metrics,urls`。

- `config` 为可重新编辑的原始格式（case_id、corner_angle_deg、parameters、advanced），不含 resolved 派生字段。
- `metrics` 字段可空：`templates_per_side,parts_count,board_width_mm,board_height_mm,board_thickness_mm,utilization_percent,translation_max_error_mm`。
- `urls`：`model,layout,index,obj,points,svg,config,validation`，均为服务内的可用 URL。

Job 字段：`id,status,stage,started_at,finished_at,elapsed_seconds,case_id,result:Case|null,log_tail,error`。

- status 为 queued/running/succeeded/failed/cancelled/interrupted。
- stage 是实际日志映射出的中文阶段，不制造百分比；log_tail 为有长度上限的字符串。
- succeeded 才能自动载入 result；失败/取消保持此前已显示的有效模型。
- elapsed_seconds 由服务计算，前端轮询间隔约 2 秒。

## 计算身份与初始结果

身份包含规范化参数和实际生成核心源码的指纹，不以案例名称为几何身份。相同参数改名允许复用并明确显示所载入结果名称。缓存命中仍需检查 validation、文件完整性与核心来源；代码改变不能静默复用旧代码缓存。

内置两个配置示例：default、size_variant_valid64。现有通过结果的原始源指纹、计算 key 和逐文件哈希固定在 `configs/seed_catalog.json`。新建本地状态时，只对与目录记录一致的旧产物登记原始身份；缺少目录记录或哈希不符时跳过，绝不能套用当前源码指纹。已有持久历史保留；只有匹配当前核心的结果可以用于缓存。

完整成功结果缓存与网格导出前的实体检查点分开处理。后者由生成子进程管理：相同几何配置、派生量及几何源码身份，并通过 BRep 哈希、单实体与体积复核时，可在新任务中恢复几何；案例名可变，网格精度参数因参与内部几何采样仍属于几何身份。检查点记录全部生产源码指纹供追溯，但允许显示和导出代码修复后重做导出。恢复任务仍须经过当前截面、排料、网格及完整结果验证，不能直接登记失败输出。

## 页面行为

固定版本与直角范围提示；左侧分组参数（主参数默认展开，高级参数折叠），右侧模型/排料标签页。模型和排料复用已有离线 HTML，iframe 与服务同源。初始加载默认有效结果；示例切换、历史、导入/下载配置、校验、生成、取消、导出均有明确入口。

区分“正在编辑参数”与“当前显示结果”：修改后提示尚未生成；校验成功也不能表示模型更新。点击“查看此结果”时同步回填该结果的主参数、高级参数和案例名，并更新名称、统计和状态；不提交任务。独立的“载入参数到草稿”保留。后台任务不因查看历史而改变，页面须明确显示这一状态。输出链接只指向所显示结果。文件上传仅前端读取 JSON，不要求用户手工编辑文件。

## 验收

后端覆盖配置拒绝、缓存命中、单任务忙碌、失败不升级为成功、取消、重启恢复、路径保护等有意义测试；使用可控短进程测试任务生命周期，不重复 15 分钟 CAD。现有 CAD 核心已完成整模验证。

前端完成参数组与所有有效字段、同页双查看器、草稿状态、任务状态、错误显示和结果下载。界面适配普通桌面窗口与窄窗口；本地资源离线可用。集成验证通过真实 HTTP 服务联通现有两组成功结果，以及有效配置的同参数复用。

新参数的完整生成仍调用 generate.py。v0.2 更新两侧独立直线轮廓；排料保留中心截面近似，非直角和双面精确排料另行处理。
