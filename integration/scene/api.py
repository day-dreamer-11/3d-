"""自建工厂场景 FastAPI 业务接口。

路径沿用项目的 /api 风格；运行方式：
    python -m uvicorn integration.scene.api:app --host 127.0.0.1 --port 8701

所有指标来自实际 Python 离散事件仿真；接口不返回硬编码 KPI。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from integration.project_store import ProjectError, load_experiment, load_project

from .analysis import (
    analyze_scene_record,
    apply_and_rerun,
    compare_scene_records,
    evaluate_scene_recommendations,
    generate_scene_recommendations,
)
from .connections import derive_geometry_candidates
from .edge import SceneEdgeError, run_scene_edge_experiment
from .engine import SceneSimulationError, apply_scene_patch, run_scene_simulation
from .manager import get_simulation_manager
from .mapping import bridge_ports
from .models import SceneDocument, new_connection_id
from .oip_service import OipServiceError, get_oip_manager
from .report import generate_scene_report
from .store import (
    SceneError,
    create_scene,
    delete_scene,
    ingest_oip_project,
    list_reports,
    list_scene_experiments,
    list_scenes,
    list_versions,
    load_scene,
    restore_version,
    save_scene,
    validate_scene_document,
)

app = FastAPI(
    title="AIC 场景编辑与 Python 仿真接口",
    version="2.0.0",
    description="OIP 三维场景 → 统一设备模型 → Python 离散事件仿真 → EdgeCoder → 分析报告。全部指标为软件仿真结果。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SceneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    oip_project: Optional[dict[str, Any]] = None
    simulation_settings: Optional[dict[str, Any]] = None


class ScenePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    simulation_settings: Optional[dict[str, Any]] = None


class OipSaveBody(BaseModel):
    project_id: str
    scene_id: str
    project: dict[str, Any]
    save_as_name: Optional[str] = Field(default=None, min_length=1, max_length=80)


class ConnectionsBody(BaseModel):
    connections: list[dict[str, Any]]


class DeriveBody(BaseModel):
    adopt: bool = False
    tolerance_m: float = 1.5


class RunBody(BaseModel):
    duration_sec: Optional[float] = None
    seed: Optional[int] = None
    persist: bool = True


class LiveStartBody(BaseModel):
    duration_sec: float = 1800
    seed: int = 42
    speed: float = 1.0


class EdgeBody(BaseModel):
    experiment_id: Optional[str] = None


class WhatIfBody(BaseModel):
    experiment_id: Optional[str] = None
    patch: dict[str, Any]
    duration_sec: Optional[float] = None
    seed: Optional[int] = None


class OptimizeBody(BaseModel):
    experiment_id: Optional[str] = None
    duration_sec: Optional[float] = None
    max_candidates: int = 3


class ApplyOptimizeBody(BaseModel):
    patch: dict[str, Any]
    duration_sec: Optional[float] = None
    seed: Optional[int] = None
    name: Optional[str] = None


class ReportBody(BaseModel):
    experiment_id: Optional[str] = None
    edge_experiment_id: Optional[str] = None
    optimization_id: Optional[str] = None


def _http_error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


def _scene_experiments(project_id: str, scene_id: str) -> list[dict[str, Any]]:
    records = list_scene_experiments(project_id, scene_id, kind="factory")
    return [r for r in records if r.get("factory_engine") == "scene_discrete_event"]


def _is_live_record(record: dict[str, Any]) -> bool:
    return bool(record.get("live")) or record.get("source") == "scene_engine_live" or "实时" in str(record.get("label") or "")


def _latest_factory(project_id: str, scene_id: str) -> Optional[dict[str, Any]]:
    records = _scene_experiments(project_id, scene_id)
    offline = [r for r in records if r.get("source") == "scene_engine" and not _is_live_record(r)]
    if offline:
        return offline[0]
    return records[0] if records else None


def _load_record(project_id: str, experiment_id: Optional[str], scene_id: Optional[str] = None) -> dict[str, Any]:
    if experiment_id:
        record = load_experiment(project_id, experiment_id)
        if scene_id and record.get("scene_id") and record.get("scene_id") != scene_id:
            raise SceneError("实验不属于该场景")
        return record
    if not scene_id:
        raise SceneError("必须提供 experiment_id 或 scene_id")
    record = _latest_factory(project_id, scene_id)
    if record is None:
        raise SceneError("该场景还没有仿真实验，请先运行一次")
    return record


def _summary(doc: SceneDocument) -> dict[str, Any]:
    return {
        "scene_id": doc.scene_id,
        "project_id": doc.project_id,
        "name": doc.name,
        "version": doc.version,
        "updated_at": doc.updated_at,
        "devices": len(doc.devices),
        "connections": len(doc.connections),
        "errors": len(doc.validation.get("errors", [])),
        "warnings": len(doc.validation.get("warnings", [])),
    }


# ---------------------------------------------------------------------------
# health / scenes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "aic-scene-api",
        "version": "2.0.0",
        "oip_commit": "dcebf53",
        "engine": "scene_discrete_event",
        "note": "全部指标为 Python 软件仿真结果，不是真实工厂实测。",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/projects/{project_id}/scenes")
def scenes(project_id: str) -> list[dict[str, Any]]:
    try:
        return list_scenes(project_id)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404 if "不存在" in str(exc) else 400)


@app.post("/api/projects/{project_id}/scenes", status_code=201)
def create(project_id: str, body: SceneCreate) -> dict[str, Any]:
    try:
        doc = create_scene(
            project_id,
            body.name,
            oip_project=body.oip_project,
            simulation_settings=body.simulation_settings,
        )
        return doc.to_dict()
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


@app.get("/api/projects/{project_id}/scenes/{scene_id}")
def get_scene(project_id: str, scene_id: str, include_oip: bool = False) -> dict[str, Any]:
    try:
        payload = load_scene(project_id, scene_id, include_oip=include_oip).to_dict()
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)
    # OIP 工程可能 ~20MB；页面刷新只取统一场景摘要，编辑器走专用 /oip-project。
    if not include_oip:
        payload["oip_project"] = None
    return payload


@app.put("/api/projects/{project_id}/scenes/{scene_id}")
def update_scene(project_id: str, scene_id: str, body: ScenePatch) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=True)
        if body.name is not None:
            doc.name = body.name
        if body.simulation_settings is not None:
            doc.simulation_settings.update(body.simulation_settings)
        saved, changed = save_scene(doc, ingest_source="api_update")
        return {**_summary(saved), "changed": changed, "validation": saved.validation}
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


@app.delete("/api/projects/{project_id}/scenes/{scene_id}", status_code=204)
def remove_scene(project_id: str, scene_id: str) -> None:
    try:
        delete_scene(project_id, scene_id)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/devices")
def devices(project_id: str, scene_id: str) -> list[dict[str, Any]]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=False)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)
    return [
        {**device.to_dict(), "ports": bridge_ports(device)}
        for device in doc.devices
    ]


@app.get("/api/projects/{project_id}/scenes/{scene_id}/connections")
def connections(project_id: str, scene_id: str) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=False)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)
    return {
        "connections": [c.to_dict() for c in doc.connections],
        "geometry_candidates": [c.to_dict() for c in doc.geometry_candidates],
        "validation": doc.validation,
    }


@app.put("/api/projects/{project_id}/scenes/{scene_id}/connections")
def set_connections(project_id: str, scene_id: str, body: ConnectionsBody) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=True)
        from .models import Connection

        parsed = []
        for raw in body.connections:
            conn = Connection.from_dict(raw)
            if not raw.get("id"):
                conn.id = new_connection_id()
            parsed.append(conn)
        doc.connections = parsed
        saved, changed = save_scene(doc, ingest_source="api_connections")
        return {**_summary(saved), "changed": changed, "validation": saved.validation, "connections": [c.to_dict() for c in saved.connections]}
    except (SceneError, ProjectError, ValueError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/derive-connections")
def derive(project_id: str, scene_id: str, body: DeriveBody) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=True)
        candidates, issues = derive_geometry_candidates(doc.devices, tolerance_m=body.tolerance_m)
        doc.geometry_candidates = candidates
        if body.adopt:
            doc.connections = candidates
        saved, changed = save_scene(doc, ingest_source="api_derive_connections")
        return {
            "candidates": [c.to_dict() for c in candidates],
            "adopted": body.adopt,
            "changed": changed,
            "validation": saved.validation,
            "issues": [i.to_dict() for i in issues],
        }
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/validate")
def validate(project_id: str, scene_id: str) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=True)
        result = validate_scene_document(doc)
        doc.validation = result
        save_scene(doc, bump_version=False, ingest_source="api_validate")
        return result
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/oip-project")
def oip_project(project_id: str, scene_id: str) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=True)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)
    if not doc.oip_project:
        return {"version": 7, "name": doc.name, "scene": {"version": 4, "parts": []}, "packages": []}
    return doc.oip_project


@app.post("/api/projects/{project_id}/scenes/{scene_id}/oip-import")
def import_oip(project_id: str, scene_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        oip = body.get("oip_project") if isinstance(body.get("oip_project"), dict) else body
        doc = ingest_oip_project(project_id, scene_id, oip)
        return {**_summary(doc), "validation": doc.validation, "devices": [d.to_dict() for d in doc.devices], "connections": [c.to_dict() for c in doc.connections]}
    except (SceneError, ProjectError, ValueError) as exc:
        raise _http_error(exc)


@app.post("/api/oip/scene-save")
def oip_scene_save(body: OipSaveBody) -> dict[str, Any]:
    """OIP 编辑器（bridge）保存后自动调用：转换、保存新版本并返回摘要。"""
    try:
        doc = ingest_oip_project(
            body.project_id, body.scene_id, body.project, save_as_name=body.save_as_name
        )
        return {
            "ok": True,
            "scene_id": doc.scene_id,
            "scene_version": doc.version,
            "name": doc.name,
            "devices": len(doc.devices),
            "connections": len(doc.connections),
            "errors": len(doc.validation.get("errors", [])),
            "warnings": len(doc.validation.get("warnings", [])),
            "validation": doc.validation,
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }
    except (SceneError, ProjectError, ValueError) as exc:
        raise _http_error(exc)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/export")
def export_scene(project_id: str, scene_id: str) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=True)
        return doc.to_dict()
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/versions")
def versions(project_id: str, scene_id: str) -> list[dict[str, Any]]:
    try:
        return list_versions(project_id, scene_id)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/versions/{version}")
def version_detail(project_id: str, scene_id: str, version: int) -> dict[str, Any]:
    try:
        return load_scene(project_id, scene_id, version=version, include_oip=False).to_dict()
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/versions/{version}/restore")
def restore(project_id: str, scene_id: str, version: int) -> dict[str, Any]:
    try:
        doc = restore_version(project_id, scene_id, version)
        return {**_summary(doc), "restored_from": version}
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------


@app.post("/api/projects/{project_id}/scenes/{scene_id}/simulations")
def run_simulation(project_id: str, scene_id: str, body: RunBody) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=False)
        record = run_scene_simulation(
            doc,
            duration_sec=body.duration_sec,
            seed=body.seed,
            persist=body.persist,
            label="场景离线仿真",
        )
        return record
    except (SceneError, ProjectError, SceneSimulationError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/simulations/live")
def start_live(project_id: str, scene_id: str, body: LiveStartBody) -> dict[str, Any]:
    try:
        doc = load_scene(project_id, scene_id, include_oip=False)
        sim = get_simulation_manager().create(doc, duration_sec=body.duration_sec, seed=body.seed, speed=body.speed)
        sim.start()
        return sim.snapshot()
    except (SceneError, ProjectError, SceneSimulationError) as exc:
        raise _http_error(exc)


@app.get("/api/simulations")
def list_simulations() -> list[dict[str, Any]]:
    return get_simulation_manager().list()


@app.get("/api/simulations/{sim_id}")
def get_simulation(sim_id: str) -> dict[str, Any]:
    try:
        return get_simulation_manager().get(sim_id).snapshot()
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.post("/api/simulations/{sim_id}/pause")
def pause(sim_id: str) -> dict[str, Any]:
    try:
        sim = get_simulation_manager().get(sim_id)
        sim.pause()
        return sim.snapshot()
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.post("/api/simulations/{sim_id}/resume")
def resume(sim_id: str) -> dict[str, Any]:
    try:
        sim = get_simulation_manager().get(sim_id)
        sim.resume()
        return sim.snapshot()
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.post("/api/simulations/{sim_id}/stop")
def stop(sim_id: str) -> dict[str, Any]:
    try:
        sim = get_simulation_manager().get(sim_id)
        sim.stop()
        return sim.snapshot()
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.get("/api/simulations/{sim_id}/kpis")
def sim_kpis(sim_id: str) -> dict[str, Any]:
    try:
        snap = get_simulation_manager().get(sim_id).snapshot()
        return {"sim_id": sim_id, "status": snap["status"], "sim_time_sec": snap["sim_time_sec"], "kpis": snap["kpis"], "flow_insight": snap["flow_insight"], "timestamp": snap["timestamp"]}
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.get("/api/simulations/{sim_id}/devices/status")
def sim_devices(sim_id: str) -> list[dict[str, Any]]:
    try:
        return get_simulation_manager().get(sim_id).snapshot()["device_states"]
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.get("/api/simulations/{sim_id}/events")
def sim_events(sim_id: str, limit: int = Query(default=200, ge=1, le=5000)) -> dict[str, Any]:
    try:
        sim = get_simulation_manager().get(sim_id)
        with sim._lock:  # noqa: SLF001 - 同一包内的只读快照
            events = list(sim.engine.events[-limit:])
            seq = sim.engine._seq  # noqa: SLF001
        return {"events": events, "seq": seq}
    except SceneSimulationError as exc:
        raise _http_error(exc, 404)


@app.get("/api/simulations/{sim_id}/result")
def sim_result(sim_id: str) -> dict[str, Any]:
    try:
        sim = get_simulation_manager().get(sim_id)
        result = sim.result()
        if result is None:
            raise SceneSimulationError("仿真尚未结束/停止，暂无完整结果")
        return result
    except SceneSimulationError as exc:
        raise _http_error(exc, 409)


# ---------------------------------------------------------------------------
# analysis / edge / optimization / report
# ---------------------------------------------------------------------------


@app.get("/api/projects/{project_id}/scenes/{scene_id}/experiments")
def scene_experiments(project_id: str, scene_id: str) -> list[dict[str, Any]]:
    try:
        records = list_scene_experiments(project_id, scene_id)
        return [
            {
                "experiment_id": r.get("experiment_id"),
                "kind": r.get("kind"),
                "factory_engine": r.get("factory_engine"),
                "scene_version": r.get("scene_version"),
                "created_at": r.get("created_at"),
                "label": r.get("label"),
                "kpis": r.get("kpis"),
                "source": r.get("source"),
            }
            for r in records
        ]
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


@app.get("/api/projects/{project_id}/experiments/{experiment_id}")
def get_experiment(project_id: str, experiment_id: str) -> dict[str, Any]:
    try:
        return load_experiment(project_id, experiment_id)
    except ProjectError as exc:
        raise _http_error(exc, 404)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/compare")
def compare_experiments(
    project_id: str,
    scene_id: str,
    baseline: str = Query(...),
    candidate: str = Query(...),
) -> dict[str, Any]:
    try:
        base = load_experiment(project_id, baseline)
        cand = load_experiment(project_id, candidate)
        return compare_scene_records(base, cand)
    except ProjectError as exc:
        raise _http_error(exc, 404)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/analysis")
def analysis(project_id: str, scene_id: str, experiment_id: Optional[str] = None) -> dict[str, Any]:
    try:
        record = _load_record(project_id, experiment_id, scene_id)
        result = analyze_scene_record(record)
        result["experiment_id"] = record.get("experiment_id")
        result["scene_id"] = scene_id
        result["scene_version"] = record.get("scene_version")
        return result
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/edge")
def run_edge(project_id: str, scene_id: str, body: EdgeBody) -> dict[str, Any]:
    try:
        project = load_project(project_id)
        record = _load_record(project_id, body.experiment_id, scene_id)
        return run_scene_edge_experiment(project, record)
    except (SceneError, ProjectError, SceneEdgeError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/what-if")
def what_if(project_id: str, scene_id: str, body: WhatIfBody) -> dict[str, Any]:
    try:
        scene = load_scene(project_id, scene_id, include_oip=False)
        base = _load_record(project_id, body.experiment_id, scene_id)
        patched_scene = apply_scene_patch(scene, body.patch)
        candidate = run_scene_simulation(
            patched_scene,
            duration_sec=body.duration_sec or (base.get("config") or {}).get("duration_sec"),
            seed=body.seed if body.seed is not None else (base.get("config") or {}).get("seed"),
            persist=True,
            label="What-if 候选实验",
        )
        return {"candidate": candidate, "comparison": compare_scene_records(base, candidate)}
    except (SceneError, ProjectError, SceneSimulationError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/optimize")
def optimize(project_id: str, scene_id: str, body: OptimizeBody) -> dict[str, Any]:
    try:
        scene = load_scene(project_id, scene_id, include_oip=False)
        record = _load_record(project_id, body.experiment_id, scene_id)
        base_duration = (record.get("config") or {}).get("duration_sec") or scene.simulation_settings.get("duration_sec") or 1800
        if record.get("source") == "scene_engine_live":
            base_duration = scene.simulation_settings.get("duration_sec") or 1800
        duration = float(body.duration_sec or base_duration)
        recommendations = generate_scene_recommendations(scene, record)
        result = evaluate_scene_recommendations(
            scene,
            record,
            recommendations,
            duration_sec=duration,
            persist=True,
            max_candidates=max(1, min(5, body.max_candidates)),
        )
        best = result.get("best")
        return {
            "optimization_id": result["optimization_id"],
            "baseline_experiment_id": result["baseline"].get("experiment_id"),
            "baseline_metrics": result["baseline_metrics"],
            "recommendations": recommendations,
            "evaluations": [
                {k: v for k, v in ev.items() if k != "candidate_record"} for ev in result["evaluations"]
            ],
            "best": {k: v for k, v in best.items() if k != "candidate_record"} if best else None,
            "assumptions": result["summary"]["assumptions"],
        }
    except (SceneError, ProjectError, SceneSimulationError) as exc:
        raise _http_error(exc)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/optimize/apply")
def apply_optimization(project_id: str, scene_id: str, body: ApplyOptimizeBody) -> dict[str, Any]:
    try:
        scene = load_scene(project_id, scene_id, include_oip=True)
        patched, record = apply_and_rerun(
            scene,
            body.patch,
            duration_sec=body.duration_sec,
            seed=body.seed,
        )
        if body.name:
            patched.name = body.name
        saved, changed = save_scene(patched, ingest_source="optimization_applied")
        persisted = run_scene_simulation(
            saved,
            duration_sec=body.duration_sec,
            seed=body.seed,
            persist=True,
            label="应用优化后的场景实验",
        )
        return {"scene": saved.to_dict(), "changed": changed, "experiment": persisted}
    except (SceneError, ProjectError, SceneSimulationError) as exc:
        raise _http_error(exc)


@app.get("/api/projects/{project_id}/scenes/{scene_id}/reports")
def reports(project_id: str, scene_id: str) -> list[dict[str, Any]]:
    try:
        return list_reports(project_id, scene_id)
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc, 404)


@app.post("/api/projects/{project_id}/scenes/{scene_id}/reports")
def create_report(project_id: str, scene_id: str, body: ReportBody) -> dict[str, Any]:
    try:
        project = load_project(project_id)
        scene = load_scene(project_id, scene_id, include_oip=False)
        record = _load_record(project_id, body.experiment_id, scene_id)
        edge_record = load_experiment(project_id, body.edge_experiment_id) if body.edge_experiment_id else None
        optimization = load_experiment(project_id, body.optimization_id) if body.optimization_id else None
        result = generate_scene_report(
            project,
            scene,
            record,
            analyze_scene_record(record),
            edge_record=edge_record,
            optimization=optimization,
        )
        return {
            "report_id": result["report_id"],
            "json_path": result["json_path"],
            "markdown_path": result["markdown_path"],
            "html_path": result["html_path"],
            "markdown": result["markdown"],
            "html": result["html"],
        }
    except (SceneError, ProjectError) as exc:
        raise _http_error(exc)


# ---------------------------------------------------------------------------
# OIP service management
# ---------------------------------------------------------------------------


@app.get("/api/oip/status")
def oip_status() -> dict[str, Any]:
    return get_oip_manager().status()


@app.post("/api/oip/start")
def oip_start() -> dict[str, Any]:
    try:
        return get_oip_manager().start()
    except OipServiceError as exc:
        raise _http_error(exc, 500)


@app.post("/api/oip/stop")
def oip_stop() -> dict[str, Any]:
    get_oip_manager().stop()
    return {"stopped": True}


@app.get("/api/oip/url")
def oip_url(request: Request, project_id: Optional[str] = None, scene_id: Optional[str] = None) -> dict[str, Any]:
    try:
        api_base = str(request.base_url).rstrip("/")
        url = get_oip_manager().editor_url(api_base=api_base, project_id=project_id, scene_id=scene_id)
        return {"url": url, "commit": "dcebf53", "api": api_base}
    except OipServiceError as exc:
        raise _http_error(exc, 500)
