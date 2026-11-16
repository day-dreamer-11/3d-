"""只读公开接口测试：项目摘要、实验列表、HTML 报告与令牌鉴权。"""

from __future__ import annotations

import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

import public_api
from integration.factory_adapter import run_factory_experiment
from integration.paths import PROJECTS_DIR
from integration.project_store import create_project


@pytest.fixture(scope="module")
def api_project():
    project = create_project(
        f"公开接口测试-{uuid.uuid4().hex[:6]}",
        "只读接口自动测试项目。",
        "验证 ChatGPT 可读取的数据接口。",
    )
    record = run_factory_experiment(project, duration_min=1, persist=True)
    project["_test_experiment"] = record["experiment_id"]
    try:
        yield project
    finally:
        shutil.rmtree(PROJECTS_DIR / project["project_id"], ignore_errors=True)


@pytest.fixture()
def client():
    return TestClient(public_api.app)


def test_health_and_projects(client, api_project):
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["projects"] >= 1

    projects = client.get("/api/projects").json()
    match = next(p for p in projects if p["project_id"] == api_project["project_id"])
    assert match["name"] == api_project["name"]
    assert match["latest_factory"]["experiment_id"] == api_project["_test_experiment"]
    assert "oee_breakdown" in match["latest_factory"]
    assert match["experiment_counts"]["factory"] >= 1


def test_experiment_endpoints(client, api_project):
    pid = api_project["project_id"]
    experiments = client.get(f"/api/projects/{pid}/experiments", params={"kind": "factory"}).json()
    assert experiments
    assert experiments[0]["kind"] == "factory"
    assert experiments[0]["good_units"] is not None

    exp_id = api_project["_test_experiment"]
    full = client.get(f"/api/projects/{pid}/experiments/{exp_id}").json()
    assert full["experiment_id"] == exp_id
    assert "kpis" in full and "events" in full


def test_report_is_server_rendered_html(client, api_project):
    response = client.get("/report", params={"project_id": api_project["project_id"]})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert api_project["name"] in body
    assert "OEE" in body
    assert "仿真结果" in body


def test_token_authentication(client, api_project, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_TOKEN", "test-secret")
    assert client.get("/api/projects").status_code == 401
    assert client.get("/report", params={"project_id": api_project["project_id"]}).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer test-secret"}).status_code == 200
    assert (
        client.get("/report", params={"project_id": api_project["project_id"], "token": "test-secret"}).status_code
        == 200
    )
    assert client.get("/health").status_code == 200
