"""通用场景离散事件仿真引擎（生产仿真的 Python 权威）。

不修改 RFTwin：RFTwin 仍负责固定四单元产线；本引擎负责用户自建场景。
模型（可解释、确定性）：
- 物料以 item 为单位，从 spawner 生成，沿显式 material_flow 连接移动；
- 每个设备单服务台 + 有限队列：conveyor 节拍 = 长度/速度，inspection/robot = 参数节拍；
- 下游容量满时上游阻塞；设备故障按指数间隔发生，维修期间停机；
- 所有指标（产量、WIP、OEE、利用率、能耗、故障）来自本次 tick 级仿真，
  不写死任何 KPI；相同场景 + 相同 seed + 相同时长 ⇒ 相同结果。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from integration.project_store import new_experiment_id

from .connections import topological_order, validate_connections
from .models import Connection, Device, SceneDocument, now_iso
from .store import content_hash, save_scene_experiment, validate_scene_document

PRODUCTION_TYPES = ("conveyor", "inspection", "robot", "buffer")
FAILURE_TOKENS = ("FAIL", "FAULT", "JAM", "EMERGENCY_STOP", "BLOCKED")


class SceneSimulationError(ValueError):
    """场景不满足仿真条件。"""


@dataclass
class Item:
    item_id: str
    created_at: float
    admitted_at: Optional[float] = None
    product: str = "product"
    good: Optional[bool] = None
    history: list[str] = field(default_factory=list)


@dataclass
class DeviceRuntime:
    device: Device
    capacity: int
    cycle_sec: float
    queue: deque[Item] = field(default_factory=deque)
    current: Optional[Item] = None
    blocked_item: Optional[Item] = None
    busy_until: float = 0.0
    state: str = "idle"
    state_since: float = 0.0
    processed: int = 0
    good: int = 0
    defect: int = 0
    busy_sec: float = 0.0
    blocked_sec: float = 0.0
    starved_sec: float = 0.0
    failed_sec: float = 0.0
    failure_next_at: Optional[float] = None
    failure_until: float = 0.0
    failure_count: int = 0
    done: int = 0

    def occupancy(self) -> int:
        return len(self.queue) + (1 if self.current is not None else 0) + (1 if self.blocked_item is not None else 0)

    def has_space(self) -> bool:
        return self.occupancy() < self.capacity

    def set_state(self, state: str, t: float) -> bool:
        if state != self.state:
            self.state = state
            self.state_since = t
            return True
        return False


def _finite(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def device_cycle_sec(device: Device) -> float:
    params = device.params
    if device.simulation_type == "conveyor":
        length = max(0.05, _finite(params.get("length_m"), 4.0))
        speed = max(0.01, abs(_finite(params.get("speed_mps"), 0.5)))
        return max(0.05, length / speed)
    if device.simulation_type in ("inspection", "robot"):
        return max(0.05, _finite(params.get("cycle_sec"), 2.0))
    if device.simulation_type == "spawner":
        return max(0.05, _finite(params.get("interval_sec"), 4.0))
    if device.simulation_type == "buffer":
        return max(0.05, _finite(params.get("cycle_sec"), 0.2))
    return 0.05


def device_capacity(device: Device) -> int:
    params = device.params
    if device.simulation_type == "conveyor":
        return max(1, int(_finite(params.get("capacity"), 1)))
    if device.simulation_type in ("inspection", "robot"):
        return max(1, int(_finite(params.get("capacity"), 1)))
    if device.simulation_type == "spawner":
        return max(1, int(_finite(params.get("capacity"), 1)))
    if device.simulation_type == "despawner":
        return 100000
    if device.simulation_type == "buffer":
        return max(1, int(_finite(params.get("capacity"), 20)))
    return 1


def apply_scene_patch(scene: SceneDocument, patch: dict[str, Any]) -> SceneDocument:
    """把优化建议应用到场景副本（不保存），只允许已知设备参数与仿真设置。"""
    result = copy.deepcopy(scene)
    devices = result.device_map()
    for device_id, params in (patch.get("device_params") or {}).items():
        device = devices.get(device_id)
        if device is None:
            raise SceneSimulationError(f"参数补丁指向不存在的设备：{device_id}")
        if not isinstance(params, dict):
            raise SceneSimulationError(f"设备 {device_id} 的参数补丁必须是对象")
        for key, value in params.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(float(value)):
                raise SceneSimulationError(f"设备 {device_id} 的参数 {key} 不是有限数值")
            device.params[key] = value
    if patch.get("simulation_settings"):
        result.simulation_settings.update(copy.deepcopy(patch["simulation_settings"]))
    return result


class SceneFactoryEngine:
    """一个场景的确定性离散事件引擎。"""

    def __init__(self, scene: SceneDocument, *, duration_sec: Optional[float] = None, seed: Optional[int] = None, tick_sec: Optional[float] = None, sim_id: Optional[str] = None):
        self.scene = copy.deepcopy(scene)
        settings = self.scene.simulation_settings or {}
        self.duration_sec = float(duration_sec if duration_sec is not None else settings.get("duration_sec", 1800))
        self.seed = int(seed if seed is not None else settings.get("seed", 42))
        self.tick_sec = float(tick_sec if tick_sec is not None else settings.get("tick_sec", 0.25))
        if not 1.0 <= self.duration_sec <= 86400:
            raise SceneSimulationError("仿真时长必须在 1 秒 ~ 24 小时之间")
        if not 0.05 <= self.tick_sec <= 10.0:
            raise SceneSimulationError("仿真步长必须在 0.05 ~ 10 秒之间")
        self.sim_id = sim_id or new_experiment_id("SEXP")
        self.t = 0.0
        self._validate()
        self.runtimes: dict[str, DeviceRuntime] = {}
        self.downstream: dict[str, list[tuple[Connection, str]]] = defaultdict(list)
        self.upstream: dict[str, list[tuple[Connection, str]]] = defaultdict(list)
        self._round_robin: dict[str, int] = defaultdict(int)
        for conn in self.scene.connections:
            if conn.connection_type != "material_flow":
                continue
            if conn.source_device in self.scene.device_map() and conn.target_device in self.scene.device_map():
                self.downstream[conn.source_device].append((conn, conn.target_device))
                self.upstream[conn.target_device].append((conn, conn.source_device))
        self.order = topological_order(self.scene)
        self.events: list[dict[str, Any]] = []
        self.flow_history: list[dict[str, Any]] = []
        self.recent_inspections: list[dict[str, Any]] = []
        self.completed_lead_times: list[float] = []
        self.created = 0
        self.admitted = 0
        self.completed = 0
        self.good_units = 0
        self.defect_units = 0
        self.discarded = 0
        self._seq = 0
        self._minute_mark = 0
        self._rng = random.Random(self.seed)
        self._build_runtimes()
        self._started = time.perf_counter()

    # ------------------------------------------------------------------ setup
    def _validate(self) -> None:
        result = validate_scene_document(self.scene)
        if result["errors"]:
            messages = "；".join(e["message"] for e in result["errors"][:8])
            raise SceneSimulationError(f"场景校验失败：{messages}")
        sim_devices = [d for d in self.scene.devices if d.simulation_type != "display_only"]
        if not sim_devices:
            raise SceneSimulationError("场景没有可仿真设备")
        if not any(d.simulation_type == "spawner" for d in sim_devices):
            raise SceneSimulationError("场景缺少 Spawner（物料源）")
        if not any(d.simulation_type == "despawner" for d in sim_devices):
            raise SceneSimulationError("场景缺少 Despawner（物料出口）")

    def _build_runtimes(self) -> None:
        for device in self.scene.devices:
            if device.simulation_type == "display_only":
                continue
            rt = DeviceRuntime(
                device=device,
                capacity=device_capacity(device),
                cycle_sec=device_cycle_sec(device),
            )
            p = max(0.0, _finite(device.params.get("failure_prob_per_hour"), 0.0))
            if p > 0 and device.simulation_type not in ("spawner", "despawner"):
                rt.failure_next_at = self._sample_failure_interval(p)
            self.runtimes[device.id] = rt

    def _sample_failure_interval(self, probability_per_hour: float) -> float:
        mean = 3600.0 / max(1e-9, probability_per_hour)
        return max(self.tick_sec, self._rng.expovariate(1.0 / mean))

    def _sample_repair_sec(self, device: Device) -> float:
        mttr = max(0.0, _finite(device.params.get("mttr_min"), 2.0)) * 60.0
        if mttr <= 0:
            return self.tick_sec
        return max(self.tick_sec, self._rng.expovariate(1.0 / mttr))

    # ------------------------------------------------------------------ events
    def _event(self, event_type: str, device_id: Optional[str], message: str, *, severity: str = "INFO", **extra: Any) -> None:
        self._seq += 1
        seconds = int(self.t)
        payload = {
            "seq": self._seq,
            "sim_time_sec": round(self.t, 3),
            "sim_time": f"T+{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}",
            "sim_tick": int(self.t / self.tick_sec),
            "event_type": event_type,
            "source_id": device_id,
            "source_type": "scene_device",
            "severity": severity,
            "message": message,
            **extra,
        }
        self.events.append(payload)

    # ------------------------------------------------------------------ helpers
    def _try_push(self, target_id: str, item: Item) -> bool:
        target = self.runtimes.get(target_id)
        if target is None or not target.has_space():
            return False
        target.queue.append(item)
        return True

    def _push_downstream(self, source_id: str, item: Item) -> bool:
        targets = self.downstream.get(source_id, [])
        if not targets:
            return False
        start = self._round_robin[source_id] % len(targets)
        for offset in range(len(targets)):
            conn, target_id = targets[(start + offset) % len(targets)]
            if self._try_push(target_id, item):
                self._round_robin[source_id] = (start + offset + 1) % len(targets)
                return True
        return False

    def _deliver_or_block(self, rt: DeviceRuntime, item: Item) -> None:
        """把已完成加工的物料送入下游；失败则停在 blocked_item，等待下一 tick 重试。"""
        device = rt.device
        if self._push_downstream(device.id, item):
            rt.blocked_item = None
            rt.set_state("running", self.t)
            return
        rt.blocked_item = item
        rt.set_state("blocked", self.t)
        self._event(
            "BLOCKED",
            device.id,
            f"{item.item_id} 在 {device.name} 出口等待下游容量",
            severity="WARN",
            item_id=item.item_id,
        )

    def _finish_processing(self, rt: DeviceRuntime) -> None:
        """一个物料完成加工（只执行一次）：检测判定/出口计数/尝试下游。"""
        device = rt.device
        item = rt.current
        if item is None:
            return
        rt.current = None
        rt.processed += 1
        if device.simulation_type == "inspection":
            pass_rate = min(1.0, max(0.0, _finite(device.params.get("pass_rate"), 0.95)))
            passed = self._rng.random() < pass_rate
            item.good = passed
            stamp = f"T+{int(self.t) // 3600:02d}:{(int(self.t) % 3600) // 60:02d}:{int(self.t) % 60:02d}"
            if passed:
                rt.good += 1
                self._event("INSPECTION_PASS", device.id, f"{item.item_id} 通过 {device.name} 检测", item_id=item.item_id)
                self.recent_inspections.append(
                    {
                        "part_id": item.item_id,
                        "device_id": device.id,
                        "first_pass": True,
                        "ground_truth": "PASS",
                        "sim_time": stamp,
                        "sim_tick": int(self.t / self.tick_sec),
                    }
                )
                self._deliver_or_block(rt, item)
            else:
                rt.defect += 1
                self.defect_units += 1
                self._event("INSPECTION_FAIL", device.id, f"{item.item_id} 未通过 {device.name} 检测，判定为不良", severity="WARN", item_id=item.item_id)
                self.recent_inspections.append(
                    {
                        "part_id": item.item_id,
                        "device_id": device.id,
                        "first_pass": False,
                        "ground_truth": "FAIL",
                        "sim_time": stamp,
                        "sim_tick": int(self.t / self.tick_sec),
                    }
                )
                rt.set_state("idle", self.t)
            return
        if device.simulation_type == "despawner":
            rt.done += 1
            self.completed += 1
            self.completed_lead_times.append(max(0.0, self.t - item.created_at))
            if item.good is not False:
                self.good_units += 1
            self._event("DESPAWNED", device.id, f"{item.item_id} 到达出口 {device.name}", item_id=item.item_id)
            rt.set_state("idle", self.t)
            return
        self._deliver_or_block(rt, item)

    def _maybe_start_failure(self, rt: DeviceRuntime) -> None:
        if rt.state == "failed":
            return
        if rt.failure_next_at is None:
            return
        if self.t + 1e-9 < rt.failure_next_at:
            return
        if self.t < rt.failure_until:
            return
        device = rt.device
        rt.failed_sec += 0.0
        rt.failure_until = self.t + self._sample_repair_sec(device)
        rt.failure_count += 1
        rt.set_state("failed", self.t)
        self._event(
            "FAILURE",
            device.id,
            f"{device.name} 发生故障，预计维修 {rt.failure_until - self.t:.1f} 秒（仿真假设）",
            severity="HIGH",
            failure_type="device_failure",
        )

    def _finish_failure_if_due(self, rt: DeviceRuntime) -> None:
        if rt.state == "failed" and self.t + 1e-9 >= rt.failure_until:
            rt.set_state("idle", self.t)
            self._event("REPAIRED", rt.device.id, f"{rt.device.name} 维修完成，恢复生产")
            probability = max(0.0, _finite(rt.device.params.get("failure_prob_per_hour"), 0.0))
            rt.failure_next_at = self.t + self._sample_failure_interval(probability) if probability > 0 else None

    # ------------------------------------------------------------------ tick
    def step(self, dt: Optional[float] = None) -> None:
        dt = float(dt if dt is not None else self.tick_sec)
        if dt <= 0:
            raise SceneSimulationError("步长必须大于 0")
        self.t += dt
        for device_id in self.order:
            rt = self.runtimes.get(device_id)
            if rt is None:
                continue
            device = rt.device
            # 故障状态：先结算维修，再判断是否开始下一次故障
            self._finish_failure_if_due(rt)
            self._maybe_start_failure(rt)
            if rt.state == "failed":
                rt.failed_sec += dt
                continue

            # Spawner 生产
            if device.simulation_type == "spawner":
                self._step_spawner(rt, dt)
                continue

            # 已完成加工但下游满：只重试传递，不重复加工/判定
            if rt.blocked_item is not None:
                if self._push_downstream(device.id, rt.blocked_item):
                    rt.blocked_item = None
                    rt.set_state("running", self.t)
                else:
                    rt.blocked_sec += dt
                    rt.set_state("blocked", self.t)
                    continue

            # 加工刚到时间：只结算一次
            if rt.current is not None and self.t + 1e-9 >= rt.busy_until:
                self._finish_processing(rt)
                if rt.blocked_item is not None:
                    rt.blocked_sec += dt
                    continue

            # 开始加工
            if rt.current is None and rt.queue:
                rt.current = rt.queue.popleft()
                rt.current.history.append(device.id)
                rt.busy_until = self.t + rt.cycle_sec
                rt.set_state("processing" if device.simulation_type in ("inspection", "robot") else "running", self.t)
                self._event("TASK_STARTED", device.id, f"{rt.current.item_id} 进入 {device.name}", item_id=rt.current.item_id)
                if rt.cycle_sec <= 1e-9:
                    self._finish_processing(rt)
                continue

            if rt.current is not None:
                rt.busy_sec += dt
                if device.simulation_type in ("inspection", "robot"):
                    rt.set_state("processing", self.t)
                else:
                    rt.set_state("running", self.t)
            elif rt.queue:
                rt.busy_sec += dt
            else:
                rt.starved_sec += dt
                changed = rt.set_state("idle", self.t)
                if changed and self.upstream.get(device.id):
                    self._event("STARVED", device.id, f"{device.name} 缺料等待", severity="INFO")

        self._record_minute()
        if self.t + 1e-9 >= self.duration_sec:
            self._emit_final_states()

    def _step_spawner(self, rt: DeviceRuntime, dt: float) -> None:
        device = rt.device
        interval = max(0.05, _finite(device.params.get("interval_sec"), 4.0))
        batch = max(1, int(_finite(device.params.get("batch_size"), 1)))
        limit = int(_finite(device.params.get("total_limit"), 0))
        # 先尝试把已生成但未进入下游的物料送出
        while rt.queue:
            if not self._push_downstream(device.id, rt.queue[0]):
                rt.blocked_sec += dt
                rt.set_state("blocked", self.t)
                return
            item = rt.queue.popleft()
            item.admitted_at = self.t
            self.admitted += 1
            rt.processed += 1
            rt.set_state("running", self.t)
        if limit > 0 and self.created >= limit:
            rt.set_state("idle", self.t)
            return
        if rt.busy_until <= self.t:
            made = 0
            for _ in range(batch):
                if limit > 0 and self.created >= limit:
                    break
                self.created += 1
                made += 1
                self._event("SPAWNED", device.id, f"{device.name} 生成物料 {self.created:05d}", item_id=f"{device.id}-{self.created:05d}")
                item = Item(
                    item_id=f"{device.id}-{self.created:05d}",
                    created_at=self.t,
                    product=str(device.params.get("product_type") or "product"),
                    history=[device.id],
                )
                if self._push_downstream(device.id, item):
                    item.admitted_at = self.t
                    self.admitted += 1
                    rt.processed += 1
                    rt.set_state("running", self.t)
                else:
                    rt.queue.append(item)
                    if rt.set_state("blocked", self.t):
                        self._event("BLOCKED", device.id, f"{device.name} 下游容量已满，物料等待", severity="WARN", item_id=item.item_id)
            rt.busy_until = self.t + interval
        else:
            if not rt.queue:
                rt.set_state("idle", self.t)

    def _record_minute(self) -> None:
        minute = int(self.t // 60)
        if minute == self._minute_mark or minute == 0:
            return
        self._minute_mark = minute
        elapsed = max(1e-9, self.t)
        self.flow_history.append(
            {
                "minute": minute,
                "sim_time_sec": round(self.t, 2),
                "good_units": self.good_units,
                "defect_units": self.defect_units,
                "wip": self._wip(),
                "throughput_uph": round(self.completed / elapsed * 3600.0, 2),
                "oee": round(self._oee()["oee"], 4),
            }
        )

    def _emit_final_states(self) -> None:
        for rt in self.runtimes.values():
            rt.set_state(rt.state, self.t)

    # ------------------------------------------------------------------ metrics
    def _wip(self) -> int:
        return max(0, self.created - self.good_units - self.defect_units)

    def _utilization(self, rt: DeviceRuntime) -> float:
        return min(1.0, rt.busy_sec / max(1e-9, self.t))

    def _device_metrics(self) -> list[dict[str, Any]]:
        rows = []
        for rt in self.runtimes.values():
            device = rt.device
            busy = self._utilization(rt)
            energy = _finite(device.params.get("power_kw"), 0.1) * (
                rt.busy_sec + 0.1 * max(0.0, self.t - rt.busy_sec - rt.failed_sec)
            ) / 3600.0
            rows.append(
                {
                    "device_id": device.id,
                    "name": device.name,
                    "type": device.type,
                    "simulation_type": device.simulation_type,
                    "state": rt.state,
                    "state_since": round(rt.state_since, 2),
                    "utilization": round(busy, 4),
                    "busy_sec": round(rt.busy_sec, 2),
                    "blocked_sec": round(rt.blocked_sec, 2),
                    "starved_sec": round(rt.starved_sec, 2),
                    "failed_sec": round(rt.failed_sec, 2),
                    "failure_count": rt.failure_count,
                    "queue": len(rt.queue),
                    "occupancy": rt.occupancy(),
                    "capacity": rt.capacity,
                    "cycle_sec": round(rt.cycle_sec, 3),
                    "processed": rt.processed,
                    "good": rt.good,
                    "defect": rt.defect,
                    "done": rt.done,
                    "power_kw": _finite(device.params.get("power_kw"), 0.1),
                    "energy_kwh": round(energy, 4),
                    "last_update": now_iso(),
                }
            )
        rows.sort(key=lambda r: r["device_id"])
        return rows

    def _oee(self) -> dict[str, float]:
        metrics = self._device_metrics()
        production = [m for m in metrics if m["simulation_type"] in PRODUCTION_TYPES]
        if production:
            availability = sum(1.0 - (m["failed_sec"] / max(1e-9, self.t)) for m in production) / len(production)
        else:
            availability = 0.0
        theoretical = [3600.0 / max(1e-9, m["cycle_sec"]) for m in production if m["cycle_sec"] > 0]
        theoretical_uph = min(theoretical) if theoretical else 0.0
        actual = self.completed / max(1e-9, self.t) * 3600.0
        performance = min(1.0, actual / theoretical_uph) if theoretical_uph > 0 else 0.0
        quality = self.good_units / max(1, self.good_units + self.defect_units)
        availability = max(0.0, min(1.0, availability))
        return {
            "availability": round(availability, 4),
            "performance": round(performance, 4),
            "quality": round(quality, 4),
            "oee": round(availability * performance * quality, 4),
            "target_oee": 0.85,
        }

    def _bottleneck(self) -> tuple[Optional[str], str, str]:
        metrics = [m for m in self._device_metrics() if m["simulation_type"] in PRODUCTION_TYPES]
        if not metrics:
            return None, "—", "场景没有生产设备"
        ranked = sorted(
            metrics,
            key=lambda m: (m["utilization"], m["queue"], m["blocked_sec"]),
            reverse=True,
        )
        top = ranked[0]
        reason = (
            f"{top['name']} 利用率 {top['utilization'] * 100:.1f}%，队列 {top['queue']}，"
            f"阻塞 {top['blocked_sec']:.0f}s；节拍 {top['cycle_sec']:.2f}s。"
        )
        return top["device_id"], top["name"], reason

    def kpis(self) -> dict[str, Any]:
        oee = self._oee()
        runtime = max(1e-9, self.t)
        downtime = sum(rt.failed_sec for rt in self.runtimes.values())
        energy = sum(
            _finite(rt.device.params.get("power_kw"), 0.1) * rt.busy_sec / 3600.0 for rt in self.runtimes.values()
        ) + sum(
            _finite(rt.device.params.get("power_kw"), 0.1) * 0.1 * max(0.0, runtime - rt.busy_sec - rt.failed_sec) / 3600.0
            for rt in self.runtimes.values()
        )
        lead_times = self.completed_lead_times
        total_output = self.good_units + self.defect_units
        return {
            "total_output": total_output,
            "good_units": self.good_units,
            "defect_units": self.defect_units,
            "wip": self._wip(),
            "throughput_uph": round(self.completed / runtime * 3600.0, 2),
            "oee": oee,
            "first_pass_yield": round(self.good_units / max(1, total_output), 4),
            "defect_rate": round(self.defect_units / max(1, total_output), 4),
            "avg_lead_time_sec": round(sum(lead_times) / len(lead_times), 2) if lead_times else 0.0,
            "runtime_sec": round(runtime, 2),
            "downtime_sec": round(downtime, 2),
            "energy_kwh_total": round(energy, 4),
            "created_units": self.created,
        }

    def flow_insight(self) -> dict[str, Any]:
        metrics = self._device_metrics()
        bottleneck_id, bottleneck_name, reason = self._bottleneck()
        utilization = {m["device_id"]: m["utilization"] for m in metrics}
        rates = {
            m["device_id"]: round(m["processed"] / max(1e-9, self.t) * 3600.0, 2)
            for m in metrics
        }
        return {
            "bottleneck": bottleneck_id,
            "bottleneck_name": bottleneck_name,
            "utilization": utilization,
            "rates_uph": rates,
            "reason": reason,
        }

    def device_states(self) -> list[dict[str, Any]]:
        return [
            {
                "device_id": m["device_id"],
                "name": m["name"],
                "state": m["state"],
                "queue": m["queue"],
                "occupancy": m["occupancy"],
                "capacity": m["capacity"],
                "processed": m["processed"],
                "utilization": m["utilization"],
                "timestamp": now_iso(),
                "sim_time_sec": round(self.t, 2),
            }
            for m in self._device_metrics()
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "sim_id": self.sim_id,
            "scene_id": self.scene.scene_id,
            "scene_version": self.scene.version,
            "sim_time_sec": round(self.t, 3),
            "duration_sec": self.duration_sec,
            "kpis": self.kpis(),
            "device_states": self.device_states(),
            "device_metrics": self._device_metrics(),
            "flow_insight": self.flow_insight(),
            "event_count": len(self.events),
            "timestamp": now_iso(),
        }

    def run(self, duration_sec: Optional[float] = None) -> None:
        target = float(duration_sec if duration_sec is not None else self.duration_sec)
        steps = int(math.ceil((target - self.t) / self.tick_sec))
        for _ in range(max(0, steps)):
            self.step(self.tick_sec)
            if self.t + 1e-9 >= target:
                break
        self.duration_sec = target
        self._record_minute()

    def build_record(self, *, persist: bool = True, label: Optional[str] = None) -> dict[str, Any]:
        kpis = self.kpis()
        insight = self.flow_insight()
        device_metrics = self._device_metrics()
        bottleneck_id, bottleneck_name, reason = self._bottleneck()
        event_stats = {
            "total": len(self.events),
            "failures": sum(1 for e in self.events if "FAIL" in e["event_type"] or "FAULT" in e["event_type"]),
            "inspection_events": sum(1 for e in self.events if e["event_type"] in ("INSPECTION_PASS", "INSPECTION_FAIL")),
            "blocked_events": sum(1 for e in self.events if e["event_type"] == "BLOCKED"),
            "starved_events": sum(1 for e in self.events if e["event_type"] == "STARVED"),
        }
        scene_snapshot = {
            "scene_id": self.scene.scene_id,
            "version": self.scene.version,
            "name": self.scene.name,
            "devices": [d.to_dict() for d in self.scene.devices],
            "connections": [c.to_dict() for c in self.scene.connections],
            "simulation_settings": self.scene.simulation_settings,
        }
        param_payload = {
            "devices": [{"id": d.id, "params": d.params, "position": d.position, "rotation": d.rotation} for d in self.scene.devices],
            "connections": [c.to_dict() for c in self.scene.connections],
        }
        record = {
            "schema_version": "2.0",
            "experiment_id": self.sim_id,
            "project_id": self.scene.project_id,
            "scene_id": self.scene.scene_id,
            "scene_version": self.scene.version,
            "kind": "factory",
            "source": "scene_engine",
            "factory_engine": "scene_discrete_event",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": label or f"场景仿真 {self.sim_id}",
            "config": {
                "duration_sec": self.duration_sec,
                "duration_min": round(self.duration_sec / 60.0, 3),
                "seed": self.seed,
                "tick_sec": self.tick_sec,
                "scene_hash": content_hash(self.scene),
                "scene_snapshot": scene_snapshot,
            },
            "param_hash": hashlib.sha256(json.dumps(param_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16],
            "duration_min": round(self.duration_sec / 60.0, 3),
            "kpis": kpis,
            "window_metrics": {
                "minutes": len(self.flow_history),
                "good_units": kpis["good_units"],
                "defect_units": kpis["defect_units"],
                "avg_throughput_uph": kpis["throughput_uph"],
                "avg_oee": kpis["oee"]["oee"],
            },
            "flow_history": self.flow_history,
            "cell_flow_history": [],
            "flow_insight": insight,
            "device_metrics": device_metrics,
            "station_metrics": [],
            "cell_metrics": [],
            "device_states": self.device_states(),
            "orders": [],
            "amr_kpis": {},
            "recent_inspections": self.recent_inspections,
            "events": self.events,
            "event_stats": event_stats,
            "snapshot": {
                "sim_id": self.sim_id,
                "scene_id": self.scene.scene_id,
                "scene_version": self.scene.version,
                "state": {"devices": device_metrics},
            },
            "assumptions": [
                "全部指标由本次 Python 离散事件仿真实际计算，属于软件仿真结果，不是真实工厂实测。",
                "设备节拍、故障率、维修时间、能耗为可编辑的仿真假设参数。",
                "conveyor 按单服务台 + 有限队列建模，节拍 = 长度 / 速度；不是 Jolt 连续物理。",
                "相同场景、相同 seed、相同仿真时长会得到相同结果。",
            ],
            "wall_time_ms": round((time.perf_counter() - self._started) * 1000.0, 1),
        }
        if persist:
            save_scene_experiment(self.scene.project_id, record)
        return record


def run_scene_simulation(
    scene: SceneDocument,
    *,
    duration_sec: Optional[float] = None,
    seed: Optional[int] = None,
    tick_sec: Optional[float] = None,
    persist: bool = True,
    label: Optional[str] = None,
    experiment_id: Optional[str] = None,
    engine: Optional[SceneFactoryEngine] = None,
) -> dict[str, Any]:
    """离线批处理运行（确定性、可复现）。"""
    engine = engine or SceneFactoryEngine(scene, duration_sec=duration_sec, seed=seed, tick_sec=tick_sec, sim_id=experiment_id)
    engine.run(duration_sec)
    return engine.build_record(persist=persist, label=label)


def create_factory_from_config(scene: SceneDocument, **kwargs: Any) -> SceneFactoryEngine:
    """对外统一入口：由场景配置创建 Python 仿真工厂。"""
    return SceneFactoryEngine(scene, **kwargs)
