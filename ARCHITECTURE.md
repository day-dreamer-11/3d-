# AIC Factory Studio 集成架构

> OIP 三维场景编辑 → 统一设备模型 → Python 离散事件仿真 → EdgeCoder → 分析与报告
>
> 图中所有 KPI/调度/网络数值均来自软件仿真，不是真实工厂或 6G 实测。

---

## 1. 系统总览

```mermaid
flowchart LR
  subgraph Browser["浏览器（用户只打开一个工作台）"]
    UI["Streamlit 工作台<br/>factory_app.py"]
    OIP["OIP 编辑器 iframe<br/>frontend/oip/dist"]
    V3D["Plotly 3D 设备状态视图"]
  end

  subgraph Py["Python 服务"]
    API["场景 FastAPI<br/>integration/scene/api.py :8701+"]
    STORE["场景/实验存储<br/>data/projects/**"]
    ENG["场景离散事件引擎<br/>integration/scene/engine.py"]
    LIVE["实时仿真管理器<br/>manager.py"]
    EDGE["EdgeCoder<br/>network_sim + scheduler"]
    AN["规则式分析/优化<br/>analysis.py"]
    REP["报告<br/>report.py"]
    RFTS["RFTwin Live 服务管理<br/>factory_service.py"]
  end

  subgraph Legacy["原有能力（未改写）"]
    RFT["RFTwin 固定四单元引擎<br/>domain_factory"]
    RFF["RFTwin React/R3F 前端"]
    EC["EdgeCoder 原模块/独立入口"]
  end

  UI -->|REST/iframe| API
  UI -->|iframe| OIP
  OIP -->|Ctrl+S 桥接 POST| API
  API --> STORE
  API --> ENG
  API --> LIVE
  LIVE --> ENG
  API --> EDGE
  API --> AN
  AN --> ENG
  API --> REP
  UI -->|原有页面| RFTS
  RFTS --> RFT
  RFTS --> RFF
  EDGE --- EC
  V3D --- UI
```

RFTwin 固定四单元产线仍走原来的 `build_factory_params → FactoryEngine`；自建场景走新的
`SceneFactoryEngine`。两者共享项目/实验存储，但引擎与数据模型隔离。

---

## 2. 统一数据模型

```mermaid
erDiagram
  PROJECT ||--o{ SCENE : has
  SCENE ||--|{ SCENE_VERSION : snapshots
  SCENE ||--o{ DEVICE : contains
  DEVICE ||--o{ PORT : exposes
  SCENE ||--o{ CONNECTION : explicit_flow
  SCENE ||--o{ GEOMETRY_CANDIDATE : suggestions
  SCENE ||--o{ EXPERIMENT : runs
  EXPERIMENT ||--o{ EDGE_EXPERIMENT : maps_to
  EXPERIMENT ||--o{ OPTIMIZATION : evaluates
  EXPERIMENT ||--o{ REPORT : summarizes

  PROJECT { string project_id string name }
  SCENE { string scene_id string project_id int version json oip_project }
  DEVICE { string id string type string simulation_type list position json params json ports }
  PORT { string id string direction string flow list offset }
  CONNECTION { string id string source_device string target_device string type }
  EXPERIMENT { string experiment_id string scene_id int scene_version json config json kpis }
```

关键字段：

- `Device`：`id/type/name/position/rotation/dimensions/params/simulation_type/ports/metadata`
- `Port`：`id/direction(input/output)/flow(material)/offset/out`
- `Connection`：`source_device/source_port/target_device/target_port/connection_type`
- `SceneDocument`：`devices/connections/geometry_candidates/validation/simulation_settings/oip_project`

设备 ID 使用 OIP `part.id`，场景版本/实验快照保证可追溯。

---

## 3. 数据流（主编排链）

```mermaid
sequenceDiagram
  autonumber
  actor U as 用户
  participant S as Streamlit
  participant O as OIP 编辑器(iframe)
  participant A as 场景 FastAPI
  participant E as 场景引擎
  participant C as EdgeCoder
  participant R as 报告

  U->>S: 打开「场景编辑」
  S->>A: GET /api/oip/url
  A-->>S: OIP URL(api/projectId/sceneId/hook=1)
  S->>O: iframe 加载
  O->>A: GET /oip-project
  A-->>O: 已保存 OIP 工程
  U->>O: 放置/移动/吸附/改参
  U->>O: Ctrl+S / 同步到 AIC
  O->>A: POST /api/oip/scene-save {project}
  A->>A: OIP→统一设备→几何候选→保留/采用显式连接
  A->>A: 版本+1，原子写 scene.json + versions/v.json
  A-->>O: 设备/连接/校验摘要
  U->>S: 进入「场景仿真」
  S->>A: POST /simulations {duration, seed}
  A->>E: create_factory_from_config(scene)
  E->>E: tick 级离散事件仿真
  E-->>A: KPI/设备状态/事件/分钟曲线
  A-->>S: 实验记录（持久化）
  S->>A: POST /edge {experiment_id}
  A->>C: 场景事件→任务→三种策略
  C-->>A: 满足率/时延/拒绝/成本
  S->>A: POST /optimize
  A->>E: baseline + 候选参数真实重跑
  A-->>S: 建议/对比/最佳
  S->>A: POST /reports
  A->>R: JSON/Markdown/HTML
  R-->>S: 报告
```

---

## 4. 仿真状态机

```mermaid
stateDiagram-v2
  [*] --> idle
  idle --> running: 有物料且开始加工(conveyor/buffer)
  idle --> processing: 有物料且开始加工(inspection/robot)
  running --> blocked: 下游容量满
  processing --> blocked: 检测通过但下游满
  blocked --> running: 下游有空位
  processing --> idle: 检测不良(离开系统)
  running --> idle: 无物料/生产节拍结束
  idle --> failed: 故障发生
  running --> failed: 故障发生
  processing --> failed: 故障发生
  blocked --> failed: 故障发生
  failed --> idle: 维修完成
  idle --> maintenance: (预留状态)
  maintenance --> idle: 维护结束
```

设备状态每次快照带 `timestamp` 与 `sim_time_sec`；三维视图按真实状态着色。

---

## 5. 仿真引擎数据流

```mermaid
flowchart TD
  S[Unified Scene] --> V{校验}
  V -->|errors| X[拒绝运行 + 明确错误]
  V -->|ok| G[显式 material_flow 图]
  G --> T[拓扑排序]
  T --> R[DeviceRuntime<br/>queue/current/blocked_item]
  R --> L{每个 tick}
  L --> F[故障：结算维修→可能新故障]
  L --> P[阻塞重试：只传递，不重复加工]
  L --> C[加工完成：结算一次<br/>检测判定/出口计数]
  L --> N[开始下一件]
  L --> M[分钟曲线/KPI]
  M --> O[Experiment Record<br/>KPI/设备/事件/快照]
```

确定性约束：

- 固定 `tick_sec`、单一 `random.Random(seed)`；
- 相同场景 + 相同 seed + 相同时长 ⇒ 相同 KPI；
- 实时仿真由 wall-clock 推进，不作为可复现实验权威。

---

## 6. 连接与校验

```mermaid
flowchart LR
  OIP[OIP parts + ports] --> GEO[几何候选<br/>距离/方向/高度]
  GEO --> CAND[geometry_candidates]
  OLD[已有显式 connections] -->|非空| KEEP[原样保留]
  CAND -->|连接表为空| ADOPT[作为初始 connections]
  KEEP --> VAL[校验]
  ADOPT --> VAL
  VAL -->|errors| BLOCK[禁止仿真]
  VAL -->|warnings| WARN[展示/确认]
```

- 空间吸附 ≠ 生产流程；移动设备只产生 `connection_geometry_mismatch` warning。
- 删除设备后旧连接保留并报 `missing_*_device`，不会静默重连。
- 重新推导/采用候选必须由 API/UI 显式触发。

---

## 7. 持久化布局

```text
data/projects/<project_id>/
├─ project.json                      # 原有项目（RFTwin 参数等）
├─ experiments/<experiment_id>.json  # 工厂/边缘/优化/实时实验
└─ scenes/<scene_id>/
   ├─ scene.json                     # 当前统一场景（OIP 存哈希引用，不含整份工程）
   ├─ summary.json                   # 列表用轻量摘要（设备数/版本/校验计数）
   ├─ oip/<hash>.json                # OIP 工程，按内容寻址，相同工程只存一份
   ├─ versions/0001.json ...         # 不可变版本（同样只存 OIP 哈希引用）
   └─ reports/<report_id>.{json,md,html}
```

`load_scene()` 会按 `oip_blob` 自动从 `oip/` 读回工程；旧格式（工程内联在
`scene.json`/版本文件）仍可直接读取，可用 `tools/compact_scene_storage.py` 迁移。

- 所有 JSON 使用临时文件 + `os.replace` 原子写；
- 场景写入在 API 进程内加可重入锁；多 API 实例未做分布式锁；
- 实验保存 `config.scene_snapshot`、`scene_id`、`scene_version`，后续场景变化不影响历史实验。

---

## 8. 服务与端口

```mermaid
flowchart LR
  B[浏览器] -->|8502+| ST[Streamlit]
  ST -->|8701+| API[场景 FastAPI]
  ST -->|8020+| RT[RFTwin Live]
  API -->|8610+| OIP[OIP 静态服务]
  API -->|内嵌| ENG[场景引擎/EdgeCoder]
```

| 服务 | 默认 | 启动 |
|---|---|---|
| Streamlit | 8502–8529 | `run_platform.py`（前台） |
| 场景 API | 8701+ | `run_platform.py` 自动 |
| OIP | 8610+ | 场景编辑页按需（Python http.server） |
| RFTwin Live | 8020+ | 原三维仿真页按需 |

健康检查：`/api/health`（场景 API）、`/app/`（OIP）、`/api/health`（RFTwin）。
运行时状态：`data/runtime/{scene_api,oip_service,service}.json`；`stop_platform.py` 统一停止。

---

## 9. EdgeCoder 与 AI 分析

```mermaid
flowchart TD
  EV[Scene events] --> MAP[任务映射]
  MAP --> V[vision_inspection]
  MAP --> Q[quality_judgment]
  MAP --> F[fault_warning]
  MAP --> M[device_monitoring]
  MAP --> S[production_scheduling]
  V & Q & F & M & S --> TASKS[TaskSpec]
  TASKS --> L[fixed_local]
  TASKS --> C[fixed_cloud]
  TASKS --> D[dynamic]
  L & C & D --> MET[满足率/时延/拒绝/成本]
  MET --> EDGE[Edge experiment]
  EDGE --> ANA[场景分析]
  ANA --> OPT[规则建议]
  OPT --> RERUN[真实重跑 baseline/candidate]
  RERUN --> BEST[最佳补丁]
  BEST --> APPLY[新场景版本 + 新实验]
  APPLY --> REP[报告]
```

分析只引用实验记录中的真实指标；候选评估必须重跑；应用优化会生成新版本并保留旧实验。

---

## 10. 核心不变量

1. Python 场景/仿真引擎是生产 KPI 的唯一权威；OIP 不做生产仿真。
2. 设备 ID 跨导入稳定；场景版本不可变；实验引用版本与参数快照。
3. 显式 `connections` 与几何候选分离；移动不自动改业务流。
4. 未映射设备显式标记 display_only；参与物料流即报错。
5. 所有 KPI 来自 tick 级仿真，不写死、不伪造；实时与离线实验明确区分。
6. RFTwin/EdgeCoder 核心代码零改动；原有四单元产线与测试保留。
7. 所有服务只监听 127.0.0.1；日志/状态/数据均可追溯。
