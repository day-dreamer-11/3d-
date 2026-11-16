"""场景仿真事件 → EdgeCoder 任务 → 三种调度策略。

任务不是固定数量：来源是本次场景仿真真实产生的事件。
- INSPECTION_PASS / FAIL → 视觉检测 / 质量判定任务；
- FAILURE → 故障预警任务；
- 设备状态监测 → 每台设备每 10 分钟一个监测任务；
- 生产调度 → 每 15 分钟一个调度任务。
任务 source 保留设备 ID 与事件序号，可追溯到场景设备。
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from experiments.metrics import compute_metrics
from experiments.runner import default_strategies
from integration.edge_mapping import build_edge_config
from integration.project_store import new_experiment_id, save_experiment
from models.defaults import default_config
from models.schemas import NodeConfig, SimulationConfig, TaskSpec
from network_sim.simulator import EdgeSimulator

TASK_DEFAULTS = {
    "vision_inspection": {"data_mb": 4.0, "gflops": 3.0, "deadline_ms": 800.0, "priority": 1},
    "quality_judgment": {"data_mb": 4.5, "gflops": 4.0, "deadline_ms": 600.0, "priority": 0},
    "fault_warning": {"data_mb": 1.0, "gflops": 4.5, "deadline_ms": 500.0, "priority": 0},
    "device_monitoring": {"data_mb": 0.5, "gflops": 0.8, "deadline_ms": 1500.0, "priority": 2},
    "production_scheduling": {"data_mb": 0.2, "gflops": 1.5, "deadline_ms": 2000.0, "priority": 1},
}


class SceneEdgeError(ValueError):
    """无法从场景实验生成边缘任务。"""


def _edge_config(project: dict[str, Any]) -> dict[str, Any]:
    edge = project.get("edge")
    if isinstance(edge, dict) and edge.get("nodes") and edge.get("mapping"):
        return edge
    base = default_config()
    nodes = []
    for node in base.nodes:
        nodes.append(
            {
                "node_id": node.node_id,
                "display_name": node.display_name,
                "node_type": node.node_type,
                "compute_gflops": node.compute_gflops,
                "base_load": node.base_load,
                "max_task_gflops": node.max_task_gflops,
                "max_queue_slots": node.max_queue_slots,
                "bandwidth_mbps": node.link.bandwidth_mbps,
                "latency_ms": node.link.latency_ms,
                "congestion": node.link.congestion,
                "cost_per_gflop": {"local": 0.02, "edge": 0.06, "cloud": 0.03}.get(node.node_type, 0.05),
            }
        )
    return {
        "seed": 42,
        "nodes": nodes,
        "mapping": {
            "data_size_mb": 4.0,
            "compute_demand_gflops": 3.0,
            "deadline_ms": 800.0,
            "deadline_jitter_ms": 150.0,
            "arrival_interval_ms": 180.0,
            "network_jitter_ms": 6.0,
            "result_size_mb": 0.02,
            "max_tasks": 200,
            "failed_compute_factor": 1.25,
            "compute_variation": 0.15,
        },
    }


def build_scene_tasks(
    project: dict[str, Any], scene_record: dict[str, Any]
) -> tuple[list[TaskSpec], list[dict[str, Any]], dict[str, Any]]:
    """把场景仿真事件映射为 EdgeCoder 任务集（任务数量由场景/时长决定）。"""
    edge = _edge_config(project)
    mapping = edge["mapping"]
    events = list(scene_record.get("events") or [])
    if not events:
        raise SceneEdgeError("该场景实验没有产生事件，无法生成边缘任务。请先运行场景仿真。")

    raw: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        device_id = event.get("source_id")
        sim_sec = float(event.get("sim_time_sec") or 0.0)
        if event_type == "INSPECTION_PASS":
            raw.append({"task_type": "vision_inspection", "device_id": device_id, "sim_sec": sim_sec, "event_seq": event.get("seq"), "event_type": event_type})
        elif event_type == "INSPECTION_FAIL":
            raw.append({"task_type": "quality_judgment", "device_id": device_id, "sim_sec": sim_sec, "event_seq": event.get("seq"), "event_type": event_type})
        elif event_type == "FAILURE":
            raw.append({"task_type": "fault_warning", "device_id": device_id, "sim_sec": sim_sec, "event_seq": event.get("seq"), "event_type": event_type})

    duration_sec = float((scene_record.get("config") or {}).get("duration_sec") or scene_record.get("kpis", {}).get("runtime_sec") or 1800)
    devices = scene_record.get("device_metrics") or []
    # 设备监测：每台设备每 10 分钟一个任务（上限 24 个/设备）
    period = 600.0
    for device in devices:
        if device.get("simulation_type") == "display_only":
            continue
        count = min(24, max(1, int(duration_sec // period)))
        for index in range(count):
            raw.append(
                {
                    "task_type": "device_monitoring",
                    "device_id": device.get("device_id"),
                    "sim_sec": min(duration_sec, (index + 1) * period),
                    "event_seq": None,
                    "event_type": "MONITORING",
                }
            )
    # 生产调度：每 15 分钟一个任务
    for index in range(min(16, max(1, int(duration_sec // 900.0)))):
        raw.append(
            {
                "task_type": "production_scheduling",
                "device_id": None,
                "sim_sec": min(duration_sec, (index + 1) * 900.0),
                "event_seq": None,
                "event_type": "SCHEDULING",
            }
        )

    raw.sort(key=lambda x: (x["sim_sec"], x["event_seq"] or 0, x["task_type"]))
    max_tasks = int(mapping.get("max_tasks", 200))
    if len(raw) > max_tasks:
        # 均匀抽样，保留整个时间窗口的覆盖
        step = len(raw) / float(max_tasks)
        raw = [raw[min(len(raw) - 1, int(i * step))] for i in range(max_tasks)]
    if not raw:
        raise SceneEdgeError("场景实验没有可映射为边缘任务的检测/故障/监测事件。")

    rng = random.Random(int(edge.get("seed", 42)))
    variation = float(mapping.get("compute_variation", 0.15))
    failed_factor = float(mapping.get("failed_compute_factor", 1.25))
    network_jitter = float(mapping.get("network_jitter_ms", 6.0))
    start_sec = min(item["sim_sec"] for item in raw)
    tasks: list[TaskSpec] = []
    metas: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    for index, item in enumerate(raw):
        defaults = TASK_DEFAULTS[item["task_type"]]
        failed = item["task_type"] == "quality_judgment"
        demand = defaults["gflops"] * (failed_factor if failed else 1.0)
        demand *= rng.uniform(1.0 - variation, 1.0 + variation)
        deadline = defaults["deadline_ms"] * (1.0 + rng.uniform(-0.15, 0.15))
        if item["task_type"] == "device_monitoring":
            deadline = max(300.0, deadline)
        task = TaskSpec(
            task_id=index + 1,
            arrival_time_ms=round(max(0.0, (item["sim_sec"] - start_sec) * 1000.0), 3),
            data_size_mb=float(defaults["data_mb"]),
            compute_demand_gflops=round(demand, 4),
            deadline_ms=round(deadline, 2),
            priority=int(defaults["priority"]),
            jitter_ms=round(rng.uniform(0.0, network_jitter), 3),
            source=f"场景 {item['device_id'] or '线体'} · {item['event_type']} · seq {item['event_seq'] or '-'}",
        )
        tasks.append(task)
        metas.append(
            {
                "task_id": task.task_id,
                "task_type": item["task_type"],
                "source_device": item["device_id"],
                "source_event": item["event_type"],
                "source_event_seq": item["event_seq"],
                "sim_time_sec": item["sim_sec"],
            }
        )
        type_counts[item["task_type"]] = type_counts.get(item["task_type"], 0) + 1

    meta = {
        "source_experiment_id": scene_record.get("experiment_id"),
        "scene_id": scene_record.get("scene_id"),
        "scene_version": scene_record.get("scene_version"),
        "task_type_counts": type_counts,
        "task_count": len(tasks),
        "inspection_events": type_counts.get("vision_inspection", 0) + type_counts.get("quality_judgment", 0),
        "failure_events": type_counts.get("fault_warning", 0),
        "monitoring_tasks": type_counts.get("device_monitoring", 0),
        "scheduling_tasks": type_counts.get("production_scheduling", 0),
        "assumptions": [
            "任务来自本次场景仿真事件与设备清单，不是固定任务数。",
            "任务到达时间按仿真时间（秒 → 毫秒）相对首个事件展开；网络时延/带宽为软件仿真参数。",
            "计算需求、数据量、时延上限按任务类型设置并叠加固定种子波动。",
        ],
    }
    return tasks, metas, meta


def _costs(project: dict[str, Any], runs: dict[str, Any]) -> dict[str, dict[str, float]]:
    edge = _edge_config(project)
    coefficients = {str(n["node_id"]): float(n.get("cost_per_gflop", 0.05)) for n in edge["nodes"]}
    summary: dict[str, dict[str, float]] = {}
    for key, run in runs.items():
        total = 0.0
        executed = 0
        for result in run.results:
            if result.node_id is None or result.compute_ms <= 0:
                continue
            total += result.task.compute_demand_gflops * coefficients.get(result.node_id, 0.0)
            executed += 1
        summary[key] = {
            "total_cost": round(total, 4),
            "avg_cost_per_executed_task": round(total / executed, 4) if executed else 0.0,
            "executed_tasks": executed,
            "cost_note": "成本单位 = GFLOP × 节点成本系数（仿真假设，非真实货币）",
        }
    return summary


def run_scene_edge_experiment(
    project: dict[str, Any],
    scene_record: dict[str, Any],
    *,
    experiment_id: Optional[str] = None,
    persist: bool = True,
) -> dict[str, Any]:
    tasks, task_meta, meta = build_scene_tasks(project, scene_record)
    project_for_edge = {**project, "edge": _edge_config(project)}
    config = build_edge_config(project_for_edge, tasks)
    experiment_id = experiment_id or new_experiment_id("SEEXP")
    started = time.perf_counter()
    runs: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    for strategy in default_strategies():
        simulator = EdgeSimulator(config, tasks)
        run = simulator.run(strategy)
        runs[strategy.key] = run
        metrics[strategy.key] = compute_metrics(run, config)
    costs = _costs(project, runs)
    record = {
        "schema_version": "2.0",
        "experiment_id": experiment_id,
        "project_id": project["project_id"],
        "scene_id": scene_record.get("scene_id"),
        "scene_version": scene_record.get("scene_version"),
        "kind": "edge",
        "source": "edgecoder_scene_simulation",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "label": f"场景边缘调度实验 {experiment_id}",
        "factory_experiment_id": scene_record.get("experiment_id"),
        "factory_experiment_created_at": scene_record.get("created_at"),
        "mapping": meta,
        "config": asdict(config),
        "tasks": [asdict(task) for task in tasks],
        "task_meta": task_meta,
        "runs": {key: asdict(run) for key, run in runs.items()},
        "metrics": {key: asdict(value) for key, value in metrics.items()},
        "costs": costs,
        "assumptions": [
            "三种策略共享同一任务集、网络条件与随机种子，唯一变量是调度策略。",
            "任务集来自场景仿真事件，任务数量与设备/时长/产量相关。",
            "网络时延、带宽、计算需求均为软件仿真参数，不是真实 6G 或工厂实测。",
            "决策开销由本机 perf_counter 实测，含 Python 解释器开销。",
        ],
        "wall_time_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }
    if persist:
        save_experiment(project["project_id"], record)
    return record
