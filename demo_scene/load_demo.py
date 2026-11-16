"""加载最小六设备演示工厂并运行基准/优化实验。

用法（在 aic 根目录）：
    python demo_scene/load_demo.py [--project-id PRJ-...] [--duration 600]

不依赖已启动的服务：直接使用场景存储与 Python 离散事件引擎。
会创建（或复用）项目、创建新场景、导入 OIP 工程、写入 unified_scene.json 的仿真参数，
然后真实运行基准与优化参数，打印 KPI。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.project_store import create_project, list_projects  # noqa: E402
from integration.scene.engine import run_scene_simulation  # noqa: E402
from integration.scene.models import Device  # noqa: E402
from integration.scene.store import create_scene, ingest_oip_project, load_scene, save_scene  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--duration", type=float, default=600.0)
    args = parser.parse_args()

    project_id = args.project_id
    if project_id is None:
        existing = [p for p in list_projects() if p.get("name") == "自建工厂演示项目"]
        project = existing[0] if existing else create_project(
            "自建工厂演示项目",
            "OIP 三维场景 + Python 离散事件仿真全链路演示",
            "从空白场景搭建六设备产线并分析优化",
        )
        project_id = project["project_id"]

    here = Path(__file__).resolve().parent
    oip_project = json.loads((here / "oip_project.json").read_text(encoding="utf-8"))
    baseline_record = json.loads((here / "baseline_experiment.json").read_text(encoding="utf-8"))
    unified = json.loads((here / "unified_scene.json").read_text(encoding="utf-8"))

    scene = create_scene(project_id, "最小六设备产线（演示加载）")
    scene = ingest_oip_project(project_id, scene.scene_id, oip_project)
    # 基准演示：使用基准实验的仿真快照参数（故障率等不在 OIP 部件 schema 中）
    baseline_devices = (baseline_record.get("config") or {}).get("scene_snapshot", {}).get("devices", [])
    by_id = {d.id: d for d in scene.devices}
    for raw in baseline_devices:
        device = by_id.get(raw["id"])
        if isinstance(device, Device):
            device.params.update(raw.get("params") or {})
    scene.simulation_settings.update(unified.get("simulation_settings") or {})
    scene, _ = save_scene(scene, ingest_source="demo_load")

    print(f"项目 {project_id} · 场景 {scene.scene_id} v{scene.version}")
    print(f"设备 {len(scene.devices)} · 显式连接 {len(scene.connections)}")
    print("连接：", " -> ".join(
        [f"{c.source_device}.{c.source_port}->{c.target_device}.{c.target_port}" for c in scene.connections]
    ))

    baseline = run_scene_simulation(scene, duration_sec=args.duration, seed=42, persist=False, label="演示基准")
    print("基准 KPI：", json.dumps(
        {k: baseline["kpis"][k] for k in ("good_units", "total_output", "wip", "throughput_uph", "defect_rate")},
        ensure_ascii=False,
    ), "OEE", baseline["kpis"]["oee"]["oee"], "瓶颈", baseline["flow_insight"]["bottleneck_name"])

    # 演示优化：应用 optimization_record.json 中的最佳建议（真实验证过的参数补丁）
    optimization = json.loads((here / "optimization_record.json").read_text(encoding="utf-8"))
    best = optimization.get("best") or {}
    patch = best.get("patch") or {}
    if patch:
        from integration.scene.engine import apply_scene_patch

        optimized_scene = apply_scene_patch(scene, patch)
        optimized = run_scene_simulation(optimized_scene, duration_sec=args.duration, seed=42, persist=False, label="演示优化")
        print(f"优化建议：{best.get('title')}")
        print("优化 KPI：", json.dumps(
            {k: optimized["kpis"][k] for k in ("good_units", "total_output", "wip", "throughput_uph", "defect_rate")},
            ensure_ascii=False,
        ), "OEE", optimized["kpis"]["oee"]["oee"], "瓶颈", optimized["flow_insight"]["bottleneck_name"])
    else:
        print("优化记录没有可应用参数")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
