"""SQLite 存储后端与 JSON→SQLite 迁移测试（桌面版使用）。"""

from __future__ import annotations

import uuid

import pytest

from integration.project_store import (
    default_project,
    list_experiments,
    list_projects,
    load_experiment,
    load_project,
    migrate_json_to_sqlite,
    save_experiment,
    save_project,
)


def _project():
    return default_project(name=f"SQLite测试-{uuid.uuid4().hex[:6]}")


def _experiment(project_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "experiment_id": f"TEXP-{uuid.uuid4().hex[:8].upper()}",
        "project_id": project_id,
        "kind": "factory",
        "created_at": "2026-10-30T12:00:00",
        "kpis": {"total_output": 1, "oee": {"oee": 0.9}},
    }


def test_sqlite_project_and_experiment_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AIC_STORAGE", "sqlite")
    project = save_project(_project(), base_dir=tmp_path)
    loaded = load_project(project["project_id"], base_dir=tmp_path)
    assert loaded["name"] == project["name"]
    assert [p["project_id"] for p in list_projects(base_dir=tmp_path)] == [project["project_id"]]

    record = _experiment(project["project_id"])
    save_experiment(project["project_id"], record, base_dir=tmp_path)
    fetched = load_experiment(project["project_id"], record["experiment_id"], base_dir=tmp_path)
    assert fetched["experiment_id"] == record["experiment_id"]
    assert len(list_experiments(project["project_id"], base_dir=tmp_path)) == 1
    assert list_experiments(project["project_id"], kind="edge", base_dir=tmp_path) == []
    assert (tmp_path / "aic_factory.db").exists()


def test_json_data_is_migrated_into_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("AIC_STORAGE", raising=False)
    project = save_project(_project(), base_dir=tmp_path)
    record = _experiment(project["project_id"])
    save_experiment(project["project_id"], record, base_dir=tmp_path)

    monkeypatch.setenv("AIC_STORAGE", "sqlite")
    loaded = load_project(project["project_id"], base_dir=tmp_path)
    assert loaded["name"] == project["name"]
    records = list_experiments(project["project_id"], base_dir=tmp_path)
    assert [r["experiment_id"] for r in records] == [record["experiment_id"]]

    counts = migrate_json_to_sqlite(base_dir=tmp_path)
    assert counts["projects"] >= 1
    assert counts["experiments"] >= 1


def test_sqlite_rejects_invalid_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("AIC_STORAGE", "sqlite")
    from integration.project_store import ProjectError

    with pytest.raises(ProjectError):
        load_project("../escape", base_dir=tmp_path)
