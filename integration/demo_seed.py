"""从 demo_seed 恢复演示项目（系统设置里的「重置演示数据」）。

- 现有同名演示项目先移动到 data/archive/demo-reset-<时间>，不直接删除；
- JSON 模式：复制文件即可；SQLite 模式：复制后把项目/实验 upsert 进数据库；
- 场景/报告始终是文件存储，随目录一起复制。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from integration.paths import DATA_DIR, PROJECT_ROOT, PROJECTS_DIR, ensure_data_dirs
from integration.project_store import _storage_mode, save_experiment, save_project


class DemoSeedError(RuntimeError):
    pass


def seed_projects_dir() -> Path:
    return PROJECT_ROOT / "demo_seed" / "projects"


def preferred_demo_project_id() -> str | None:
    """发布清单中的默认案例；不改变用户在会话内已选中的项目。"""
    try:
        manifest = json.loads((PROJECT_ROOT / "demo_seed" / "manifest.json").read_text(encoding="utf-8"))
        return manifest.get("default_project_id")
    except (OSError, ValueError, AttributeError):
        return None


def reset_demo_projects() -> dict[str, Any]:
    source = seed_projects_dir()
    if not source.is_dir():
        raise DemoSeedError(f"未找到演示数据目录：{source}")
    ensure_data_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    archive = DATA_DIR / "archive" / f"demo-reset-{stamp}"
    restored: list[str] = []
    archived: list[str] = []
    errors: list[str] = []
    sqlite_mode = _storage_mode() == "sqlite"

    for project_dir in sorted(source.iterdir()):
        project_file = project_dir / "project.json"
        if not project_dir.is_dir() or not project_file.exists():
            continue
        target = PROJECTS_DIR / project_dir.name
        try:
            if target.exists():
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(archive / project_dir.name))
                archived.append(project_dir.name)
            shutil.copytree(project_dir, target)
            if sqlite_mode:
                project = json.loads(project_file.read_text(encoding="utf-8"))
                save_project(project)
                for experiment_file in sorted((target / "experiments").glob("*.json")):
                    record = json.loads(experiment_file.read_text(encoding="utf-8"))
                    save_experiment(record["project_id"], record)
            restored.append(project_dir.name)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"{project_dir.name}: {exc}")

    return {
        "restored": restored,
        "archived": archived,
        "archive_dir": str(archive) if archived else None,
        "errors": errors,
    }


__all__ = ["DemoSeedError", "reset_demo_projects", "seed_projects_dir"]
