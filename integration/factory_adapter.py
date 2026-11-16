"""复用 RFTwin 确定性引擎，把统一工程项目的配置映射为工厂仿真实验。

职责边界：
- RFTwin 继续负责设备级生产仿真（tick、工位节拍、故障、AMR、能耗、OEE）；
- 本模块只做“项目配置 → RFTwin 参数”的映射、运行编排、事件/KPI 归一化与持久化；
- 不修改 RFTwin 引擎逻辑，不复制其仿真代码。

时间与单位约定：
- RFTwin 仿真时钟 tick = 100 ms（10 ticks/秒），记录中的 sim_time 为 RFTwin 仿真时间；
- EdgeCoder 边缘调度实验使用毫秒，映射时显式换算（见 edge_mapping.py）；
- 工厂 KPI 默认是“自班次开始（含 4 小时预热）”的累计值；
  实验窗口指标另存为 window_metrics，二者在界面中分开标注。
"""

from __future__ import annotations

import copy
import re
import sys
import time
from datetime import datetime
from typing import Any, Optional

from integration.paths import RFTWIN_PACKAGES
from integration.project_store import (
    new_experiment_id,
    save_experiment,
    validate_project,
)

if str(RFTWIN_PACKAGES) not in sys.path:
    sys.path.insert(0, str(RFTWIN_PACKAGES))

from domain_factory.engine import FactoryEngine  # noqa: E402
from domain_factory.params import load_params, parameter_hash  # noqa: E402
from domain_factory.serialize import snapshot_message  # noqa: E402

CELL_ORDER = ("welding", "assembly", "machine_tending", "vision_inspection")
PART_PATTERN = re.compile(r"PART-\d+")
FAILURE_TOKENS = ("FAIL", "FAULT", "JAM", "EMERGENCY_STOP")
TASK_STATUS_MAP = {
    "INSPECTION_PASS": "PASS",
    "INSPECTION_FAIL": "FAIL",
    "CYCLE_COMPLETED": "COMPLETED",
    "TASK_COMPLETED": "COMPLETED",
    "TASK_STARTED": "RUNNING",
    "DISPATCH_DECISION": "DISPATCHED",
}

TICK_MS = 100
TICKS_PER_MINUTE = 60_000 // TICK_MS


class FactoryExperimentError(ValueError):
    """工厂仿真实验无法运行。"""


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置；overrides 中的键覆盖 base，不修改输入。"""
    result = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def build_factory_params(project: dict[str, Any]) -> dict[str, Any]:
    """把项目工厂/产线配置合并进 RFTwin 参数集（保留其余平台参数）。"""
    if not isinstance(project, dict) or "factory" not in project:
        raise FactoryExperimentError("项目配置缺少 factory 段")
    params = copy.deepcopy(load_params())
    factory = project["factory"]
    line = project.get("line", {})

    params["seed_default"] = int(factory["seed"])
    params["target_good_units_per_shift"] = int(line.get("target_quantity", 1100))

    for key, cell_cfg in factory["cells"].items():
        if key not in params["cells"]:
            raise FactoryExperimentError(f"RFTwin 参数中不存在工位 {key}")
        current = dict(params["cells"][key]["station_cycle_sec"])
        total = sum(float(v) for v in current.values())
        target = float(cell_cfg["cycle_sec"])
        if total <= 0:
            raise FactoryExperimentError(f"工位 {key} 的基础节拍参数非法")
        if abs(target - total) > 1e-9:
            keys = list(current)
            scaled: dict[str, float] = {}
            running = 0.0
            for index, name in enumerate(keys):
                if index == len(keys) - 1:
                    scaled[name] = round(target - running, 4)
                else:
                    value = round(float(current[name]) / total * target, 4)
                    scaled[name] = value
                    running += value
            params["cells"][key]["station_cycle_sec"] = scaled

    for key, buffer_cfg in factory["buffers"].items():
        if key in params["buffers"]:
            params["buffers"][key]["capacity"] = int(buffer_cfg["capacity"])

    params["cells"]["vision_inspection"]["fail_probability"] = float(
        factory["vision_fail_probability"]
    )
    params["minor_stop"]["probability_per_cycle"] = float(
        factory["minor_stop_probability"]
    )
    amr_units = int(factory.get("amr_units", 2))
    params["amr"]["units"] = list(params["amr"]["units"])[:amr_units]
    return params


def _event_failure(event_type: str, severity: str) -> Optional[str]:
    """只有真正的故障/质量失败事件计入 failure_event；阻塞/缺料属于生产状态事件。"""
    if any(token in event_type for token in FAILURE_TOKENS):
        return event_type
    return None


def normalize_event(
    event: dict[str, Any], project: dict[str, Any], experiment_id: str
) -> dict[str, Any]:
    """把 RFTwin TwinEvent 映射为统一数据采集结构（保留原始事件）。"""
    message = str(event.get("message", ""))
    match = PART_PATTERN.search(message)
    part_id = match.group(0) if match else None
    source_type = event.get("source_type")
    event_type = str(event.get("event_type", ""))
    return {
        "experiment_id": experiment_id,
        "timestamp": event.get("sim_time"),
        "simulation_time": event.get("sim_time"),
        "sim_tick": event.get("sim_tick"),
        "seq": event.get("seq"),
        "machine_id": event.get("source_id"),
        "source_type": source_type,
        "cell_id": event.get("cell_id"),
        "task_id": part_id or event.get("event_id"),
        "product_id": project.get("line", {}).get("product_id") if part_id else None,
        "event_type": event_type,
        "processing_time": None,
        "waiting_time": None,
        "machine_state": None,
        "task_status": TASK_STATUS_MAP.get(event_type),
        "throughput": None,
        "failure_event": _event_failure(event_type, str(event.get("severity", "INFO"))),
        "severity": event.get("severity"),
        "message": message,
        "raw": event,
    }


def _station_metrics(snapshot: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    stations: list[dict] = []
    cells: list[dict] = []
    for cell in snapshot["state"]["cells"]:
        cells.append(
            {
                "cell_id": cell["cell_id"],
                "name": cell["name"],
                "state": cell["state"],
                "oee": cell["oee"],
                "throughput_uph": cell["throughput_uph"],
                "avg_station_cycle_sec": cell["avg_station_cycle_sec"],
                "is_bottleneck": cell["is_bottleneck"],
                "active_stations": cell["active_stations"],
                "idle_stations": cell["idle_stations"],
                "error_stations": cell["error_stations"],
                "input_buffer_occupancy": cell["input_buffer"]["occupancy"],
                "input_buffer_capacity": cell["input_buffer"]["capacity"],
                "output_buffer_occupancy": cell["output_buffer"]["occupancy"],
                "output_buffer_capacity": cell["output_buffer"]["capacity"],
            }
        )
        for station in cell["stations"]:
            stations.append(
                {
                    "station_id": station["station_id"],
                    "cell_id": cell["cell_id"],
                    "robot_id": station["robot_id"],
                    "machine_id": station.get("machine_id"),
                    "state": station["state"],
                    "processing_time_sec": station["station_cycle_sec"],
                    "target_cycle_sec": station["target_station_cycle_sec"],
                    "blocked_time_sec": station["blocked_time_sec"],
                    "starved_time_sec": station["starved_time_sec"],
                    "idle_time_sec": station["idle_time_sec"],
                    "waiting_time_sec": round(
                        station["blocked_time_sec"] + station["starved_time_sec"], 3
                    ),
                    "current_part_id": station["current_part_id"],
                    "fault_kind": station["fault_kind"],
                }
            )
    return stations, cells


def station_metrics_from_snapshot(
    snapshot: dict[str, Any]
) -> tuple[list[dict], list[dict]]:
    """公开包装：从 RFTwin snapshot 提取工位与单元指标。"""
    return _station_metrics(snapshot)


def run_factory_experiment(
    project: dict[str, Any],
    duration_min: Optional[int] = None,
    seed: Optional[int] = None,
    overrides: Optional[dict[str, Any]] = None,
    experiment_id: Optional[str] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """运行一轮无头工厂实验并保存。

    - 同一项目配置 + 同一 seed ⇒ 相同结果（RFTwin 确定性引擎）；
    - 事件从预热结束（10:00）之后开始采集，预热只用于建立初始在制品/队列状态；
    - overrides 用于 AI 优化的候选参数验证，不直接写入项目。
    """
    validate_project(project)
    duration = int(duration_min or project["factory"].get("duration_min", 30))
    if not 1 <= duration <= 180:
        raise FactoryExperimentError("仿真时长必须在 1~180 分钟之间")
    run_seed = int(project["factory"]["seed"] if seed is None else seed)
    experiment_id = experiment_id or new_experiment_id("FEXP")

    started_wall = time.perf_counter()
    params = build_factory_params(project)
    if overrides:
        params = _deep_merge(params, overrides)

    engine = FactoryEngine(run_id=experiment_id, seed=run_seed, params=params)
    engine.preroll()
    start_seq = engine.bus.seq
    start_history = len(engine.history)
    start_sim_time = engine.clock.sim_time_iso
    last_seq = start_seq

    collected_events: list[dict[str, Any]] = []
    ticks = duration * TICKS_PER_MINUTE
    chunk = 500
    remaining = ticks
    while remaining > 0:
        step_count = min(chunk, remaining)
        for _ in range(step_count):
            engine.step()
        new_events, _complete = engine.bus.after(last_seq)
        if new_events:
            collected_events.extend(new_events)
            last_seq = new_events[-1]["seq"]
        remaining -= step_count

    snapshot = snapshot_message(engine)
    kpis = engine.kpis()
    flow_insight = engine.flow_insight()
    amr_kpis = engine.amr_kpis()
    flow_history = copy.deepcopy(engine.history[start_history:])
    cell_flow_history = copy.deepcopy(engine.flow_history[start_history:])
    stations, cells = _station_metrics(snapshot)
    normalized_events = [normalize_event(ev, project, experiment_id) for ev in collected_events]

    window_good = sum(int(h.get("good_units", 0)) for h in flow_history)
    window_defect = sum(int(h.get("defect_units", 0)) for h in flow_history)
    window_uph = (
        sum(float(h.get("throughput_uph", 0.0)) for h in flow_history) / len(flow_history)
        if flow_history
        else 0.0
    )
    window_oee = (
        sum(float(h.get("oee", 0.0)) for h in flow_history) / len(flow_history)
        if flow_history
        else 0.0
    )
    failure_events = [e for e in normalized_events if e["failure_event"]]
    inspection_events = [
        e for e in normalized_events if e["event_type"] in ("INSPECTION_PASS", "INSPECTION_FAIL")
    ]
    blocked_events = [e for e in normalized_events if "BLOCKED" in e["event_type"]]
    starved_events = [e for e in normalized_events if "STARVED" in e["event_type"]]

    record: dict[str, Any] = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "project_id": project["project_id"],
        "kind": "factory",
        "source": "headless_engine",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "label": f"工厂实验 {experiment_id}",
        "config": {
            "factory": copy.deepcopy(project["factory"]),
            "line": copy.deepcopy(project.get("line", {})),
            "seed": run_seed,
            "duration_min": duration,
            "overrides": copy.deepcopy(overrides or {}),
        },
        "param_hash": parameter_hash(params),
        "provenance": copy.deepcopy(engine.provenance),
        "start_sim_time": start_sim_time,
        "end_sim_time": engine.clock.sim_time_iso,
        "duration_min": duration,
        "kpis": kpis,
        "window_metrics": {
            "minutes": len(flow_history),
            "good_units": window_good,
            "defect_units": window_defect,
            "avg_throughput_uph": round(window_uph, 2),
            "avg_oee": round(window_oee, 4),
        },
        "flow_history": flow_history,
        "cell_flow_history": cell_flow_history,
        "flow_insight": flow_insight,
        "station_metrics": stations,
        "cell_metrics": cells,
        "orders": copy.deepcopy(snapshot["state"]["orders"]),
        "amr_kpis": amr_kpis,
        "recent_inspections": copy.deepcopy(engine.recent_inspections),
        "events": normalized_events,
        "event_stats": {
            "total": len(normalized_events),
            "failures": len(failure_events),
            "inspection_events": len(inspection_events),
            "blocked_events": len(blocked_events),
            "starved_events": len(starved_events),
        },
        "snapshot": snapshot,
        "assumptions": [
            "全部指标由 RFTwin 确定性引擎按项目参数计算，属于仿真结果，不是真实工厂实测。",
            "KPI 为自班次 06:00 开始（含 4 小时预热）的累计值；window_metrics 仅统计本次实验窗口。",
            "事件从预热结束的 10:00 状态开始采集，预热用于建立在制品与队列初始状态。",
            "视觉检测失败概率、设备短暂停机概率、工位节拍与缓冲区容量均为仿真假设参数。",
        ],
        "wall_time_ms": round((time.perf_counter() - started_wall) * 1000.0, 1),
    }
    if persist:
        save_experiment(project["project_id"], record)
    return record


def run_short_replay(
    project: dict[str, Any],
    overrides: Optional[dict[str, Any]] = None,
    duration_min: int = 10,
    seed: Optional[int] = None,
) -> dict[str, Any]:
    """AI 优化用的短时重放（不持久化）。"""
    return run_factory_experiment(
        project,
        duration_min=duration_min,
        seed=seed,
        overrides=overrides,
        persist=False,
    )
