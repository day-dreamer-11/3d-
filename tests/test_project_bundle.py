"""整包导出/导入（.aicproj）的回归测试。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from integration.project_bundle import (
    BUNDLE_FORMAT,
    MANIFEST_NAME,
    ProjectBundleError,
    export_project_bytes,
    import_project_bytes,
)
from integration.project_store import create_project, load_project, save_experiment
from integration.scene.store import create_scene, list_scenes, load_scene


def _seed_project(base):
    project = create_project("整包测试项目", "导出导入测试", "验证单文件", base_dir=base)
    pid = project["project_id"]
    scene = create_scene(
        pid,
        "整包测试场景",
        oip_project={"format": "oip", "parts": [{"id": "part-1"}]},
        base_dir=base,
    )
    save_experiment(
        pid,
        {
            "experiment_id": "SEXP-20260101-000000-ABCD",
            "project_id": pid,
            "scene_id": scene.scene_id,
            "kind": "factory",
            "created_at": "2026-01-01T00:00:00",
            "kpis": {"good_units": 12},
        },
        base_dir=base,
    )
    return project, scene


def test_export_import_roundtrip(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    project, scene = _seed_project(source)

    filename, data = export_project_bytes(project["project_id"], base_dir=source)
    assert filename.endswith(".aicproj")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert MANIFEST_NAME in names
        assert "project.json" in names
        assert f"scenes/{scene.scene_id}/scene.json" in names
        manifest = json.loads(archive.read(MANIFEST_NAME))
        assert manifest["format"] == BUNDLE_FORMAT
        assert manifest["counts"] == {"scenes": 1, "oip_projects": 1, "experiments": 1}

    summary = import_project_bytes(data, base_dir=target)
    assert summary["project_id"] != project["project_id"]
    assert summary["scenes"] == 1
    assert summary["experiments"] == 1
    assert summary["oip_projects"] == 1

    imported = load_project(summary["project_id"], base_dir=target)
    assert imported["name"].endswith("（导入）")
    scenes = list_scenes(summary["project_id"], base_dir=target)
    assert len(scenes) == 1
    doc = load_scene(summary["project_id"], scene.scene_id, base_dir=target)
    assert doc.project_id == summary["project_id"]
    assert doc.oip_project == {"format": "oip", "parts": [{"id": "part-1"}]}
    experiment_file = target / summary["project_id"] / "experiments" / "SEXP-20260101-000000-ABCD.json"
    record = json.loads(experiment_file.read_text(encoding="utf-8"))
    assert record["project_id"] == summary["project_id"]

    again = import_project_bytes(data, base_dir=target)
    assert again["name"].endswith("（导入）2")


def test_import_rejects_path_traversal(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.json", "{}")
        archive.writestr("../evil.txt", "x")
    with pytest.raises(ProjectBundleError):
        import_project_bytes(buffer.getvalue(), base_dir=tmp_path / "target")
    assert not (tmp_path / "evil.txt").exists()


def test_import_rejects_non_project_zip(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a project")
    with pytest.raises(ProjectBundleError):
        import_project_bytes(buffer.getvalue(), base_dir=tmp_path / "target")
