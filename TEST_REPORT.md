# 集成测试报告（TEST_REPORT）

- 日期：2026-11-03（最近一次全量回归）
- 环境：Windows 11 Home 10.0.26200；Python 3.12.10；Node 24.16.0；Edge/Chromium（Playwright 1.60 驱动）
- 项目：`C:/Users/Administrator/Desktop/aic`
- 演示项目：`PRJ-20261030-CABE65`（自建工厂演示项目）
- 演示场景：`SCN-20261030-220854-B10F`（最小六设备产线，最终 v8）
- 全部指标为 Python 软件仿真结果，不是真实工厂或 6G 实测

> 上一版安装包测试报告已保留为 `docs/TEST_REPORT_v1.0_20261029.md`。

---

## 1. 自动化测试总览

| 套件 | 数量 | 结果 | 用时 |
|---|---|---|---|
| 原有测试（tests/ 除新增） | 60 | 60 passed | 基线 67.5 s |
| 新增场景集成测试 | 9 | 9 passed | 2.1 s |
| 新增整包导出/导入测试 | 3 | 3 passed | 0.3 s |
| 新增装饰件映射测试 | 3 | 3 passed | 0.2 s |
| 新增存储压缩/实时清理测试 | 2 | 2 passed | 0.3 s |
| 新增演示数据重置测试 | 1 | 1 passed | 0.2 s |
| 全量回归 | **79** | **79 passed** | **约 40 s**（2026-11-03 优化后复测） |
| 浏览器 E2E（OIP + Streamlit 五页） | 9 个场景 | 全部通过，0 控制台错误 | 见下 |

复现：

```bash
cd C:/Users/Administrator/Desktop/aic
python -m pytest -q
```

新增测试文件：`tests/test_scene_integration.py`、`tests/test_scene_storage_compact.py`、`tests/test_demo_seed.py`

### 2026-11-03 追加验证

| 项目 | 结果 |
|---|---|
| 场景存储压缩 | OIP 工程单份存储；版本文件 KB 级；`list_scenes` <1ms/项目 |
| 实时实例清理 | `max_finished` 生效；实例 ID 唯一；场景不重复 deepcopy |
| 金标 KPI | 固定场景 + seed 11 + 600s：66 良品 / 7 不良 / 396 件每小时 / WIP 9 / OEE 0.7956 |
| UI 回归 | AppTest 五页渲染通过；项目/实验缓存按目录指纹自动失效 |
| 浏览器五页 | 平台总览/场景编辑器/建模分析工作台/实验历史/系统设置 render-error 0、控制台错误 0；场景编辑器 OIP 加载 12 部件 |
| IndexedDB 自动保存 | 20MB 演示工程 `persistProject()` 返回 ok，IDB 记录 19.9MB；重载后提示「已恢复本地未同步编辑」；`Not saving` banner 0 |
| 旧 Service Worker 清理 | 预注册旧 SW（1 个 registration + 1 个 cache）后进入编辑器：registration/cache 均清零，SW 注册被拒绝，桥接脚本为新版，0 告警 |

| 测试 | 覆盖 |
|---|---|
| `test_mapping_keeps_ids_and_types_and_derives_five_connections` | ID 稳定、5 类设备映射、5 条候选/显式连接、无校验错误 |
| `test_unknown_device_is_display_only_not_silently_dropped` | 未知部件 → display_only + warning |
| `test_engine_is_deterministic_and_sensitive_to_speed_and_inspection` | 同 seed 结果一致；速度→产能/瓶颈变化；检测节拍→瓶颈变化 |
| `test_connection_validation_detects_missing_device_cycle_and_broken_flow` | 删设备、环路、断流、设备移动后几何错位 warning 且业务表不变 |
| `test_store_versions_are_immutable_and_idempotent` | 同内容不升版；改内容升版；版本恢复生成新版本 |
| `test_analysis_and_optimizer_use_real_reruns` | 分析/建议/候选真实重跑，不是预设改善 |
| `test_api_full_flow_and_history_snapshot` | 真实 uvicorn：导入→6 设备/5 连接→仿真→分析→Edge→优化→报告→改参→旧实验快照不变 |
| `test_api_delete_device_and_broken_flow_are_rejected` | 删关键设备/断连 → 校验错误，仿真 400 拒绝 |
| `test_api_add_device_keeps_ids_and_warns_isolated` | 增加设备同步、ID 稳定、孤立 warning、显式连接不被自动改写 |

---

## 2. 用户要求的 20 步真实业务测试

测试产线：`Spawner pt_1 → Conveyor A pt_2 → Inspection B pt_3 → Robot C pt_4 → Conveyor D pt_5 → Despawner pt_6`

| # | 步骤 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | 在 OIP 中保存场景 | 保存后同步到 API | OIP Ctrl+S/同步按钮 → 场景 v2；静态构建多次同步（幂等）→ 最终 v8（6 设备） | PASS |
| 2 | 刷新 OIP 页面 | 6 设备重新载入 | `store.getState().scene.parts.length = 6`，ID 为 pt_1..pt_6 | PASS |
| 3 | 重新加载场景 | 版本/设备/连接保持 | API 读取 v8，6 设备 5 连接 | PASS |
| 4 | 导入 Python | 转换成功 | `POST /oip-import` → 6 设备、5 连接、0 错误 | PASS |
| 5 | 检查设备数量 | 6 | 6（spawner 1、conveyor 2、inspection 1、robot 1、despawner 1） | PASS |
| 6 | 检查设备参数 | OIP 参数真实传递 | A/D length=4 m、speed=0.5 m/s；B cycle=2 s/pass=0.95；C cycle=4 s；Spawner 4 s | PASS |
| 7 | 检查连接关系 | 5 条物料流 | pt_1.out→pt_2.in→pt_3.in→pt_4.in→pt_5.in→pt_6.in | PASS |
| 8 | 构建 Python 仿真工厂 | 由配置创建 | `create_factory_from_config(scene)` 成功；无四单元硬编码 | PASS |
| 9 | 运行生产仿真 | 真实运行 600 s | 引擎 tick 0.25 s，2400 tick，结果持久化 `SEXP-...` | PASS |
| 10 | 产品是否真实经过流程 | 6 台设备均有处理/事件 | 事件 source_id 覆盖 pt_1..pt_6；设备 processed：A 74、B 37、C 35、D 35、Sink 35 | PASS |
| 11 | 检查产量 | 来自实际运行 | 良品 70、总产出 73、产能 420 件/小时 | PASS |
| 12 | 检查 WIP | ≥0 且与实际队列一致 | WIP 9 | PASS |
| 13 | 检查设备利用率 | 来自设备统计 | Conveyor A 96.9%、Conveyor D 90.7%、Robot 44.4%、Inspection 21.6% | PASS |
| 14 | 检查设备状态 | 带时间戳 | idle/running/processing/blocked/failed 状态与 `timestamp`、`sim_time_sec` | PASS |
| 15 | EdgeCoder 是否收到场景任务 | 任务数量/类型由场景决定 | 80 个：视觉检测 70、质量判定 3、故障预警 1、设备监测 6；动态满足率 96.25% | PASS |
| 16 | 检查分析结果 | 瓶颈来自实际数据 | 瓶颈 Conveyor A（利用率 96.9%），证据含 WIP/OEE/周期/能耗/故障 | PASS |
| 17 | 保存实验 | 实验记录关联场景版本 | `baseline_experiment.json`、`edge_experiment.json`、`optimization_record.json` | PASS |
| 18 | 生成报告 | HTML/Markdown/JSON | `RPT-20261030-220939-FAC6`，HTML 文件存在，含设备/连接/KPI/Edge/优化 | PASS |
| 19 | 修改一个设备参数 | 真正写入新版本 | 两台输送机 0.50→0.75 m/s；场景 v5（后续 OIP 同步/幂等升至 v8） | PASS |
| 20 | 重新运行并比较 | 结果真实变化 | 70→97 良品、420→582 件/小时、周期 73.0→51.4 s、OEE 89.3%→79.9% | PASS |

> 说明：步骤 20 的 OEE 下降是“提速换产能”的权衡；系统没有隐藏，报告与 UI 同时展示产能、周期、WIP、OEE。

---

## 3. 必须验证的变化关系

| 关系 | 测试方法 | 实际结果 | 结果 |
|---|---|---|---|
| 输送速度改变 → 产量合理变化 | 同场景把输送机 0.5→2.0 m/s（关闭故障，600 s，seed 42） | 70→139 良品；420→834 件/小时；瓶颈 Conveyor A→Robot C | PASS |
| 检测时间改变 → 瓶颈变化 | Inspection cycle 2→10 s | 良品 54、324 件/小时；瓶颈 Inspection B（利用率 93.9%） | PASS |
| 删除关键设备 → 识别错误 | 删除 Robot C 后重导入 | 5 设备；`missing_target_device`/`missing_source_device`；仿真返回 400“场景校验失败” | PASS |
| 断开连接 → 物料流中断 | 删除 Conveyor D→Despawner | `material_flow_broken`；仿真返回 400 | PASS |
| 增加设备 → 场景/模型同步 | 追加 pt_7 Photo Eye | 7 设备；ID 前 6 个不变；`isolated_device` warning；显式连接仍为 5 条 | PASS |
| 修改 ID/参数 → 不错误关联 | 重导入同一 OIP 工程 | 设备 ID pt_1..pt_6 稳定；已有显式连接不被自动改写 | PASS |
| 保存/重载不丢数据 | 场景版本/API 重载 | 同内容不升版；修改内容升版；版本恢复 v2→新版本 | PASS |
| 历史实验不受新场景影响 | 改速度 0.5→2.0 后读取基线实验 | 基线实验 `config.scene_snapshot` 仍为 speed 0.5 | PASS |

---

## 4. 浏览器端到端测试

脚本：`frontend/oip/aic-e2e/`

| 场景 | 步骤 | 结果 | 证据 |
|---|---|---|---|
| OIP 静态编辑器载入 | 平台 → 建模分析工作台 → ① 建模 iframe | 6 设备、同步按钮存在 | `docs/integration-shots/01-scene-editor-oip.png` |
| OIP 桥接保存 | 静态构建点击「同步到 AIC」 | `已同步 v8 · 6 设备 · 5 连接`，0 控制台错误 | `streamlit-static-oip.mjs` 输出 |
| Streamlit 四页渲染 | 平台总览/建模分析工作台/实验历史/系统设置 | 导航仅 OIP 四页；无“页面渲染异常”；0 控制台错误 | `check-oip-only.mjs` |
| 离线仿真 UI | 点击「运行离线仿真」 | KPI 卡片出现：97 良品 / 582/h / OEE 79.9% / 瓶颈 Conveyor A | `02-offline-simulation.png` |
| 实时仿真 UI | 启动/暂停/继续/停止 | 状态、仿真时间、设备状态刷新；停止后结果持久化 | `03-live-simulation.png` |
| EdgeCoder UI | 运行三策略对比 | 80 任务；动态 93.75%（本次 UI 运行）；策略表展示 | `04-edgecoder.png` |
| 优化 UI | 生成并评估 | 最佳建议：两台输送机 0.75→1.12 m/s；真实重跑评分 17.812 | `05-optimization.png` |
| 报告 UI | 生成报告 | `RPT-...` 生成，可下载 Markdown/HTML | `06-report.png` |
| 独立 OIP 六设备 | 加载完整工程 | 6 设备全部渲染，0 控制台错误 | `07-oip-six-devices.png` |

---

## 5. 测试中发现并修复的问题（如实记录）

1. **阻塞重试导致重复加工/重复判定**
   - 现象：Conveyor processed 1190 次（实际 45 件），Inspection 在阻塞时重复掷通过率，KPI 失真。
   - 修复：引入 `blocked_item`，完成加工只结算一次，阻塞时只重试传递。
   - 复测：processed 39/37/35，良品 70、420 件/小时；全量 69 测试通过。

2. **故障调度顺序错误导致连续故障**
   - 现象：维修完成的同一 tick 被误判为下一次故障，Inspection 10 分钟内故障 10 次。
   - 修复：先结算维修，再判断新故障；`failed` 状态不再触发新故障。
   - 复测：600 s 基准故障 4 次（跨设备），Inspection 1 次；结果恢复合理。

3. **优化基线误用实时短实验**
   - 现象：分析页默认选中实时停止的 16.75 s 实验，优化基线 0 产量、评分 0。
   - 修复：实时记录标记 `source=scene_engine_live`；分析页只列离线实验；优化时长 <60 s 时回退到场景默认时长；API 默认选取离线实验。
   - 复测：优化基线 70 良品，最佳建议真实提升 38.6% 产能。

> 上述问题均由测试暴露并修复，相关旧数据已归档到 `.integration_backup/`，未删除。

---

## 6. 日志与证据

| 内容 | 路径 |
|---|---|
| pytest 全量结果 | 终端 `70 passed`（2026-10-30 工作台重构后） |
| 场景 API 日志 | `data/logs/scene_api.log` |
| OIP 服务日志 | `data/logs/oip-*.log` |
| 浏览器脚本 | `frontend/oip/aic-e2e/*.mjs` |
| 浏览器截图 | `docs/integration-shots/*.png` |
| 演示数据 | `demo_scene/`（OIP 工程、统一场景、基准/优化/Edge/优化记录、报告） |
| 场景版本 | `data/projects/<pid>/scenes/<sid>/versions/*.json` |
| 实验记录 | `data/projects/<pid>/experiments/*.json` |

---

## 7. 测试边界

- 使用开发机本地 127.0.0.1，无远程/多用户/鉴权测试；
- OIP 为静态生产构建 + 官方 `?hook=1` 桥接；未重跑 OIP 上游完整 e2e 套件；
- 未做长时间（>1 小时）稳定性与大规模场景（>100 设备）性能测试；
- 连接推导只覆盖当前映射设备的端口几何；track/mount/edge/chain 未完整复刻；
- 实时仿真由 wall-clock 驱动，非逐位复现；离线实验才是确定性权威；
- 所有 KPI/OEE/能耗/网络时延均为软件仿真，不是真实工厂或 6G 实测。

---

## 8. 2026-10-30 建模分析工作台重构验证

| 项目 | 结果 |
|---|---|
| 导航 | 四页：平台总览 / 建模分析工作台 / 实验历史 / 系统设置（`check-oip-only.mjs` 输出 nav 一致） |
| 四页渲染 | 每页 render-error = 0，控制台错误 0 |
| 工作台建模 | ① 折叠内嵌 OIP 编辑器，`window.__oip...parts.length = 6` |
| 工作台采集 | ② 「运行离线仿真」生成新实验，KPI 卡片出现（良品 70 / 总产出 73 / WIP 9 / 420 件每小时 / OEE 89.3%） |
| 工作台分析 | ③ 自动选中刚生成的实验，显示瓶颈、利用率、三维快照与「场景 v8 → 实验 SEXP-…」链路 |
| 工作台优化 | ④ Edge 三策略、规则式优化（最佳候选展示）、What-if、报告生成 `RPT-…`、实验对比均可用 |
| 实时仿真 | 启动/暂停/继续/停止正常，停止后持久化 |
| AppTest | `tests/test_platform_app.py`：四页渲染 + 工作台空项目 + 带场景渲染，3 passed |
| OIP 物理动画 | 可动画场景 `SCN-20261030-234230-6C86`：展开编辑器点 ▶，`controller.boxCount` 0→3；采样箱子坐标沿 X 从 −6.5 连续移动到 +1.7，跨两段皮带不掉落 |
| 动画与仿真解耦 | 仅调整设备位置后，显式连接仍为 5 条、0 错误；Python 仿真 KPI 与基准一致（70 良品 / 420 件每小时 / OEE 89.3%） |

证据截图（本次运行）：`D:/AI数字工厂-交付/_e2e_data/shots/oiponly-02-workspace.png`、`streamlit-06-offline-result.png`、`streamlit-08-edge.png`、`streamlit-09-optimize.png`、`streamlit-10-report.png`。

发现并修复：拆页后 `_render_optimization_section` 引用未定义的 `exp_ids` 导致工作台渲染异常；已在优化段内重新获取实验列表。另将 `_render_scene_3d` 增加 `key_prefix`，避免同页多处三维图 key 冲突。

说明：本次 E2E 在演示项目上真实运行了新的离线/实时仿真并生成 Edge 与报告记录，属真实仿真产物；演示场景文件与既有基准/优化实验未被修改。

---

## 9. 2026-10-31 场景编辑器独立入口

| 项目 | 结果 |
|---|---|
| 导航 | 五页：平台总览 / 场景编辑器 / 建模分析工作台 / 实验历史 / 系统设置 |
| 编辑器页渲染 | render-error = 0，控制台错误 0；OIP iframe 加载 `parts = 6` |
| 编辑器默认展开 | 独立页 `expand_editor=True`；工作台内仍按“有设备则折叠” |
| 回归 | `python -m pytest -q`：70 passed（约 40 s） |

证据截图：`D:/AI数字工厂-交付/_e2e_data/shots/scene-editor-page.png`。

---

## 10. 2026-10-31 全平台界面中文化

| 项目 | 结果 |
|---|---|
| 平台五页 | 渲染异常 0、控制台错误 0；可见英文仅剩 WIP/OEE 缩写、设备名（标识符）、参数键与路径 |
| OIP 编辑器 | 启动装入 499 条中文词典；顶栏/共享/场景/历史/属性/部件/PLC/问题/监视/目录分类/单位/物理步进说明/导览均为中文 |
| 保持英文的标识符 | 部件名（Belt Conveyor、Robot C 等）、设备 ID、参数键、ST 代码；符合 i18n 的 prose/identity 分离 |
| 场景默认选择 | 无历史选择时默认选中设备最多的场景（避免测试遗留空场景） |
| 回归 | `python -m pytest -q`：70 passed（`NEW_PAGES` 已加入场景编辑器） |

证据截图：`D:/AI数字工厂-交付/_e2e_data/shots/zh-editor-page.png`、`oip-zh.png`。
重新构建 OIP：改词典后 `python tools/gen_oip_zh.py` → `cd frontend/oip && npm run build`。

---

## 11. 2026-10-31 项目整包导出/导入（.aicproj）

| 项目 | 结果 |
|---|---|
| 导出内容 | ZIP 根：manifest.json + project.json + scenes/<id>/{scene.json, versions, reports} + experiments/*.json；演示项目导出 298 KB |
| 真实往返 | 演示项目 `PRJ-20261030-DDC29B`：3 场景（2 个含 OIP 工程）、9 条实验全部恢复；新 project_id，场景 ID/设备数（6/0/6）一致 |
| 导入语义 | 始终创建新项目；重名自动追加「（导入）」「（导入）2」 |
| 安全校验 | 拒绝 ../ 路径穿越、缺 project.json 的 ZIP、超大解压体积；失败自动清理临时目录 |
| 浏览器闭环 | 侧栏打包 → 下载 → 上传 → 导入成功提示「已导入为项目…」；测试项目已清理 |
| 单元测试 | `tests/test_project_bundle.py`：3 passed |
| 回归 | `python -m pytest -q`：73 passed |

---

## 12. 2026-10-31 工作台移除内嵌编辑器

| 项目 | 结果 |
|---|---|
| 建模分析工作台 | ① 建模改为「场景与设备」：无 OIP iframe（实测 `/app/` iframe 数 = 0），保留设备清单/物料连接/校验与「打开场景编辑器」按钮 |
| 场景编辑器页 | 仍内嵌 OIP 编辑器（iframe 数 = 1），跳转按钮实测可用 |
| 页面异常 | render-error 0、控制台错误 0 |
| 回归 | `python -m pytest -q`：73 passed |

证据截图：`D:/AI数字工厂-交付/_e2e_data/shots/workbench-no-editor.png`。

---

## 13. 2026-10-31 OIP 场景自动动态运行

| 项目 | 结果 |
|---|---|
| 手动运行 | 可动画场景（`SCN-20261030-234230-6C86`）点「开始仿真」后 `running=true`，箱子 0→1→2 持续生成并沿皮带移动 |
| 自动播放 | 编辑器 URL 带 `autoplay=1`；场景加载后 bridge 调用 `sim.run`，不点击即 `running=true`，14 s 内箱子数量 4，0 控制台错误 |
| 暂停/停止 | 顶部「暂停仿真 / 停止仿真」正常可用 |
| 说明 | OIP 动画仅视觉演示；生产 KPI 仍来自 Python 离散事件仿真，两者不混用 |

证据截图：`D:/AI数字工厂-交付/_e2e_data/shots/oip-animated.png`、`oip-autoplay.png`。

---

## 14. 2026-10-31 内置部件新增容器

| 项目 | 结果 |
|---|---|
| 新增部件 | 油桶 Drum、吨桶 IBC tote、周转箱 Tote（官方 Containers 库，离线内置，含缩略图） |
| 目录验证 | `getPackage` 三个 ID 均存在；`bundledPackages` 包含；部件卡片可见；0 构建错误、0 控制台错误 |
| 平台映射 | 三个容器按货物处理（`CARGO_PACKAGES`），`map_oip_project` 不生成设备、issues=0 |
| 部件总数 | 38 → 41 |

---

## 15. 2026-10-31 外部装饰模型导入（Poly Haven CC0）

| 项目 | 结果 |
|---|---|
| 模型来源 | Poly Haven CC0：tool_cart、industrial_storage_cart、WetFloorSign_01、ladder_sectioned_01、portable_welding_cart、power_box_01 |
| 处理 | `tools/fetch_ph_glb.py` 下载 gltf + 贴图并打包为单文件 glb（内嵌 JPEG），输出 `data/decoration/` |
| 导入 | 在「场景编辑器」逐个导入，重命名为「装饰·X」，分类设为「装饰」；同步后 OIP 工程包 6→12 |
| 放置 | 通过目录拖放把 6 个装饰件摆到产线两侧；场景部件 6→12 |
| 演示场景 | DDC29B `SCN-…6C86` → v8；CABE65 `SCN-…B10F` → v10；统一设备 12（6 仿真 + 6 装饰）/ 连接 5 / 0 错误 0 警告 |
| 目录验证 | 部件面板出现「装饰」分类，6 张卡片均可加载；0 控制台错误 |
| 平台标注 | 按工程包分类/名称识别装饰件 → `type=decoration`、`simulation_type=display_only`、不产生 unsupported 警告（单元测试 3 项） |
| 备份 | 导入前场景备份 `data/logs/backup-6C86-before-decoration.json`、`backup-B10F-before-decoration.json` |

---

## 16. 2026-10-31 参赛三方向增强

| 项目 | 结果 |
|---|---|
| 一键演示 | 侧栏「▶ 一键演示」：自动选项目/场景 → 离线仿真 `SEXP-20261031-222807-8799` → 跳转工作台并显示「一键演示：已在…运行离线仿真…」；0 渲染异常 |
| 实时数据联动 | 启动实时仿真后打开编辑器：右侧「实时仿真联动」面板显示仿真时间/良品/6 台设备状态（Spawner 阻塞、Conveyor A 运行…）；空闲无选中时自动选中阻塞设备 `pt_1`（OIP 高亮）；停止仿真后面板自动隐藏 |
| 现场稳定 | 手动终止场景 API 进程后打开页面：API 自动重启，侧栏显示「场景 API：运行中」，无报错；页面提供「启动 / 修复场景服务」按钮 |
| 回归 | `python -m pytest -q`：76 passed（41.0 s） |

证据截图：`_e2e_data/shots/one-click-demo.png`、`live-hud.png`、`live-hud2.png`。

---

## 17. 2026-10-31 安装包 1.1.0 交付验证

| 项目 | 结果 |
|---|---|
| 安装包 | `AIIndustrialFactory-Setup-1.1.0.exe`，124 MB；含 OIP dist、package.json、demo_seed、便携 Python |
| 静默安装 | `/VERYSILENT` 安装成功；`frontend/oip/dist/index.html`、`demo_seed/projects` 均存在 |
| 启动 | `launcher.py` 1.1.0 动态选端口 8503，自动启动 Streamlit / 三维服务；首次启动复制 2 个演示项目到 `%AIC_DATA_DIR%` |
| 页面 | 五页导航正常；场景编辑器内 OIP iframe 加载，`parts=12`、`running=true`（自动动画）；0 渲染异常、0 控制台错误 |
| 停止/卸载 | 启动器 `--stop` 正常停止；`unins000.exe /VERYSILENT` 卸载完成 |
| 文档 | `docs/评委使用说明.md`、`docs/演示流程.md` 更新为五页/一键演示/实时联动；release 与交付目录同步 |
| SHA256 | `114d7c3f35516ad1…`（见 `release/SHA256.txt`） |

说明：开发机的 8610 端口同时有源码版 OIP 服务，安装版会复用端口（Windows SO_REUSEADDR）；评委机器单实例无此情况。旧 1.0.0 安装包与 ZIP 已归档到 `_archive_1.0.0/`。

---

## 18. 2026-11-03 安装包 1.1.1 交付验证

| 项目 | 结果 |
|---|---|
| 安装包 | `AIIndustrialFactory-Setup-1.1.1.exe`，124 MB；含新版 OIP dist（`reset.html` + IndexedDB 桥接）、`ui_cache/ui_shared/legacy_pages`、`demo_seed`、便携 Python |
| 静默安装 | `/VERYSILENT` 安装成功；`reset.html`、`ui_cache.py`、`ui_shared.py`、`demo_seed` 均在安装目录 |
| 启动 | `launcher.py` 1.1.1 启动成功，首次载入 2 个演示项目；Streamlit/场景 API 健康 |
| 页面 | 平台总览/工作台/实验历史/系统设置 render-error 0、控制台错误 0；五页导航一致 |
| 新增功能 | 场景版本历史（差异/恢复）、系统设置演示数据重置、工作台 5 步进度 |
| 停止/卸载 | 启动器 `--stop` 正常停止；`unins000.exe /VERYSILENT` 卸载完成 |
| SHA256 | `822153fad7fca6bc…`（见 `release/` 与交付目录 `SHA256.txt`） |
