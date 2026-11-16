"""三种调度策略的公平性与行为测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from experiments.runner import run_experiment
from models.defaults import default_config


@pytest.fixture(scope="module")
def experiment():
    config = replace(default_config(), num_tasks=60)
    return run_experiment(config)


def _local_max(experiment):
    return next(n.max_task_gflops for n in experiment.config.nodes if n.node_id == "local")


def test_all_strategies_share_identical_tasks(experiment):
    base_ids = [task.task_id for task in experiment.tasks]
    for key, run in experiment.runs.items():
        assert [r.task.task_id for r in run.results] == base_ids, key
        for index, result in enumerate(run.results):
            assert result.task is experiment.tasks[index]


def test_fixed_strategies_always_target_same_node(experiment):
    assert all(r.node_id == "local" for r in experiment.runs["fixed_local"].results)
    assert all(r.node_id == "cloud" for r in experiment.runs["fixed_cloud"].results)


def test_dynamic_uses_multiple_nodes_and_explains(experiment):
    run = experiment.runs["dynamic"]
    used = {r.node_id for r in run.results if r.node_id is not None}
    assert len(used) >= 2, used
    assert all("动态调度" in r.decision_reason for r in run.results if r.node_id)


def test_dynamic_never_sends_oversized_task_to_local(experiment):
    local_max = _local_max(experiment)
    for result in experiment.runs["dynamic"].results:
        if result.node_id == "local":
            assert result.task.compute_demand_gflops <= local_max + 1e-9


def test_dynamic_avoids_local_for_heavy_tasks(experiment):
    local_max = _local_max(experiment)
    heavy = [
        r
        for r in experiment.runs["dynamic"].results
        if r.task.compute_demand_gflops > local_max
    ]
    assert heavy
    assert all(r.node_id != "local" for r in heavy)


def test_fixed_local_rejects_oversized_tasks(experiment):
    local_max = _local_max(experiment)
    rejected = [
        r
        for r in experiment.runs["fixed_local"].results
        if r.task.compute_demand_gflops > local_max
    ]
    assert rejected
    assert all(r.status == "rejected" for r in rejected)
    assert all("上限" in r.decision_reason for r in rejected)


def test_seed_makes_experiment_reproducible():
    config = replace(default_config(), num_tasks=30)
    first = run_experiment(config)
    second = run_experiment(config)
    for key in first.runs:
        signature_a = [
            (r.task.task_id, r.node_id, r.status, r.total_latency_ms)
            for r in first.runs[key].results
        ]
        signature_b = [
            (r.task.task_id, r.node_id, r.status, r.total_latency_ms)
            for r in second.runs[key].results
        ]
        assert signature_a == signature_b, key


def test_different_seed_changes_task_set():
    config = replace(default_config(), num_tasks=20)
    first = run_experiment(config)
    second = run_experiment(replace(config, seed=config.seed + 1))
    assert [t.arrival_time_ms for t in first.tasks] != [
        t.arrival_time_ms for t in second.tasks
    ]


def test_dynamic_falls_back_when_nothing_meets_deadline():
    config = replace(
        default_config(), num_tasks=20, deadline_range_ms=(1.0, 1.0)
    )
    experiment = run_experiment(config)
    results = [
        r for r in experiment.runs["dynamic"].results if r.node_id is not None
    ]
    assert results
    assert any("退化" in r.decision_reason for r in results)
    assert experiment.metrics["dynamic"].timeout_tasks > 0


def test_capacity_rejection_when_all_nodes_too_small():
    config = replace(default_config(), num_tasks=10)
    tiny_nodes = tuple(
        replace(node, max_task_gflops=0.001) for node in config.nodes
    )
    experiment = run_experiment(replace(config, nodes=tiny_nodes))
    results = experiment.runs["dynamic"].results
    assert all(r.status == "rejected" for r in results)
    assert all("算力上限" in r.decision_reason for r in results)
