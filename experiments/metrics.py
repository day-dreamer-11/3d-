"""实验指标计算（全部基于实际仿真结果，不包含任何预设数字）。

指标定义：
- 任务完成率 success_rate = (success + timeout) / 总任务数；
  表示任务被执行引擎成功处理的比例（超时也算完成执行）。
- 时延约束满足率 satisfaction_rate = success / 总任务数；
  只有总完成时延 ≤ 时延上限的任务才计入。
- 平均完成时延 / P95 完成时延：仅统计完成执行的任务（success + timeout）。
- 节点资源利用率 = 该节点累计计算忙碌时间 / 本策略仿真跨度（makespan）。
  只反映仿真任务带来的计算占用，不含节点基础负载；基础负载已通过有效算力体现。
- 调度决策开销：使用 time.perf_counter_ns 实测每次 decide() 调用耗时，
  包含 Python 解释器开销，与真实网络时延不可直接比较；不同机器结果不同。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from models.schemas import SimulationConfig, StrategyRun


@dataclass
class StrategyMetrics:
    strategy_key: str
    strategy_name: str
    total_tasks: int
    completed_tasks: int
    on_time_tasks: int
    timeout_tasks: int
    rejected_tasks: int
    success_rate: float
    satisfaction_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    avg_decision_ms: float
    total_decision_ms: float
    throughput_per_sec: float
    makespan_ms: float
    node_distribution: dict[str, int] = field(default_factory=dict)
    node_utilization: dict[str, float] = field(default_factory=dict)
    node_busy_ms: dict[str, float] = field(default_factory=dict)

    def to_row(self, config: SimulationConfig) -> dict[str, object]:
        """转为用于界面展示的一行指标（含策略中文名）。"""
        return {
            "策略": self.strategy_name,
            "完成率": self.success_rate,
            "时延满足率": self.satisfaction_rate,
            "超时任务数": self.timeout_tasks,
            "拒绝任务数": self.rejected_tasks,
            "平均时延(ms)": self.avg_latency_ms,
            "P95时延(ms)": self.p95_latency_ms,
            "平均决策开销(µs)": self.avg_decision_ms * 1000.0,
            "仿真跨度(ms)": self.makespan_ms,
            "吞吐(任务/秒)": self.throughput_per_sec,
        }


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(ratio * len(ordered)) - 1))
    return ordered[index]


def compute_metrics(run: StrategyRun, config: SimulationConfig) -> StrategyMetrics:
    """根据一次策略运行记录计算全部对比指标。"""
    total = len(run.results)
    completed = [r for r in run.results if r.is_completed]
    on_time = [r for r in run.results if r.is_on_time]
    timeouts = [r for r in run.results if r.status == "timeout"]
    rejected = [r for r in run.results if r.status == "rejected"]
    latencies = [r.total_latency_ms for r in completed if r.total_latency_ms is not None]

    makespan = run.makespan_ms
    utilization: dict[str, float] = {}
    for node in config.nodes:
        busy = run.node_busy_ms.get(node.node_id, 0.0)
        utilization[node.node_id] = (
            min(1.0, busy / makespan) if makespan > 0 else 0.0
        )

    return StrategyMetrics(
        strategy_key=run.strategy_key,
        strategy_name=run.strategy_name,
        total_tasks=total,
        completed_tasks=len(completed),
        on_time_tasks=len(on_time),
        timeout_tasks=len(timeouts),
        rejected_tasks=len(rejected),
        success_rate=(len(completed) / total) if total else 0.0,
        satisfaction_rate=(len(on_time) / total) if total else 0.0,
        avg_latency_ms=(sum(latencies) / len(latencies)) if latencies else math.nan,
        p95_latency_ms=_percentile(latencies, 0.95),
        avg_decision_ms=(run.total_decision_ms / total) if total else 0.0,
        total_decision_ms=run.total_decision_ms,
        throughput_per_sec=(
            len(completed) / (makespan / 1000.0) if makespan > 0 else 0.0
        ),
        makespan_ms=makespan,
        node_distribution=dict(run.node_counts),
        node_utilization=utilization,
        node_busy_ms=dict(run.node_busy_ms),
    )
