"""工厂生产事件 → EdgeCoder 边缘计算任务 → 三种调度策略公平对比。

映射规则（全部为显式假设，可在项目页面调整）：
1. 以工厂实验采集到的视觉检测事件（INSPECTION_PASS / INSPECTION_FAIL）作为工作负载来源；
2. 每个检测事件映射为一个计算任务：任务 ID 顺序编号，来源记录为产品/工件编号；
3. 任务到达时间按配置的“到达间隔”均匀展开（不直接使用工厂仿真的分钟级时间戳），
   以便在毫秒级的边缘调度模型中形成可比较的并发负载；
4. 数据量固定为配置值 MB；计算需求 = 基础值 ×（失败任务放大系数）× 波动；
5. 时延上限 = 配置基准 ± 均匀抖动；检测失败任务为高优先级，通过任务为中优先级；
6. 三种策略（固定本地/固定云端/动态）使用同一任务列表、同一网络参数与同一种子。
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from experiments.metrics import compute_metrics
from experiments.runner import config_signature, default_strategies
from integration.project_store import new_experiment_id, save_experiment, validate_project
from models.schemas import NetworkLink, NodeConfig, SimulationConfig, TaskSpec
from network_sim.simulator import EdgeSimulator


class EdgeMappingError(ValueError):
    """无法从工厂实验生成计算任务。"""


def _inspection_source_events(factory_record: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        e
        for e in factory_record.get("events", [])
        if e.get("event_type") in ("INSPECTION_PASS", "INSPECTION_FAIL")
    ]
    if events:
        return events
    fallback = []
    for item in factory_record.get("recent_inspections", []) or []:
        fallback.append(
            {
                "event_type": "INSPECTION_PASS" if item.get("first_pass") else "INSPECTION_FAIL",
                "task_id": item.get("part_id"),
                "sim_time": item.get("sim_time"),
                "sim_tick": item.get("sim_tick"),
                "seq": item.get("sim_tick"),
                "message": f"{item.get('part_id')} ground_truth={item.get('ground_truth')}",
            }
        )
    return fallback


def build_compute_tasks(
    project: dict[str, Any], factory_record: dict[str, Any]
) -> tuple[list[TaskSpec], dict[str, Any]]:
    """根据工厂实验的检测事件生成统一的计算任务列表。"""
    validate_project(project)
    mapping = project["edge"]["mapping"]
    source_events = _inspection_source_events(factory_record)
    if not source_events:
        raise EdgeMappingError(
            "该工厂实验没有采集到视觉检测事件，无法映射计算任务。"
            "请先在“生产数据分析”运行至少 3 分钟的实验，或采集一段 3D 实时运行数据。"
        )

    max_tasks = int(mapping["max_tasks"])
    ordered = sorted(source_events, key=lambda e: (e.get("seq") or 0, e.get("sim_tick") or 0))
    selected = ordered[-max_tasks:]

    rng = random.Random(int(project["edge"]["seed"]))
    base_compute = float(mapping["compute_demand_gflops"])
    variation = float(mapping["compute_variation"])
    failed_factor = float(mapping["failed_compute_factor"])
    deadline = float(mapping["deadline_ms"])
    deadline_jitter = float(mapping["deadline_jitter_ms"])
    interval = float(mapping["arrival_interval_ms"])
    data_size = float(mapping["data_size_mb"])
    network_jitter = float(mapping["network_jitter_ms"])

    tasks: list[TaskSpec] = []
    fail_count = 0
    for index, event in enumerate(selected):
        failed = event.get("event_type") == "INSPECTION_FAIL"
        fail_count += int(failed)
        demand = base_compute * (failed_factor if failed else 1.0)
        demand *= rng.uniform(1.0 - variation, 1.0 + variation)
        task_deadline = max(1.0, deadline + rng.uniform(-deadline_jitter, deadline_jitter))
        task_id = event.get("task_id") or f"EVT-{index + 1}"
        tasks.append(
            TaskSpec(
                task_id=index + 1,
                arrival_time_ms=round(index * interval, 3),
                data_size_mb=round(data_size, 4),
                compute_demand_gflops=round(demand, 4),
                deadline_ms=round(task_deadline, 2),
                priority=0 if failed else 1,
                jitter_ms=round(rng.uniform(0.0, network_jitter), 3),
                source=f"RFTwin 检测事件 {task_id}",
            )
        )

    meta = {
        "source_experiment_id": factory_record.get("experiment_id"),
        "source_kind": factory_record.get("kind"),
        "source_events": len(source_events),
        "mapped_tasks": len(tasks),
        "failed_tasks": fail_count,
        "passed_tasks": len(tasks) - fail_count,
        "arrival_interval_ms": interval,
        "data_size_mb": data_size,
        "base_compute_gflops": base_compute,
        "deadline_ms": deadline,
        "assumptions": [
            "检测事件到计算任务的映射是平台假设，不代表真实产线 IT/OT 集成。",
            "任务到达时间按配置间隔展开，未使用工厂分钟级时间戳直接换算。",
            "失败检测任务具有更高优先级和更高计算需求（复检假设）。",
            "数据量、计算需求、时延上限均为可调仿真参数，不是实测网络数据。",
        ],
    }
    return tasks, meta


def build_edge_config(
    project: dict[str, Any], tasks: list[TaskSpec]
) -> SimulationConfig:
    edge = project["edge"]
    mapping = edge["mapping"]
    nodes = []
    for node in edge["nodes"]:
        nodes.append(
            NodeConfig(
                node_id=node["node_id"],
                display_name=node["display_name"],
                node_type=node["node_type"],
                compute_gflops=float(node["compute_gflops"]),
                base_load=float(node["base_load"]),
                max_task_gflops=float(node["max_task_gflops"]),
                max_queue_slots=int(node["max_queue_slots"]),
                link=NetworkLink(
                    bandwidth_mbps=float(node["bandwidth_mbps"]),
                    latency_ms=float(node["latency_ms"]),
                    congestion=float(node["congestion"]),
                ),
            )
        )
    data_values = [t.data_size_mb for t in tasks] or [1.0]
    compute_values = [t.compute_demand_gflops for t in tasks] or [1.0]
    deadline_values = [t.deadline_ms for t in tasks] or [500.0]
    return SimulationConfig(
        num_tasks=len(tasks),
        arrival_rate_per_sec=1000.0 / max(1.0, float(mapping["arrival_interval_ms"])),
        seed=int(edge["seed"]),
        data_size_range_mb=(min(data_values), max(data_values)),
        compute_range_gflops=(min(compute_values), max(compute_values)),
        deadline_range_ms=(min(deadline_values), max(deadline_values)),
        network_jitter_ms=float(mapping["network_jitter_ms"]),
        result_size_mb=float(mapping["result_size_mb"]),
        nodes=tuple(nodes),
    )


def _compute_costs(
    project: dict[str, Any], runs: dict[str, Any]
) -> dict[str, dict[str, float]]:
    costs = {str(n["node_id"]): float(n["cost_per_gflop"]) for n in project["edge"]["nodes"]}
    summary: dict[str, dict[str, float]] = {}
    for key, run in runs.items():
        total = 0.0
        executed = 0
        for result in run.results:
            if result.node_id is None or result.compute_ms <= 0:
                continue
            coefficient = costs.get(result.node_id, 0.0)
            total += result.task.compute_demand_gflops * coefficient
            executed += 1
        summary[key] = {
            "total_cost": round(total, 4),
            "avg_cost_per_executed_task": round(total / executed, 4) if executed else 0.0,
            "executed_tasks": executed,
            "cost_note": "成本单位 = GFLOP × 节点成本系数（仿真假设，非真实货币）",
        }
    return summary


def run_edge_experiment(
    project: dict[str, Any],
    factory_record: dict[str, Any],
    experiment_id: Optional[str] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """生成工作负载并运行三种策略；返回可持久化的边缘实验结果。"""
    tasks, meta = build_compute_tasks(project, factory_record)
    config = build_edge_config(project, tasks)
    experiment_id = experiment_id or new_experiment_id("EEXP")
    started = time.perf_counter()

    runs = {}
    metrics = {}
    for strategy in default_strategies():
        simulator = EdgeSimulator(config, tasks)
        run = simulator.run(strategy)
        runs[strategy.key] = run
        metrics[strategy.key] = compute_metrics(run, config)

    costs = _compute_costs(project, runs)
    record = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "project_id": project["project_id"],
        "kind": "edge",
        "source": "edgecoder_simulation",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "label": f"调度实验 {experiment_id}",
        "factory_experiment_id": factory_record.get("experiment_id"),
        "factory_experiment_created_at": factory_record.get("created_at"),
        "mapping": meta,
        "config": asdict(config),
        "config_signature": config_signature(config),
        "tasks": [asdict(task) for task in tasks],
        "runs": {key: asdict(run) for key, run in runs.items()},
        "metrics": {key: asdict(value) for key, value in metrics.items()},
        "costs": costs,
        "assumptions": [
            "三种策略共享同一任务集、网络条件与随机种子，唯一变量是调度策略。",
            "任务集来自工厂检测事件映射，映射参数为仿真假设。",
            "决策开销由 time.perf_counter_ns 在本机实测，含 Python 解释器开销。",
            "成本系数用于策略相对比较，不代表真实计费。",
        ],
        "wall_time_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }
    if persist:
        save_experiment(project["project_id"], record)
    return record
