"""统一场景/设备/连接数据模型。

设计原则：
- OIP 的设备数据不直接进入仿真代码；先映射为本 Schema；
- 空间关系（OIP scene）与业务关系（connections）分开保存；
- 设备 ID 来自 OIP 的 part id，转换过程保持稳定；
- 所有字段可 JSON 序列化，便于版本快照与实验追溯。
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

SCENE_SCHEMA_VERSION = "1.0"
CONNECTION_TYPES = ("material_flow", "control", "data", "power")
SIMULATION_TYPES = (
    "conveyor",
    "inspection",
    "robot",
    "spawner",
    "despawner",
    "buffer",
    "display_only",
)
SCENE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_scene_id() -> str:
    return f"SCN-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}"


def new_connection_id() -> str:
    return f"conn_{uuid.uuid4().hex[:10]}"


@dataclass
class Port:
    """设备端口。position/offset 为设备本地坐标（米），方向为本地 XZ 单位向量。"""

    id: str
    name: str
    direction: str  # input | output
    flow: str = "material"
    offset: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    out: Optional[list[float]] = None  # 本地方向 [x, z]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Port":
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            direction=str(raw.get("direction") or "input"),
            flow=str(raw.get("flow") or "material"),
            offset=[float(v) for v in (raw.get("offset") or [0, 0, 0])],
            out=([float(v) for v in raw["out"]] if raw.get("out") is not None else None),
        )


@dataclass
class Device:
    """统一设备。type 为语义类别，simulation_type 为引擎处理类型。"""

    id: str
    type: str
    name: str
    position: list[float]
    rotation: dict[str, float] = field(default_factory=lambda: {"yaw": 0.0, "pitch": 0.0, "roll": 0.0})
    dimensions: dict[str, float] = field(default_factory=lambda: {"length": 1.0, "width": 1.0, "height": 1.0})
    params: dict[str, Any] = field(default_factory=dict)
    simulation_type: str = "display_only"
    ports: list[Port] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ports"] = [p.to_dict() for p in self.ports]
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Device":
        rotation = raw.get("rotation") or {}
        if isinstance(rotation, (int, float)):
            rotation = {"yaw": float(rotation)}
        return cls(
            id=str(raw["id"]),
            type=str(raw.get("type") or "unknown"),
            name=str(raw.get("name") or raw["id"]),
            position=[float(v) for v in raw["position"]],
            rotation={
                "yaw": float(rotation.get("yaw", 0.0)),
                "pitch": float(rotation.get("pitch", 0.0)),
                "roll": float(rotation.get("roll", 0.0)),
            },
            dimensions={
                "length": float((raw.get("dimensions") or {}).get("length", 1.0)),
                "width": float((raw.get("dimensions") or {}).get("width", 1.0)),
                "height": float((raw.get("dimensions") or {}).get("height", 1.0)),
            },
            params=dict(raw.get("params") or {}),
            simulation_type=str(raw.get("simulation_type") or "display_only"),
            ports=[Port.from_dict(p) for p in (raw.get("ports") or [])],
            metadata=dict(raw.get("metadata") or {}),
        )

    def yaw(self) -> float:
        return float(self.rotation.get("yaw", 0.0))

    def world_point(self, local: list[float]) -> list[float]:
        """本地 XZ 偏移 → 世界坐标（OIP rotationY：+X 转向 -Z）。"""
        yaw = self.yaw()
        c, s = math.cos(yaw), math.sin(yaw)
        x, y, z = float(local[0]), float(local[1]), float(local[2])
        return [
            self.position[0] + x * c + z * s,
            self.position[1] + y,
            self.position[2] - x * s + z * c,
        ]

    def world_direction(self, local: Optional[list[float]]) -> Optional[list[float]]:
        if local is None:
            return None
        yaw = self.yaw()
        c, s = math.cos(yaw), math.sin(yaw)
        return [local[0] * c + local[1] * s, -local[0] * s + local[1] * c]


@dataclass
class Connection:
    """显式业务连接。空间吸附候选另存于 SceneDocument.geometry_candidates。"""

    id: str
    source_device: str
    source_port: str
    target_device: str
    target_port: str
    connection_type: str = "material_flow"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Connection":
        return cls(
            id=str(raw.get("id") or new_connection_id()),
            source_device=str(raw["source_device"]),
            source_port=str(raw.get("source_port") or "out"),
            target_device=str(raw["target_device"]),
            target_port=str(raw.get("target_port") or "in"),
            connection_type=str(raw.get("connection_type") or "material_flow"),
            metadata=dict(raw.get("metadata") or {}),
        )


@dataclass
class ValidationIssue:
    severity: str  # error | warning
    code: str
    message: str
    devices: list[str] = field(default_factory=list)
    connection_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneDocument:
    schema_version: str
    scene_id: str
    project_id: str
    name: str
    version: int = 1
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    devices: list[Device] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    geometry_candidates: list[Connection] = field(default_factory=list)
    validation: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"errors": [], "warnings": []})
    simulation_settings: dict[str, Any] = field(
        default_factory=lambda: {"duration_sec": 1800, "seed": 42, "tick_sec": 0.25}
    )
    source: dict[str, Any] = field(default_factory=dict)
    oip_project: Optional[dict[str, Any]] = None  # 原始 OIP 工程，用于还原编辑器
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scene_id": self.scene_id,
            "project_id": self.project_id,
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "devices": [d.to_dict() for d in self.devices],
            "connections": [c.to_dict() for c in self.connections],
            "geometry_candidates": [c.to_dict() for c in self.geometry_candidates],
            "validation": self.validation,
            "simulation_settings": self.simulation_settings,
            "source": self.source,
            "oip_project": self.oip_project,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SceneDocument":
        return cls(
            schema_version=str(raw.get("schema_version") or SCENE_SCHEMA_VERSION),
            scene_id=str(raw["scene_id"]),
            project_id=str(raw["project_id"]),
            name=str(raw.get("name") or raw["scene_id"]),
            version=int(raw.get("version") or 1),
            created_at=str(raw.get("created_at") or now_iso()),
            updated_at=str(raw.get("updated_at") or now_iso()),
            devices=[Device.from_dict(d) for d in (raw.get("devices") or [])],
            connections=[Connection.from_dict(c) for c in (raw.get("connections") or [])],
            geometry_candidates=[Connection.from_dict(c) for c in (raw.get("geometry_candidates") or [])],
            validation=dict(raw.get("validation") or {"errors": [], "warnings": []}),
            simulation_settings=dict(raw.get("simulation_settings") or {}),
            source=dict(raw.get("source") or {}),
            oip_project=raw.get("oip_project"),
            metadata=dict(raw.get("metadata") or {}),
        )

    def device_map(self) -> dict[str, Device]:
        return {d.id: d for d in self.devices}
