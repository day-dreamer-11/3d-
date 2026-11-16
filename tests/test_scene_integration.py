"""自建场景集成测试：映射、连接、引擎、版本、API 全链路。"""

from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from integration.project_store import default_project, load_experiment, save_project
from integration.scene.analysis import (
    analyze_scene_record,
    compare_scene_records,
    evaluate_scene_recommendations,
    generate_scene_recommendations,
)
from integration.scene.connections import (
    derive_geometry_candidates,
    initial_connections,
    validate_connections,
)
from integration.scene.engine import SceneSimulationError, run_scene_simulation
from integration.scene.mapping import map_oip_project
from integration.scene.models import Connection, SceneDocument
from integration.scene.store import (
    create_scene,
    ingest_oip_project,
    list_versions,
    load_scene,
    restore_version,
)

ROOT = Path(__file__).resolve().parents[1]


def oip_project(*, speed: float = 1.0, inspection_device: bool = True, robot: bool = True, extra_device: bool = False) -> dict:
    parts = [
        {
            "id": "pt_1",
            "type": "part",
            "packageId": "oip.box-spawner",
            "name": "Spawner",
            "position": [-8.0, 2.0, 0.0],
            "rotationY": 0.0,
            "params": {"boxesPerMinute": 15, "product": "oip.box", "fixedRate": True},
        },
        {
            "id": "pt_2",
            "type": "part",
            "packageId": "oip.roller-conveyor",
            "name": "Conveyor A",
            "position": [-5.0, 2.0, 0.0],
            "rotationY": 0.0,
            "params": {"length": 4.0, "width": 1.524, "speed": speed, "fpmUnits": False},
        },
    ]
    if inspection_device:
        parts.append(
            {
                "id": "pt_3",
                "type": "part",
                "packageId": "oip.color-sensor",
                "name": "Inspection B",
                "position": [-2.9, 2.0, 0.0],
                "rotationY": 0.0,
                "params": {"maxRange": 2.0},
            }
        )
    if robot:
        parts.append(
            {
                "id": "pt_4",
                "type": "part",
                "packageId": "oip.six-axis-robot",
                "name": "Robot C",
                "position": [-1.5, 2.0, 0.0],
                "rotationY": 0.0,
                "params": {"scale": 1.0, "speed": 120},
            }
        )
    parts += [
        {
            "id": "pt_5",
            "type": "part",
            "packageId": "oip.roller-conveyor",
            "name": "Conveyor D",
            "position": [2.0, 2.0, 0.0],
            "rotationY": 0.0,
            "params": {"length": 4.0, "width": 1.524, "speed": speed, "fpmUnits": False},
        },
        {
            "id": "pt_6",
            "type": "part",
            "packageId": "oip.despawner",
            "name": "Despawner",
            "position": [4.9, 2.0, 0.0],
            "rotationY": 0.0,
            "params": {},
        },
    ]
    if extra_device:
        parts.append(
            {
                "id": "pt_7",
                "type": "part",
                "packageId": "oip.diffuse-sensor",
                "name": "Extra Inspection",
                "position": [7.5, 2.0, 0.0],
                "rotationY": 0.0,
                "params": {},
            }
        )
    return {
        "version": 7,
        "name": "Minimal Factory",
        "scene": {"version": 4, "parts": parts},
        "packages": [],
    }


def project_and_scene(tmp_path: Path) -> tuple[dict, SceneDocument]:
    project = save_project(default_project(name=f"场景测试-{uuid.uuid4().hex[:6]}"), base_dir=tmp_path)
    scene = create_scene(project["project_id"], "最小工厂", base_dir=tmp_path)
    return project, scene


def imported_scene(tmp_path: Path, *, speed: float = 1.0) -> tuple[dict, SceneDocument]:
    project, scene = project_and_scene(tmp_path)
    doc = ingest_oip_project(project["project_id"], scene.scene_id, oip_project(speed=speed), base_dir=tmp_path)
    return project, doc


def test_mapping_keeps_ids_and_types_and_derives_five_connections(tmp_path):
    project, doc = imported_scene(tmp_path)
    device_ids = [d.id for d in doc.devices]
    assert device_ids == ["pt_1", "pt_2", "pt_3", "pt_4", "pt_5", "pt_6"]
    types = {d.id: d.simulation_type for d in doc.devices}
    assert types["pt_1"] == "spawner"
    assert types["pt_2"] == "conveyor"
    assert types["pt_3"] == "inspection"
    assert types["pt_4"] == "robot"
    assert types["pt_6"] == "despawner"
    assert len(doc.connections) == 5
    assert not doc.validation["errors"], doc.validation["errors"]
    assert doc.devices[1].params["speed_mps"] == pytest.approx(1.0)
    assert doc.devices[1].params["length_m"] == pytest.approx(4.0)
    assert doc.devices[2].params["cycle_sec"] == pytest.approx(2.0)
    assert doc.devices[3].params["cycle_sec"] == pytest.approx(4.0)
    assert doc.devices[0].params["interval_sec"] == pytest.approx(4.0)


def test_unknown_device_is_display_only_not_silently_dropped(tmp_path):
    project, scene = project_and_scene(tmp_path)
    oip = oip_project()
    oip["scene"]["parts"].append(
        {
            "id": "pt_x",
            "type": "part",
            "packageId": "vendor.unknown-machine",
            "position": [0.0, 0.0, 3.0],
            "rotationY": 0.0,
            "params": {},
        }
    )
    doc = ingest_oip_project(project["project_id"], scene.scene_id, oip, base_dir=tmp_path)
    unknown = next(d for d in doc.devices if d.id == "pt_x")
    assert unknown.simulation_type == "display_only"
    assert any(w["code"] == "unsupported_device" for w in doc.validation["warnings"])


def test_engine_is_deterministic_and_sensitive_to_speed_and_inspection(tmp_path):
    _project, doc = imported_scene(tmp_path, speed=0.5)
    first = run_scene_simulation(doc, duration_sec=600, seed=11, persist=False)
    second = run_scene_simulation(doc, duration_sec=600, seed=11, persist=False)
    assert first["kpis"] == second["kpis"]
    # 金标：引擎/映射/默认参数改动若改变结果，必须显式更新这里。
    assert first["kpis"]["good_units"] == 66
    assert first["kpis"]["defect_units"] == 7
    assert first["kpis"]["throughput_uph"] == 396.0
    assert first["kpis"]["wip"] == 9
    assert first["kpis"]["oee"]["oee"] == pytest.approx(0.7956, abs=1e-4)
    assert first["kpis"]["good_units"] > 0
    assert first["kpis"]["wip"] >= 0
    assert first["flow_insight"]["bottleneck"] == "pt_2"  # 0.5 m/s conveyor is the constraint
    assert len(first["device_metrics"]) == 6
    inspected = [e for e in first["events"] if e["event_type"] in ("INSPECTION_PASS", "INSPECTION_FAIL")]
    assert inspected, "inspection station must actually process items"
    assert all(e["source_id"] == "pt_3" for e in inspected)

    fast = copy.deepcopy(doc)
    for device in fast.devices:
        if device.simulation_type == "conveyor":
            device.params["speed_mps"] = 2.0
    fast_record = run_scene_simulation(fast, duration_sec=600, seed=11, persist=False)
    assert fast_record["kpis"]["throughput_uph"] > first["kpis"]["throughput_uph"]
    assert fast_record["flow_insight"]["bottleneck"] != "pt_2"

    slow_inspection = copy.deepcopy(doc)
    next(d for d in slow_inspection.devices if d.id == "pt_3").params["cycle_sec"] = 10.0
    slow_record = run_scene_simulation(slow_inspection, duration_sec=600, seed=11, persist=False)
    assert slow_record["flow_insight"]["bottleneck"] == "pt_3"
    assert slow_record["device_metrics"][2]["utilization"] > 0.8


def test_connection_validation_detects_missing_device_cycle_and_broken_flow(tmp_path):
    _project, doc = imported_scene(tmp_path)

    missing = copy.deepcopy(doc)
    missing.devices = [d for d in missing.devices if d.id != "pt_4"]
    result = validate_connections(missing)
    # 删除 pt_4 后，引用它的连接应报缺失
    assert any(e["code"] in ("missing_source_device", "missing_target_device") for e in result["errors"])

    cycle = copy.deepcopy(doc)
    cycle.connections.append(
        Connection(id="cycle", source_device="pt_6", source_port="in", target_device="pt_2", target_port="out")
    )
    result = validate_connections(cycle)
    assert any(e["code"] == "material_flow_cycle" for e in result["errors"])

    broken = copy.deepcopy(doc)
    broken.connections = [c for c in broken.connections if not (c.source_device == "pt_5" and c.target_device == "pt_6")]
    result = validate_connections(broken)
    assert any(e["code"] == "material_flow_broken" for e in result["errors"])

    moved = copy.deepcopy(doc)
    moved.devices[4].position = [20.0, 2.0, 0.0]
    result = validate_connections(moved)
    assert any(e["code"] == "connection_geometry_mismatch" for e in result["warnings"])
    assert [c.to_dict() for c in moved.connections] == [c.to_dict() for c in doc.connections]
    with pytest.raises(SceneSimulationError, match="场景校验失败"):
        run_scene_simulation(broken, duration_sec=60, persist=False)


def test_store_versions_are_immutable_and_idempotent(tmp_path):
    project, scene = project_and_scene(tmp_path)
    v1 = load_scene(project["project_id"], scene.scene_id, base_dir=tmp_path)
    doc2 = ingest_oip_project(project["project_id"], scene.scene_id, oip_project(speed=1.0), base_dir=tmp_path)
    assert doc2.version == v1.version + 1
    doc3, changed = __import__("integration.scene.store", fromlist=["save_scene"]).save_scene(doc2, base_dir=tmp_path)
    assert not changed
    assert doc3.version == doc2.version
    doc4 = ingest_oip_project(project["project_id"], scene.scene_id, oip_project(speed=2.0), base_dir=tmp_path)
    assert doc4.version == doc2.version + 1
    versions = list_versions(project["project_id"], scene.scene_id, base_dir=tmp_path)
    assert {v["version"] for v in versions} == {1, 2, 3}
    restored = restore_version(project["project_id"], scene.scene_id, 2, base_dir=tmp_path)
    assert restored.version == 4
    assert next(d for d in restored.devices if d.id == "pt_2").params["speed_mps"] == pytest.approx(1.0)


def test_analysis_and_optimizer_use_real_reruns(tmp_path):
    _project, doc = imported_scene(tmp_path, speed=0.5)
    record = run_scene_simulation(doc, duration_sec=600, seed=11, persist=False)
    analysis = analyze_scene_record(record)
    assert analysis["bottleneck"] == "pt_2"
    assert analysis["evidence"]
    recs = generate_scene_recommendations(doc, record)
    assert recs and recs[0]["patch"]
    result = evaluate_scene_recommendations(doc, record, recs, duration_sec=600, persist=False, max_candidates=2)
    assert result["evaluations"]
    ok = [e for e in result["evaluations"] if e["status"] == "ok"]
    assert ok
    assert all("baseline" in e and "candidate" in e for e in ok)
    # 候选必须来自真实重跑：至少一个候选的参数补丁改变了结果
    assert any(e["deltas"]["throughput_uph"] != 0 or e["deltas"]["avg_lead_time_sec"] != 0 for e in ok)
    comparison = compare_scene_records(record, result["baseline"])
    assert comparison["comparable"] is True


# ---------------------------------------------------------------------------
# API 集成：用独立数据目录启动真实 uvicorn
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("scene_api_data")
    projects = data_dir / "projects"
    project = save_project(default_project(name="API 场景测试项目"), base_dir=projects)
    port = _free_port()
    env = os.environ.copy()
    env["AIC_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(ROOT)
    log = data_dir / "api.log"
    handle = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "integration.scene.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base, timeout=60.0)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if client.get("/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError("API exited: " + log.read_text(encoding="utf-8", errors="replace")[-4000:])
        time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError("API did not start: " + log.read_text(encoding="utf-8", errors="replace")[-4000:])
    yield client, project["project_id"], data_dir
    client.close()
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    handle.close()


def _new_scene(client: httpx.Client, project_id: str, name: str) -> dict:
    response = client.post(f"/api/projects/{project_id}/scenes", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_api_full_flow_and_history_snapshot(api):
    client, project_id, data_dir = api
    scene = _new_scene(client, project_id, "API 最小工厂")
    sid = scene["scene_id"]
    response = client.post(f"/api/projects/{project_id}/scenes/{sid}/oip-import", json={"oip_project": oip_project(speed=0.5)})
    assert response.status_code == 200, response.text
    imported = response.json()
    assert len(imported["devices"]) == 6
    assert len(imported["connections"]) == 5
    assert len(imported["validation"]["errors"]) == 0, imported["validation"]

    devices = client.get(f"/api/projects/{project_id}/scenes/{sid}/devices").json()
    assert {d["simulation_type"] for d in devices} == {"spawner", "conveyor", "inspection", "robot", "despawner"}

    run = client.post(f"/api/projects/{project_id}/scenes/{sid}/simulations", json={"duration_sec": 600, "seed": 11})
    assert run.status_code == 200, run.text
    record = run.json()
    assert record["kpis"]["good_units"] > 0
    assert record["kpis"]["wip"] >= 0
    assert record["flow_insight"]["bottleneck"] == "pt_2"
    baseline_id = record["experiment_id"]
    baseline_speed = record["config"]["scene_snapshot"]["devices"][1]["params"]["speed_mps"]
    assert baseline_speed == pytest.approx(0.5)

    analysis = client.get(f"/api/projects/{project_id}/scenes/{sid}/analysis").json()
    assert analysis["bottleneck"] == "pt_2"
    assert analysis["evidence"]

    edge = client.post(f"/api/projects/{project_id}/scenes/{sid}/edge", json={"experiment_id": baseline_id})
    assert edge.status_code == 200, edge.text
    edge_record = edge.json()
    assert edge_record["mapping"]["task_count"] > 0
    assert edge_record["mapping"]["task_type_counts"].get("vision_inspection", 0) > 0
    assert len(edge_record["metrics"]) == 3
    assert all(m["total_tasks"] == edge_record["mapping"]["task_count"] for m in edge_record["metrics"].values())

    optimize = client.post(
        f"/api/projects/{project_id}/scenes/{sid}/optimize",
        json={"experiment_id": baseline_id, "duration_sec": 600, "max_candidates": 2},
    )
    assert optimize.status_code == 200, optimize.text
    opt = optimize.json()
    assert opt["evaluations"]
    assert any(e["status"] == "ok" for e in opt["evaluations"])

    report = client.post(
        f"/api/projects/{project_id}/scenes/{sid}/reports",
        json={"experiment_id": baseline_id, "edge_experiment_id": edge_record["experiment_id"], "optimization_id": opt["optimization_id"]},
    )
    assert report.status_code == 200, report.text
    rep = report.json()
    assert Path(rep["html_path"]).exists()
    assert "场景仿真报告" in rep["markdown"]
    assert "EdgeCoder" in rep["markdown"]

    # 参数变化 → 新场景版本；旧实验快照保持不变
    changed = client.post(
        f"/api/projects/{project_id}/scenes/{sid}/oip-import", json={"oip_project": oip_project(speed=2.0)}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == scene["version"] + 2
    old = client.get(f"/api/projects/{project_id}/experiments/{baseline_id}") if False else None
    # 通过实验列表 + 直接文件验证快照
    exp_file = data_dir / "projects" / project_id / "experiments" / f"{baseline_id}.json"
    assert exp_file.exists()
    assert json.loads(exp_file.read_text(encoding="utf-8"))["config"]["scene_snapshot"]["devices"][1]["params"]["speed_mps"] == pytest.approx(0.5)

    rerun = client.post(f"/api/projects/{project_id}/scenes/{sid}/simulations", json={"duration_sec": 600, "seed": 11})
    assert rerun.status_code == 200
    assert rerun.json()["kpis"]["throughput_uph"] > record["kpis"]["throughput_uph"]

    versions = client.get(f"/api/projects/{project_id}/scenes/{sid}/versions").json()
    assert len(versions) >= 3


def test_api_delete_device_and_broken_flow_are_rejected(api):
    client, project_id, _data_dir = api
    scene = _new_scene(client, project_id, "删除设备测试")
    sid = scene["scene_id"]
    first = client.post(f"/api/projects/{project_id}/scenes/{sid}/oip-import", json={"oip_project": oip_project()})
    assert first.status_code == 200

    deleted = client.post(
        f"/api/projects/{project_id}/scenes/{sid}/oip-import",
        json={"oip_project": oip_project(robot=False)},
    )
    assert deleted.status_code == 200
    payload = deleted.json()
    assert len(payload["devices"]) == 5
    assert len(payload["validation"]["errors"]) > 0
    assert any(e["code"] == "missing_target_device" for e in payload["validation"]["errors"])
    sim = client.post(f"/api/projects/{project_id}/scenes/{sid}/simulations", json={"duration_sec": 60})
    assert sim.status_code == 400
    assert "场景校验失败" in sim.json()["detail"]

    # 断开连接：显式表缺少 Conveyor D → Despawner
    scene2 = _new_scene(client, project_id, "断连测试")
    sid2 = scene2["scene_id"]
    client.post(f"/api/projects/{project_id}/scenes/{sid2}/oip-import", json={"oip_project": oip_project()})
    current = client.get(f"/api/projects/{project_id}/scenes/{sid2}/connections").json()
    conns = [c for c in current["connections"] if not (c["source_device"] == "pt_5" and c["target_device"] == "pt_6")]
    put = client.put(f"/api/projects/{project_id}/scenes/{sid2}/connections", json={"connections": conns})
    assert put.status_code == 200, put.text
    assert any(e["code"] == "material_flow_broken" for e in put.json()["validation"]["errors"])
    sim2 = client.post(f"/api/projects/{project_id}/scenes/{sid2}/simulations", json={"duration_sec": 60})
    assert sim2.status_code == 400


def test_api_add_device_keeps_ids_and_warns_isolated(api):
    client, project_id, _data_dir = api
    scene = _new_scene(client, project_id, "增加设备测试")
    sid = scene["scene_id"]
    client.post(f"/api/projects/{project_id}/scenes/{sid}/oip-import", json={"oip_project": oip_project()})
    added = client.post(
        f"/api/projects/{project_id}/scenes/{sid}/oip-import",
        json={"oip_project": oip_project(extra_device=True)},
    )
    assert added.status_code == 200, added.text
    payload = added.json()
    assert len(payload["devices"]) == 7
    ids = [d["id"] for d in payload["devices"]]
    assert ids[:6] == ["pt_1", "pt_2", "pt_3", "pt_4", "pt_5", "pt_6"]
    assert "pt_7" in ids
    assert any(w["code"] == "isolated_device" and "pt_7" in w.get("devices", []) for w in payload["validation"]["warnings"])
    # 已有显式连接不被自动改写
    assert len(payload["connections"]) == 5

def test_save_as_keeps_original_scene_and_saves_current_edits(api):
    client, project_id, _ = api
    scene_id = _new_scene(client, project_id, "原场景")["scene_id"]
    url = "/api/oip/scene-save"
    payload = {"project_id": project_id, "scene_id": scene_id}
    saved = client.post(url, json={**payload, "project": oip_project(speed=0.5)})
    assert saved.status_code == 200, saved.text

    copied = client.post(url, json={
        **payload, "project": oip_project(speed=0.75), "save_as_name": "新方案",
    })
    assert copied.status_code == 200, copied.text
    result = copied.json()
    assert result["scene_id"] != scene_id
    assert result["name"] == "新方案"
    assert result["scene_version"] == 1

    def conveyor_speed(sid):
        devices = client.get(f"/api/projects/{project_id}/scenes/{sid}/devices").json()
        return next(d for d in devices if d["id"] == "pt_2")["params"]["speed_mps"]

    assert conveyor_speed(scene_id) == pytest.approx(0.5)
    assert conveyor_speed(result["scene_id"]) == pytest.approx(0.75)
