"""三种可公平对比的边缘计算调度策略。

共同前提：调度器只能看到当前时刻的状态快照（节点下一次空闲时间、当前排队数），
是“在线”决策；所有策略使用完全相同的任务集、网络条件与随机种子。

1. 固定本地（fixed_local）：所有任务发往本地节点，不做任何权衡。
2. 固定云端（fixed_cloud）：所有任务发往云服务器，不做任何权衡。
3. 动态调度（dynamic）：对每个计算节点估算完成时延，并按可解释的加权评分选择。

动态调度的预测模型与仿真器一致：
    预测完成时延 = 上行传输 + 预测排队 + 计算 + 结果回传
    预测排队 = max(0, 节点下一次空闲时刻 − (任务到达时刻 + 预测上行时延))
评分公式（分值越低越好）：
    score = 紧迫度权重 × (预测完成时延 / 任务时延上限) + 0.3 × 排队压力
    - 紧迫度权重 = 1.0 + 0.5 × (2 − 优先级) / 2，即高优先级对时延比值更敏感；
    - 排队压力 = 决策时刻排队任务数 / 节点队列容量，用于规避即将拥塞的节点。
可行节点需同时满足：单任务算力不超限、预测完成时延不超过时延上限、
决策时刻队列未满。若没有可行节点，则退化为“预测完成时延最小”的可执行节点，
并在决策原因中明确说明该任务预计超时。若所有节点都无法承载该任务的算力需求，
则直接拒绝任务。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from models.schemas import Decision, NodeSnapshot, SimulationConfig, TaskSpec

QUEUE_PRESSURE_WEIGHT = 0.3


def _predict(node: NodeSnapshot, task: TaskSpec, config: SimulationConfig) -> dict[str, float]:
    """按节点的当前状态预测该任务在节点上的四个时延分量。"""
    cfg = node.config
    upload = cfg.link.upload_delay_ms(task.data_size_mb, task.jitter_ms)
    predicted_arrival = task.arrival_time_ms + upload
    wait = max(0.0, node.next_free_ms - predicted_arrival)
    compute = cfg.compute_delay_ms(task.compute_demand_gflops)
    download = cfg.link.download_delay_ms(config.result_size_mb)
    return {
        "upload": upload,
        "wait": wait,
        "compute": compute,
        "download": download,
        "total": upload + wait + compute + download,
    }


class SchedulingStrategy(ABC):
    """调度策略抽象基类。"""

    key: str = ""
    name: str = ""
    description: str = ""

    @abstractmethod
    def decide(
        self,
        task: TaskSpec,
        nodes: list[NodeSnapshot],
        config: SimulationConfig,
    ) -> Decision:
        """为单个任务给出目标节点、预测时延、评分与可读的决策原因。"""

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"{type(self).__name__}(key={self.key!r})"


class _FixedNodeStrategy(SchedulingStrategy):
    """固定发送到某一类节点的策略。"""

    target_type: str = ""
    policy_label: str = ""

    def decide(
        self,
        task: TaskSpec,
        nodes: list[NodeSnapshot],
        config: SimulationConfig,
    ) -> Decision:
        target = next(
            (n for n in nodes if n.config.node_type == self.target_type), None
        )
        if target is None:
            return Decision(
                node_id=None,
                estimated_total_ms=None,
                score=None,
                reason=f"配置中不存在 {self.policy_label} 节点，任务无法调度。",
            )

        pred = _predict(target, task, config)
        capacity_ok = task.compute_demand_gflops <= target.config.max_task_gflops + 1e-9
        queue_ok = target.queued_count < target.config.max_queue_slots
        detail = {
            **pred,
            "score": None,
            "feasible": capacity_ok and queue_ok and pred["total"] <= task.deadline_ms,
            "over_capacity": not capacity_ok,
            "queue_full": not queue_ok,
            "within_deadline": pred["total"] <= task.deadline_ms + 1e-9,
            "queue_pressure": target.queued_count / max(target.config.max_queue_slots, 1),
        }
        reason = (
            f"固定策略：不比较其他节点，任务统一发送到{target.config.display_name}。"
            f"按当前状态预测完成时延约 {pred['total']:.1f} ms"
            f"（上行 {pred['upload']:.1f} + 排队 {pred['wait']:.1f} + "
            f"计算 {pred['compute']:.1f} + 回传 {pred['download']:.1f}），"
            f"任务时延上限 {task.deadline_ms:.0f} ms。"
        )
        return Decision(
            node_id=target.config.node_id,
            estimated_total_ms=pred["total"],
            score=None,
            reason=reason,
            candidate_details={target.config.node_id: detail},
        )


class FixedLocalStrategy(_FixedNodeStrategy):
    key = "fixed_local"
    name = "固定本地"
    description = "所有任务发送到本地工控机，不评估网络与负载。"
    target_type = "local"
    policy_label = "本地"


class FixedCloudStrategy(_FixedNodeStrategy):
    key = "fixed_cloud"
    name = "固定云端"
    description = "所有任务发送到云服务器，不评估时延约束。"
    target_type = "cloud"
    policy_label = "云端"


class DynamicStrategy(SchedulingStrategy):
    key = "dynamic"
    name = "动态调度"
    description = (
        "综合任务计算需求、节点有效算力与负载、网络时延与带宽、任务时延上限，"
        "用加权评分选择预测完成时延最小且可行的节点。"
    )

    def decide(
        self,
        task: TaskSpec,
        nodes: list[NodeSnapshot],
        config: SimulationConfig,
    ) -> Decision:
        details: dict[str, dict[str, Any]] = {}
        feasible: list[tuple[float, NodeSnapshot]] = []

        for node in nodes:
            cfg = node.config
            pred = _predict(node, task, config)
            over_capacity = task.compute_demand_gflops > cfg.max_task_gflops + 1e-9
            queue_full = node.queued_count >= cfg.max_queue_slots
            within_deadline = pred["total"] <= task.deadline_ms + 1e-9
            queue_pressure = node.queued_count / max(cfg.max_queue_slots, 1)
            urgency_weight = 1.0 + 0.5 * (2 - task.priority) / 2.0
            score = (
                urgency_weight * pred["total"] / max(task.deadline_ms, 1e-6)
                + QUEUE_PRESSURE_WEIGHT * queue_pressure
            )
            is_feasible = not over_capacity and not queue_full and within_deadline
            details[cfg.node_id] = {
                **pred,
                "score": score,
                "feasible": is_feasible,
                "over_capacity": over_capacity,
                "queue_full": queue_full,
                "within_deadline": within_deadline,
                "queue_pressure": queue_pressure,
                "urgency_weight": urgency_weight,
            }
            if is_feasible:
                feasible.append((score, node))

        if feasible:
            score, chosen = min(
                feasible, key=lambda item: (item[0], item[1].config.node_id)
            )
            detail = details[chosen.config.node_id]
            reason = (
                f"动态调度：在 {len(feasible)} 个可行节点中，"
                f"{chosen.config.display_name} 综合评分最低（{score:.3f}）。"
                f"预测完成时延 {detail['total']:.1f} ms"
                f"（上行 {detail['upload']:.1f} + 排队 {detail['wait']:.1f} + "
                f"计算 {detail['compute']:.1f} + 回传 {detail['download']:.1f}），"
                f"任务时延上限 {task.deadline_ms:.0f} ms。"
                f"评分 = {detail['urgency_weight']:.2f} × (预测时延/时延上限) + "
                f"{QUEUE_PRESSURE_WEIGHT} × 排队压力。"
            )
            return Decision(
                node_id=chosen.config.node_id,
                estimated_total_ms=detail["total"],
                score=score,
                reason=reason,
                candidate_details=details,
            )

        # 没有同时满足时延与容量约束的节点，进入退化路径。
        capacity_nodes = [
            node
            for node in nodes
            if not details[node.config.node_id]["over_capacity"]
        ]
        if not capacity_nodes:
            return Decision(
                node_id=None,
                estimated_total_ms=None,
                score=None,
                reason=(
                    "动态调度：所有计算节点的单任务算力上限都低于该任务的计算需求"
                    f"（{task.compute_demand_gflops:.2f} GFLOP），任务无法执行。"
                ),
                candidate_details=details,
            )

        available = [
            node
            for node in capacity_nodes
            if not details[node.config.node_id]["queue_full"]
        ]
        candidates = available or capacity_nodes
        chosen = min(
            candidates,
            key=lambda n: (details[n.config.node_id]["total"], n.config.node_id),
        )
        detail = details[chosen.config.node_id]
        gap = detail["total"] - task.deadline_ms
        if available:
            reason = (
                "动态调度：没有节点能在时延上限内完成该任务，退化为选择预测完成时延最小的节点 "
                f"{chosen.config.display_name}（预测 {detail['total']:.1f} ms，"
                f"超出上限 {gap:.1f} ms），该任务预计超时。"
            )
        else:
            reason = (
                "动态调度：所有可执行该任务的节点队列均已满，退化为预测完成时延最小的节点 "
                f"{chosen.config.display_name}（预测 {detail['total']:.1f} ms），"
                "执行阶段可能因队列容量不足被拒绝。"
            )
        return Decision(
            node_id=chosen.config.node_id,
            estimated_total_ms=detail["total"],
            score=detail["score"],
            reason=reason,
            candidate_details=details,
        )
