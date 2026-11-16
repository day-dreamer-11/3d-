"""统一平台界面（建模分析一体化工作台）的 AppTest 冒烟测试。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from integration.paths import PROJECTS_DIR
from integration.project_store import create_project

APP_PATH = Path(__file__).resolve().parents[1] / "factory_app.py"

NEW_PAGES = ["平台总览", "场景编辑器", "建模分析工作台", "实验历史", "系统设置"]


@pytest.fixture()
def platform_project():
    project = create_project(
        f"自动测试平台项目-{uuid.uuid4().hex[:6]}",
        "AppTest 自动创建，测试后清理。",
        "验证统一入口。",
    )
    try:
        yield project
    finally:
        shutil.rmtree(PROJECTS_DIR / project["project_id"], ignore_errors=True)


def _app_with_project(project) -> "AppTest":
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=300)
    at.run()
    at.sidebar.selectbox[0].set_value(project["project_id"]).run()
    return at


def test_all_pages_render_without_errors(platform_project):
    at = _app_with_project(platform_project)
    assert not at.exception
    assert not at.error
    for page in NEW_PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
        assert not at.error, f"{page}: {[e.value for e in at.error]}"


def test_workspace_empty_project_and_create_scene(platform_project):
    at = _app_with_project(platform_project)
    at.sidebar.radio[0].set_value("建模分析工作台").run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    assert "建模分析工作台" in " ".join(getattr(m, "value", "") for m in at.markdown)

    create = next(b for b in at.button if b.label == "新建空白场景")
    create.click().run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]

    from integration.scene.client import SceneClient

    client = SceneClient()
    try:
        assert client.list_scenes(platform_project["project_id"])
    finally:
        client.close()


def test_workspace_with_scene_renders(platform_project):
    from integration.scene.client import SceneClient

    client = SceneClient()
    try:
        created = client.create_scene(platform_project["project_id"], "AppTest 空白场景")
    finally:
        client.close()

    at = _app_with_project(platform_project)
    at.sidebar.radio[0].set_value("建模分析工作台").run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    assert created["scene_id"]
