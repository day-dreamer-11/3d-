"""场景持久化：每项目独立目录、版本快照、原子写入。

目录：
    data/projects/<project_id>/scenes/<scene_id>/scene.json         当前版本
    data/projects/<project_id>/scenes/<scene_id>/versions/<v>.json 不可变版本
    data/projects/<project_id>/scenes/<scene_id>/reports/           报告

并发：API 单进程内使用可重入锁；跨进程写入使用原子 replace（os.replace）。
     Streamlit 只通过 API 写入，避免多写者。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from integration.paths import PROJECTS_DIR, ensure_data_dirs
from integration.project_store import list_experiments, load_project, save_experiment

from .connections import derive_geometry_candidates, initial_connections, validate_connections
from .models import (
    SCENE_SCHEMA_VERSION,
    SceneDocument,
    now_iso,
    new_scene_id,
)

_LOCK = threading.RLock()
SCENE_DIR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
SCENE_FILE = "scene.json"
VERSIONS_DIR = "versions"
SUMMARY_FILE = "summary.json"
OIP_DIR = "oip"
_SUMMARY_KEYS = (
    "scene_id",
    "project_id",
    "name",
    "version",
    "devices",
    "connections",
    "updated_at",
    "errors",
    "warnings",
)


class SceneError(ValueError):
    """场景不存在或非法。"""


def _check_project(project_id: str, base_dir: Optional[Path] = None) -> None:
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$", project_id or ""):
        raise SceneError(f"非法的项目 ID：{project_id!r}")
    load_project(project_id, base_dir=base_dir)  # 项目不存在/损坏时抛出 ProjectError


def _check_scene_id(scene_id: str) -> None:
    if not SCENE_DIR_PATTERN.match(scene_id or ""):
        raise SceneError(f"非法的场景 ID：{scene_id!r}")


def scene_dir(project_id: str, scene_id: str, base_dir: Optional[Path] = None) -> Path:
    _check_scene_id(scene_id)
    root = Path(base_dir) if base_dir is not None else PROJECTS_DIR
    return root / project_id / "scenes" / scene_id


def _scene_path(project_id: str, scene_id: str, base_dir: Optional[Path] = None) -> Path:
    return scene_dir(project_id, scene_id, base_dir) / SCENE_FILE


def _version_path(project_id: str, scene_id: str, version: int, base_dir: Optional[Path] = None) -> Path:
    return scene_dir(project_id, scene_id, base_dir) / VERSIONS_DIR / f"{int(version):04d}.json"


def _summary_path(project_id: str, scene_id: str, base_dir: Optional[Path] = None) -> Path:
    return scene_dir(project_id, scene_id, base_dir) / SUMMARY_FILE


def _oip_blob_path(project_id: str, scene_id: str, oip_hash: str, base_dir: Optional[Path] = None) -> Path:
    return scene_dir(project_id, scene_id, base_dir) / OIP_DIR / f"{oip_hash}.json"


def _oip_hash(project: Optional[dict[str, Any]]) -> Optional[str]:
    """OIP 工程按内容寻址：相同工程只落盘一份，版本快照只存哈希引用。"""
    if project is None:
        return None
    encoded = json.dumps(project, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _write_oip_blob(
    project: Optional[dict[str, Any]],
    project_id: str,
    scene_id: str,
    base_dir: Optional[Path] = None,
    *,
    oip_hash: Optional[str] = None,
) -> Optional[str]:
    if project is None:
        return None
    oip_hash = oip_hash or _oip_hash(project)
    path = _oip_blob_path(project_id, scene_id, oip_hash, base_dir)
    if not path.exists():
        _atomic_write_json(path, project)
    return oip_hash


def _read_oip_blob(
    project_id: str,
    scene_id: str,
    oip_hash: Optional[str],
    base_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    if not oip_hash:
        return None
    path = _oip_blob_path(project_id, scene_id, oip_hash, base_dir)
    try:
        return _read_json(path)
    except SceneError:
        return None


def _normalized(doc: SceneDocument) -> SceneDocument:
    """统一数值类型（如 int 坐标 → float），保证内存对象与落盘后再加载的哈希一致。"""
    return SceneDocument.from_dict(copy.deepcopy(doc).to_dict())


def _scene_summary(doc: SceneDocument, content_hash_value: Optional[str] = None) -> dict[str, Any]:
    """列表页只需要摘要；避免为了拿设备数去解析整份（可能 20MB 的）OIP 工程。"""
    return {
        "scene_id": doc.scene_id,
        "project_id": doc.project_id,
        "name": doc.name,
        "version": doc.version,
        "devices": len(doc.devices),
        "connections": len(doc.connections),
        "updated_at": doc.updated_at,
        "errors": len((doc.validation or {}).get("errors", [])),
        "warnings": len((doc.validation or {}).get("warnings", [])),
        "has_oip": doc.oip_project is not None,
        "content_hash": content_hash_value,
    }


def _write_summary(doc: SceneDocument, base_dir: Optional[Path] = None, content_hash_value: Optional[str] = None) -> None:
    _atomic_write_json(
        _summary_path(doc.project_id, doc.scene_id, base_dir),
        _scene_summary(doc, content_hash_value),
    )


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SceneError(f"场景文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SceneError(f"场景文件损坏：{path.name}（{exc}）") from exc
    if not isinstance(data, dict):
        raise SceneError(f"场景文件格式错误：{path.name}")
    return data


def content_hash(doc: SceneDocument, *, oip_hash: Optional[str] = None) -> str:
    # geometry_candidates 只是建议，且候选 ID 每次推导会变；不参与版本判定，
    # 否则每次打开编辑器保存都会无意义升版。
    # OIP 工程用内容哈希代替整份嵌入，避免每次保存重复序列化 ~20MB。
    payload = {
        "devices": [d.to_dict() for d in doc.devices],
        "connections": [c.to_dict() for c in doc.connections],
        "simulation_settings": doc.simulation_settings,
        "oip_hash": oip_hash if oip_hash is not None else _oip_hash(doc.oip_project),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]


def validate_scene_document(doc: SceneDocument, *, check_connections: bool = True) -> dict[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not doc.scene_id or not SCENE_DIR_PATTERN.match(doc.scene_id):
        errors.append({"severity": "error", "code": "invalid_scene_id", "message": f"非法场景 ID：{doc.scene_id}"})
    if not doc.name.strip():
        errors.append({"severity": "error", "code": "empty_name", "message": "场景名称不能为空"})
    seen: set[str] = set()
    for device in doc.devices:
        if not device.id:
            errors.append({"severity": "error", "code": "device_without_id", "message": "存在没有 id 的设备"})
            continue
        if device.id in seen:
            errors.append({"severity": "error", "code": "duplicate_device_id", "message": f"设备 ID 重复：{device.id}", "devices": [device.id]})
        seen.add(device.id)
        if len(device.position) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in device.position):
            errors.append({"severity": "error", "code": "invalid_position", "message": f"设备 {device.id} 的位置非法", "devices": [device.id]})
        if device.simulation_type not in (
            "conveyor",
            "inspection",
            "robot",
            "spawner",
            "despawner",
            "buffer",
            "display_only",
        ):
            errors.append({"severity": "error", "code": "invalid_simulation_type", "message": f"设备 {device.id} 的仿真类型非法：{device.simulation_type}", "devices": [device.id]})
        for key, value in device.params.items():
            if isinstance(value, float) and not math.isfinite(value):
                errors.append({"severity": "error", "code": "invalid_param", "message": f"设备 {device.id} 的参数 {key} 不是有限数值", "devices": [device.id]})
    if check_connections:
        result = validate_connections(doc)
        errors.extend(result["errors"])
        warnings.extend(result["warnings"])
    return {"errors": errors, "warnings": warnings}


def create_scene(
    project_id: str,
    name: str,
    *,
    oip_project: Optional[dict[str, Any]] = None,
    simulation_settings: Optional[dict[str, Any]] = None,
    base_dir: Optional[Path] = None,
) -> SceneDocument:
    _check_project(project_id, base_dir)
    ensure_data_dirs()
    doc = SceneDocument(
        schema_version=SCENE_SCHEMA_VERSION,
        scene_id=new_scene_id(),
        project_id=project_id,
        name=name.strip() or "未命名场景",
        simulation_settings=simulation_settings
        or {"duration_sec": 1800, "seed": 42, "tick_sec": 0.25},
        oip_project=oip_project,
        source={"oip_commit": "dcebf53", "created_by": "aic_integration"},
    )
    with _LOCK:
        doc = _normalized(doc)
        doc.validation = validate_scene_document(doc)
        oip_hash = _oip_hash(doc.oip_project)
        digest = content_hash(doc, oip_hash=oip_hash)
        payload = _version_payload(doc, digest, oip_hash, base_dir)
        _atomic_write_json(_scene_path(project_id, doc.scene_id, base_dir), payload)
        _atomic_write_json(
            _version_path(project_id, doc.scene_id, doc.version, base_dir),
            payload,
        )
        _write_summary(doc, base_dir, digest)
    return doc


def _version_payload(
    doc: SceneDocument,
    digest: str,
    oip_hash: Optional[str],
    base_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """版本快照：OIP 工程只保留哈希引用，恢复时从 oip/ 目录读回。"""
    payload = doc.to_dict()
    payload["content_hash"] = digest
    if oip_hash:
        _write_oip_blob(doc.oip_project, doc.project_id, doc.scene_id, base_dir, oip_hash=oip_hash)
        payload["oip_project"] = None
        payload["oip_blob"] = oip_hash
    return payload


def load_scene(
    project_id: str,
    scene_id: str,
    *,
    base_dir: Optional[Path] = None,
    version: Optional[int] = None,
    include_oip: bool = True,
) -> SceneDocument:
    _check_project(project_id, base_dir)
    _check_scene_id(scene_id)
    path = _version_path(project_id, scene_id, version, base_dir) if version is not None else _scene_path(project_id, scene_id, base_dir)
    raw = _read_json(path)
    if include_oip and raw.get("oip_project") is None and raw.get("oip_blob"):
        raw["oip_project"] = _read_oip_blob(project_id, scene_id, raw.get("oip_blob"), base_dir)
    doc = SceneDocument.from_dict(raw)
    if doc.project_id != project_id or doc.scene_id != scene_id:
        raise SceneError("场景文件的项目/场景 ID 与路径不一致")
    return doc


def list_scenes(project_id: str, *, base_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    _check_project(project_id, base_dir)
    root = Path(base_dir) if base_dir is not None else PROJECTS_DIR
    directory = root / project_id / "scenes"
    if not directory.exists():
        return []
    items = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or not SCENE_DIR_PATTERN.match(child.name):
            continue
        scene_file = child / SCENE_FILE
        if not scene_file.exists():
            continue
        summary_file = child / SUMMARY_FILE
        if summary_file.exists():
            try:
                summary = _read_json(summary_file)
            except SceneError:
                summary = None
            if summary and summary.get("scene_id") == child.name:
                items.append({key: summary.get(key) for key in _SUMMARY_KEYS})
                continue
        try:
            doc = SceneDocument.from_dict(_read_json(scene_file))
        except SceneError:
            continue
        summary = _scene_summary(doc)
        items.append({key: summary.get(key) for key in _SUMMARY_KEYS})
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return items


def save_scene(
    doc: SceneDocument,
    *,
    ingest_source: Optional[str] = None,
    bump_version: bool = True,
    base_dir: Optional[Path] = None,
    extra_issues: Optional[list[Any]] = None,
) -> tuple[SceneDocument, bool]:
    """保存场景；内容未变化时返回原版本（changed=False）。"""
    _check_project(doc.project_id, base_dir)
    _check_scene_id(doc.scene_id)
    with _LOCK:
        current = None
        current_hash: Optional[str] = None
        raw_current: dict[str, Any] = {}
        current_path = _scene_path(doc.project_id, doc.scene_id, base_dir)
        if current_path.exists():
            try:
                raw_current = _read_json(current_path)
                current = SceneDocument.from_dict(raw_current)
                current_hash = raw_current.get("content_hash")
            except SceneError:
                current = None
        doc = _normalized(doc)
        if doc.oip_project is None and raw_current:
            # 调用方可能只加载了设备/连接（include_oip=False）；保存时保留已有工程，避免误删。
            existing = raw_current.get("oip_project")
            if existing is None and raw_current.get("oip_blob"):
                existing = _read_oip_blob(doc.project_id, doc.scene_id, raw_current["oip_blob"], base_dir)
            doc.oip_project = existing
        doc.updated_at = now_iso()
        oip_hash = _oip_hash(doc.oip_project)
        new_hash = content_hash(doc, oip_hash=oip_hash)
        if current is not None:
            previous_hash = current_hash or content_hash(current)
            if previous_hash == new_hash:
                _write_summary(current, base_dir, previous_hash)
                return current, False
        if bump_version and current is not None:
            doc.version = int(current.version) + 1
        elif current is None:
            doc.version = max(1, int(doc.version))
        doc.validation = validate_scene_document(doc)
        if extra_issues:
            for issue in extra_issues:
                payload = issue.to_dict() if hasattr(issue, "to_dict") else dict(issue)
                bucket = "errors" if payload.get("severity") == "error" else "warnings"
                if payload not in doc.validation[bucket]:
                    doc.validation[bucket].append(payload)
        if ingest_source:
            doc.source = {**(doc.source or {}), "last_ingest": ingest_source, "last_ingest_at": now_iso()}
        payload = _version_payload(doc, new_hash, oip_hash, base_dir)
        _atomic_write_json(
            _version_path(doc.project_id, doc.scene_id, doc.version, base_dir),
            payload,
        )
        _atomic_write_json(current_path, payload)
        _write_summary(doc, base_dir, new_hash)
        return doc, True


def list_reports(project_id: str, scene_id: str, *, base_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    _check_project(project_id, base_dir)
    _check_scene_id(scene_id)
    directory = scene_dir(project_id, scene_id, base_dir) / "reports"
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = _read_json(path)
        except SceneError:
            continue
        items.append(
            {
                "report_id": data.get("report_id") or path.stem,
                "generated_at": data.get("generated_at"),
                "scene_version": data.get("scene_version"),
                "experiment_id": data.get("experiment_id"),
                "has_markdown": path.with_suffix(".md").exists(),
                "has_html": path.with_suffix(".html").exists(),
            }
        )
    items.sort(key=lambda item: item.get("generated_at") or "", reverse=True)
    return items


def delete_scene(project_id: str, scene_id: str, *, base_dir: Optional[Path] = None) -> None:
    import shutil

    _check_project(project_id, base_dir)
    path = scene_dir(project_id, scene_id, base_dir)
    if not path.exists():
        raise SceneError(f"场景不存在：{scene_id}")
    with _LOCK:
        shutil.rmtree(path)


def list_versions(project_id: str, scene_id: str, *, base_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    _check_project(project_id, base_dir)
    _check_scene_id(scene_id)
    directory = scene_dir(project_id, scene_id, base_dir) / VERSIONS_DIR
    if not directory.exists():
        return []
    versions = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = _read_json(path)
            doc = SceneDocument.from_dict(raw)
        except SceneError:
            continue
        versions.append(
            {
                "version": doc.version,
                "created_at": doc.updated_at,
                "devices": len(doc.devices),
                "connections": len(doc.connections),
                "content_hash": raw.get("content_hash") or content_hash(doc),
            }
        )
    versions.sort(key=lambda x: x["version"], reverse=True)
    return versions


def restore_version(project_id: str, scene_id: str, version: int, *, base_dir: Optional[Path] = None) -> SceneDocument:
    old = load_scene(project_id, scene_id, base_dir=base_dir, version=version)
    if old.oip_project is None:
        # 版本只存 OIP 哈希引用；引用缺失时保留当前工程，避免恢复后编辑器空白。
        try:
            old.oip_project = load_scene(project_id, scene_id, base_dir=base_dir).oip_project
        except SceneError:
            pass
    old.version = 0
    old.name = old.name or f"恢复自 v{version}"
    restored, _ = save_scene(old, ingest_source=f"restore_v{version}", base_dir=base_dir)
    return restored


def ingest_oip_project(
    project_id: str,
    scene_id: str,
    oip_project: dict[str, Any],
    *,
    save_as_name: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> SceneDocument:
    """OIP 保存 → 统一设备 → 显式连接（保留/采用）→ 新版本。"""
    from .mapping import map_oip_project

    current = load_scene(project_id, scene_id, base_dir=base_dir)
    mapped = map_oip_project(oip_project, existing_devices=current.devices)
    device_ids = {d.id for d in mapped.devices}
    candidates, issues = derive_geometry_candidates(mapped.devices)
    chosen, adopted = initial_connections(current.connections, candidates, device_ids)
    doc = copy.deepcopy(current)
    if save_as_name is not None:
        if not save_as_name.strip():
            raise SceneError("请输入新场景名称。")
        doc.scene_id = new_scene_id()
        doc.name = save_as_name.strip()
        doc.version = 1
        doc.created_at = now_iso()
        oip_project = {**oip_project, "name": doc.name}
    doc.devices = mapped.devices
    doc.geometry_candidates = candidates
    doc.connections = chosen
    doc.oip_project = oip_project
    doc.source = {
        **(doc.source or {}),
        "oip_commit": "dcebf53",
        "oip_project_name": (oip_project.get("name") if isinstance(oip_project, dict) else None),
        "connections_adopted_from_geometry": adopted,
    }
    doc.metadata["ignored_parts"] = mapped.ignored_parts
    doc.metadata["mapping_defaults"] = mapped.defaults_applied
    return save_scene(
        doc,
        ingest_source="oip_editor_save",
        base_dir=base_dir,
        extra_issues=[*mapped.issues, *issues],
    )[0]


def save_scene_experiment(project_id: str, record: dict[str, Any], *, base_dir: Optional[Path] = None) -> Path:
    return save_experiment(project_id, record, base_dir=base_dir)


def list_scene_experiments(project_id: str, scene_id: Optional[str] = None, *, kind: Optional[str] = None) -> list[dict[str, Any]]:
    records = list_experiments(project_id, kind=kind)
    if scene_id:
        records = [r for r in records if r.get("scene_id") == scene_id]
    return records
