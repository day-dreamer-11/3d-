"""实验编排：生成统一任务集，运行三种调度策略，计算对比指标。

公平性保证：
- 任务集在实验开始时由固定随机种子生成一次，三种策略共享同一份不可变任务集；
- 网络、节点参数完全相同；
- 每种策略都从相同的初始节点状态（空闲）开始；
- 唯一变量是调度策略本身。
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from experiments.metrics import StrategyMetrics, compute_metrics
from models.schemas import SimulationConfig, StrategyRun, TaskSpec
from network_sim.simulator import EdgeSimulator
from scheduler.strategies import (
    DynamicStrategy,
    FixedCloudStrategy,
    FixedLocalStrategy,
    SchedulingStrategy,
)


class ConfigError(ValueError):
    """仿真配置参数不合法。"""


def default_strategies() -> list[SchedulingStrategy]:
    return [FixedLocalStrategy(), FixedCloudStrategy(), DynamicStrategy()]


def validate_config(config: SimulationConfig) -> None:
    """检查配置合法性，发现问题时抛出带中文说明的 ConfigError。"""
    errors: list[str] = []
    if not 1 <= config.num_tasks <= 2000:
        errors.append("任务数量必须在 1 ~ 2000 之间")
    if config.arrival_rate_per_sec <= 0:
        errors.append("任务到达频率必须大于 0")
    if config.seed < 0:
        errors.append("随机种子必须为非负整数")
    if config.network_jitter_ms < 0:
        errors.append("网络抖动不能为负")
    if config.result_size_mb <= 0:
        errors.append("结果数据量必须大于 0")

    for name, rng in (
        ("数据量范围", config.data_size_range_mb),
        ("计算需求范围", config.compute_range_gflops),
        ("时延上限范围", config.deadline_range_ms),
    ):
        if len(rng) != 2 or rng[0] <= 0 or rng[0] > rng[1]:
            errors.append(f"{name}必须满足 0 < 最小值 ≤ 最大值")

    if not config.nodes:
        errors.append("至少需要配置一个计算节点")
    seen: set[str] = set()
    for node in config.nodes:
        if not node.node_id or node.node_id in seen:
            errors.append(f"节点 ID 必须非空且唯一：{node.node_id!r}")
        seen.add(node.node_id)
        if node.compute_gflops <= 0:
            errors.append(f"节点 {node.display_name} 的算力必须大于 0")
        if not 0.0 <= node.base_load < 1.0:
            errors.append(f"节点 {node.display_name} 的基础负载必须在 [0, 1) 内")
        if node.max_task_gflops <= 0:
            errors.append(f"节点 {node.display_name} 的单任务算力上限必须大于 0")
        if node.max_queue_slots < 1:
            errors.append(f"节点 {node.display_name} 的队列容量必须至少为 1")
        if node.link.bandwidth_mbps <= 0:
            errors.append(f"节点 {node.display_name} 的带宽必须大于 0")
        if node.link.latency_ms < 0:
            errors.append(f"节点 {node.display_name} 的链路时延不能为负")
        if not 0.0 <= node.link.congestion < 1.0:
            errors.append(f"节点 {node.display_name} 的拥塞系数必须在 [0, 1) 内")

    if errors:
        raise ConfigError("；".join(errors))


def config_signature(config: SimulationConfig) -> str:
    """生成配置指纹，用于判断界面上的实验结果是否已过期。"""
    payload = json.dumps(asdict(config), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def generate_tasks(config: SimulationConfig) -> list[TaskSpec]:
    """按随机种子生成任务集。

    任务生成假设：
    - 相邻任务到达间隔服从指数分布，均值 = 1000 / 到达频率（毫秒），
      模拟工业相机产线上近似泊松到达的检测请求；
    - 数据量、计算需求、时延上限均在用户设定范围内均匀分布；
    - 约 12% 的任务为“高计算量任务”（计算需求 ×2，上限不超过范围上限的 2 倍），
      模拟大尺寸图像或更复杂的检测模型；
    - 优先级按 高/中/低 = 20%/50%/30% 随机抽取；
    - 每个任务采样一次网络抖动并写入任务本身，保证三种策略面对完全相同的抖动序列。
    """
    rng = random.Random(config.seed)
    mean_interarrival_ms = 1000.0 / config.arrival_rate_per_sec
    compute_high = config.compute_range_gflops[1] * 2.0
    tasks: list[TaskSpec] = []
    now_ms = 0.0

    for index in range(config.num_tasks):
        now_ms += rng.expovariate(1.0 / mean_interarrival_ms)
        data_size = rng.uniform(*config.data_size_range_mb)
        compute = rng.uniform(*config.compute_range_gflops)
        if rng.random() < 0.12:
            compute = min(compute * 2.0, compute_high)
        deadline = rng.uniform(*config.deadline_range_ms)
        priority = rng.choices((0, 1, 2), weights=(0.2, 0.5, 0.3), k=1)[0]
        jitter = rng.uniform(0.0, config.network_jitter_ms)
        tasks.append(
            TaskSpec(
                task_id=index + 1,
                arrival_time_ms=now_ms,
                data_size_mb=round(data_size, 4),
                compute_demand_gflops=round(compute, 4),
                deadline_ms=round(deadline, 2),
                priority=priority,
                jitter_ms=round(jitter, 3),
            )
        )
    return tasks


@dataclass
class ExperimentResult:
    """一次完整实验（三种策略）的结果快照。"""

    config: SimulationConfig
    signature: str
    tasks: list[TaskSpec]
    runs: dict[str, StrategyRun]
    metrics: dict[str, StrategyMetrics]
    generated_at: str
    wall_time_ms: float


def run_experiment(config: SimulationConfig) -> ExperimentResult:
    """运行三种策略的完整对比实验。"""
    validate_config(config)
    started = time.perf_counter()
    tasks = generate_tasks(config)

    runs: dict[str, StrategyRun] = {}
    metrics: dict[str, StrategyMetrics] = {}
    for strategy in default_strategies():
        simulator = EdgeSimulator(config, tasks)
        run = simulator.run(strategy)
        runs[strategy.key] = run
        metrics[strategy.key] = compute_metrics(run, config)

    return ExperimentResult(
        config=config,
        signature=config_signature(config),
        tasks=tasks,
        runs=runs,
        metrics=metrics,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        wall_time_ms=(time.perf_counter() - started) * 1000.0,
    )
