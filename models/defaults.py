"""默认仿真场景：一个包含本地工控机、边缘服务器与云服务器的智能工厂。"""

from __future__ import annotations

from models.schemas import NetworkLink, NodeConfig, SimulationConfig


def default_config() -> SimulationConfig:
    """返回默认场景配置。

    参数取值均来自可解释的工程假设（见 README），不对应任何真实设备实测值。
    """
    nodes = (
        NodeConfig(
            node_id="local",
            display_name="本地工控机",
            node_type="local",
            compute_gflops=10.0,
            base_load=0.35,
            max_task_gflops=3.0,
            max_queue_slots=8,
            link=NetworkLink(bandwidth_mbps=200.0, latency_ms=2.0, congestion=0.05),
        ),
        NodeConfig(
            node_id="edge",
            display_name="边缘服务器",
            node_type="edge",
            compute_gflops=40.0,
            base_load=0.30,
            max_task_gflops=12.0,
            max_queue_slots=16,
            link=NetworkLink(bandwidth_mbps=80.0, latency_ms=15.0, congestion=0.20),
        ),
        NodeConfig(
            node_id="cloud",
            display_name="云服务器",
            node_type="cloud",
            compute_gflops=120.0,
            base_load=0.20,
            max_task_gflops=60.0,
            max_queue_slots=32,
            link=NetworkLink(bandwidth_mbps=30.0, latency_ms=60.0, congestion=0.35),
        ),
    )
    return SimulationConfig(
        num_tasks=80,
        arrival_rate_per_sec=5.0,
        seed=42,
        data_size_range_mb=(0.5, 2.5),
        compute_range_gflops=(0.5, 6.0),
        deadline_range_ms=(150.0, 900.0),
        network_jitter_ms=6.0,
        result_size_mb=0.02,
        nodes=nodes,
    )
