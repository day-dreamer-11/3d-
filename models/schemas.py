"""EdgeCoder 核心数据模型。

量纲约定（全项目统一）：
- 计算需求 ``compute_demand_gflops``：GFLOP（十亿次浮点运算）。
- 算力 ``compute_gflops``：GFLOPS（每秒十亿次浮点运算）。
- 带宽 ``bandwidth_mbps``：Mbps（兆比特每秒）。
- 数据量 ``data_size_mb``：MB（兆字节，1 MB = 8 Mbit）。
- 所有时间量统一使用毫秒（ms）。

本模块只描述数据结构，不包含仿真逻辑，便于测试与界面复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

NodeType = Literal["local", "edge", "cloud"]
TaskStatus = Literal["success", "timeout", "rejected", "failed"]

PRIORITY_LABELS = {0: "高", 1: "中", 2: "低"}
STRATEGY_ORDER = ("fixed_local", "fixed_cloud", "dynamic")


@dataclass(frozen=True)
class NetworkLink:
    """任务源（工业相机/网关）到目标节点的链路模型。

    假设（简化、可解释）：
    - 上行时延 = 数据量(MB) × 8 × 1000 / 有效带宽(Mbps) + 基础单向时延 + 本任务抖动。
    - 下行回传按同一链路带宽估算，用于返回检测结果。
    - 有效带宽 = 带宽 × (1 − 拥塞系数)，表示拥塞对可用带宽的折损。
    """

    bandwidth_mbps: float
    latency_ms: float
    congestion: float = 0.0

    def effective_bandwidth_mbps(self) -> float:
        return max(self.bandwidth_mbps * (1.0 - self.congestion), 1e-6)

    def upload_delay_ms(self, data_size_mb: float, jitter_ms: float = 0.0) -> float:
        transfer = data_size_mb * 8.0 * 1000.0 / self.effective_bandwidth_mbps()
        return transfer + self.latency_ms + max(0.0, jitter_ms)

    def download_delay_ms(self, result_size_mb: float) -> float:
        transfer = result_size_mb * 8.0 * 1000.0 / self.effective_bandwidth_mbps()
        return transfer + self.latency_ms


@dataclass(frozen=True)
class NodeConfig:
    """计算节点配置（静态参数）。

    有效算力 = 算力 × (1 − 基础负载)。基础负载表示节点上与本实验无关的
    常驻业务占用，不随时间变化；动态排队则由仿真器按 FIFO 队列建模。
    """

    node_id: str
    display_name: str
    node_type: NodeType
    compute_gflops: float
    base_load: float
    max_task_gflops: float
    max_queue_slots: int
    link: NetworkLink

    def effective_compute_gflops(self) -> float:
        return max(self.compute_gflops * (1.0 - self.base_load), 1e-9)

    def compute_delay_ms(self, compute_demand_gflops: float) -> float:
        return compute_demand_gflops / self.effective_compute_gflops() * 1000.0


@dataclass(frozen=True)
class SimulationConfig:
    """一次实验的完整配置。三种调度策略共享同一份配置与任务集。"""

    num_tasks: int = 80
    arrival_rate_per_sec: float = 5.0
    seed: int = 42
    data_size_range_mb: tuple[float, float] = (0.5, 2.5)
    compute_range_gflops: tuple[float, float] = (0.5, 6.0)
    deadline_range_ms: tuple[float, float] = (150.0, 900.0)
    network_jitter_ms: float = 6.0
    result_size_mb: float = 0.02
    nodes: tuple[NodeConfig, ...] = ()


@dataclass(frozen=True)
class TaskSpec:
    """一个工业视觉检测任务（不可变，跨策略共享以保证公平对比）。"""

    task_id: int
    arrival_time_ms: float
    data_size_mb: float
    compute_demand_gflops: float
    deadline_ms: float
    priority: int
    jitter_ms: float
    source: str = "工业视觉相机"


@dataclass(frozen=True)
class NodeSnapshot:
    """调度决策时刻的节点状态快照（只读）。"""

    config: NodeConfig
    next_free_ms: float
    queued_count: int

    @property
    def free_slots(self) -> int:
        return max(0, self.config.max_queue_slots - self.queued_count)


@dataclass
class Decision:
    """一次调度决策的结果与解释信息。"""

    node_id: Optional[str]
    estimated_total_ms: Optional[float]
    score: Optional[float]
    reason: str
    candidate_details: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class TaskResult:
    """单个任务在某个策略下的执行结果。"""

    task: TaskSpec
    strategy_key: str
    node_id: Optional[str]
    node_name: Optional[str]
    status: TaskStatus
    decision_reason: str
    candidate_details: dict[str, dict[str, Any]]
    estimated_total_ms: Optional[float]
    decision_cost_ms: float
    upload_ms: float = 0.0
    queue_ms: float = 0.0
    compute_ms: float = 0.0
    download_ms: float = 0.0
    total_latency_ms: Optional[float] = None
    start_time_ms: Optional[float] = None
    compute_finish_ms: Optional[float] = None
    finish_time_ms: Optional[float] = None

    @property
    def is_on_time(self) -> bool:
        return self.status == "success"

    @property
    def is_completed(self) -> bool:
        return self.status in ("success", "timeout")

    def delay_breakdown(self) -> dict[str, float]:
        return {
            "上行传输": self.upload_ms,
            "排队等待": self.queue_ms,
            "计算处理": self.compute_ms,
            "结果回传": self.download_ms,
        }


@dataclass
class StrategyRun:
    """一种调度策略在一组任务上的完整运行记录。"""

    strategy_key: str
    strategy_name: str
    results: list[TaskResult]
    makespan_ms: float
    total_decision_ms: float
    node_busy_ms: dict[str, float]
    node_counts: dict[str, int]
    started_at: str = ""
