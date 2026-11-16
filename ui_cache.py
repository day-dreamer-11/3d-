"""Streamlit 数据读取缓存：项目/实验摘要每次重跑都读盘，这里统一缓存并提供失效入口。"""

from __future__ import annotations

from typing import Any, Optional

import streamlit as st

from integration.paths import PROJECTS_DIR
from integration.project_store import _sqlite_db_path, _storage_mode, experiments_dir
from integration.project_store import list_experiments as _list_experiments
from integration.project_store import list_projects as _list_projects


def _projects_signature() -> tuple[Any, ...]:
    """目录指纹：新增/删除项目（含测试或其它进程写入）时自动失效缓存。"""
    try:
        if _storage_mode() == "sqlite":
            path = _sqlite_db_path()
            return ("sqlite", path.stat().st_mtime_ns if path.exists() else 0)
        return ("json", tuple(sorted((p.name, p.stat().st_mtime_ns) for p in PROJECTS_DIR.iterdir() if p.is_dir())))
    except OSError:
        return ("unavailable",)


def _experiments_signature(project_id: str) -> tuple[Any, ...]:
    try:
        if _storage_mode() == "sqlite":
            path = _sqlite_db_path()
            return ("sqlite", path.stat().st_mtime_ns if path.exists() else 0)
        return ("json", tuple(sorted((p.name, p.stat().st_mtime_ns) for p in experiments_dir(project_id).glob("*.json"))))
    except OSError:
        return ("unavailable",)


@st.cache_data(ttl=15, max_entries=32, show_spinner=False)
def _cached_projects(signature: tuple[Any, ...]) -> list[dict[str, Any]]:
    return _list_projects()


@st.cache_data(ttl=15, max_entries=64, show_spinner=False)
def _cached_experiments(project_id: str, kind: Optional[str], signature: tuple[Any, ...]) -> list[dict[str, Any]]:
    return _list_experiments(project_id, kind=kind)


def list_projects() -> list[dict[str, Any]]:
    return _cached_projects(_projects_signature())


def list_experiments(project_id: str, kind: Optional[str] = None) -> list[dict[str, Any]]:
    return _cached_experiments(project_id, kind, _experiments_signature(project_id))


def invalidate_data_cache() -> None:
    """写操作后清空摘要缓存；失败也不影响主流程。"""
    for cached in (_cached_projects, _cached_experiments):
        try:
            cached.clear()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["invalidate_data_cache", "list_experiments", "list_projects"]
