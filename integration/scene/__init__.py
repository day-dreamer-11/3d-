"""自建工厂场景：OIP 场景 → 统一设备模型 → Python 离散事件仿真。

该包不修改 RFTwin/EdgeCoder 核心，只增加集成层：
- models：统一设备、端口、连接、场景文档；
- mapping：OIP 部件 → 统一设备与仿真类型；
- connections：由几何生成“候选连接”，以及显式连接表校验；
- store：场景版本、原子写入、实验快照；
- engine：通用离散事件仿真引擎（生产仿真权威）；
- edge/analysis/report：EdgeCoder、规则式 AI 分析、报告；
- api：FastAPI 业务接口；oip_service：OIP 编辑器进程管理。
"""

from .models import (
    CONNECTION_TYPES,
    SCENE_SCHEMA_VERSION,
    Connection,
    Device,
    Port,
    SceneDocument,
    ValidationIssue,
    new_connection_id,
    new_scene_id,
    now_iso,
)

__all__ = [
    "CONNECTION_TYPES",
    "SCENE_SCHEMA_VERSION",
    "Connection",
    "Device",
    "Port",
    "SceneDocument",
    "ValidationIssue",
    "new_connection_id",
    "new_scene_id",
    "now_iso",
]
