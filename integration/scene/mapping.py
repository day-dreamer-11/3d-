"""OIP 场景 → 统一设备 Schema 的映射层。

每种设备类型有独立的映射逻辑；不支持的部件不会被静默忽略：
- 建筑/分组/注释/货物：记录为“非设备”，不进入仿真；
- 未知设备：保留为 display_only（仅三维展示），并产生明确 warning；
- 已知设备：映射为 conveyor / inspection / robot / spawner / despawner。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .models import Device, Port, ValidationIssue

# OIP 部件包 → (语义类型, 引擎类型, 默认名称, 参数映射函数名)
DEVICE_REGISTRY: dict[str, tuple[str, str, str, str]] = {
    # 输送
    "oip.roller-conveyor": ("conveyor", "conveyor", "Roller Conveyor", "conveyor"),
    "oip.belt-conveyor": ("conveyor", "conveyor", "Belt Conveyor", "conveyor"),
    "oip.curved-conveyor": ("conveyor", "conveyor", "Curved Belt Conveyor", "conveyor"),
    "oip.curved-roller-conveyor": ("conveyor", "conveyor", "Curved Roller Conveyor", "conveyor"),
    "oip.flex-conveyor": ("conveyor", "conveyor", "Flex Conveyor", "conveyor"),
    "oip.spur-conveyor": ("conveyor", "conveyor", "Spur Belt Conveyor", "conveyor"),
    "oip.spur-roller-conveyor": ("conveyor", "conveyor", "Spur Roller Conveyor", "conveyor"),
    "oip.turntable-conveyor": ("conveyor", "conveyor", "Turntable Conveyor", "conveyor"),
    # 检测
    "oip.diffuse-sensor": ("inspection", "inspection", "Photo Eye", "inspection"),
    "oip.laser-sensor": ("inspection", "inspection", "Laser Sensor", "inspection"),
    "oip.color-sensor": ("inspection", "inspection", "Color Sensor", "inspection"),
    "oip.barcode-scanner": ("inspection", "inspection", "Barcode Scanner", "inspection"),
    "oip.light-curtain": ("inspection", "inspection", "Light Curtain", "inspection"),
    # 机器人 / 搬运
    "oip.six-axis-robot": ("robot", "robot", "Six-Axis Robot", "robot"),
    "oip.gantry": ("robot", "robot", "Gantry", "robot"),
    "oip.agv": ("robot", "robot", "AGV", "robot"),
    # 产品源 / 出口
    "oip.box-spawner": ("spawner", "spawner", "Box Spawner", "spawner"),
    "oip.despawner": ("despawner", "despawner", "Despawner", "despawner"),
}

CARGO_PACKAGES = {"oip.box", "oip.pallet", "oip.unit-load", "oip.drum", "oip.ibc-tote", "oip.tote"}
# 外部导入的装饰件以「装饰」命名（如「装饰·工具车」）：仅三维展示，不参与物料流，也不报未映射警告。
DECORATION_PREFIX = "装饰"
DECORATION_CATEGORY = "装饰"
IGNORED_TYPES = {"building", "group", "annotation"}

DEFAULT_SIM_PARAMS: dict[str, dict[str, Any]] = {
    "conveyor": {
        "speed_mps": 0.5,
        "item_spacing_m": 0.6,
        "power_kw": 0.6,
        "failure_prob_per_hour": 0.4,
        "mttr_min": 2.0,
    },
    "inspection": {
        "cycle_sec": 2.0,
        "pass_rate": 0.95,
        "accuracy": 0.98,
        "capacity": 1,
        "power_kw": 0.8,
        "failure_prob_per_hour": 0.3,
        "mttr_min": 1.5,
    },
    "robot": {
        "cycle_sec": 4.0,
        "reach_m": 0.94,
        "payload_kg": 10.0,
        "capacity": 1,
        "power_kw": 2.5,
        "failure_prob_per_hour": 0.5,
        "mttr_min": 3.0,
    },
    "spawner": {
        "interval_sec": 4.0,
        "batch_size": 1,
        "total_limit": 0,
        "capacity": 1,
        "power_kw": 0.3,
        "failure_prob_per_hour": 0.1,
        "mttr_min": 1.0,
    },
    "despawner": {
        "capacity": 100000,
        "power_kw": 0.1,
        "failure_prob_per_hour": 0.0,
        "mttr_min": 0.0,
    },
}

# 这些字段由 OIP 部件参数直接推导；重导入时覆盖。
OIP_DERIVED_FIELDS = {
    "length_m",
    "width_m",
    "speed_mps",
    "boxes_per_minute",
    "product_type",
    "deck_angle_deg",
}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _conveyor_params(part: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    params = dict(base)
    raw = part.get("params") or {}
    length = _num(raw.get("length"), 4.0)
    width = _num(raw.get("width"), 1.524)
    if bool(raw.get("fpmUnits", False)):
        speed = _num(raw.get("speedFpm"), 197.0) / 196.85
    else:
        speed = _num(raw.get("speed"), 1.0)
    params.update(
        {
            "length_m": round(max(0.1, length), 4),
            "width_m": round(max(0.1, width), 4),
            "speed_mps": round(max(0.01, abs(speed)), 4),
            "direction": -1 if speed < 0 else 1,
            "deck_angle_deg": _num(raw.get("deckAngleDeg"), 0.0),
        }
    )
    spacing = _num(params.get("item_spacing_m"), 0.6) or 0.6
    params["capacity"] = max(1, int(math.ceil(params["length_m"] / spacing)))
    return params


def _inspection_params(part: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    params = dict(base)
    raw = part.get("params") or {}
    if "maxRange" in raw:
        params.setdefault("range_m", _num(raw.get("maxRange"), 2.0))
    package = str(part.get("packageId") or "")
    if package.endswith("barcode-scanner"):
        params.setdefault("pass_rate", 0.99)
    params.setdefault("inspection_kind", package.split(".")[-1])
    return params


def _robot_params(part: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    params = dict(base)
    raw = part.get("params") or {}
    scale = _num(raw.get("scale"), 1.0)
    if scale > 0:
        params["reach_m"] = round(0.94 * scale, 4)
    if "speed" in raw and "oip.six-axis-robot" in str(part.get("packageId")):
        # OIP 机器人 speed 为关节速度（deg/s 量级），不能直接当节拍；保留原始值供追溯。
        params["oip_speed"] = _num(raw.get("speed"), 120.0)
    if "frameLength" in raw:
        params["frame_length_m"] = _num(raw.get("frameLength"), 4.0)
    return params


def _spawner_params(part: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    params = dict(base)
    raw = part.get("params") or {}
    bpm = _num(raw.get("boxesPerMinute"), 45.0)
    bpm = min(3600.0, max(1.0 / 60.0, bpm))
    params.update(
        {
            "boxes_per_minute": round(bpm, 4),
            "interval_sec": round(60.0 / bpm, 4),
            "product_type": str(raw.get("product") or "oip.box"),
        }
    )
    return params


def _despawner_params(part: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    params = dict(base)
    raw = part.get("params") or {}
    params["count_only"] = bool(raw.get("countOnly", False))
    return params


PARAM_MAPPERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "conveyor": _conveyor_params,
    "inspection": _inspection_params,
    "robot": _robot_params,
    "spawner": _spawner_params,
    "despawner": _despawner_params,
}


def _ports_for(sim_type: str, params: dict[str, Any]) -> list[Port]:
    if sim_type == "conveyor":
        half = float(params.get("length_m", 4.0)) / 2.0
        return [
            Port("in", "Input", "input", offset=[-half, 0.0, 0.0], out=[-1.0, 0.0]),
            Port("out", "Output", "output", offset=[half, 0.0, 0.0], out=[1.0, 0.0]),
        ]
    if sim_type == "spawner":
        return [Port("out", "Output", "output", offset=[0.0, 0.0, 0.0], out=[1.0, 0.0])]
    if sim_type == "despawner":
        return [Port("in", "Input", "input", offset=[0.0, 0.0, 0.0], out=[-1.0, 0.0])]
    if sim_type in ("inspection", "robot"):
        return [
            Port("in", "Input", "input", offset=[0.0, 0.0, 0.0], out=None),
            Port("out", "Output", "output", offset=[0.0, 0.0, 0.0], out=None),
        ]
    if sim_type == "buffer":
        return [
            Port("in", "Input", "input", offset=[0.0, 0.0, 0.0], out=None),
            Port("out", "Output", "output", offset=[0.0, 0.0, 0.0], out=None),
        ]
    return []


def _dimensions(sim_type: str, params: dict[str, Any]) -> dict[str, float]:
    if sim_type == "conveyor":
        return {
            "length": float(params.get("length_m", 4.0)),
            "width": float(params.get("width_m", 1.524)),
            "height": 0.9,
        }
    if sim_type == "inspection":
        return {"length": 0.15, "width": 0.15, "height": 0.3}
    if sim_type == "robot":
        return {"length": 1.88, "width": 0.83, "height": 3.49}
    if sim_type == "spawner":
        return {"length": 0.5, "width": 0.5, "height": 0.96}
    if sim_type == "despawner":
        return {"length": 2.0, "width": 2.0, "height": 1.0}
    return {"length": 0.5, "width": 0.5, "height": 0.5}


@dataclass
class MappingResult:
    devices: list[Device]
    issues: list[ValidationIssue]
    ignored_parts: list[dict[str, Any]]
    defaults_applied: dict[str, list[str]]


def extract_scene(project: dict[str, Any]) -> dict[str, Any]:
    """接受 OIP ProjectJSON 或单独的 SceneJSON。"""
    if not isinstance(project, dict):
        raise ValueError("OIP 工程必须是 JSON 对象")
    scene = project.get("scene") if isinstance(project.get("scene"), dict) else project
    if not isinstance(scene, dict) or not isinstance(scene.get("parts"), list):
        raise ValueError("OIP 工程缺少 scene.parts 数组")
    return scene


def map_oip_project(
    project: dict[str, Any],
    *,
    existing_devices: Optional[list[Device]] = None,
) -> MappingResult:
    """把 OIP 工程映射为统一设备；保留已有设备的仿真专属参数。"""
    scene = extract_scene(project)
    packages = {str(p.get("id") or ""): p for p in project.get("packages") or [] if isinstance(p, dict)}
    existing = {d.id: d for d in (existing_devices or [])}
    devices: list[Device] = []
    issues: list[ValidationIssue] = []
    ignored: list[dict[str, Any]] = []
    defaults_applied: dict[str, list[str]] = {}

    for part in scene["parts"]:
        part_id = str(part.get("id") or "")
        part_type = str(part.get("type") or "")
        package_id = str(part.get("packageId") or "")
        if not part_id:
            issues.append(ValidationIssue("warning", "part_without_id", "OIP 场景中存在没有 id 的部件，已忽略"))
            continue
        if part_type in IGNORED_TYPES:
            ignored.append({"id": part_id, "type": part_type, "packageId": package_id, "reason": "建筑/分组/注释不是仿真设备"})
            continue
        if package_id in CARGO_PACKAGES:
            ignored.append({"id": part_id, "type": part_type, "packageId": package_id, "reason": "货物模型不是生产设备"})
            continue
        if part_type != "part" or not package_id:
            ignored.append({"id": part_id, "type": part_type, "packageId": package_id, "reason": "不是 SDK 部件实例"})
            continue

        spec = DEVICE_REGISTRY.get(package_id)
        position_raw = part.get("position") or [0.0, 0.0, 0.0]
        try:
            position = [float(v) for v in position_raw][:3]
        except (TypeError, ValueError):
            position = [0.0, 0.0, 0.0]
        while len(position) < 3:
            position.append(0.0)
        yaw = _num(part.get("rotationY"), 0.0)

        if spec is None:
            package = packages.get(package_id) or {}
            package_name = str(package.get("name") or "")
            decoration = (
                str(part.get("name") or "").startswith(DECORATION_PREFIX)
                or str(package.get("category") or "") == DECORATION_CATEGORY
                or package_name.startswith(DECORATION_PREFIX)
            )
            device = Device(
                id=part_id,
                type="decoration" if decoration else "unknown",
                name=str(part.get("name") or package_name or package_id or part_id),
                position=position,
                rotation={"yaw": yaw, "pitch": _num(part.get("rotationX"), 0.0), "roll": _num(part.get("rotationZ"), 0.0)},
                dimensions={"length": 0.5, "width": 0.5, "height": 0.5},
                params={},
                simulation_type="display_only",
                ports=[],
                metadata={
                    "oip_package_id": package_id,
                    "oip_part_type": part_type,
                    "unsupported_reason": "装饰模型（仅三维展示）" if decoration else f"暂未映射的 OIP 部件类型：{package_id}",
                    **({"decoration": True} if decoration else {}),
                },
            )
            devices.append(device)
            if not decoration:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "unsupported_device",
                        f"设备 {part_id}（{package_id}）暂未映射到 Python 模型，标记为“仅三维展示”，不能参与物料流。",
                        devices=[part_id],
                    )
                )
            continue

        semantic_type, sim_type, default_name, mapper_key = spec
        previous = existing.get(part_id)
        base: dict[str, Any] = {}
        if previous is not None and previous.simulation_type == sim_type:
            base = copy.deepcopy(previous.params)
            for key in OIP_DERIVED_FIELDS:
                base.pop(key, None)
        defaults = DEFAULT_SIM_PARAMS.get(sim_type, {})
        applied: list[str] = []
        for key, value in defaults.items():
            if key not in base:
                base[key] = copy.deepcopy(value)
                applied.append(key)
        params = PARAM_MAPPERS[mapper_key](part, base)
        params.setdefault("simulation_notes", "")
        defaults_applied[part_id] = applied

        ports = _ports_for(sim_type, params)
        device = Device(
            id=part_id,
            type=semantic_type,
            name=str(part.get("name") or default_name),
            position=position,
            rotation={"yaw": yaw, "pitch": _num(part.get("rotationX"), 0.0), "roll": _num(part.get("rotationZ"), 0.0)},
            dimensions=_dimensions(sim_type, params),
            params=params,
            simulation_type=sim_type,
            ports=ports,
            metadata={
                "oip_package_id": package_id,
                "oip_part_type": part_type,
                "oip_part_name": part.get("name"),
                "defaults_applied": applied,
                "source": "oip_mapping",
            },
        )
        devices.append(device)

    return MappingResult(devices=devices, issues=issues, ignored_parts=ignored, defaults_applied=defaults_applied)


def bridge_ports(device: Device) -> list[dict[str, Any]]:
    """给前端的三维状态/连接编辑用：端口世界坐标。"""
    result = []
    for port in device.ports:
        result.append(
            {
                "id": port.id,
                "name": port.name,
                "direction": port.direction,
                "flow": port.flow,
                "world": device.world_point(port.offset),
                "out": device.world_direction(port.out),
            }
        )
    return result
