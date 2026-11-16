"""EdgeCoder × RFTwin 集成层测试（项目存储、参数映射、工厂/边缘实验、规则优化）。"""

from __future__ import annotations

import copy
import math
import uuid

import pytest

from integration.analysis import (
    analyze_record,
    apply_project_patch,
    compare_factory_records,
    evaluate_recommendations,
    generate_recommendations,
    experiment_kpis,
    oee_breakdown,
    strategy_ranking,
    target_attainment,
)
from integration.edge_mapping import (
    EdgeMappingError,
    build_compute_tasks,
    run_edge_experiment,
)
from integration.factory_adapter import (
    build_factory_params,
    normalize_event,
    run_factory_experiment,
)
from integration.project_store import (
    ProjectError,
    default_project,
    list_experiments,
    list_projects,
    load_experiment,
    load_project,
    save_experiment,
    save_project,
)


def _project() -> dict:
    return default_project(name=f"集成测试项目-{uuid.uuid4().hex[:6]}")


def test_project_save_load_roundtrip(tmp_path):
    project = _project()
    saved = save_project(project, base_dir=tmp_path)
    assert saved["project_id"] == project["project_id"]
    loaded = load_project(project["project_id"], base_dir=tmp_path)
    assert loaded["name"] == project["name"]
    assert loaded["factory"]["seed"] == 42
    assert [p["project_id"] for p in list_projects(base_dir=tmp_path)] == [project["project_id"]]


def test_project_validation_rejects_invalid_parameters(tmp_path):
    project = _project()
    project["factory"]["cells"]["welding"]["cycle_sec"] = 1.0
    with pytest.raises(ProjectError):
        save_project(project, base_dir=tmp_path)

    project = _project()
    project["edge"]["nodes"] = [n for n in project["edge"]["nodes"] if n["node_type"] != "cloud"]
    with pytest.raises(ProjectError):
        save_project(project, base_dir=tmp_path)

    project = _project()
    project["factory"]["seed"] = -1
    with pytest.raises(ProjectError):
        save_project(project, base_dir=tmp_path)


def test_experiment_persistence_and_nan_sanitizing(tmp_path):
    project = save_project(_project(), base_dir=tmp_path)
    record = {
        "experiment_id": f"TEXP-{uuid.uuid4().hex[:8].upper()}",
        "project_id": project["project_id"],
        "kind": "factory",
        "created_at": "2026-10-29T10:00:00",
        "value": float("nan"),
        "nested": [{"value": float("inf")}],
    }
    save_experiment(project["project_id"], record, base_dir=tmp_path)
    loaded = load_experiment(project["project_id"], record["experiment_id"], base_dir=tmp_path)
    assert loaded["value"] is None
    assert loaded["nested"][0]["value"] is None
    assert len(list_experiments(project["project_id"], base_dir=tmp_path)) == 1
    assert len(list_experiments(project["project_id"], kind="edge", base_dir=tmp_path)) == 0


def test_build_factory_params_maps_project_config():
    project = _project()
    project["factory"]["seed"] = 7
    project["factory"]["cells"]["welding"]["cycle_sec"] = 30.0
    project["factory"]["buffers"]["raw_material"]["capacity"] = 77
    project["factory"]["vision_fail_probability"] = 0.01
    project["factory"]["minor_stop_probability"] = 0.02
    project["line"]["target_quantity"] = 900
    params = build_factory_params(project)
    welding_cycle = params["cells"]["welding"]["station_cycle_sec"]
    assert math.isclose(sum(welding_cycle.values()), 30.0, abs_tol=1e-6)
    assert params["buffers"]["raw_material"]["capacity"] == 77
    assert params["cells"]["vision_inspection"]["fail_probability"] == 0.01
    assert params["minor_stop"]["probability_per_cycle"] == 0.02
    assert params["seed_default"] == 7
    assert params["target_good_units_per_shift"] == 900


def test_normalize_event_failure_classification():
    project = _project()
    base = {
        "seq": 1,
        "sim_tick": 10,
        "sim_time": "2026-08-22T10:00:01+08:00",
        "source_type": "STATION",
        "source_id": "Q-01",
        "severity": "HIGH",
        "message": "Q-01 READY -> BLOCKED",
        "event_id": "EVT-1",
    }
    blocked = normalize_event({**base, "event_type": "STATION_BLOCKED"}, project, "EXP-1")
    assert blocked["failure_event"] is None
    jam = normalize_event({**base, "event_type": "CONVEYOR_JAMMED"}, project, "EXP-1")
    assert jam["failure_event"] == "CONVEYOR_JAMMED"
    failed = normalize_event(
        {**base, "event_type": "INSPECTION_FAIL", "message": "PART-0001 failed inspection"},
        project,
        "EXP-1",
    )
    assert failed["failure_event"] == "INSPECTION_FAIL"
    assert failed["task_id"] == "PART-0001"


@pytest.fixture(scope="module")
def project():
    return _project()


@pytest.fixture(scope="module")
def factory_run(project):
    return run_factory_experiment(project, duration_min=3, persist=False)


def test_factory_experiment_is_deterministic(project):
    first = run_factory_experiment(project, duration_min=2, persist=False)
    second = run_factory_experiment(project, duration_min=2, persist=False)
    assert first["kpis"]["total_output"] == second["kpis"]["total_output"]
    assert first["snapshot"]["seq"] == second["snapshot"]["seq"]
    assert [e["event_type"] for e in first["events"]] == [e["event_type"] for e in second["events"]]
    assert first["flow_insight"]["bottleneck"] == second["flow_insight"]["bottleneck"]


def test_factory_experiment_consistency(factory_run):
    parts = factory_run["snapshot"]["state"]["parts"]
    assert parts["created"] == parts["wip"] + parts["good_completed"] + parts["scrap"]
    assert factory_run["window_metrics"]["minutes"] == 3
    assert factory_run["event_stats"]["inspection_events"] > 0
    assert factory_run["flow_insight"]["bottleneck"]
    assert factory_run["station_metrics"]
    assert factory_run["orders"]
    blocked_failures = [
        e for e in factory_run["events"] if e["failure_event"] and "BLOCKED" in e["event_type"]
    ]
    assert blocked_failures == []


def test_edge_mapping_and_fair_comparison(project, factory_run):
    tasks, meta = build_compute_tasks(project, factory_run)
    assert len(tasks) == meta["mapped_tasks"] >= 1
    assert tasks[0].arrival_time_ms == 0.0
    arrivals = [t.arrival_time_ms for t in tasks]
    assert arrivals == sorted(arrivals)

    record = run_edge_experiment(project, factory_run, persist=False)
    assert set(record["runs"]) == {"fixed_local", "fixed_cloud", "dynamic"}
    expected_ids = [t.task_id for t in tasks]
    for key in ("fixed_local", "fixed_cloud", "dynamic"):
        run = record["runs"][key]
        assert [r["task"]["task_id"] for r in run["results"]] == expected_ids
        assert all(r["decision_reason"] for r in run["results"])
        metrics = record["metrics"][key]
        assert (
            metrics["on_time_tasks"] + metrics["timeout_tasks"] + metrics["rejected_tasks"]
            == metrics["total_tasks"]
        )
    assert set(record["costs"]) == {"fixed_local", "fixed_cloud", "dynamic"}
    assert record["factory_experiment_id"] == factory_run["experiment_id"]


def test_edge_mapping_without_inspections_fails(project):
    with pytest.raises(EdgeMappingError):
        build_compute_tasks(project, {"events": [], "recent_inspections": []})


def test_apply_project_patch_is_copy_on_write():
    project = _project()
    updated = apply_project_patch(
        project, {"factory": {"cells": {"welding": {"cycle_sec": 30.0}}}}
    )
    assert updated["factory"]["cells"]["welding"]["cycle_sec"] == 30.0
    assert project["factory"]["cells"]["welding"]["cycle_sec"] == 40.0
    assert updated["project_id"] == project["project_id"]


def test_analysis_recommendations_and_verification(project, factory_run):
    analysis = analyze_record(factory_run)
    assert analysis["bottleneck"]
    assert analysis["evidence"]
    assert experiment_kpis(factory_run)["window_good_units"] >= 0

    recommendations = generate_recommendations(project, factory_run)
    assert recommendations
    assert any(rec.get("evidence") for rec in recommendations)
    assert all(rec.get("title") for rec in recommendations)

    evaluation = evaluate_recommendations(
        project, factory_run, recommendations, duration_min=2, max_candidates=1
    )
    first = evaluation["evaluations"][0]
    assert first["status"] in ("ok", "skipped")
    assert evaluation["baseline"]["window_good_units"] >= 0
    if first["status"] == "ok":
        assert math.isfinite(first["delta_pct"]["window_good_units"])
        assert math.isfinite(first["score"])


def test_live_capture_events_share_record_experiment_id(project):
    """实时采集记录中的事件必须携带该记录自己的 experiment_id（可追溯）。"""
    from integration.factory_client import capture_live_record

    cell = {
        "cell_id": "CELL-WELDING",
        "name": "焊接",
        "state": "RUNNING",
        "oee": 0.9,
        "throughput_uph": 100.0,
        "avg_station_cycle_sec": 40.0,
        "is_bottleneck": False,
        "active_stations": 4,
        "idle_stations": 0,
        "error_stations": 0,
        "input_buffer": {"occupancy": 1, "capacity": 8},
        "output_buffer": {"occupancy": 1, "capacity": 8},
        "stations": [],
    }
    snapshot = {
        "seq": 10,
        "sim_time": "2026-08-22T10:05:00+08:00",
        "state": {"cells": [cell], "orders": []},
    }
    event = {
        "seq": 9,
        "sim_tick": 3000,
        "sim_time": "2026-08-22T10:05:00+08:00",
        "source_type": "STATION",
        "source_id": "Q-01",
        "event_type": "INSPECTION_PASS",
        "severity": "INFO",
        "event_id": "EVT-9",
        "message": "PART-0042 passed inspection",
    }

    class StubClient:
        def health(self):
            return {"run_id": "LIVE-001"}

        def snapshot(self, run_id="LIVE-001"):
            return snapshot

        def kpis(self):
            return {"total_output": 1, "good_units": 1, "oee": {"oee": 0.9}}

        def flow_insight(self):
            return {"bottleneck": "CELL-WELDING", "bottleneck_name": "焊接"}

        def flow_history(self):
            return [
                {"m": "09:59", "cell_uph": {"welding": 120, "assembly": 110}, "util": {"welding": 0.5}},
                {"m": "10:00", "cell_uph": {"welding": 130, "assembly": 100}, "util": {"welding": 0.6}},
            ]

        def amr(self):
            return {}

        def inspections(self, limit=50):
            return []

        def runs(self):
            return [{"run_id": "LIVE-001"}]

        def events(self, run_id, after_seq=0, limit=2000):
            return {"events": [event]}

    record = capture_live_record(
        project, StubClient(), experiment_id="LEXP-TEST-001", persist=False
    )
    assert record["experiment_id"] == "LEXP-TEST-001"
    assert [e["experiment_id"] for e in record["events"]] == ["LEXP-TEST-001"]
    assert record["events"][0]["task_id"] == "PART-0042"
    # RFTwin Live 分钟快照没有良品数字段：不能把缺失当成 0 产量。
    window = record["window_metrics"]
    assert window["good_units"] is None
    assert window["defect_units"] is None
    assert window["basis"] == "live_cell_uph"
    assert window["avg_throughput_uph"] == 105.0  # mean(min(120,110)=110, min(130,100)=100)


def test_oee_breakdown_and_target_attainment(project, factory_run):
    breakdown = oee_breakdown(factory_run)
    assert 0.0 <= breakdown["oee"] <= 1.0
    assert sum(breakdown["losses"].values()) + breakdown["oee"] == pytest.approx(1.0, abs=1e-6)
    attainment = target_attainment(factory_run, project)
    assert attainment["target_quantity"] == project["line"]["target_quantity"]
    assert attainment["good_units"] >= 0
    if attainment["attainment"] is not None:
        assert attainment["attainment"] >= 0


def test_strategy_ranking_is_computed_from_metrics(project, factory_run):
    record = run_edge_experiment(project, factory_run, persist=False)
    rows = strategy_ranking(record)
    assert [row["rank"] for row in rows] == [1, 2, 3]
    assert rows[0]["satisfaction_rate"] == max(row["satisfaction_rate"] for row in rows)
    assert rows == sorted(
        rows,
        key=lambda row: (-row["satisfaction_rate"], row["avg_latency_ms"], row["total_cost"]),
    )


def test_compare_factory_records_deltas(project):
    faster = copy.deepcopy(project)
    faster["factory"]["cells"]["machine_tending"]["cycle_sec"] = 30.0
    baseline = run_factory_experiment(project, duration_min=3, persist=False)
    candidate = run_factory_experiment(faster, duration_min=3, persist=False)
    comparison = compare_factory_records(baseline, candidate)
    assert comparison["comparable"] is True
    assert comparison["verdict"] in ("候选更优", "基线更优", "基本持平", "各有优劣")
    assert len(comparison["rows"]) == 5
    assert comparison["rows"][0]["candidate"] == float(candidate["window_metrics"]["good_units"])
    assert comparison["rows"][0]["baseline"] == float(baseline["window_metrics"]["good_units"])

    different_duration = copy.deepcopy(candidate)
    different_duration["config"]["duration_min"] = 5
    mismatch = compare_factory_records(baseline, different_duration)
    assert mismatch["comparable"] is False
    assert "口径不一致" in mismatch["verdict"]
