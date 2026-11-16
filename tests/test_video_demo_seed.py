"""发布案例：全新安装、SQLite 升级、结果可复现、重复启动不覆盖。"""
import json
import logging
from pathlib import Path

import pytest

import launcher
from integration import project_store
from integration.scene.engine import run_scene_simulation
from integration.scene.models import SceneDocument
from integration.scene.store import load_scene, validate_scene_document

ROOT = Path(__file__).resolve().parents[1]
PID = "PRJ-VIDEO-DEMO-20261112"
SID = "SCN-20261030-234230-6C86"


@pytest.mark.parametrize("upgrade", [False, True])
def test_video_seed_first_start_and_upgrade(tmp_path, monkeypatch, upgrade):
    monkeypatch.setenv("AIC_STORAGE", "sqlite")
    monkeypatch.setenv("AIC_SQLITE_PATH", str(tmp_path / "aic_factory.db"))
    monkeypatch.setattr(launcher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path / "projects")
    if upgrade:
        project_store.create_project("已有项目，不能覆盖")
    launcher._seed_demo_projects(logging.getLogger(__name__))
    project = project_store.load_project(PID)
    assert project["name"] == "视频演示案例 · 输送与质检产线"
    records = project_store.list_experiments(PID)
    assert len(records) == 29
    assert all(r["project_id"] == PID for r in records)
    ids = {r["experiment_id"] for r in records}
    assert "VID-SEXP-20261111-164233-11AB" in ids
    project["name"] = "评委改过的名称"
    project_store.save_project(project)
    marker = tmp_path / "projects" / PID / "USER.txt"
    marker.write_text("keep", encoding="utf-8")
    launcher._seed_demo_projects(logging.getLogger(__name__))
    assert project_store.load_project(PID)["name"] == "评委改过的名称"
    assert marker.read_text() == "keep"
    assert len(project_store.list_experiments(PID)) == 29
    if upgrade:
        assert any(p["name"] == "已有项目，不能覆盖" for p in project_store.list_projects())


def test_video_seed_scene_and_results_are_reproducible(monkeypatch):
    monkeypatch.setenv("AIC_STORAGE", "json")
    root = ROOT / "demo_seed/projects"
    scene = load_scene(PID, SID, base_dir=root)
    assert len(scene.devices) == 12
    assert scene.oip_project
    assert not validate_scene_document(scene)["errors"]
    actual = run_scene_simulation(scene, duration_sec=600, seed=42, persist=False)
    assert actual["kpis"]["good_units"] == 97
    assert actual["kpis"]["throughput_uph"] == 582
    baseline = json.loads((root / PID / "experiments/VID-SEXP-20261111-164208-D07A.json").read_text(encoding="utf-8"))
    raw = baseline["config"]["scene_snapshot"]
    replay = SceneDocument.from_dict({**raw, "project_id": PID})
    result = run_scene_simulation(replay, duration_sec=600, seed=42, persist=False)
    assert result["kpis"] == baseline["kpis"]
    assert result["kpis"]["good_units"] == 70
