"""几何候选连接 + 显式业务连接表。

关键原则：
- 空间吸附只生成 geometry_candidates（建议），不直接等于生产流程；
- connections 是仿真唯一使用的显式表，保存后与几何解耦：设备移动不会自动改写它；
- 重新推导必须由用户/API 显式触发；
- 校验覆盖：设备/端口存在、方向兼容、孤立设备、环路、无源/无汇、仅展示设备混入物料流、
  连接端点因移动而明显错位（warning，不自动修改）。
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from .models import Connection, Device, SceneDocument, ValidationIssue, new_connection_id

MATERIAL = "material_flow"


def world_ports(device: Device) -> list[dict[str, Any]]:
    result = []
    for port in device.ports:
        result.append(
            {
                "port": port,
                "world": device.world_point(port.offset),
                "out": device.world_direction(port.out),
            }
        )
    return result


def _dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _dot(a: Optional[list[float]], b: Optional[list[float]]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1])


def derive_geometry_candidates(
    devices: list[Device],
    *,
    tolerance_m: float = 1.5,
    same_level_m: float = 1.2,
) -> tuple[list[Connection], list[ValidationIssue]]:
    """由设备端口世界坐标生成候选物料流连接。只生成候选，不改显式表。"""
    issues: list[ValidationIssue] = []
    outputs: list[tuple[Device, dict[str, Any]]] = []
    inputs: list[tuple[Device, dict[str, Any]]] = []
    for device in devices:
        if device.simulation_type == "display_only":
            continue
        for entry in world_ports(device):
            port = entry["port"]
            if port.flow != "material":
                continue
            if port.direction == "output":
                outputs.append((device, entry))
            elif port.direction == "input":
                inputs.append((device, entry))

    used_inputs: set[tuple[str, str]] = set()
    candidates: list[Connection] = []
    for src, sout in outputs:
        best: Optional[tuple[float, Connection]] = None
        for dst, din in inputs:
            if dst.id == src.id:
                continue
            if (dst.id, din["port"].id) in used_inputs:
                continue
            gap = _dist(sout["world"], din["world"])
            if gap > tolerance_m:
                continue
            if abs(float(sout["world"][1]) - float(din["world"][1])) > same_level_m:
                continue
            dot = _dot(sout["out"], din["out"])
            wrong_way = dot is not None and dot > 0.5
            score = gap + (1.0 if wrong_way else 0.0)
            conn = Connection(
                id=new_connection_id(),
                source_device=src.id,
                source_port=sout["port"].id,
                target_device=dst.id,
                target_port=din["port"].id,
                connection_type=MATERIAL,
                metadata={
                    "source": "geometry_derived",
                    "gap_m": round(gap, 4),
                    "direction_dot": None if dot is None else round(dot, 4),
                    "confidence": "high" if gap <= 0.3 else "medium",
                },
            )
            if best is None or score < best[0]:
                best = (score, conn)
        if best is not None:
            candidates.append(best[1])
            used_inputs.add((best[1].target_device, best[1].target_port))
        else:
            issues.append(
                ValidationIssue(
                    "warning",
                    "no_candidate_target",
                    f"设备 {src.id} 的输出端口在 {tolerance_m:.1f} m 内没有找到可连接的输入端口。",
                    devices=[src.id],
                )
            )
    return candidates, issues


def initial_connections(
    existing: list[Connection],
    candidates: list[Connection],
    device_ids: set[str],
) -> tuple[list[Connection], bool]:
    """重导入时的连接保留策略。

    - 已有显式连接 ⇒ 原样保留（设备移动/删除都不会自动改写业务流程；删除设备由校验报错）；
    - 新场景（连接表为空）⇒ 采用几何候选作为初始连接，并标记来源。
    """
    if existing:
        return existing, False
    adopted = []
    for c in candidates:
        adopted.append(
            Connection(
                id=c.id,
                source_device=c.source_device,
                source_port=c.source_port,
                target_device=c.target_device,
                target_port=c.target_port,
                connection_type=MATERIAL,
                metadata={**c.metadata, "source": "geometry_derived_initial"},
            )
        )
    return adopted, bool(adopted)


def _material_connections(connections: list[Connection]) -> list[Connection]:
    return [c for c in connections if c.connection_type == MATERIAL]


def validate_connections(
    scene: SceneDocument,
    *,
    geometry_tolerance_m: float = 3.0,
) -> dict[str, list[dict[str, Any]]]:
    """完整校验显式连接表；返回 errors / warnings。"""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    devices = scene.device_map()
    seen_ids: set[str] = set()
    seen_edges: set[str] = set()

    for conn in scene.connections:
        if conn.id in seen_ids:
            errors.append(ValidationIssue("error", "duplicate_connection_id", f"连接 ID 重复：{conn.id}", connection_id=conn.id))
        seen_ids.add(conn.id)
        if conn.connection_type not in ("material_flow", "control", "data", "power"):
            errors.append(ValidationIssue("error", "invalid_connection_type", f"连接 {conn.id} 的类型非法：{conn.connection_type}", connection_id=conn.id))
        src = devices.get(conn.source_device)
        dst = devices.get(conn.target_device)
        if src is None:
            errors.append(ValidationIssue("error", "missing_source_device", f"连接 {conn.id} 的源设备不存在：{conn.source_device}", devices=[conn.source_device], connection_id=conn.id))
            continue
        if dst is None:
            errors.append(ValidationIssue("error", "missing_target_device", f"连接 {conn.id} 的目标设备不存在：{conn.target_device}", devices=[conn.target_device], connection_id=conn.id))
            continue
        if src.id == dst.id:
            errors.append(ValidationIssue("error", "self_connection", f"连接 {conn.id} 两端是同一设备 {src.id}", devices=[src.id], connection_id=conn.id))
        sport = next((p for p in src.ports if p.id == conn.source_port), None)
        tport = next((p for p in dst.ports if p.id == conn.target_port), None)
        if sport is None:
            errors.append(ValidationIssue("error", "missing_source_port", f"连接 {conn.id} 的源端口不存在：{conn.source_device}.{conn.source_port}", devices=[src.id], connection_id=conn.id))
        if tport is None:
            errors.append(ValidationIssue("error", "missing_target_port", f"连接 {conn.id} 的目标端口不存在：{conn.target_device}.{conn.target_port}", devices=[dst.id], connection_id=conn.id))
        if sport is not None and tport is not None:
            if conn.connection_type == MATERIAL:
                if sport.direction != "output":
                    errors.append(ValidationIssue("error", "port_direction", f"连接 {conn.id} 的源端口 {sport.id} 不是输出端口", devices=[src.id], connection_id=conn.id))
                if tport.direction != "input":
                    errors.append(ValidationIssue("error", "port_direction", f"连接 {conn.id} 的目标端口 {tport.id} 不是输入端口", devices=[dst.id], connection_id=conn.id))
                if sport.flow != "material" or tport.flow != "material":
                    errors.append(ValidationIssue("error", "port_flow", f"连接 {conn.id} 的端口不是物料端口", devices=[src.id, dst.id], connection_id=conn.id))
                if src.simulation_type == "display_only" or dst.simulation_type == "display_only":
                    errors.append(
                        ValidationIssue(
                            "error",
                            "display_only_in_flow",
                            f"连接 {conn.id} 包含“仅三维展示”设备（{src.id}/{dst.id}），不能参与物料流。",
                            devices=[src.id, dst.id],
                            connection_id=conn.id,
                        )
                    )
                # 端点几何错位：仅提示，不自动修改业务流程
                try:
                    sp = src.world_point(sport.offset)
                    tp = dst.world_point(tport.offset)
                    gap = _dist(sp, tp)
                    if gap > geometry_tolerance_m:
                        warnings.append(
                            ValidationIssue(
                                "warning",
                                "connection_geometry_mismatch",
                                f"连接 {conn.id} 两端当前相距 {gap:.2f} m（设备可能已移动），业务流程保持不变；请确认或重新推导。",
                                devices=[src.id, dst.id],
                                connection_id=conn.id,
                            )
                        )
                except (TypeError, ValueError):
                    pass
        edge_key = f"{conn.source_device}:{conn.source_port}->{conn.target_device}:{conn.target_port}:{conn.connection_type}"
        if edge_key in seen_edges:
            errors.append(ValidationIssue("error", "duplicate_edge", f"重复连接：{edge_key}", connection_id=conn.id))
        seen_edges.add(edge_key)

    material = [c for c in _material_connections(scene.connections) if devices.get(c.source_device) and devices.get(c.target_device)]
    graph: dict[str, list[str]] = defaultdict(list)
    for conn in material:
        graph[conn.source_device].append(conn.target_device)

    # 环路检测（物料流不允许闭环；有环会导致引擎死锁/无限在制）
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                cycle = stack[stack.index(nxt) :] + [nxt]
                errors.append(
                    ValidationIssue(
                        "error",
                        "material_flow_cycle",
                        "物料流存在环路：" + " → ".join(cycle),
                        devices=cycle[:-1],
                    )
                )
            elif state.get(nxt, 0) == 0:
                visit(nxt)
        stack.pop()
        state[node] = 2

    for node in list(graph):
        if state.get(node, 0) == 0:
            visit(node)

    # 每个输出端口最多一条物料连接
    out_counts: dict[tuple[str, str], int] = defaultdict(int)
    for conn in material:
        out_counts[(conn.source_device, conn.source_port)] += 1
    for (device_id, port_id), count in out_counts.items():
        if count > 1:
            warnings.append(
                ValidationIssue(
                    "warning",
                    "multiple_outgoing_from_port",
                    f"设备 {device_id} 的端口 {port_id} 有 {count} 条物料连接；当前引擎按顺序尝试下游。",
                    devices=[device_id],
                )
            )

    sim_devices = [d for d in scene.devices if d.simulation_type != "display_only"]
    if sim_devices:
        sources = [d for d in sim_devices if d.simulation_type == "spawner"]
        sinks = [d for d in sim_devices if d.simulation_type == "despawner"]
        if not sources:
            errors.append(ValidationIssue("error", "no_source", "场景没有 Spawner（物料源），无法开始生产。"))
        if not sinks:
            errors.append(ValidationIssue("error", "no_sink", "场景没有 Despawner（物料出口），无法统计完成数量。"))

        # 可达性
        reachable: set[str] = set()
        queue = [d.id for d in sources]
        while queue:
            node = queue.pop()
            if node in reachable:
                continue
            reachable.add(node)
            queue.extend(graph.get(node, []))
        for device in sim_devices:
            if device.id not in reachable:
                warnings.append(
                    ValidationIssue(
                        "warning",
                        "isolated_device",
                        f"设备 {device.id}（{device.name}）未接入从 Spawner 出发的物料流，不会参与生产。",
                        devices=[device.id],
                    )
                )
        if sinks and not any(sink.id in reachable for sink in sinks):
            errors.append(
                ValidationIssue(
                    "error",
                    "material_flow_broken",
                    "物料流中断：从 Spawner 出发无法到达任何 Despawner，请检查显式连接表。",
                    devices=[sink.id for sink in sinks],
                )
            )
        # 无输入/无输出检查
        for device in sim_devices:
            has_in = any(c.target_device == device.id for c in material)
            has_out = any(c.source_device == device.id for c in material)
            if device.simulation_type not in ("spawner",) and not has_in:
                warnings.append(ValidationIssue("warning", "device_without_input", f"设备 {device.id} 没有物料输入。", devices=[device.id]))
            if device.simulation_type not in ("despawner",) and not has_out:
                warnings.append(ValidationIssue("warning", "device_without_output", f"设备 {device.id} 没有物料输出。", devices=[device.id]))

    return {
        "errors": [issue.to_dict() for issue in errors],
        "warnings": [issue.to_dict() for issue in warnings],
    }


def material_graph(scene: SceneDocument) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = defaultdict(list)
    devices = scene.device_map()
    for conn in _material_connections(scene.connections):
        if conn.source_device in devices and conn.target_device in devices:
            graph[conn.source_device].append(conn.target_device)
    return dict(graph)


def topological_order(scene: SceneDocument) -> list[str]:
    """物料流的拓扑顺序；有环/缺失设备时抛出 ValueError。"""
    devices = scene.device_map()
    graph = material_graph(scene)
    indegree = {device_id: 0 for device_id in devices if devices[device_id].simulation_type != "display_only"}
    for src, targets in graph.items():
        if src not in indegree:
            continue
        for dst in targets:
            if dst in indegree:
                indegree[dst] += 1
    ready = sorted([node for node, deg in indegree.items() if deg == 0])
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in sorted(graph.get(node, [])):
            if nxt not in indegree:
                continue
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
                ready.sort()
    if len(order) != len(indegree):
        raise ValueError("物料流存在环路，无法构建仿真顺序")
    return order
