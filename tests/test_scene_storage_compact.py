"""场景存储压缩与实时仿真内存管理回归测试。"""

from __future__ import annotations

import json

from integration.project_store import default_project, save_project
from integration.scene.manager import LiveSimulation, SimulationManager
from integration.scene.models import Connection, Device, Port, SceneDocument
from integration.scene.store import (
    OIP_DIR,
    SUMMARY_FILE,
    VERSIONS_DIR,
    create_scene,
    list_reports,
    list_scenes,
    load_scene,
    save_scene,
    scene_dir,
)


def _large_oip() -> dict:
    return {
        "version": 7,
        "name": "Compact Test",
        "scene": {"version": 4, "parts": [{"id": f"pt_{i}", "x": i} for i in range(2000)]},
        "packages": [],
    }


def test_scene_files_store_oip_once_and_versions_stay_small(tmp_path):
    project = save_project(default_project(name="压缩测试"), base_dir=tmp_path)
    project_id = project["project_id"]
    oip = _large_oip()
    doc = create_scene(project_id, "压缩场景", oip_project=oip, base_dir=tmp_path)
    directory = scene_dir(project_id, doc.scene_id, tmp_path)

    raw = json.loads((directory / "scene.json").read_text(encoding="utf-8"))
    assert raw["oip_project"] is None
    blob = raw["oip_blob"]
    assert (directory / OIP_DIR / f"{blob}.json").exists()

    summary = json.loads((directory / SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["has_oip"] is True
    assert summary["devices"] == 0

    doc.devices = [
        Device(id="a", type="conveyor", name="A", position=[0, 0, 0], simulation_type="conveyor")
    ]
    saved, changed = save_scene(doc, base_dir=tmp_path)
    assert changed and saved.version == 2
    version_file = directory / VERSIONS_DIR / "0002.json"
    assert version_file.stat().st_size < 20_000, "版本快照不应再复制整份 OIP 工程"

    current = load_scene(project_id, doc.scene_id, base_dir=tmp_path)
    assert current.oip_project == oip
    assert len(current.devices) == 1

    old = load_scene(project_id, doc.scene_id, base_dir=tmp_path, version=1)
    assert old.oip_project == oip
    assert old.devices == []

    again, changed = save_scene(current, base_dir=tmp_path)
    assert not changed and again.version == 2

    # 只加载设备/连接的调用方保存时，不应把已有 OIP 工程丢掉。
    partial = load_scene(project_id, doc.scene_id, base_dir=tmp_path, include_oip=False)
    assert partial.oip_project is None
    partial.devices = []
    saved3, changed3 = save_scene(partial, base_dir=tmp_path)
    assert changed3 and saved3.oip_project == oip

    rows = list_scenes(project_id, base_dir=tmp_path)
    assert rows[0]["devices"] == 0 and rows[0]["version"] == 3

    report_dir = directory / "reports"
    report_dir.mkdir()
    (report_dir / "RPT-TEST-0001.json").write_text(
        json.dumps({"report_id": "RPT-TEST-0001", "generated_at": "2026-11-03T12:00:00", "scene_version": 3}),
        encoding="utf-8",
    )
    (report_dir / "RPT-TEST-0001.md").write_text("# report", encoding="utf-8")
    reports = list_reports(project_id, doc.scene_id, base_dir=tmp_path)
    assert reports and reports[0]["report_id"] == "RPT-TEST-0001"
    assert reports[0]["has_markdown"] is True and reports[0]["has_html"] is False

    # 迁移工具必须可重复执行，且不能丢掉 OIP 引用。
    from tools.compact_scene_storage import migrate_scene

    migrate_scene(tmp_path, project_id, directory, dry_run=False)
    migrate_scene(tmp_path, project_id, directory, dry_run=False)
    assert load_scene(project_id, doc.scene_id, base_dir=tmp_path).oip_project == oip


def _minimal_scene() -> SceneDocument:
    spawner = Device(
        id="s",
        type="spawner",
        name="S",
        position=[0, 0, 0],
        simulation_type="spawner",
        ports=[Port(id="out", name="Out", direction="output", flow="material", offset=[0, 0, 0], out=[1, 0])],
        params={"interval_sec": 1},
    )
    despawner = Device(
        id="d",
        type="despawner",
        name="D",
        position=[1, 0, 0],
        simulation_type="despawner",
        ports=[Port(id="in", name="In", direction="input", flow="material", offset=[0, 0, 0], out=[-1, 0])],
    )
    connection = Connection(id="c1", source_device="s", source_port="out", target_device="d", target_port="in")
    return SceneDocument(
        schema_version="1.0",
        scene_id="SCN-TEST-0001",
        project_id="PRJ-TEST-0001",
        name="minimal",
        devices=[spawner, despawner],
        connections=[connection],
    )


def test_live_simulation_reuses_scene_and_manager_prunes_finished():
    scene = _minimal_scene()
    live = LiveSimulation(scene, duration_sec=5, seed=1)
    assert live.scene is live.engine.scene, "不应为实时仿真实例再复制一份场景"

    manager = SimulationManager(max_finished=1)
    first = manager.create(scene, duration_sec=5, seed=1)
    second = manager.create(scene, duration_sec=5, seed=2)
    assert first.sim_id != second.sim_id, "同一秒内创建的实时实例 ID 必须唯一"

    first.status = "completed"
    second.status = "completed"
    first.stopped_at = "2026-01-01T00:00:00"
    second.stopped_at = "2026-01-02T00:00:00"
    manager.create(scene, duration_sec=5, seed=3)

    remaining = manager.list()
    assert len(remaining) == 2  # 1 个已完成 + 1 个新建
    assert second.sim_id in {item["sim_id"] for item in remaining}
    assert first.sim_id not in {item["sim_id"] for item in remaining}
