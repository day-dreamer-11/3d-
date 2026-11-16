# AIC Factory Studio × Open Industry Project 集成报告

- 版本：2.0（2026-10-30）
- OIP 上游固定提交：`dcebf53`（MIT）
- Python 仿真权威：新增通用离散事件引擎 `integration/scene/engine.py`（RFTwin 未修改）
- 全部指标均为软件仿真结果，不是真实工厂或 6G 网络实测

---

## 1. 执行摘要

本次集成把 **OIP 三维编辑器**作为“工厂场景编排器”；主导航已收敛为 OIP 五页（平台总览/场景编辑器/建模分析工作台/实验历史/系统设置），RFTwin 固定产线页面仅隐藏、代码移至 `legacy_pages.py` 保留。

原集成说明如下：**OIP 三维编辑器**作为“工厂场景编排器”，通过桥接把编辑器保存的 OIP 工程自动转换为
**统一设备模型 + 显式物料连接表**，再由 **Python 离散事件仿真引擎**真实运行用户自建的工厂，
并把设备状态、KPI、EdgeCoder 调度、规则式优化和报告打通到现有 Streamlit 工作台。

已完成的最小闭环（实测数据见 `TEST_REPORT.md`、`demo_scene/`）：

```text
OIP 空白场景 → 放置/移动/吸附 6 台设备 → Ctrl+S/同步
→ FastAPI 导入 → 6 设备 + 5 条显式物料连接
→ Python 引擎运行 600 s（基准 70 良品 / 420 件每小时 / OEE 89.3%）
→ EdgeCoder 80 个场景任务（动态调度满足率 96.25%）
→ 规则式优化：两台输送机 0.50→0.75 m/s，真实重跑 97 良品 / 582 件每小时
→ 三维状态回传（Plotly 3D，按真实状态着色）→ 报告 HTML/Markdown
```

关键纪律：

- **不重写 RFTwin/EdgeCoder**：`rftwin/`、EdgeCoder 原模块零改动；RFTwin 固定四单元产线仍可回归使用。
- **Python 是生产仿真唯一权威**：OIP 的 Jolt 物理与内置 PLC 不参与生产 KPI。
- **不写死 KPI**：所有指标由本次场景的 tick 级仿真计算；相同场景/seed/时长可复现。
- **不静默忽略设备**：未映射的设备标记 `display_only` 并产生 warning；混入物料流会校验报错。
- **空间关系与业务关系分离**：几何只生成候选；`connections` 是显式表，设备移动不会自动改写。

---

## 2. 原项目架构审计

| 层 | 现状 |
|---|---|
| 统一工作台 | `factory_app.py`（Streamlit）+ `run_platform.py` / `launcher.py`；当前主导航为 OIP 五页 |
| RFTwin | `rftwin/packages/domain_factory`，固定 4 CELL / 12 机器人 / 2 AMR；`frontend` React+R3F 仅展示，backend-authoritative |
| FastAPI | `public_api.py`（只读项目/实验接口）；RFTwin Live 由 `integration/factory_service.py` 按需启动 |
| EdgeCoder | `network_sim/`、`scheduler/`、`experiments/`、`models/schemas.py`；三种策略共享任务集 |
| 集成层 | `integration/project_store.py`（JSON/SQLite、原子写）、`factory_adapter.py`、`edge_mapping.py`、`analysis.py` |
| 持久化 | `data/projects/<project_id>/project.json` + `experiments/*.json`，原子 `os.replace` |
| 基线测试 | 60 个 pytest 全部通过（67.50 s） |

新增场景能力不复用旧 `validate_project()` 的四单元硬约束；场景使用独立 Schema 与独立存储目录，
旧项目/实验接口保持兼容。

---

## 3. OIP 集成架构

### 3.1 受控子项目

- 源码位置：`frontend/oip/`（上游 `dcebf53`；排除 `.git`/`node_modules` 复制）
- 说明：`frontend/oip/AIC_VENDOR.md`
- 许可证：MIT；第三方声明见 `frontend/oip/THIRD_PARTY_NOTICES.md`（含 LGPL-2.1 的 OCCT/STEP 导入）
- 本地已安装 `node_modules` 并执行 `npm run build`，生成 `dist/`（60 MB，含压缩模型）
- **运行时不依赖 Node**：`integration/scene/oip_service.py` 优先用 Python `http.server` 托管
  `frontend/oip/dist`（127.0.0.1）；`AIC_OIP_DEV=1` 时才用 Vite/Node 24

### 3.2 AIC 桥接（对 OIP 的唯一改动）

1. `app/index.html` 增加 `<script src="/oip-bridge.js"></script>`；
2. 新增 `public/oip-bridge.js`：
   - 加载 `?api=...&projectId=...&sceneId=...`；
   - 就绪后从 `GET /api/projects/{pid}/scenes/{sid}/oip-project` 载入工程；
   - Ctrl+S 或左下角「同步到 AIC」→ `POST /api/oip/scene-save`；
   - 显示同步状态（版本/设备/连接/错误数）。
3. 静态构建用 OIP 官方 `?hook=1` 开关暴露 `window.__oip`（生产构建默认不暴露）。

### 3.3 统一入口

Streamlit「场景编辑」页内嵌 OIP（大视口 iframe），用户无需单独打开另一个前端。

服务拓扑：

```text
start_platform.bat / start_platform.ps1 / python run_platform.py
├─ Streamlit 工作台            8502+（自动选空闲端口）
├─ 场景 FastAPI                8701+（run_platform 启动并健康检查）
├─ OIP 静态编辑器              8610+（首次进入场景编辑页自动启动）
└─ RFTwin Live（原有能力）     8020+（原「三维仿真」页按需启动，未改动）
```

状态文件：`data/runtime/scene_api.json`、`data/runtime/oip_service.json`、`data/runtime/service.json`。
`stop_platform.py` 按状态文件 PID 停止服务；启动器退出时通过 atexit 清理本进程启动的子服务。

---

## 4. 修改和新增文件

### 修改

| 文件 | 变更 |
|---|---|
| `factory_app.py` | OIP 五页主导航；侧栏仅显示场景服务；旧 RFTwin 页面移至 `legacy_pages.py` 保留（不挂载） |
| `run_platform.py` | 启动前确保场景 API，并打印 URL |
| `start_platform.ps1` | 依赖检查、OIP dist 提示 |
| `.gitignore` | 排除 OIP node_modules/dist/cache 与备份目录 |
| `THIRD_PARTY_NOTICES.md` | 增加 OIP 子项目与许可证说明 |

### 新增

| 文件/目录 | 作用 |
|---|---|
| `integration/scene/models.py` | Device/Port/Connection/SceneDocument/ValidationIssue |
| `integration/scene/mapping.py` | OIP 部件 → 统一设备与仿真类型；默认值记录 |
| `integration/scene/connections.py` | 几何候选连接、显式表校验、拓扑排序 |
| `integration/scene/store.py` | 场景 CRUD、版本、原子写、实验保存 |
| `integration/scene/engine.py` | 通用 Python 离散事件引擎、`create_factory_from_config` |
| `integration/scene/manager.py` | 实时仿真线程、暂停/恢复/停止、状态快照 |
| `integration/scene/edge.py` | 场景事件 → EdgeCoder 任务 → 三策略 |
| `integration/scene/analysis.py` | 瓶颈分析、规则式优化、真实重跑对比 |
| `integration/scene/report.py` | JSON/Markdown/HTML 报告 |
| `integration/scene/api.py` | 场景/仿真/分析/Edge/优化/报告/OIP 管理 API（45 条路由） |
| `integration/scene/client.py` | Streamlit → API 客户端 |
| `integration/scene/api_service.py` | 场景 API 进程管理 |
| `integration/scene/oip_service.py` | OIP 静态/开发服务管理 |
| `tests/test_scene_integration.py` | 9 个单元/API 集成测试 |
| `frontend/oip/` | OIP 子项目 + 桥接 + 生产 dist |
| `frontend/oip/public/oip-bridge.js` | AIC 桥接脚本 |
| `demo_scene/` | 演示场景、基准/优化/边缘/优化记录、报告、加载脚本 |
| `docs/integration-shots/` | 7 张最终运行截图 |
| `start_platform.bat`、`stop_platform.py` | Windows 一键启动/停止 |
| `.integration_backup/` | 集成前配置与旧演示数据备份（可回滚） |

---

## 5. 统一设备模型

```json
{
  "id": "pt_2",
  "type": "conveyor",
  "name": "Conveyor A",
  "position": [-5.0, 2.0, 0.0],
  "rotation": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
  "dimensions": {"length": 4.0, "width": 1.524, "height": 0.9},
  "params": {
    "length_m": 4.0, "width_m": 1.524, "speed_mps": 0.5,
    "capacity": 7, "power_kw": 0.6,
    "failure_prob_per_hour": 0.4, "mttr_min": 2.0,
    "item_spacing_m": 0.6, "direction": 1
  },
  "simulation_type": "conveyor",
  "ports": [
    {"id": "in",  "name": "Input",  "direction": "input",  "flow": "material", "offset": [-2, 0, 0], "out": [-1, 0]},
    {"id": "out", "name": "Output", "direction": "output", "flow": "material", "offset": [2, 0, 0],  "out": [1, 0]}
  ],
  "metadata": {"oip_package_id": "oip.roller-conveyor", "defaults_applied": ["power_kw", "..."]}
}
```

`SceneDocument`：

```json
{
  "schema_version": "1.0",
  "scene_id": "SCN-...",
  "project_id": "PRJ-...",
  "name": "最小六设备产线",
  "version": 8,
  "devices": [],
  "connections": [],
  "geometry_candidates": [],
  "validation": {"errors": [], "warnings": []},
  "simulation_settings": {"duration_sec": 600, "seed": 42, "tick_sec": 0.25},
  "oip_project": {"...": "原始 OIP 工程，用于编辑器还原"},
  "metadata": {"ignored_parts": [], "mapping_defaults": {}}
}
```

设备 ID 直接使用 OIP `part.id`（如 `pt_2`），转换/重导入保持稳定；实验记录引用 `scene_id + scene_version`。

---

## 6. 设备映射机制

`integration/scene/mapping.py` 的注册表：

| OIP 部件包 | 语义类型 | 仿真类型 | 关键参数映射 |
|---|---|---|---|
| `oip.roller-conveyor`, `oip.belt-conveyor`, `oip.curved-*`, `oip.flex-conveyor`, `oip.spur-*`, `oip.turntable-conveyor` | conveyor | conveyor | `length`→`length_m`；`speed`/`speedFpm`→`speed_mps`；宽度；容量=ceil(length/spacing) |
| `oip.diffuse-sensor`, `oip.laser-sensor`, `oip.color-sensor`, `oip.barcode-scanner`, `oip.light-curtain` | inspection | inspection | 默认节拍 2 s、通过率 0.95、精度 0.98；条码枪通过率 0.99 |
| `oip.six-axis-robot`, `oip.gantry`, `oip.agv` | robot | robot | 默认节拍 4 s；reach 0.94×scale；载荷 10 kg |
| `oip.box-spawner` | spawner | spawner | `boxesPerMinute`→`interval_sec=60/bpm`；产品类型 |
| `oip.despawner` | despawner | despawner | 出口/计数 |
| 其他 `packageId` | unknown | display_only | 保留三维位置，产生 `unsupported_device` warning；参与物料流会报错 |
| building/group/annotation/box/pallet/unit-load | — | 忽略 | 记录 ignored，不冒充设备 |

默认值（不在 OIP 参数中的仿真假设）在 `DEFAULT_SIM_PARAMS` 中统一维护，并记录到
`metadata.defaults_applied`；重导入会保留用户修改过的仿真专属参数（如故障率），只覆盖 OIP 派生字段。

---

## 7. 显式连接与校验

### 7.1 两级连接

- `geometry_candidates`：按端口世界坐标生成（输出→输入，距离 ≤1.5 m，垂直差 ≤1.2 m，方向反向优先）。
  仅建议，不参与仿真。
- `connections`：仿真唯一使用的显式表。新场景连接表为空时采用候选作为初始值（标记
  `geometry_derived_initial`）；**一旦有显式表，设备移动/删除都不会自动改写它**。
- 重新推导必须显式调用 API/UI；旧表保留，差异以 warning 呈现。

### 7.2 校验规则（错误）

- 设备/端口不存在、自连接、端口方向/物料类型不匹配、重复连接；
- 仅三维展示设备混入物料流；
- 物料流环路；
- 没有 Spawner / 没有 Despawner；
- 从 Spawner 无法到达任何 Despawner（物料流中断）；
- 端口重复/非法连接类型；场景/设备 ID 重复、位置/参数非有限数值。

### 7.3 warning

- 输出端口多个下游（引擎按顺序尝试）；
- 设备未接入从 Spawner 出发的流（孤立）；
- 无输入/无输出；
- 连接端点因设备移动相距 >3 m（提示确认，不改业务表）；
- 未映射设备、几何无候选目标。

---

## 8. API 一览（场景服务，45 条路由）

### 场景管理

```text
GET    /api/health
GET    /api/projects/{pid}/scenes
POST   /api/projects/{pid}/scenes
GET    /api/projects/{pid}/scenes/{sid}
PUT    /api/projects/{pid}/scenes/{sid}
DELETE /api/projects/{pid}/scenes/{sid}
GET    /api/projects/{pid}/scenes/{sid}/devices
GET    /api/projects/{pid}/scenes/{sid}/connections
PUT    /api/projects/{pid}/scenes/{sid}/connections
POST   /api/projects/{pid}/scenes/{sid}/derive-connections
POST   /api/projects/{pid}/scenes/{sid}/validate
GET    /api/projects/{pid}/scenes/{sid}/oip-project
POST   /api/projects/{pid}/scenes/{sid}/oip-import
POST   /api/oip/scene-save                  # 编辑器桥接自动调用
GET    /api/projects/{pid}/scenes/{sid}/export
GET    /api/projects/{pid}/scenes/{sid}/versions
POST   /api/projects/{pid}/scenes/{sid}/versions/{v}/restore
```

### 仿真

```text
POST /api/projects/{pid}/scenes/{sid}/simulations          # 离线确定性实验
POST /api/projects/{pid}/scenes/{sid}/simulations/live     # 实时实例
GET  /api/simulations
GET  /api/simulations/{sim_id}
POST /api/simulations/{sim_id}/pause|resume|stop
GET  /api/simulations/{sim_id}/kpis|devices/status|events|result
```

### 分析 / Edge / 优化 / 报告

```text
GET  /api/projects/{pid}/scenes/{sid}/experiments
GET  /api/projects/{pid}/experiments/{eid}
GET  /api/projects/{pid}/scenes/{sid}/compare?baseline=&candidate=
GET  /api/projects/{pid}/scenes/{sid}/analysis
POST /api/projects/{pid}/scenes/{sid}/edge
POST /api/projects/{pid}/scenes/{sid}/what-if
POST /api/projects/{pid}/scenes/{sid}/optimize
POST /api/projects/{pid}/scenes/{sid}/optimize/apply
POST /api/projects/{pid}/scenes/{sid}/reports
```

### OIP 服务

```text
GET  /api/oip/status | POST /api/oip/start | POST /api/oip/stop | GET /api/oip/url
```

---

## 9. 仿真调用链

```python
from integration.scene.engine import create_factory_from_config, run_scene_simulation

engine = create_factory_from_config(scene_doc, duration_sec=600, seed=42)
engine.run()
record = engine.build_record(persist=True)
```

模型：

- 固定步长（默认 0.25 s）、单 RNG 种子、确定性；
- 每个设备单服务台 + 有限队列；输送机节拍 = 长度/速度；
- 检测站：按 `pass_rate` 判定，不良品在检测站离开并计为 defect；
- 下游容量满 → `blocked_item` 等待，不重复加工/判定（已修复回归 bug）；
- 故障：指数间隔（`failure_prob_per_hour`）+ 指数维修（`mttr_min`），维修期间停机；
- KPI：良品/不良/WIP/吞吐/OEE（A×P×Q）/平均周期/能耗/故障统计/设备利用率；
- 设备状态：idle/running/processing/blocked/failed/maintenance，带 `timestamp` 与 `sim_time_sec`。

实时仿真由 `manager.py` 线程按 wall-clock × speed 推进；暂停/恢复/停止；停止或完成时持久化
（`source=scene_engine_live`），实时结果不承诺逐位复现，离线实验才是可复现权威。

实验记录与场景快照：`config.scene_snapshot` 保存完整设备/连接/参数；`scene_version` 关联版本。
场景后续修改不会改变历史实验快照。

---

## 10. EdgeCoder 接入

`integration/scene/edge.py` 从**本次场景实验事件**生成任务（不是固定数量）：

| 任务类型 | 来源 | 默认数据量 / 算力 / 截止 |
|---|---|---|
| vision_inspection | INSPECTION_PASS | 4 MB / 3 GFLOP / 800 ms |
| quality_judgment | INSPECTION_FAIL | 4.5 MB / 4 GFLOP / 600 ms |
| fault_warning | FAILURE | 1 MB / 4.5 GFLOP / 500 ms |
| device_monitoring | 每设备每 10 分钟 | 0.5 MB / 0.8 GFLOP / 1500 ms |
| production_scheduling | 每 15 分钟 | 0.2 MB / 1.5 GFLOP / 2000 ms |

- 任务 `source` 保留设备 ID / 事件类型 / 事件序号；
- 同一任务集、网络参数、seed 下运行固定本地/固定云端/动态三种策略；
- 结果保存 `metrics`（满足率、平均/P95 时延、成功率、超时、拒绝、节点负载）与 `costs`；
- 与场景实验通过 `factory_experiment_id + scene_id + scene_version` 关联。

实测：80 个任务，动态调度满足率 96.25%，固定本地 47.5%，固定云端 7.5%（全部为软件仿真）。

---

## 11. AI 分析接入

`integration/scene/analysis.py`：

- 瓶颈：设备利用率 + 队列/阻塞排序；给出证据（瓶颈、WIP、OEE 分解、周期、缺陷率、能耗、故障）。
- 建议（规则式，不调用外部大模型）：
  - 输送约束：覆盖节拍相同的整组输送机（避免只改一台无效）；
  - 检测/机器人瓶颈：缩短节拍；
  - 质量假设敏感性：通过率 +1%；
  - 故障率下调 30%；
  - 缓冲 WIP 扩容。
- **评估必须真实重跑**：baseline 与每个候选使用相同场景/seed/时长，输出 delta 与评分；不写预设改善幅度。
- **应用优化**：把最佳补丁写入场景副本 → 保存新版本 → 重跑 → 保存新实验；旧版本/旧实验保留。
- 优化记录保存 `baseline_metrics / evaluations / best / assumptions`。

本次演示最佳建议：两台输送机 0.50→0.75 m/s；真实重跑 70→97 良品、420→582 件/小时、
平均周期 -29.6%；OEE 89.3%→79.9%（提速换产能的权衡，报告如实展示）。

---

## 12. 部署方式

### Windows 一键启动

```text
start_platform.bat          # 双击，内部调用 run_platform.py
start_platform.ps1          # PowerShell 版本（依赖检查）
python run_platform.py      # 也可以直接运行
python stop_platform.py     # 停止场景 API / OIP / RFTwin Live
```

- 端口冲突自动选择；只监听 127.0.0.1；
- 服务健康检查：场景 API `/api/health`、OIP `/app/`、RFTwin `/api/health`；
- 日志：`data/logs/`（scene_api.log、oip-*.log、rftwin-*.log）；
- 数据：`data/projects/<project_id>/scenes/<scene_id>/`；场景版本不可变；
- OIP 静态资源：`frontend/oip/dist`（已构建）；离线运行时只需 Python；
- 重新构建 OIP 需要 Node 24（`npm ci && npm run build`），不影响运行时；
- 现有安装包仍按原方式打包 Python 运行时；下一阶段把 `frontend/oip/dist` 纳入安装包即可
  实现完全离线（本次未改安装包构建流程）。

### 端口与进程

| 服务 | 端口 | 启动方式 |
|---|---|---|
| Streamlit | 8502–8529 | run_platform（前台） |
| 场景 API | 8701+ | run_platform 自动启动 |
| OIP 静态编辑器 | 8610+ | 场景编辑页按需启动 |
| RFTwin Live | 8020+ | 原三维仿真页按需启动 |

---

## 13. 测试结果摘要

- 全量 pytest：**79 passed**（约 40 s，含存储压缩、实时实例清理与金标 KPI 回归）。
- 真实浏览器：OIP 静态编辑器载入 6 设备；桥接同步 v8（重复同步不升版）；Streamlit 三页无异常；离线/实时/Edge/优化/报告全流程 0 控制台错误。
- 详细逐项结果与复现命令见 `TEST_REPORT.md`。

---

## 14. 已知问题与未完成事项

1. **OIP 仍是 pre-1.0**：固定 `dcebf53`；升级需重新验证 Bridge/Schema/构建。
2. **映射范围**：覆盖 19 个内置部件包；其他 OIP 部件为 `display_only`，不参与仿真。
3. **连接类型**：`control/data/power` 可保存但不参与当前仿真；仅 `material_flow` 建模。
4. **连接推导是几何启发式**：track/mount/edge/chain 等 OIP 吸附关系未全部复刻；显式表优先。
5. **引擎简化**：输送机单服务台延迟；多下游按顺序尝试；不支持复杂合流/分流优先级、累积输送物理。
6. **OEE 是线级简化公式**：提速后理论产能变化会使 OEE 与产能方向不完全一致；报告同时展示产能/周期/OWIP，不隐藏权衡。
7. **实时实验非逐位复现**：wall-clock 线程推进；离线实验才是确定性权威。
8. **OIP 状态叠加**：三维状态用独立 Plotly 3D 视图（真实坐标/状态）；未直接给 OIP 模型着色（OIP 无公开状态覆盖 API）。
9. **UI 语言**：OIP 编辑器为英文；工作台为中文。
10. **单进程 API**：场景写入用进程内锁 + 原子 replace；多 API 实例并发写同一场景未做分布式锁。
11. **安全边界**：仅监听 127.0.0.1；无认证；远程访问需反向代理与鉴权。
12. **安装包**：尚未把 OIP dist 纳入现有安装包；运行时已不依赖 Node。
13. **优化器是规则式**：候选集有限，不做参数网格/贝叶斯搜索；评估是真实重跑。

---

## 15. 下一阶段建议

1. 用 `npm run build` 产物纳入 Windows 安装包，完成完全离线交付与版本锁定。
2. 扩展映射注册表：支持更多 OIP 部件（divider/turntable/agv 路径）与自定义部件插件
   （OIP packageId → Python 模型插件）。
3. 实现 OIP 吸附类型（track/mount/edge/chain）的候选连接规则，减少人工确认。
4. 在 OIP 中叠加设备状态（通过自定义渲染层/扩展 API），或改用 R3F 状态视图与编辑器共用场景。
5. 增强引擎：多下游路由策略、合流优先级、缓存区策略、班次/换型、更多能耗模型。
6. 增加场景版本 UI（列表/对比/一键回滚），复用已有 versions/restore API。
7. 引入鉴权、多用户、远程部署与审计日志。
8. 扩充确定性回归：固定场景 + 固定 seed 的 KPI 金标；边缘任务金标。

---

## 16. 2026-11-03 性能与结构优化

- **OIP 工程内容寻址**：同一工程只落盘一份（`scenes/<sid>/oip/<hash>.json`），
  `scene.json` 与版本快照只存哈希引用；版本文件从 ~19MB 降到 KB 级。
  实测两个演示项目存储 173MB → 115MB（节省 58MB）。
- **轻量摘要**：保存时写 `summary.json`，`list_scenes` 从 ~50ms 降到 <1ms/项目。
- **接口瘦身**：`GET /scenes/{sid}` 默认不返回 OIP 工程（需要时加 `?include_oip=true`）；
  编辑器仍走专用 `/oip-project`。
- **Streamlit 缓存**：项目/实验摘要按目录指纹缓存，写操作后失效；HTTP 客户端资源复用。
- **实时实例清理**：`SimulationManager` 只保留最近 20 个已结束实例；实例 ID 改为全局唯一；
  避免每个实例重复 deepcopy 场景。
- **结构拆分**：`factory_app.py` 4427 → 约 1930 行；旧页面移至 `legacy_pages.py`，
  共享 helper/缓存拆到 `ui_shared.py` / `ui_cache.py`。
- **数据清理**：归档 3 个空测试项目与 4 个空场景到 `data/archive/`；迁移工具
  `tools/compact_scene_storage.py` 可重复执行。
- **编辑器大容量自动保存**：`oip-bridge.js` 为 localStorage 增加 IndexedDB 溢出层，
  20MB 的演示工程可正常自动保存（实测 IDB 记录 19.9MB），刷新后恢复未同步编辑；
  不再出现 OIP 的 “Not saving” 配额告警。
- **旧缓存清理**：新增 `reset.html`，进入编辑器前注销 OIP 旧 Service Worker 并清理缓存；
  内嵌模式禁用 SW 注册，避免 SW 预缓存旧桥接脚本导致修复不生效。
- **场景版本 UI**：场景编辑器新增版本历史（列表、设备增删/参数差异、恢复），
  `restore_version` 返回摘要而非整份工程；新增版本详情与报告列表接口。
- **演示数据重置**：系统设置可从 `demo_seed` 恢复演示项目，同名旧项目归档；
  SQLite 模式同步 upsert 项目/实验。
- **安装包 1.1.1**：补齐 `ui_cache/ui_shared/legacy_pages/tools`，实测静默安装→启动→五页→卸载；
  124MB，SHA256 `822153fa…`。
