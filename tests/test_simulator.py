"""网络仿真器、任务生成与指标一致性测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from experiments.runner import ConfigError, generate_tasks, run_experiment
from models.defaults import default_config
from network_sim.simulator import EdgeSimulator
from scheduler.strategies import FixedLocalStrategy


@pytest.fixture(scope="module")
def base_config():
    return replace(default_config(), num_tasks=50)


@pytest.fixture(scope="module")
def experiment(base_config):
    return run_experiment(base_config)


def test_task_generation_respects_ranges(base_config):
    tasks = generate_tasks(base_config)
    assert len(tasks) == base_config.num_tasks
    arrivals = [task.arrival_time_ms for task in tasks]
    assert arrivals == sorted(arrivals)
    assert len(set(arrivals)) == len(arrivals)
    compute_cap = base_config.compute_range_gflops[1] * 2.0 + 1e-6
    for task in tasks:
        assert base_config.data_size_range_mb[0] <= task.data_size_mb <= base_config.data_size_range_mb[1]
        assert base_config.compute_range_gflops[0] <= task.compute_demand_gflops <= compute_cap
        assert base_config.deadline_range_ms[0] <= task.deadline_ms <= base_config.deadline_range_ms[1]
        assert task.priority in (0, 1, 2)
        assert 0.0 <= task.jitter_ms <= base_config.network_jitter_ms


def test_latency_breakdown_sums_to_total(experiment):
    for run in experiment.runs.values():
        for result in run.results:
            if not result.is_completed:
                continue
            total = (
                result.upload_ms
                + result.queue_ms
                + result.compute_ms
                + result.download_ms
            )
            assert result.total_latency_ms == pytest.approx(total, abs=1e-6)
            assert result.finish_time_ms == pytest.approx(
                result.task.arrival_time_ms + result.total_latency_ms, abs=1e-6
            )
            assert result.start_time_ms >= result.task.arrival_time_ms
            assert result.queue_ms >= 0.0


def test_node_service_is_fifo_per_node(experiment):
    for run in experiment.runs.values():
        by_node: dict[str, list] = {}
        for result in run.results:
            if result.is_completed:
                by_node.setdefault(result.node_id, []).append(result)
        for node_id, results in by_node.items():
            results.sort(key=lambda r: r.start_time_ms)
            for previous, following in zip(results, results[1:]):
                assert following.start_time_ms >= previous.compute_finish_ms - 1e-6


def test_queue_capacity_rejection_is_reported():
    config = replace(default_config(), num_tasks=20, arrival_rate_per_sec=20.0)
    single_node = replace(
        config.nodes[0],
        compute_gflops=0.5,
        base_load=0.0,
        max_task_gflops=100.0,
        max_queue_slots=1,
    )
    config = replace(config, nodes=(single_node,))
    tasks = generate_tasks(config)
    run = EdgeSimulator(config, tasks).run(FixedLocalStrategy())
    rejected = [r for r in run.results if r.status == "rejected"]
    assert rejected
    assert any("队列已满" in r.decision_reason for r in rejected)


def test_metrics_totals_are_consistent(experiment):
    for metrics in experiment.metrics.values():
        assert (
            metrics.on_time_tasks
            + metrics.timeout_tasks
            + metrics.rejected_tasks
            == metrics.total_tasks
        )
        assert metrics.completed_tasks == metrics.on_time_tasks + metrics.timeout_tasks
        assert metrics.satisfaction_rate == pytest.approx(
            metrics.on_time_tasks / metrics.total_tasks
        )
        assert metrics.success_rate == pytest.approx(
            metrics.completed_tasks / metrics.total_tasks
        )
        assert 0.0 <= metrics.satisfaction_rate <= 1.0
        assert metrics.avg_decision_ms >= 0.0
        for utilization in metrics.node_utilization.values():
            assert 0.0 <= utilization <= 1.0


def test_invalid_config_raises():
    with pytest.raises(ConfigError):
        run_experiment(replace(default_config(), num_tasks=0))
    with pytest.raises(ConfigError):
        run_experiment(replace(default_config(), deadline_range_ms=(100.0, 10.0)))
    with pytest.raises(ConfigError):
        run_experiment(replace(default_config(), nodes=()))


def test_network_delay_formula():
    link = default_config().nodes[0].link
    data_mb = 10.0
    jitter = 1.5
    effective_bw = link.bandwidth_mbps * (1.0 - link.congestion)
    expected = data_mb * 8.0 * 1000.0 / effective_bw + link.latency_ms + jitter
    assert link.upload_delay_ms(data_mb, jitter) == pytest.approx(expected)
