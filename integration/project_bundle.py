"""整包导出 / 导入：把整个项目（场景 + OIP 工程 + 实验 + 报告）打包成一个文件。

格式：ZIP（扩展名 .aicproj），根目录为项目目录内容加 manifest.json：
    manifest.json              {format, exported_at, project_id, name, counts}
    project.json
    scenes/<scene_id>/{scene.json, versions/, reports/}
    experiments/<experiment_id>.json

导入始终创建“新项目”（新 project_id），避免覆盖现有数据；场景/实验 ID 保持不变，
项目 ID 在包内所有 JSON/HTML/文本文件中同步替换。仅支持 JSON 存储模式（默认）。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from integration.paths import PROJECTS_DIR
from integration.project_store import (
    PROJECT_ID_PATTERN,
    list_projects,
    load_project,
    new_project_id,
    _storage_mode,
)
from integration.scene.store import load_scene

BUNDLE_FORMAT = "aic-project-bundle/v1"
BUNDLE_SUFFIX = ".aicproj"
MANIFEST_NAME = "manifest.json"
MAX_UNCOMPRESSED_BYTES = 1_500_000_000
TEXT_SUFFIXES = {".json", ".html", ".md", ".txt", ".csv", ".log"}
SKIP_DIRS = {"__pycache__", ".git", ".cache"}
SKIP_SUFFIXES = {".tmp", ".lock", ".pyc"}


class ProjectBundleError(ValueError):
    """导入文件不是有效的项目整包。"""


def _root(base_dir: Optional[Path]) -> Path:
    return Path(base_dir) if base_dir is not None else PROJECTS_DIR


def _counts(root: Path) -> dict[str, int]:
    scenes = 0
    oip_projects = 0
    scenes_dir = root / "scenes"
    if scenes_dir.exists():
        for directory in sorted(scenes_dir.iterdir()):
            scene_file = directory / "scene.json"
            if not scene_file.exists():
                continue
            scenes += 1
            summary_file = directory / "summary.json"
            try:
                if summary_file.exists():
                    if json.loads(summary_file.read_text(encoding="utf-8")).get("has_oip"):
                        oip_projects += 1
                    continue
                data = json.loads(scene_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("oip_project"):
                oip_projects += 1
    experiments = 0
    exp_dir = root / "experiments"
    if exp_dir.exists():
        experiments = sum(1 for p in exp_dir.glob("*.json") if p.is_file())
    return {"scenes": scenes, "oip_projects": oip_projects, "experiments": experiments}


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "-", name.strip()) or "project"
    return cleaned[:40]


def export_project_bytes(project_id: str, base_dir: Optional[Path] = None) -> tuple[str, bytes]:
    """打包项目目录，返回 (下载文件名, 字节内容)。"""
    if _storage_mode() == "sqlite":
        raise ProjectBundleError("当前为 SQLite 存储模式，暂不支持整包导出；请使用默认 JSON 存储。")
    root = _root(base_dir)
    project = load_project(project_id, base_dir=base_dir)
    directory = root / project_id
    if not directory.is_dir():
        raise ProjectBundleError(f"项目目录不存在：{project_id}")
    counts = _counts(directory)
    manifest = {
        "format": BUNDLE_FORMAT,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "project_id": project_id,
        "name": project.get("name", project_id),
        "counts": counts,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
                continue
            archive.write(path, relative.as_posix())
    filename = f"{_safe_filename(str(project.get('name') or project_id))}-{datetime.now():%Y%m%d-%H%M}{BUNDLE_SUFFIX}"
    return filename, buffer.getvalue()


def _validate_member(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ProjectBundleError(f"包含非法绝对路径：{name}")
    if any(part == ".." for part in normalized.split("/")):
        raise ProjectBundleError(f"包含非法相对路径：{name}")


def _rewrite_ids(directory: Path, old_id: str, new_id: str) -> None:
    old_bytes = old_id.encode("utf-8")
    new_bytes = new_id.encode("utf-8")
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        data = path.read_bytes()
        if old_bytes in data:
            path.write_bytes(data.replace(old_bytes, new_bytes))


def _unique_name(name: str, existing: set[str]) -> str:
    if name not in existing:
        return name
    base = f"{name}（导入）"
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}{index}"
        index += 1
    return candidate


def import_project_bytes(data: bytes, base_dir: Optional[Path] = None) -> dict[str, Any]:
    """从整包字节恢复为新项目，返回摘要。"""
    if _storage_mode() == "sqlite":
        raise ProjectBundleError("当前为 SQLite 存储模式，暂不支持整包导入；请使用默认 JSON 存储。")
    root = _root(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ProjectBundleError("文件不是有效的 .aicproj（ZIP）包。") from exc
    members = archive.infolist()
    total = sum(info.file_size for info in members)
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ProjectBundleError(f"包解压后过大（{total / 1e9:.1f} GB），已拒绝。")
    for info in members:
        _validate_member(info.filename)

    staging: Optional[Path] = Path(tempfile.mkdtemp(prefix=".aic-import-", dir=root))
    try:
        archive.extractall(staging)
        project_file = staging / "project.json"
        if not project_file.exists():
            raise ProjectBundleError("包内缺少 project.json，不是 AIC 项目整包。")
        project = json.loads(project_file.read_text(encoding="utf-8"))
        old_id = str(project.get("project_id", ""))
        if not PROJECT_ID_PATTERN.match(old_id):
            raise ProjectBundleError("包内项目 ID 非法。")
        manifest = {}
        manifest_file = staging / MANIFEST_NAME
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
            if manifest.get("format") and manifest["format"] != BUNDLE_FORMAT:
                raise ProjectBundleError(f"不支持的项目包格式：{manifest['format']}")

        new_id = new_project_id()
        while (root / new_id).exists():
            new_id = new_project_id()
        existing_names = {str(p.get("name")) for p in list_projects(base_dir=base_dir)}
        new_name = _unique_name(str(project.get("name") or old_id), existing_names)
        _rewrite_ids(staging, old_id, new_id)
        project = json.loads(project_file.read_text(encoding="utf-8"))
        project["project_id"] = new_id
        project["name"] = new_name
        project_file.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_file.unlink(missing_ok=True)

        target = root / new_id
        staging.rename(target)
        staging = None

        load_project(new_id, base_dir=base_dir)
        scenes_dir = target / "scenes"
        scene_ids = []
        if scenes_dir.exists():
            for directory in sorted(scenes_dir.iterdir()):
                if (directory / "scene.json").exists():
                    load_scene(new_id, directory.name, base_dir=base_dir)
                    scene_ids.append(directory.name)
        counts = _counts(target)
        return {
            "project_id": new_id,
            "name": new_name,
            "scenes": counts["scenes"],
            "oip_projects": counts["oip_projects"],
            "experiments": counts["experiments"],
            "exported_at": manifest.get("exported_at"),
            "scene_ids": scene_ids,
        }
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


__all__ = [
    "BUNDLE_FORMAT",
    "BUNDLE_SUFFIX",
    "ProjectBundleError",
    "export_project_bytes",
    "import_project_bytes",
]
