"""可解释的工业边缘计算离散事件仿真器。

仿真模型（简化但自洽）：

1. 任务按到达时间升序进入系统，任务集由 experiments.runner 用固定随机种子
   生成，三种调度策略共享同一份不可变任务集。
2. 调度器在任务到达时刻观察各节点状态（下一次空闲时间、当前排队数），
   给出目标节点与决策原因；决策本身耗时用 perf_counter 实际测量。
3. 执行阶段按 FIFO 单服务台模型模拟每个节点：
   - 上行传输时延：数据量 / 有效带宽 + 链路基础时延 + 任务抖动；
   - 排队时延：max(0, 节点空闲时刻 − 任务到达节点时刻)；
   - 计算时延：计算需求 / 有效算力；
   - 结果回传时延：结果数据量 / 有效带宽 + 链路基础时延。
4. 到达节点时若排队任务数（含正在计算的任务）已达到队列容量，任务被拒绝；
   单任务计算需求超过节点上限时任务也无法执行。
5. 总完成时延 ≤ 时延上限记为成功（success），否则记为超时（timeout）。

所有结果均为软件仿真，不代表真实网络或服务器的实测性能。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from models.schemas import (
    Decision,
    NodeConfig,
    NodeSnapshot,
    SimulationConfig,
    StrategyRun,
    TaskResult,
    TaskSpec,
)
from scheduler.strategies import SchedulingStrategy


class SimulationError(ValueError):
    """仿真过程发生不可恢复错误。"""


@dataclass
class _NodeRuntime:
    """仿真过程中节点的可变运行状态。"""

    config: NodeConfig
    next_free_ms: float = 0.0
    accepted: list[tuple[float, float]] = field(default_factory=list)
    busy_ms: float = 0.0

    def queued_at(self, t_ms: float) -> int:
        """在 t_ms 时刻仍未完成计算的任务数（排队 + 正在计算）。"""
        return sum(1 for _, finish in self.accepted if finish > t_ms)

    def snapshot(self, now_ms: float) -> NodeSnapshot:
        return NodeSnapshot(
            config=self.config,
            next_free_ms=self.next_free_ms,
            queued_count=self.queued_at(now_ms),
        )


class EdgeSimulator:
    """对一组固定任务执行指定调度策略，返回逐任务执行记录。"""

    def __init__(self, config: SimulationConfig, tasks: list[TaskSpec]):
        if not config.nodes:
            raise SimulationError("仿真配置至少需要一个计算节点")
        if not tasks:
            raise SimulationError("任务集不能为空")
        self.config = config
        self.tasks = tasks

    def run(self, strategy: SchedulingStrategy) -> StrategyRun:
        runtimes: dict[str, _NodeRuntime] = {
            cfg.node_id: _NodeRuntime(cfg) for cfg in self.config.nodes
        }
        results: list[TaskResult] = []
        total_decision_ms = 0.0

        for task in sorted(self.tasks, key=lambda t: t.arrival_time_ms):
            snapshots = [rt.snapshot(task.arrival_time_ms) for rt in runtimes.values()]
            start_ns = time.perf_counter_ns()
            decision = strategy.decide(task, snapshots, self.config)
            decision_cost_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
            total_decision_ms += decision_cost_ms
            results.append(
                self._execute(task, strategy.key, decision, runtimes, decision_cost_ms)
            )

        makespan = max(
            (r.finish_time_ms for r in results if r.finish_time_ms is not None),
            default=0.0,
        )
        node_busy = {nid: rt.busy_ms for nid, rt in runtimes.items()}
        node_counts: dict[str, int] = {}
        for result in results:
            if result.node_id is not None:
                node_counts[result.node_id] = node_counts.get(result.node_id, 0) + 1

        return StrategyRun(
            strategy_key=strategy.key,
            strategy_name=strategy.name,
            results=results,
            makespan_ms=makespan,
            total_decision_ms=total_decision_ms,
            node_busy_ms=node_busy,
            node_counts=node_counts,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _execute(
        self,
        task: TaskSpec,
        strategy_key: str,
        decision: Decision,
        runtimes: dict[str, _NodeRuntime],
        decision_cost_ms: float,
    ) -> TaskResult:
        base = dict(
            task=task,
            strategy_key=strategy_key,
            node_id=decision.node_id,
            candidate_details=decision.candidate_details,
            estimated_total_ms=decision.estimated_total_ms,
            decision_cost_ms=decision_cost_ms,
        )

        if decision.node_id is None:
            return TaskResult(
                node_name=None,
                status="rejected",
                decision_reason=decision.reason,
                **base,
            )

        runtime = runtimes.get(decision.node_id)
        if runtime is None:
            return TaskResult(
                node_name=None,
                status="rejected",
                decision_reason=f"{decision.reason}（执行阶段：目标节点不存在）",
                **base,
            )

        cfg = runtime.config
        if task.compute_demand_gflops > cfg.max_task_gflops + 1e-9:
            reason = (
                f"{decision.reason}（执行阶段：任务计算需求 {task.compute_demand_gflops:.2f} GFLOP "
                f"超过 {cfg.display_name} 单任务上限 {cfg.max_task_gflops:.2f} GFLOP，任务被拒绝）"
            )
            return TaskResult(
                node_name=cfg.display_name,
                status="rejected",
                decision_reason=reason,
                **base,
            )

        upload_ms = cfg.link.upload_delay_ms(task.data_size_mb, task.jitter_ms)
        arrive_node_ms = task.arrival_time_ms + upload_ms

        if runtime.queued_at(arrive_node_ms) >= cfg.max_queue_slots:
            reason = (
                f"{decision.reason}（执行阶段：任务到达时 {cfg.display_name} 队列已满"
                f"（容量 {cfg.max_queue_slots}），任务被拒绝）"
            )
            return TaskResult(
                node_name=cfg.display_name,
                status="rejected",
                decision_reason=reason,
                upload_ms=upload_ms,
                **base,
            )

        compute_ms = cfg.compute_delay_ms(task.compute_demand_gflops)
        start_ms = max(arrive_node_ms, runtime.next_free_ms)
        queue_ms = start_ms - arrive_node_ms
        finish_compute_ms = start_ms + compute_ms
        download_ms = cfg.link.download_delay_ms(self.config.result_size_mb)
        finish_ms = finish_compute_ms + download_ms
        total_ms = finish_ms - task.arrival_time_ms

        runtime.next_free_ms = finish_compute_ms
        runtime.accepted.append((arrive_node_ms, finish_compute_ms))
        runtime.busy_ms += compute_ms

        status = "success" if total_ms <= task.deadline_ms + 1e-9 else "timeout"
        return TaskResult(
            node_name=cfg.display_name,
            status=status,
            decision_reason=decision.reason,
            upload_ms=upload_ms,
            queue_ms=queue_ms,
            compute_ms=compute_ms,
            download_ms=download_ms,
            total_latency_ms=total_ms,
            start_time_ms=start_ms,
            compute_finish_ms=finish_compute_ms,
            finish_time_ms=finish_ms,
            **base,
        )
