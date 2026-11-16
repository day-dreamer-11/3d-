"""演示数据重置：恢复 demo_seed，并归档同名旧项目。"""

from __future__ import annotations

import json

from integration import demo_seed
from integration.project_store import default_project


def test_reset_demo_projects_restores_and_archives(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    data_dir = tmp_path / "data"
    projects_dir = data_dir / "projects"
    seed = app_root / "demo_seed" / "projects" / "PRJ-DEMO-0001"
    seed.mkdir(parents=True)
    project = default_project(name="演示项目")
    project["project_id"] = "PRJ-DEMO-0001"
    (seed / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (seed / "scenes").mkdir()

    existing = projects_dir / "PRJ-DEMO-0001"
    existing.mkdir(parents=True)
    (existing / "OLD.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(demo_seed, "PROJECT_ROOT", app_root)
    monkeypatch.setattr(demo_seed, "DATA_DIR", data_dir)
    monkeypatch.setattr(demo_seed, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(demo_seed, "ensure_data_dirs", lambda: projects_dir.mkdir(parents=True, exist_ok=True))

    result = demo_seed.reset_demo_projects()
    assert result["restored"] == ["PRJ-DEMO-0001"]
    assert result["archived"] == ["PRJ-DEMO-0001"]
    assert (projects_dir / "PRJ-DEMO-0001" / "project.json").exists()
    assert not (projects_dir / "PRJ-DEMO-0001" / "OLD.txt").exists()
    assert result["archive_dir"] and (tmp_path / "data" / "archive").exists()
