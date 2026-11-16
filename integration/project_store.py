"""工程项目与实验结果的持久化（JSON / SQLite 双后端）。

设计约束：
- 默认 JSON：每个项目一个目录，项目配置与实验记录均为可读 JSON，原子写入；
- 桌面版可用 SQLite（`AIC_STORAGE=sqlite`），首次使用自动导入现有 JSON 数据；
- 所有 ID 经过白名单校验，防止路径穿越；
- 项目配置是仿真的唯一输入来源，实验记录保存参数哈希与 RFTwin provenance，便于追溯。
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from integration.paths import DATA_DIR, PROJECTS_DIR, ensure_data_dirs
from models.defaults import default_config

PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
EXPERIMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$")
SCHEMA_VERSION = "1.0"

CELL_LABELS = {
    "welding": "焊接工位",
    "assembly": "装配工位",
    "machine_tending": "机加工工位",
    "vision_inspection": "视觉检测工位",
}
CELL_STATION_COUNTS = {
    "welding": (4, 4),
    "assembly": (4, 4),
    "machine_tending": (2, 2),
    "vision_inspection": (2, 2),
}
BUFFER_LABELS = {
    "raw_material": "原材料库存",
    "welding_to_assembly": "焊接 → 装配缓冲区",
    "assembly_to_machining": "装配 → 机加工缓冲区",
    "machining_to_inspection": "机加工 → 视觉检测缓冲区",
    "rework_area": "返修区",
}
BUFFER_DEFAULTS = {
    "raw_material": 60,
    "welding_to_assembly": 8,
    "assembly_to_machining": 8,
    "machining_to_inspection": 6,
    "rework_area": 10,
}
DEFAULT_CELL_CYCLES = {
    "welding": 40.0,
    "assembly": 40.0,
    "machine_tending": 45.0,
    "vision_inspection": 38.0,
}

PROCESS_ROUTE = ["焊接", "装配", "机加工", "视觉检测", "成品入库"]


class ProjectError(ValueError):
    """工程配置或实验记录不合法。"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_project_id() -> str:
    return f"PRJ-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def new_experiment_id(prefix: str = "EXP") -> str:
    return f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}"


def _edge_defaults() -> dict[str, Any]:
    base = default_config()
    nodes = []
    costs = {"local": 0.02, "edge": 0.06, "cloud": 0.03}
    for node in base.nodes:
        nodes.append(
            {
                "node_id": node.node_id,
                "display_name": node.display_name,
                "node_type": node.node_type,
                "compute_gflops": node.compute_gflops,
                "base_load": node.base_load,
                "max_task_gflops": node.max_task_gflops,
                "max_queue_slots": node.max_queue_slots,
                "bandwidth_mbps": node.link.bandwidth_mbps,
                "latency_ms": node.link.latency_ms,
                "congestion": node.link.congestion,
                "cost_per_gflop": costs.get(node.node_type, 0.05),
            }
        )
    return {
        "seed": 42,
        "nodes": nodes,
        "mapping": {
            "data_size_mb": 4.0,
            "compute_demand_gflops": 3.0,
            "deadline_ms": 800.0,
            "deadline_jitter_ms": 150.0,
            "arrival_interval_ms": 180.0,
            "network_jitter_ms": 6.0,
            "result_size_mb": 0.02,
            "max_tasks": 80,
            "failed_compute_factor": 1.25,
            "compute_variation": 0.15,
        },
    }


def default_project(
    name: str = "示例智能工厂项目",
    description: str = "基于 RFTwin 机器人产线的工业视觉检测与边缘计算联合仿真项目。",
    goal: str = "识别产线瓶颈，并在本地 / 边缘 / 云端之间优化检测任务调度。",
) -> dict[str, Any]:
    """返回一份完整的默认项目配置（不含持久化）。"""
    timestamp = _now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": new_project_id(),
        "name": name,
        "description": description,
        "goal": goal,
        "created_at": timestamp,
        "updated_at": timestamp,
        "line": {
            "product_id": "PRODUCT-A",
            "product_name": "工业产品 A（RFTwin 默认产品）",
            "target_quantity": 1100,
            "shift_start": "06:00",
            "shift_length_hours": 8,
            "process_route": list(PROCESS_ROUTE),
            "priority_policy": "PRIORITY_THEN_FIFO",
        },
        "factory": {
            "seed": 42,
            "duration_min": 30,
            "cells": {
                key: {
                    "display_name": CELL_LABELS[key],
                    "cycle_sec": DEFAULT_CELL_CYCLES[key],
                    "stations": CELL_STATION_COUNTS[key][0],
                    "robots": CELL_STATION_COUNTS[key][1],
                }
                for key in CELL_LABELS
            },
            "buffers": {
                key: {"display_name": BUFFER_LABELS[key], "capacity": capacity}
                for key, capacity in BUFFER_DEFAULTS.items()
            },
            "vision_fail_probability": 0.0068,
            "minor_stop_probability": 0.015,
            "amr_units": 2,
        },
        "edge": _edge_defaults(),
    }


def _project_dir(project_id: str, base_dir: Optional[Path] = None) -> Path:
    if not PROJECT_ID_PATTERN.match(project_id or ""):
        raise ProjectError(f"非法的项目 ID：{project_id!r}")
    root = Path(base_dir) if base_dir is not None else PROJECTS_DIR
    return root / project_id


def _project_file(project_id: str, base_dir: Optional[Path] = None) -> Path:
    return _project_dir(project_id, base_dir) / "project.json"


def _json_sanitize(value: Any) -> Any:
    """把 NaN / Infinity 转为 null，保证写出的是标准 JSON。"""
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {key: _json_sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_sanitize(item) for item in value]
    return value


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_sanitize(payload)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _storage_mode() -> str:
    return os.environ.get("AIC_STORAGE", "json").strip().lower() or "json"


def _sqlite_db_path(base_dir: Optional[Path] = None) -> Path:
    if base_dir is not None:
        return Path(base_dir) / "aic_factory.db"
    configured = os.environ.get("AIC_SQLITE_PATH", "").strip()
    return Path(configured) if configured else DATA_DIR / "aic_factory.db"


def _sqlite_connect(base_dir: Optional[Path] = None) -> sqlite3.Connection:
    path = _sqlite_db_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projects ("
        "project_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, data TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS experiments ("
        "experiment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, "
        "created_at TEXT NOT NULL, data TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_project_kind ON experiments(project_id, kind)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    _sqlite_import_json(conn, base_dir)
    return conn


def _sqlite_import_json(conn: sqlite3.Connection, base_dir: Optional[Path] = None) -> None:
    """首次使用 SQLite 时导入现有 JSON 项目与实验；幂等、跳过损坏文件。"""
    done = conn.execute("SELECT value FROM meta WHERE key='json_imported'").fetchone()
    if done is not None:
        return
    # base_dir 在测试/嵌入场景中即“项目根目录”；默认全局路径是 DATA_DIR/projects。
    root = Path(base_dir) if base_dir is not None else PROJECTS_DIR
    if root.exists():
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            project_path = directory / "project.json"
            if project_path.exists():
                try:
                    project = json.loads(project_path.read_text(encoding="utf-8"))
                    project_id = str(project.get("project_id", ""))
                    if PROJECT_ID_PATTERN.match(project_id):
                        conn.execute(
                            "INSERT OR IGNORE INTO projects(project_id, updated_at, data) VALUES(?,?,?)",
                            (
                                project_id,
                                str(project.get("updated_at") or ""),
                                json.dumps(_json_sanitize(project), ensure_ascii=False),
                            ),
                        )
                except (OSError, json.JSONDecodeError, TypeError):
                    pass
            for experiment_path in (directory / "experiments").glob("*.json"):
                try:
                    record = json.loads(experiment_path.read_text(encoding="utf-8"))
                    experiment_id = str(record.get("experiment_id", ""))
                    project_id = str(record.get("project_id", ""))
                    kind = str(record.get("kind", ""))
                    if (
                        EXPERIMENT_ID_PATTERN.match(experiment_id)
                        and PROJECT_ID_PATTERN.match(project_id)
                        and kind in ("factory", "edge", "live")
                    ):
                        conn.execute(
                            "INSERT OR IGNORE INTO experiments"
                            "(experiment_id, project_id, kind, created_at, data) VALUES(?,?,?,?,?)",
                            (
                                experiment_id,
                                project_id,
                                kind,
                                str(record.get("created_at") or ""),
                                json.dumps(_json_sanitize(record), ensure_ascii=False),
                            ),
                        )
                except (OSError, json.JSONDecodeError, TypeError):
                    pass
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('json_imported','1')")
    conn.commit()


def migrate_json_to_sqlite(base_dir: Optional[Path] = None) -> dict[str, int]:
    """显式导入 JSON 数据（桌面版首次启动或运维迁移使用）。"""
    with closing(_sqlite_connect(base_dir)) as conn:
        conn.execute("DELETE FROM meta WHERE key='json_imported'")
        _sqlite_import_json(conn, base_dir)
        projects = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        experiments_count = conn.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"]
    return {"projects": int(projects), "experiments": int(experiments_count)}


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectError(f"{label} 必须为数值")
    value = float(value)
    if not low <= value <= high:
        raise ProjectError(f"{label} 必须在 [{low}, {high}] 范围内，当前为 {value}")
    return value


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectError(f"{label} 必须为整数")
    if not low <= value <= high:
        raise ProjectError(f"{label} 必须在 [{low}, {high}] 范围内，当前为 {value}")
    return value


def validate_project(project: dict[str, Any]) -> None:
    """校验项目配置；失败抛出带中文说明的 ProjectError。"""
    if not isinstance(project, dict):
        raise ProjectError("项目配置必须是 JSON 对象")
    if not PROJECT_ID_PATTERN.match(str(project.get("project_id", ""))):
        raise ProjectError("项目 ID 缺失或格式非法")
    name = str(project.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ProjectError("项目名称必须为 1~80 个字符")
    if len(str(project.get("description", ""))) > 1000:
        raise ProjectError("项目描述不能超过 1000 个字符")
    if len(str(project.get("goal", ""))) > 500:
        raise ProjectError("项目目标不能超过 500 个字符")

    line = project.get("line") or {}
    if not isinstance(line, dict):
        raise ProjectError("line 配置必须是对象")
    product_id = str(line.get("product_id", "")).strip()
    if not product_id or len(product_id) > 64:
        raise ProjectError("产品编号必须为 1~64 个字符")
    if not str(line.get("product_name", "")).strip():
        raise ProjectError("产品名称不能为空")
    _integer(line.get("target_quantity"), "生产目标数量", 1, 100_000)
    _integer(line.get("shift_length_hours"), "班次长度（小时）", 1, 24)

    factory = project.get("factory") or {}
    if not isinstance(factory, dict):
        raise ProjectError("factory 配置必须是对象")
    _integer(factory.get("seed"), "工厂随机种子", 0, 2_147_483_647)
    _integer(factory.get("duration_min"), "默认仿真时长（分钟）", 1, 180)
    cells = factory.get("cells") or {}
    if set(cells) != set(CELL_LABELS):
        raise ProjectError("工厂单元必须包含焊接、装配、机加工、视觉检测四个工位")
    for key, cell in cells.items():
        if not isinstance(cell, dict):
            raise ProjectError(f"{CELL_LABELS[key]} 配置必须是对象")
        _number(cell.get("cycle_sec"), f"{CELL_LABELS[key]} 节拍（秒）", 5.0, 300.0)
        expected = CELL_STATION_COUNTS[key]
        _integer(cell.get("stations"), f"{CELL_LABELS[key]} 工位数量", expected[0], expected[0])
        _integer(cell.get("robots"), f"{CELL_LABELS[key]} 机器人数", expected[1], expected[1])
    buffers = factory.get("buffers") or {}
    if set(buffers) != set(BUFFER_LABELS):
        raise ProjectError("缓冲区配置必须包含原材料、三个线间缓冲与返修区")
    for key, buffer in buffers.items():
        if not isinstance(buffer, dict):
            raise ProjectError(f"{BUFFER_LABELS[key]} 配置必须是对象")
        _integer(buffer.get("capacity"), f"{BUFFER_LABELS[key]} 容量", 0, 1000)
    _number(factory.get("vision_fail_probability"), "视觉检测失败概率", 0.0, 0.2)
    _number(factory.get("minor_stop_probability"), "设备短暂停机概率", 0.0, 0.2)
    _integer(factory.get("amr_units"), "AMR 数量", 1, 2)

    edge = project.get("edge") or {}
    if not isinstance(edge, dict):
        raise ProjectError("edge 配置必须是对象")
    _integer(edge.get("seed"), "边缘实验随机种子", 0, 2_147_483_647)
    nodes = edge.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 10:
        raise ProjectError("边缘节点数量必须在 1~10 之间")
    seen_ids: set[str] = set()
    node_types: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ProjectError("节点配置必须是对象")
        node_id = str(node.get("node_id", ""))
        if not re.match(r"^[A-Za-z0-9_-]{2,32}$", node_id) or node_id in seen_ids:
            raise ProjectError(f"节点 ID 必须唯一且仅含字母数字/下划线：{node_id!r}")
        seen_ids.add(node_id)
        node_type = node.get("node_type")
        if node_type not in ("local", "edge", "cloud"):
            raise ProjectError(f"节点 {node_id} 的 type 必须为 local/edge/cloud")
        node_types.add(node_type)
        _number(node.get("compute_gflops"), f"节点 {node_id} 算力", 0.5, 1000.0)
        _number(node.get("base_load"), f"节点 {node_id} 基础负载", 0.0, 0.9)
        _number(node.get("max_task_gflops"), f"节点 {node_id} 单任务上限", 0.1, 500.0)
        _integer(node.get("max_queue_slots"), f"节点 {node_id} 队列容量", 1, 200)
        _number(node.get("bandwidth_mbps"), f"节点 {node_id} 带宽", 0.5, 5000.0)
        _number(node.get("latency_ms"), f"节点 {node_id} 时延", 0.0, 2000.0)
        _number(node.get("congestion"), f"节点 {node_id} 拥塞系数", 0.0, 0.95)
        _number(node.get("cost_per_gflop"), f"节点 {node_id} 成本系数", 0.0, 100.0)
    if "local" not in node_types or "cloud" not in node_types:
        raise ProjectError("边缘节点配置必须同时包含 local 与 cloud 节点，才能公平比较三种策略")

    mapping = edge.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise ProjectError("任务映射配置必须是对象")
    _number(mapping.get("data_size_mb"), "映射：检测数据量", 0.1, 100.0)
    _number(mapping.get("compute_demand_gflops"), "映射：基础计算需求", 0.1, 200.0)
    _number(mapping.get("deadline_ms"), "映射：时延上限", 10.0, 10_000.0)
    _number(mapping.get("deadline_jitter_ms"), "映射：时延抖动", 0.0, 5000.0)
    _number(mapping.get("arrival_interval_ms"), "映射：任务到达间隔", 1.0, 60_000.0)
    _number(mapping.get("network_jitter_ms"), "映射：网络抖动", 0.0, 1000.0)
    _number(mapping.get("result_size_mb"), "映射：结果数据量", 0.001, 10.0)
    _integer(mapping.get("max_tasks"), "映射：最大任务数", 1, 500)
    _number(mapping.get("failed_compute_factor"), "映射：失败任务计算放大系数", 1.0, 3.0)
    _number(mapping.get("compute_variation"), "映射：计算需求波动", 0.0, 0.5)


def save_project(project: dict[str, Any], base_dir: Optional[Path] = None) -> dict[str, Any]:
    """校验并以原子写入保存项目，返回更新了 updated_at 的副本。"""
    ensure_data_dirs()
    project = copy.deepcopy(project)
    project["updated_at"] = _now_iso()
    validate_project(project)
    if _storage_mode() == "sqlite":
        payload = json.dumps(_json_sanitize(project), ensure_ascii=False)
        with closing(_sqlite_connect(base_dir)) as conn:
            conn.execute(
                "INSERT INTO projects(project_id, updated_at, data) VALUES(?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET updated_at=excluded.updated_at, data=excluded.data",
                (project["project_id"], project["updated_at"], payload),
            )
            conn.commit()
        return project
    _atomic_write_json(_project_file(project["project_id"], base_dir), project)
    return project


def load_project(project_id: str, base_dir: Optional[Path] = None) -> dict[str, Any]:
    if _storage_mode() == "sqlite":
        if not PROJECT_ID_PATTERN.match(project_id or ""):
            raise ProjectError(f"非法的项目 ID：{project_id!r}")
        with closing(_sqlite_connect(base_dir)) as conn:
            row = conn.execute(
                "SELECT data FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectError(f"项目不存在：{project_id}")
        project = json.loads(row["data"])
        validate_project(project)
        return project
    path = _project_file(project_id, base_dir)
    if not path.exists():
        raise ProjectError(f"项目不存在：{project_id}")
    try:
        with open(path, encoding="utf-8") as handle:
            project = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ProjectError(f"项目文件损坏：{path.name}（{exc}）") from exc
    validate_project(project)
    return project


def create_project(
    name: str,
    description: str = "",
    goal: str = "",
    base_dir: Optional[Path] = None,
) -> dict[str, Any]:
    project = default_project(name=name, description=description, goal=goal)
    return save_project(project, base_dir=base_dir)


def list_projects(base_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    if _storage_mode() == "sqlite":
        with closing(_sqlite_connect(base_dir)) as conn:
            rows = conn.execute("SELECT data FROM projects ORDER BY updated_at DESC").fetchall()
        projects: list[dict[str, Any]] = []
        for row in rows:
            try:
                project = json.loads(row["data"])
                validate_project(project)
            except (json.JSONDecodeError, ProjectError):
                continue
            projects.append(project)
        projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return projects
    root = Path(base_dir) if base_dir is not None else PROJECTS_DIR
    if not root.exists():
        return []
    projects = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        path = directory / "project.json"
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                project = json.load(handle)
            validate_project(project)
        except (json.JSONDecodeError, ProjectError):
            continue
        projects.append(project)
    projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return projects


def experiments_dir(project_id: str, base_dir: Optional[Path] = None) -> Path:
    return _project_dir(project_id, base_dir) / "experiments"


def save_experiment(
    project_id: str,
    record: dict[str, Any],
    base_dir: Optional[Path] = None,
) -> Path:
    """保存一条工厂/边缘实验结果（按 experiment_id 命名）。"""
    experiment_id = str(record.get("experiment_id", ""))
    if not EXPERIMENT_ID_PATTERN.match(experiment_id):
        raise ProjectError(f"非法的实验 ID：{experiment_id!r}")
    if record.get("project_id") != project_id:
        raise ProjectError("实验记录的项目 ID 与保存目标不一致")
    if record.get("kind") not in ("factory", "edge", "live"):
        raise ProjectError("实验类型必须为 factory / edge / live")
    if not record.get("created_at"):
        raise ProjectError("实验记录缺少 created_at")
    if _storage_mode() == "sqlite":
        payload = json.dumps(_json_sanitize(record), ensure_ascii=False)
        with closing(_sqlite_connect(base_dir)) as conn:
            conn.execute(
                "INSERT INTO experiments(experiment_id, project_id, kind, created_at, data) "
                "VALUES(?,?,?,?,?) ON CONFLICT(experiment_id) DO UPDATE SET "
                "project_id=excluded.project_id, kind=excluded.kind, "
                "created_at=excluded.created_at, data=excluded.data",
                (
                    experiment_id,
                    project_id,
                    str(record.get("kind")),
                    str(record.get("created_at")),
                    payload,
                ),
            )
            conn.commit()
        return _sqlite_db_path(base_dir)
    path = experiments_dir(project_id, base_dir) / f"{experiment_id}.json"
    _atomic_write_json(path, record)
    return path


def load_experiment(
    project_id: str,
    experiment_id: str,
    base_dir: Optional[Path] = None,
) -> dict[str, Any]:
    if not EXPERIMENT_ID_PATTERN.match(experiment_id or ""):
        raise ProjectError(f"非法的实验 ID：{experiment_id!r}")
    if _storage_mode() == "sqlite":
        with closing(_sqlite_connect(base_dir)) as conn:
            row = conn.execute(
                "SELECT data FROM experiments WHERE experiment_id=? AND project_id=?",
                (experiment_id, project_id),
            ).fetchone()
        if row is None:
            raise ProjectError(f"实验不存在：{experiment_id}")
        return json.loads(row["data"])
    path = experiments_dir(project_id, base_dir) / f"{experiment_id}.json"
    if not path.exists():
        raise ProjectError(f"实验不存在：{experiment_id}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def list_experiments(
    project_id: str,
    kind: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    if _storage_mode() == "sqlite":
        query = "SELECT data FROM experiments WHERE project_id=?"
        params: list[Any] = [project_id]
        if kind:
            query += " AND kind=?"
            params.append(kind)
        query += " ORDER BY created_at DESC"
        with closing(_sqlite_connect(base_dir)) as conn:
            rows = conn.execute(query, params).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                records.append(json.loads(row["data"]))
            except json.JSONDecodeError:
                continue
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return records
    directory = experiments_dir(project_id, base_dir)
    if not directory.exists():
        return []
    records = []
    for path in directory.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if kind and record.get("kind") != kind:
            continue
        records.append(record)
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return records


def project_params_path(project_id: str, base_dir: Optional[Path] = None) -> Path:
    return _project_dir(project_id, base_dir) / "factory_params.yaml"


def write_project_params_yaml(
    project: dict[str, Any],
    params: dict[str, Any],
    base_dir: Optional[Path] = None,
) -> Path:
    """把合并后的 RFTwin 参数写入项目目录，供 Live 服务通过环境变量加载。"""
    path = project_params_path(project["project_id"], base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 由 EdgeCoder 统一平台生成：项目工厂参数。\n"
        f"# project_id: {project['project_id']}\n"
        f"# generated_at: {_now_iso()}\n"
        "# 本文件是仿真假设参数，不是真实工厂数据。\n"
    )
    text = header + yaml.safe_dump(params, allow_unicode=True, sort_keys=False)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".yaml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
