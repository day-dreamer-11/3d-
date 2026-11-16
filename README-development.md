# AIC Factory Studio

**OIP 三维场景 × Python 离散事件仿真平台**

> 三维搭建 → 生产仿真 → 数据分析 → 边缘调度 → AI 优化：一套可运行的软件闭环。
> 当前主导航以 OIP 自建场景为主线，生产仿真权威为场景 Python 离散事件引擎；
> RFTwin 固定产线与 EdgeCoder 核心代码保留，但固定产线页面不在主导航显示。
> 所有结果均为仿真计算，不是真实工厂或 6G 网络实测。

---

## 0. 自建工厂场景（Open Industry Project 集成）

从空白场景自由搭建工厂：OIP 三维编辑器负责布局/吸附/参数，保存后自动转换为统一设备模型与
显式物料连接，由 Python 离散事件引擎真实运行生产仿真、EdgeCoder 调度与优化分析。

- 启动：`start_platform.bat`（或 `python run_platform.py`）
- 页面：场景编辑器搭建 → 建模分析工作台（采集 → 分析 → 优化）→ 实验历史；全局入口在平台总览与系统设置
- 演示：`demo_scene/`（六设备产线、基准/优化/边缘结果、报告、`load_demo.py`）
- 文档：[项目报告书](docs/项目报告书.md) · [INTEGRATION_REPORT.md](INTEGRATION_REPORT.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [TEST_REPORT.md](TEST_REPORT.md)
- OIP 固定上游提交 `dcebf53`，位于 `frontend/oip/`；运行时为 Python 静态托管，不需要 Node。
- **主导航为 OIP 五页**：平台总览 / 场景编辑器 / 建模分析工作台 / 实验历史 / 系统设置。
  RFTwin 固定产线页面不再显示（代码移到 `legacy_pages.py`，便于回滚）；生产 KPI 以 OIP 场景的 Python 仿真为准。
- **存储与性能**：OIP 工程按内容哈希只存一份，版本快照为 KB 级；列表页读取 `summary.json`；
  Streamlit 对项目/实验摘要做缓存并复用 HTTP 连接。已有数据可用 `python tools/compact_scene_storage.py --root data/projects demo_seed/projects` 迁移。

---

## 1. 保留的底层能力（RFTwin/EdgeCoder，当前主导航不显示）

| 阶段 | 已实现 | 数据来源 |
| --- | --- | --- |
| A 项目与工厂管理 | 创建/保存/重新加载项目；项目目录内保存工厂、产线、边缘配置与全部实验记录 | `data/projects/<project_id>/` |
| B 工厂建模与设备配置 | RFTwin 四单元产线、12 机器人与 2 台 AMR；支持节拍、缓冲、故障概率、种子等参数编辑；三维查看 | RFTwin `factory_layout.json` + 项目参数 |
| C 生产线构建与仿真 | 产品/生产目标、班次、缓冲区、质量与停机假设；确定性引擎实际产生 KPI、事件与 OEE | RFTwin `FactoryEngine` |
| D 统一仿真控制 | 一键启动项目专用 RFTwin Live 服务；暂停/继续/倍速/重置/故障注入；嵌入三维视图；实时 KPI 卡片 | RFTwin FastAPI + React/R3F |
| E 数据采集与分析 | 总产量、良品/不良、WIP、OEE、平均周期、工位等待、缓冲区、故障事件、瓶颈识别 | RFTwin 引擎输出 |
| F 边缘计算整合 | 检测事件 → 计算任务 → 本地/边缘/云端三种策略；时延分解、超时、拒绝、利用率、成本 | EdgeCoder 仿真器 |
| G AI 辅助优化 | 规则式瓶颈建议；候选参数通过 RFTwin 短时重放验证；一键应用建议；候选差异柱状图 | 真实实验指标 + 引擎复算 |
| H 界面与可视化 | 四阶段工作流导航；控制室式总览（OEE 分解/损失/目标达成）；实时状态栏与异常提示；彩色工位看板；策略评价与场景并排对比 | 引擎事件与实验记录 |
| 原有能力保留 | EdgeCoder 独立入口、可信模板代码生成、静态检查、隔离 pytest 测试 | 原 `app.py` 与 `tests/` |

### 同一轮仿真的边界（重要）

- **三维页面内的实时 KPI**：直接读取当前 RFTwin Live 服务的 REST API，与三维画面为同一条 WebSocket/同一引擎状态；
- **实时采集记录**：保存 `live_run_id`、事件序号 `live_seq`、仿真时间，指向画面正在显示的那一轮仿真；
- **离线确定性实验**：用相同项目参数与种子重新运行 RFTwin 引擎，可复现、可保存，界面明确标注为“离线实验”；
  它不会冒充三维画面的当前状态；
- **边缘调度实验**：任务来自某个已保存工厂实验的检测事件，实验记录保存来源实验 ID、映射参数和配置指纹。

---

## 2. 架构

```text
 Streamlit 统一工作台 (factory_app.py, :8502)
 ├── 项目/配置/实验历史  → integration/project_store.py → data/projects/*.json
 ├── 工厂仿真与数据分析    → integration/factory_adapter.py
 │                          └── 复用 rftwin/packages/domain_factory (FactoryEngine)
 ├── 边缘任务/调度/成本    → integration/edge_mapping.py
 │                          └── 复用 EdgeCoder network_sim + scheduler + experiments
 ├── 规则优化与验证        → integration/analysis.py → 重新运行 RFTwin 引擎
 └── 3D 页面 (iframe)     → integration/factory_service.py 启动
                            RFTwin FastAPI + 已构建 React 前端 (:8020+)
                            ↳ REST/WebSocket；Streamlit 用同一 API 显示实时 KPI
```

职责划分：

| 系统 | 职责 | 保留方式 |
| --- | --- | --- |
| RFTwin | 设备级确定性生产仿真、三维场景、Live API、故障注入、What-if/Copilot | 独立目录 `rftwin/`，仅增加两个向后兼容环境变量钩子 |
| EdgeCoder | 边缘节点/网络仿真、三种调度策略、指标、可信代码生成与测试 | 原模块与 `app.py` 保持不变，被统一工作台直接复用 |
| 集成层 `integration/` | 项目存储、参数映射、事件归一化、任务映射、规则优化、服务管理 | 新增代码，不复制两边核心引擎 |

### 关键接口

| 对象 | 实现位置 |
| --- | --- |
| Project / FactoryLayout / Machine / ProductionLine | `integration/project_store.py` + `rftwin/config/*` |
| SimulationConfig（工厂/边缘） | 工厂：RFTwin 参数 YAML；边缘：`models.schemas.SimulationConfig` |
| Experiment / SimulationEvent / ExperimentResult | `data/projects/<id>/experiments/<exp_id>.json` |
| EdgeNode / ComputeTask / SchedulingPolicy | `integration/edge_mapping.py` + EdgeCoder 模型/策略 |
| 创建/加载项目、保存配置、运行仿真、保存与读取实验 | `project_store` / `factory_adapter` / `edge_mapping` 的 Python API |
| 3D 服务控制与实时数据 | `integration/factory_service.py`、`integration/factory_client.py` |

工厂 KPI 使用 RFTwin 引擎单位（件、秒、小时产能）；边缘任务统一使用毫秒、MB、GFLOP、GFLOPS。
映射时在 `edge_mapping.py` 显式换算，不静默混用两套时间尺度。

---

## 3. 快速开始（Windows PowerShell）

环境要求：Python 3.12/3.13（本次在 **Python 3.12.10** 验证），Node.js 仅在需要重新构建 RFTwin 前端时使用。

```powershell
cd C:\Users\Administrator\Desktop\aic

# 1. 安装 Python 依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 如 rftwin/frontend/dist 不存在，构建 RFTwin 前端（首次或前端更新后）
npm --prefix rftwin/frontend ci
npm --prefix rftwin/frontend run build

# 3. 一键启动统一平台
python run_platform.py
```

浏览器会自动打开工作台（默认 http://localhost:8502）。也可以直接运行：

```powershell
streamlit run factory_app.py --server.port 8502
```

### Windows 安装包（本地 Web 版）

本仓库可构建可离线安装的 Windows 安装包（内置 Python 运行时，评委无需安装任何环境）：

```powershell
powershell -ExecutionPolicy Bypass -File build_runtime.ps1   # 首次组装运行时
powershell -ExecutionPolicy Bypass -File build_installer.ps1 # 生成安装包
# 输出：dist\AIIndustrialFactory-Setup-1.1.1.exe
```

安装后双击桌面快捷方式：启动器自动选端口、启动 Streamlit 与三维服务、健康检查通过后
打开浏览器 `http://127.0.0.1:<端口>`。数据保存在
`%LOCALAPPDATA%\AIIndustrialFactory`（SQLite），卸载默认保留。
详细步骤见 `docs/评委使用说明.md` 与 `docs/演示流程.md`。

进入「三维仿真」页面，点击 **启动项目三维仿真**。平台会按当前项目参数启动仅监听
`127.0.0.1` 的 RFTwin 服务，并托管已构建的前端；启动约 5~15 秒。

**界面语言**：统一工作台为简体中文；嵌入式 RFTwin 三维界面默认简体中文
（其词库已在本地转换为简体，也可在三维界面标题栏切回 EN）。
RFTwin 引擎产生的事件原文与瓶颈解释保留英文工程日志，界面以中文事件类型/状态展示，
原始日志放在可展开区域，便于追溯。

原 EdgeCoder 独立入口仍可用：

```powershell
streamlit run app.py   # http://localhost:8501
```

### 测试

```powershell
# 根项目（EdgeCoder + 集成层 + 统一界面），包含 60 项测试
python -m pytest -q

# RFTwin 原项目（157 项测试，需在其目录运行）
cd rftwin
python -m pytest tests -q
```

---

## 4. 新增与修改的主要文件

```text
factory_app.py                   统一 Streamlit 工作台（五页；旧 RFTwin 页面已移至 legacy_pages.py）
legacy_pages.py                  已从导航移除的旧页面（保留用于回滚，不参与启动）
ui_cache.py                      Streamlit 项目/实验摘要缓存与失效
ui_shared.py                     新旧页面共用 helper
tools/compact_scene_storage.py   场景存储压缩迁移（OIP 内容寻址 + 轻量版本快照）
run_platform.py                  一键启动（端口检测、依赖检查、浏览器打开）
start_platform.ps1               PowerShell 启动脚本
integration/
  paths.py                       集成层路径
  project_store.py               项目/实验 JSON 持久化与校验
  factory_adapter.py             RFTwin 参数映射、无头实验、事件/KPI 归一化
  edge_mapping.py                检测事件 → 计算任务 → 三策略公平实验
  analysis.py                    瓶颈诊断、规则建议、候选参数仿真验证
  factory_client.py              RFTwin REST 客户端、实时数据采集
  factory_service.py             Live 服务进程与端口管理
  templates?                     无（复用 generator/）
tests/test_integration_core.py   集成层测试
tests/test_platform_app.py       统一界面 AppTest
pytest.ini                       根测试范围限定为 tests/
THIRD_PARTY_NOTICES.md           第三方许可与改动说明
requirements.txt                 新增 fastapi/uvicorn/pyyaml/httpx/wsproto
rftwin/                          第三方项目（MIT），仅后端 2 个环境变量钩子
```

三维前端构建产物 `rftwin/frontend/dist/` 与运行数据 `data/` 已加入 `.gitignore`；
首次运行或重新部署时需要执行 npm 构建（或保留现有 dist 目录）。

---

## 5. 使用流程

1. **平台总览**：控制室视图——项目、实时运行、OEE 分解与损失、目标达成率、最近实验；提供“一键演示（启动三维并 30×）”与四阶段工作流入口；
2. **工厂搭建**：设置仿真种子、时长、AMR 数量、四个工位节拍；查看固定产线拓扑与设备清单；
3. **生产线配置**：设置产品、班次目标、缓冲区、视觉失败率与短暂停机率；
4. **三维仿真**：启动项目专用 Live 服务，查看 RFTwin 三维画面；实时状态栏（运行/暂停、仿真时间、事件序号、倍速、异常数）、
   KPI、12 个彩色工位状态与事件流每 2 秒自动刷新；支持暂停/倍速（1×/10×/30×/60× 预设）/重置/注入故障；
5. **生产数据分析**：运行离线实验或采集 3D 实时数据；OEE 可用率/性能/质量分解、损失柱状图、目标达成率、
   瓶颈利用率排名与证据；提供**分钟级仿真过程回放**（工位利用率、缓冲区、分钟产出联动动画）；
6. **边缘计算**：配置本地/边缘/云端节点、网络与成本，生成工厂检测事件到计算任务的映射预览；
7. **调度策略实验**：运行固定本地、固定云端与动态调度；按“满足率 → 平均时延 → 成本”计算策略评价并标注公平性；
   提供**任务执行甘特时间线**与累计完成进度曲线；
8. **AI 优化建议**：基于真实实验指标生成建议，用短时确定性重放验证，展示候选相对基线的实际变化，再决定是否应用；
9. **实验历史**：重新加载、导出；**场景并排对比**会校验仿真时长与种子，口径不一致时明确提示，不给出误导性结论；
10. **AI 服务生成**：保留 EdgeCoder 模板生成、静态检查与自动化测试；
11. **系统设置**：环境、服务、数据目录、架构与限制。

---

## 6. 集成测试与真实验证结果（本次运行）

| 验证项 | 实际结果 |
| --- | --- |
| 根项目 pytest（EdgeCoder + 集成 + 界面） | **79 passed**，约 40 s（含实时采集、OEE/对比、过程可视化、SQLite 迁移、启动器、只读公开接口、场景存储压缩与实时实例清理）；仅有第三方 pytest 弃用警告 |
| 过程可视化（本次） | 三维页 KPI/12 工位/事件流 2 秒刷新；离线实验回放动画可播放；调度甘特与完成进度渲染；均无页面异常 |
| RFTwin 原测试 | **157 passed**，236.7 s（安装可选 `onnxruntime` 后；未安装时 155 passed，2 个视觉精度测试因环境缺失失败） |
| EdgeCoder 原界面回归 | AppTest 5 项通过；原 `app.py` 未改动 |
| 统一界面页面渲染 | 五页全部通过 AppTest，无兜底异常（旧 RFTwin 页面保留在 legacy_pages.py，不挂载） |
| 浏览器 E2E | Edge + Playwright：总览、3D iframe（RFTwin 显示 LIVE、实时 KPI/三维场景）、数据分析、调度页均正常，无页面/控制台错误 |
| 3D 服务生命周期 | 启动 → `/api/health` ok → 前端 HTTP 200（含 `#root`）→ 实时采集 → 停止，全部实测 |
| 项目参数注入验证 | 修改节拍 30s、种子 7、目标 1500 后：Live 引擎参数哈希变化、seed=7、焊接节拍 30.0、目标 1500，确认参数真实生效 |
| 保存/加载 | 项目与实验 JSON 往返一致；NaN/Infinity 写为 null；刷新后可从磁盘重新加载 |
| 示例闭环（演示项目，结果随参数变化） | 79 个检测任务：固定本地满足率 6.3%、固定云端 0.0%、动态调度 97.5%；建议验证由重新运行引擎计算 |

截图保存在 `data/runtime/screenshots/`（运行数据，不纳入版本控制）。

---

## 7. 已知限制

1. **三维布局固定**：RFTwin 场景与 `factory_layout.json` 固定，当前支持参数级编辑，不支持自由拖拽、添加或旋转设备；
   工位数量固定为 4/4/2/2，AMR 仅支持 1~2 台。新增设备需要同时扩展 RFTwin 场景、布局与引擎。
2. **整体嵌入**：三维页面嵌入的是 RFTwin 完整前端（含其自身导航和面板），Streamlit 负责项目/实验外壳；
   两部分视觉风格已统一背景与说明，但不是把每个 RFTwin 面板重写为 Streamlit 组件。
3. **实时与离线区分**：3D 实时采集与三维画面同一轮仿真；离线实验是相同参数与种子的确定性重放，
   两者在界面和记录中明确区分，不混用指标。RFTwin Live 的分钟快照只提供 `cell_uph`/`util`/`buf`，
   不含逐分钟良品数，因此实时记录展示累计 KPI 与 Live 趋势，不推算窗口良品数。
4. **任务映射为平台假设**：检测事件到计算任务的数据量、计算需求、时延上限、到达间隔均由项目配置决定，
   不代表真实 IT/OT 集成配置。
5. **网络模型简化**：带宽/时延/拥塞模型，不含丢包、重传、无线信道；成本为 GPOLP × 系数假设，不是真实计费。
6. **AI 为规则优化**：未接入大模型 API；建议与差异均来自引擎复算。RFTwin 自带 Copilot 在其界面内独立提供。
7. **视觉模型可选**：`onnxruntime` 未安装时 RFTwin 视觉推理自动退回 heuristic，导致其 2 个精度测试失败，
   其余功能正常。
8. **仅在本机验证**：3D 服务绑定 `127.0.0.1`，未做鉴权与外网部署；正式部署需加反向代理、认证与资源限制。
9. **环境**：本机为 Python 3.12.10、Streamlit 1.66.1、Plotly 7.1.0、pandas 3.0.6、FastAPI 0.141.1；
   Python 3.13 未在本机实测。

### 界面设计参考（业界实践）

本次界面优化参考了以下公开资料中的通用做法，并在平台中落地：

- **以 OEE 与损失为中心、三层信息结构**（工厂/线体 → 单元 → 设备）、绿/黄/红状态色：
  [FlowFuse: How to Design and Scale an OEE Dashboard](https://flowfuse.com/blog/2025/04/design-and-scale-oee-dashboard/)；
- **避免 dashboard fatigue、用数字孪生替代堆叠图表**：
  [Siemens Opcenter: From dashboard fatigue to digital twin clarity](https://blogs.sw.siemens.com/opcenter/from-dashboard-fatigue-to-digital-twin-clarity-opcenter-x-intosite-at-siemens-healthineers/)；
- **场景并排对比、可行动结论、瓶颈证据化**：
  [Simio: Digital twin dashboard design](https://www.simio.com/zh-cn/blog/digital-twin-dashboard-design-that-drives-real-business-decisions)、
  [Polaris 生产仿真](https://www.ifakt.de/en/simulation)、
  [mode40 what-if scheduling](https://mode40.com/resources/what-if-scheduling-putaway-widget-and-investigations-module/)；
- **调度仿真用甘特图 + 完成进度 + 多策略对比表**：
  [EdgeFedSim](https://pypi.org/project/edgefedsim/)、
  [TaskSim 类可视化模式](https://github.com/rissingh23/ml-os-task-scheduling-simulator)。

落地方式：四阶段工作流导航、控制室式总览、OEE/损失/目标达成、实时状态栏与异常提示、彩色工位看板、
瓶颈利用率排名、策略评价、场景并排对比（含时长/种子口径校验）、调度甘特与完成进度。
所有结论仍由本平台仿真数值计算，未照搬任何外部截图或示例数据。

---

## 8. 后续开发方向

- **自由工厂布局**：扩展 `factory_layout.json` 与 RFTwin 场景生成器，增加设备目录、拖拽编辑、布局校验，
  并把位置/数量变化同步到引擎的空间模型；
- **真实设备接入**：增加 OPC UA / MQTT / REST 适配器，把真实或半实物事件写入统一的 SimulationEvent 结构，
  并标注 `source=real` 与采集时间/质量；在此之前不得把仿真数据描述为实测；
- **更高级优化**：在参数空间上使用贝叶斯优化或强化学习，以 RFTwin 短时重放作为评估器；
  可选用 LLM 生成解释，但必须附上实验证据与参数来源；
- **更丰富产线**：多产线、多产品换型、更多 AMR 与工位类型，需要同步扩展三维场景与布局；
- **部署与权限**：容器化 RFTwin + Streamlit，增加登录、审计、实验配额与端口隔离。

---

## 9. 许可与致谢

- RFTwin：MIT License © 2026 Wayne Chou，https://github.com/WayneChou-bot/RFTwin ；
  本地改动与依赖说明见 `THIRD_PARTY_NOTICES.md`；
- EdgeCoder 原有代码与本文档中描述的扩展由本项目维护；
- 本项目不包含真实工厂数据、真实 6G 测量数据或付费大模型调用。


---

## 10. 让 ChatGPT 网页版读取平台数据（只读接口）

ChatGPT 网页版**无法访问 localhost**，而且它抓取网页时不执行 Streamlit 的 JavaScript/WebSocket，
直接给它 8502 地址通常只能看到空壳。因此平台提供了一个独立的**只读接口 + 服务端渲染 HTML 报告**，
再通过隧道暴露给 ChatGPT。

### 10.1 启动本机只读接口

```powershell
cd C:\Users\Administrator\Desktop\aic
# 可选：设置访问令牌（强烈建议对外暴露时设置）
$env:PUBLIC_API_TOKEN = "换成你自己的随机令牌"
python run_public_api.py
```

默认监听 `127.0.0.1:8600`，提供：

| 地址 | 内容 |
| --- | --- |
| `/report?project_id=<ID>` | 服务端渲染的中文 HTML 报告（项目、最新实验 KPI、OEE 分解、瓶颈、调度对比） |
| `/api/projects` | 全部项目摘要（含最新工厂/边缘实验） |
| `/api/projects/<ID>/experiments` | 实验列表（可按 kind 过滤） |
| `/api/projects/<ID>/experiments/<EXP>` | 单条实验完整 JSON |
| `/api/live` | 当前三维实时仿真的 KPI 摘要 |
| `/openapi.json`、`/docs` | OpenAPI 规范与交互文档（供 ChatGPT Actions 使用） |

本机先验证：<http://127.0.0.1:8600/report>

### 10.2 用隧道暴露（二选一）

```powershell
# 方式 A：Cloudflare 临时隧道（需安装 cloudflared）
cloudflared tunnel --url http://127.0.0.1:8600

# 方式 B：ngrok（需安装并登录）
ngrok http 8600
```

拿到 `https://xxxx.trycloudflare.com` 这类公网地址后，建议同时设置：

```powershell
$env:PUBLIC_API_BASE_URL = "https://xxxx.trycloudflare.com"   # 写入 OpenAPI servers
python run_public_api.py
```

### 10.3 在 ChatGPT 中使用

- **最简单：直接读报告。** 在开启联网/浏览能力的对话里粘贴
  `https://<隧道地址>/report?project_id=<项目ID>&token=<令牌>`，让 ChatGPT “读取这个页面并分析”。
  该页面是服务端渲染的纯 HTML，能被抓取器读到内容。
- **结构化访问（Custom GPT Actions）。** 新建 Custom GPT → Actions → 导入
  `https://<隧道地址>/openapi.json` → 认证选 Bearer，填 `PUBLIC_API_TOKEN`。
  之后可以直接问：“列出项目的最新工厂实验、OEE 和瓶颈”。
- 只读接口不提供运行仿真、修改参数或删除数据的能力。

### 10.4 安全与限制

- 不要把 Streamlit（8502）直接暴露到公网：它没有鉴权，且会暴露完整操作界面；
- 对外只暴露只读接口，并设置 `PUBLIC_API_TOKEN`；使用完尽快关闭隧道；
- 接口返回的全部是仿真数据，不包含真实工厂或真实网络测量；
- ChatGPT 看到的 `/report` 是生成时刻的快照，实时数据请用 `/api/live`；
- 如果企业网络限制临时隧道域名，可改用自建反向代理 + 域名 + HTTPS 证书。

---

## 11. 部署上线

仓库根目录已包含 Render 部署包：

- `Dockerfile`：单容器（nginx + Streamlit + RFTwin 三维服务）；
- `render.yaml`：Render Blueprint；
- `deploy/`：nginx / supervisor / entrypoint / 推送助手；
- `DEPLOY.md`：Render 与自有服务器上线步骤、密码保护、磁盘持久化与常见问题；
- `deploy/docker-compose.server.yml` + `deploy/Caddyfile`：自有服务器一键部署（自动 HTTPS）；
- `deploy/CAPACITY.md`：实测资源占用、服务器配置、容错与扩展建议。

要点：Render 免费实例 512MB 可能内存不足，正式使用建议 Starter 并挂载磁盘；
全站由 nginx Basic Auth 保护，三维界面通过同源 `/twin/` 加载，原有参数注入与 WebSocket 能力保持不变。

---

# 附录 A：EdgeCoder 原平台

**AI 驱动的工业边缘智能仿真与软件自动生成平台**

EdgeCoder 是一个面向人工智能与 6G 融合创新场景的纯软件项目：模拟智能工厂中的工业视觉缺陷检测任务，
对比本地计算、云端计算与动态边缘调度三种策略，并用本地可信代码模板自动生成可运行、可测试的工业服务示例。

> 本平台不连接真实 6G 网络、工业设备或云服务器。所有网络、服务器与任务行为均由可解释的仿真模型计算，
> 界面指标全部来自仿真程序的实际运行，不包含硬编码的预设结果，也不得表述为真实网络测试结果。

---

## 1. 功能特性

- **工业场景仿真**：任务生成器模拟工业相机连续产生检测任务；任务包含 ID、数据量、计算需求、时延上限、
  优先级与执行状态。可调整任务数量、到达频率、数据量/计算需求/时延上限范围、网络带宽、时延、拥塞、
  节点算力、基础负载、单任务算力上限与队列容量。
- **AI 工业软件生成（第一阶段）**：基于三个本地可信模板（缺陷检测、预测性维护、质检统计）生成可运行的
  Python 服务示例，展示输入/输出说明、静态安全检查与真实执行的 pytest 测试结果。**不调用任何付费大模型 API**，
  需求文本仅作为注释附加到代码头部，不参与代码拼接或执行。
- **三种可公平对比的调度策略**：固定本地、固定云端、动态加权评分调度。三种策略共享同一任务集、
  网络条件与随机种子，只有调度策略不同。
- **可解释实验结果**：平均/ P95 完成时延、任务完成率、时延约束满足率、超时与拒绝任务数、节点任务分布、
  节点资源利用率、调度决策开销（本机实测）等指标，配合 Plotly 交互式图表与逐任务决策原因。
- **安全边界清晰**：只执行本仓库维护的可信模板；生成代码先经过 AST 白名单静态检查；
  自动化测试在临时目录的独立子进程中运行并设置超时（非强安全沙箱，详见第 8 节）。

---

## 2. 快速开始（Windows PowerShell）

环境要求：Python 3.12 或 3.13（本项目在 **Python 3.12.10** 上完成开发与测试；
代码不依赖 3.13 独有语法，可直接在 3.13 运行）。

```powershell
# 1. 进入项目目录（按实际路径调整）
cd C:\Users\Administrator\Desktop\aic

# 2. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 如果 PowerShell 拦截脚本执行，先临时放开当前会话策略：
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 3. 安装依赖
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. 启动 Web 界面
streamlit run app.py
```

启动后浏览器访问 <http://localhost:8501>（Streamlit 通常会自动打开）。

如果已经装好依赖，也可以不创建虚拟环境，直接在项目目录执行 `streamlit run app.py`。

### 在 VS Code 中启动

1. 用 VS Code 打开项目根目录 `C:\Users\Administrator\Desktop\aic`；
2. 打开终端（`Ctrl + ~`），选择 PowerShell；
3. 激活虚拟环境：`.\.venv\Scripts\Activate.ps1`；
4. 执行 `streamlit run app.py`；
5. 在浏览器中打开终端提示的地址（默认 <http://localhost:8501>）。

### 运行测试

```powershell
# 运行全部测试
python -m pytest -q

# 只运行界面冒烟测试（AppTest，真实执行页面逻辑）
python -m pytest tests/test_app.py -v

# 查看详细测试输出
python -m pytest -v
```

已验证的环境版本：Python 3.12.10、Streamlit 1.66.1、Plotly 7.1.0、pandas 3.0.6、pytest 9.2.0。

---

## 3. 界面使用流程

| 页面 | 说明 |
| --- | --- |
| **项目总览** | 平台用途、当前仿真状态、最近一次实验关键指标与公平性声明。 |
| **场景配置** | 调整任务规模、随机种子、网络条件与节点参数；保存后实验配置实时更新。 |
| **AI 软件生成** | 选择任务类型、输入需求、生成服务代码；查看输入输出说明、静态检查结果，并运行自动化测试。 |
| **仿真实验** | 点击「运行仿真实验」，运行三种策略并展示指标卡片、对比表格与 7 张 Plotly 图表；可下载逐任务 CSV。 |
| **任务详情** | 选择策略与任务，查看目标节点、时延构成、候选节点评分与调度原因，以及同一任务在三种策略下的对比。 |
| **项目说明** | 系统架构、仿真公式、调度算法、指标定义、安全设计与局限性。 |

推荐流程：

1. **场景配置** → 修改参数（例如把任务数量改为 120、提高边缘服务器拥塞系数）；
2. **仿真实验** → 点击「运行仿真实验」，结果按新参数重新计算；
3. **任务详情** → 选中某个超时任务，查看它的时延构成和决策原因；
4. **AI 软件生成** → 选择「工业产品缺陷检测」，生成代码并运行自动化测试。

参数修改后，旧实验结果会显示「配置已修改」提示，需要重新运行实验才会得到对应结果。

---

## 4. 项目结构

```text
项目根目录（本目录）
├── app.py                     # Streamlit 中文界面（六个页面）
├── requirements.txt           # 依赖清单
├── README.md
├── .streamlit/
│   └── config.toml            # 工业风格主题与服务器配置
├── models/
│   ├── __init__.py
│   ├── schemas.py             # 任务、节点、链路、配置、结果数据模型
│   └── defaults.py            # 默认智能工厂场景（本地/边缘/云端三节点）
├── network_sim/
│   ├── __init__.py
│   └── simulator.py           # 可解释的离散事件仿真器（传输/排队/计算/回传）
├── scheduler/
│   ├── __init__.py
│   └── strategies.py          # 固定本地、固定云端、动态加权评分调度
├── experiments/
│   ├── __init__.py
│   ├── runner.py              # 统一任务集生成、三策略编排、配置校验
│   └── metrics.py             # 指标计算（完成率、满足率、利用率、决策开销等）
├── generator/
│   ├── __init__.py
│   ├── template_generator.py  # 本地可信模板生成器 + 预留 LLMBackend 协议
│   └── templates.py           # 三个工业服务模板及其测试代码
├── tester/
│   ├── __init__.py
│   └── validator.py           # AST 静态检查 + 隔离子进程 pytest 执行
└── tests/
    ├── test_generator.py      # 模板生成、静态检查、真实测试执行
    ├── test_scheduler.py      # 三策略公平性、动态调度行为、容量与退化处理
    ├── test_simulator.py      # 任务生成、时延分解、FIFO、队列拒绝、指标一致性
    └── test_app.py            # Streamlit AppTest 界面冒烟测试
```

---

## 5. 仿真模型与假设

量纲统一：计算需求 GFLOP，算力 GFLOPS，带宽 Mbps，数据量 MB，时间 ms。

### 5.1 任务生成

- 相邻任务到达间隔服从**指数分布**，均值 = `1000 / 到达频率`（毫秒），近似产线泊松到达；
- 数据量、计算需求、时延上限在用户设定范围内**均匀分布**；
- 约 **12%** 的任务为高计算量任务（计算需求 ×2，上限不超过范围上限的 2 倍）；
- 优先级按 高/中/低 = 20%/50%/30% 随机抽取；
- 每个任务采样一次网络抖动并写入任务本身，保证三种策略面对完全相同的抖动序列；
- 相同随机种子生成完全相同的任务集，实验可复现。

### 5.2 网络与计算时延

上行传输时延：

```text
t_up = 数据量(MB) × 8 × 1000 / [带宽(Mbps) × (1 − 拥塞系数)] + 单向时延(ms) + 任务抖动(ms)
```

- 结果回传使用同一链路的带宽与单向时延（不含抖动），回传数据量由配置给定；
- 计算时延：`t_compute = 计算需求(GFLOP) / [算力(GFLOPS) × (1 − 基础负载)] × 1000`；
- 基础负载表示与本实验无关的常驻业务占用，是静态比例；动态排队由 FIFO 队列单独建模。

### 5.3 排队、容量与状态

- 每个节点是 **FIFO 单服务台**：任务到达节点后，若节点仍在忙则排队，`开门时刻 = max(到达时刻, 节点下一次空闲时刻)`；
- **队列容量**（含正在计算的任务）满时，任务在执行阶段被拒绝（`rejected`）；
- 任务计算需求超过节点**单任务算力上限**时无法执行，被拒绝；
- 总完成时延 = 上行 + 排队 + 计算 + 回传；
- 总时延 ≤ 时延上限记为**准时完成**（`success`），否则记为**超时完成**（`timeout`）；
- 所有被拒绝或失败的任务仍保留调度记录与原因，便于分析。

### 5.4 公平性保证

每次实验只生成一次任务集；三种策略使用同一份不可变任务集、相同网络与节点参数，
且都从相同的初始状态（节点空闲）开始，唯一变量是调度策略。

---

## 6. 调度算法

### 6.1 固定本地 / 固定云端

所有任务分别发往本地节点 / 云服务器，不做任何权衡。它们为动态调度提供基线，
也会真实暴露容量不足、超时与拒绝问题。

### 6.2 动态加权评分调度

对每个候选节点，调度器在任务到达时刻基于节点状态（下一次空闲时刻、排队数）预测完成时延，并计算：

```text
score = w_p × (预测完成时延 / 任务时延上限) + 0.3 × (决策时刻排队数 / 队列容量)
w_p   = 1.0 + 0.5 × (2 − 优先级) / 2      # 高优先级对时延比值更敏感
```

- 预测排队时延 = `max(0, 节点下一次空闲时刻 − (到达时刻 + 预测上行时延))`；
- **可行节点**需同时满足：单任务算力不超限、预测完成时延 ≤ 时延上限、决策时刻队列未满；
- 在可行节点中选择**评分最低**者，分值越低代表预测完成越快、排队压力越小；
- 若没有可行节点，退化为「预测完成时延最小」的可执行节点，并在决策原因中说明预计超时；
- 若所有节点都无法承载任务的算力需求，则直接拒绝任务；
- 每个任务都记录：候选节点预测时延与评分、是否满足时延、是否可执行、是否选中，以及完整中文决策原因。

### 6.3 调度决策开销

决策开销使用 Python `time.perf_counter_ns` 对每次 `strategy.decide(...)` 调用**实际计时**，
统计平均值与总耗时。它包含 Python 解释器与数据访问开销，属于本机测量结果，
会随机器和运行时刻波动，**不能与毫秒级的仿真时延直接比较**，只适合做策略间的相对参考。

---

## 7. 指标定义

| 指标 | 定义 |
| --- | --- |
| 任务完成率 | (准时完成 + 超时完成) / 总任务数，表示任务被执行引擎成功处理的比例 |
| 时延约束满足率 | 准时完成 / 总任务数 |
| 平均 / P95 完成时延 | 仅统计完成执行的任务（准时 + 超时） |
| 超时任务数 | 完成执行但总时延超过上限的任务数 |
| 拒绝任务数 | 因队列满、单任务算力不足或无可执行节点被拒绝的任务数 |
| 节点任务分布 | 各节点承接（调度）的任务数量 |
| 节点资源利用率 | 节点累计计算忙碌时间 / 本策略仿真跨度（makespan），不含基础负载 |
| 调度决策开销 | 每次决策调用耗时的实测平均值与总和（本机、含解释器开销） |
| 吞吐 | 完成任务数 / 仿真跨度（秒） |

> 说明：任务完成率（成功执行）与时延满足率（按时完成）是两个不同指标，界面与表格中均分别标注。

---

## 8. 安全设计（重要）

- **绝不执行用户自由输入的代码**；需求文本只以注释形式附加到生成代码头部，不参与代码拼接或执行；
- 只执行本仓库维护、经过静态审查的**可信模板**及其配套测试；
- 生成代码先经过 AST 静态检查：语法校验、模块白名单、危险调用（`eval`、`exec`、`open`、`os.system` 等）拦截，
  并要求存在约定入口 `handle_task`；
- 自动化测试在系统临时目录中由独立 Python 子进程执行，设置 60 秒超时，测试结束后临时目录自动删除；
- **局限**：上述子进程机制不是强安全沙箱，可能被恶意代码逃逸。后续接入真实大模型生成的代码时，
  必须改用容器 / 虚拟机等强隔离环境执行，并在界面中如实标注代码来源。

---

## 9. 测试说明

原 EdgeCoder 测试套件包含 **35 个 pytest 测试**（根目录现在还会收集集成层与统一界面测试，共 60 项；
只跑原 EdgeCoder 测试可执行：
`python -m pytest tests/test_generator.py tests/test_scheduler.py tests/test_simulator.py tests/test_app.py -q`）。

35 项测试覆盖：

- **代码生成与验证**：三个模板均通过静态检查；生成的测试在子进程中真实执行且全部通过；
  用户需求仅作为注释、截断处理；静态检查能拦截危险导入与调用；测试失败时能如实报告。
- **调度策略**：三种策略共享同一任务集；固定策略节点正确；动态策略使用多个节点；
  不会把超算力任务发往本地；重任务避开本地；固定本地如实拒绝超算力任务；
  相同种子结果可复现；不同种子任务集不同；无满足时延节点时退化；全部节点算力不足时拒绝。
- **仿真器与指标**：任务范围、到达时间单调；时延四分量之和等于总时延；节点 FIFO 顺序；
  队列满拒绝；指标计数一致且利用率在 [0, 1]；非法配置抛出中文错误；网络时延公式正确。
- **界面**：项目总览加载；生成页面生成服务；仿真页面真实运行三种策略；场景配置表单修改后
  实验输入随之变化；任务详情页可加载。

运行：`python -m pytest -q`。测试结果以实际运行输出为准；如遇依赖或环境问题导致失败，
应阅读输出定位，不作“全部通过”的推断。

---

## 10. 已知局限与后续计划

- 网络模型为简化的带宽 / 时延 / 拥塞模型，未建模无线信道、丢包重传、路由与动态带宽波动；
- 节点为 FIFO 单服务台，未建模多核并行、任务拆分、优先级抢占与故障恢复；
- 任务到达与参数分布为简化假设，不对应特定工厂的真实数据；
- 动态调度是规则加权算法，未使用强化学习；
- 决策开销为本机实测，受解释器与系统负载影响，存在微秒级波动；
- 代码生成阶段使用本地可信模板，**尚未接入真实大模型 API**，已预留 `LLMBackend` 协议；
- 后续计划：实现真实 LLM 后端与强隔离执行、增加能耗与成本指标、支持多工厂协同与异构任务、
  引入历史数据驱动的调度策略评估、增加丢包与重传模型。

---

## 11. 免责声明

EdgeCoder 是用于教学、算法对比与方案演示的软件仿真平台。界面中出现的所有时延、利用率、
成功率与开销数据均由仿真模型和本机实测产生，不代表真实 6G 网络、云服务器或工业设备的性能。
不得将仿真结果表述为真实网络测试结果。
