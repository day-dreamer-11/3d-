"""自建场景实验的瓶颈分析与规则式优化（全部基于实际仿真结果）。

原则：
- 不编造 KPI：结论只引用 experiment record 中的 device_metrics / kpis / events；
- 每条建议都带可执行参数补丁；评估时必须真正重新运行场景引擎；
- 应用优化会生成新的场景版本，不覆盖历史实验快照。
"""

from __future__ import annotations

import copy
import math
from typing import Any, Optional

from integration.project_store import new_experiment_id, save_experiment

from .engine import apply_scene_patch, run_scene_simulation
from .models import SceneDocument


def scene_kpis(record: dict[str, Any]) -> dict[str, Any]:
    kpis = record.get("kpis") or {}
    oee = kpis.get("oee") or {}
    return {
        "total_output": kpis.get("total_output", 0),
        "good_units": kpis.get("good_units", 0),
        "defect_units": kpis.get("defect_units", 0),
        "wip": kpis.get("wip", 0),
        "throughput_uph": kpis.get("throughput_uph", 0.0),
        "oee": oee.get("oee", 0.0),
        "availability": oee.get("availability", 0.0),
        "performance": oee.get("performance", 0.0),
        "quality": oee.get("quality", 0.0),
        "first_pass_yield": kpis.get("first_pass_yield", 0.0),
        "defect_rate": kpis.get("defect_rate", 0.0),
        "avg_lead_time_sec": kpis.get("avg_lead_time_sec", 0.0),
        "energy_kwh_total": kpis.get("energy_kwh_total", 0.0),
        "runtime_sec": kpis.get("runtime_sec", 0.0),
        "downtime_sec": kpis.get("downtime_sec", 0.0),
    }


def analyze_scene_record(record: dict[str, Any]) -> dict[str, Any]:
    kpis = scene_kpis(record)
    devices = record.get("device_metrics") or []
    production = [d for d in devices if d.get("simulation_type") in ("conveyor", "inspection", "robot", "buffer")]
    ranked = sorted(production, key=lambda d: (d.get("utilization", 0.0), d.get("queue", 0)), reverse=True)
    high_util = [d for d in ranked if float(d.get("utilization", 0.0)) >= 0.85]
    wip_hotspots = sorted(devices, key=lambda d: (d.get("queue", 0), d.get("blocked_sec", 0.0)), reverse=True)
    wip_hotspots = [d for d in wip_hotspots if int(d.get("queue", 0)) > 0][:5]
    failures = [
        {
            "device_id": d["device_id"],
            "name": d.get("name"),
            "failure_count": d.get("failure_count", 0),
            "failed_sec": d.get("failed_sec", 0.0),
        }
        for d in devices
        if int(d.get("failure_count", 0)) > 0
    ]
    failures.sort(key=lambda x: (x["failure_count"], x["failed_sec"]), reverse=True)
    bottleneck_id = (record.get("flow_insight") or {}).get("bottleneck")
    bottleneck = next((d for d in devices if d["device_id"] == bottleneck_id), None)
    reason = (record.get("flow_insight") or {}).get("reason") or ""
    evidence = [
        f"瓶颈设备：{bottleneck.get('name') if bottleneck else '—'}，利用率 {(bottleneck or {}).get('utilization', 0) * 100:.1f}%",
        f"窗口良品 {kpis['good_units']} 件，平均产能 {kpis['throughput_uph']:.1f} 件/小时，WIP {kpis['wip']}",
        f"OEE {kpis['oee'] * 100:.1f}%（可用率 {kpis['availability'] * 100:.1f}% / 性能 {kpis['performance'] * 100:.1f}% / 质量 {kpis['quality'] * 100:.1f}%）",
        f"平均生产周期 {kpis['avg_lead_time_sec']:.1f} 秒，缺陷率 {kpis['defect_rate'] * 100:.2f}%",
        f"仿真能耗 {kpis['energy_kwh_total']:.3f} kWh（按设备功率与占用时间估算）",
    ]
    if failures:
        evidence.append(f"故障设备 {failures[0]['device_id']}：{failures[0]['failure_count']} 次，停机 {failures[0]['failed_sec']:.0f} 秒")
    return {
        "bottleneck": bottleneck_id,
        "bottleneck_name": (bottleneck or {}).get("name"),
        "bottleneck_reason": reason,
        "kpis": kpis,
        "device_metrics": devices,
        "ranking": ranked,
        "high_utilization": high_util,
        "wip_hotspots": wip_hotspots,
        "failures": failures,
        "energy_kwh_total": kpis["energy_kwh_total"],
        "evidence": evidence,
    }


def _device(scene: SceneDocument, device_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not device_id:
        return None
    return next((d for d in scene.devices if d.id == device_id), None)


def generate_scene_recommendations(
    scene: SceneDocument, record: dict[str, Any], *, max_items: int = 5
) -> list[dict[str, Any]]:
    analysis = analyze_scene_record(record)
    kpis = analysis["kpis"]
    recommendations: list[dict[str, Any]] = []
    bottleneck = analysis["ranking"][0] if analysis["ranking"] else None

    if bottleneck:
        device = _device(scene, bottleneck["device_id"])
        if device is not None:
            utilization = float(bottleneck.get("utilization", 0.0))
            base = {
                "utilization": utilization,
                "device": bottleneck["device_id"],
                "name": bottleneck.get("name"),
            }
            if device.simulation_type == "conveyor":
                conveyors = [d for d in scene.devices if d.simulation_type == "conveyor"]
                cycles = {
                    d.id: float(d.params.get("length_m", 4.0)) / max(0.01, float(d.params.get("speed_mps", 0.5)))
                    for d in conveyors
                }
                max_cycle = max(cycles.values())
                # 同一条产线上多台输送机节拍相同：只改一台不会解除约束，必须一起改。
                targets = [d for d in conveyors if cycles[d.id] >= max_cycle * 0.95]
                patch: dict[str, dict[str, float]] = {}
                changes = []
                for target in targets:
                    old = float(target.params.get("speed_mps", 0.5))
                    new = round(min(4.0, max(old * 1.5, old + 0.1)), 3)
                    if new > old:
                        patch[target.id] = {"speed_mps": new}
                        changes.append(f"{target.name} {old:.2f}→{new:.2f}")
                if patch:
                    names = "、".join(d.name for d in targets)
                    recommendations.append(
                        {
                            "recipe_id": "SCENE-CONVEYOR-SPEED",
                            "title": f"提高输送线速度：{'; '.join(changes)} m/s",
                            "category": "输送能力",
                            "rationale": f"{names} 的利用率接近 {utilization * 100:.1f}%，且节拍同为 {max_cycle:.2f}s，是当前物料流约束；只改一台不会解除约束，因此整组一起调整。",
                            "evidence": analysis["evidence"][:3],
                            "expected_effect": "缩短整条输送约束链的节拍，可能显著提高整线产能。",
                            "risk": "带速提高可能增加磨损与能耗；真实产线需核算电机能力。",
                            "patch": {"device_params": patch},
                            "applicable": True,
                            "metrics": base,
                        }
                    )
            elif device.simulation_type == "inspection":
                old = float(device.params.get("cycle_sec", 2.0))
                new = round(max(0.5, old * 0.8), 3)
                recommendations.append(
                    {
                        "recipe_id": "SCENE-INSPECTION-CYCLE",
                        "title": f"缩短 {device.name} 检测节拍 {old:.2f} → {new:.2f} 秒",
                        "category": "检测能力",
                        "rationale": f"{device.name} 利用率为 {utilization * 100:.1f}%，检测节拍是当前约束。",
                        "evidence": analysis["evidence"][:3],
                        "expected_effect": "提高检测工位处理能力，缓解上游等待。",
                        "risk": "缩短检测时间可能降低检测精度；本次仿真保持 pass_rate 不变，需真实验证。",
                        "patch": {"device_params": {device.id: {"cycle_sec": new}}},
                        "applicable": True,
                        "metrics": base,
                    }
                )
            elif device.simulation_type == "robot":
                old = float(device.params.get("cycle_sec", 4.0))
                new = round(max(0.5, old * 0.85), 3)
                recommendations.append(
                    {
                        "recipe_id": "SCENE-ROBOT-CYCLE",
                        "title": f"缩短 {device.name} 取放节拍 {old:.2f} → {new:.2f} 秒",
                        "category": "机器人节拍",
                        "rationale": f"{device.name} 利用率为 {utilization * 100:.1f}%，机器人取放是当前约束。",
                        "evidence": analysis["evidence"][:3],
                        "expected_effect": "提高机器人处理能力；在输送能力足够时提升整线产能。",
                        "risk": "节拍缩短需评估机器人可达性、加速度与安全；真实产线不能仅按仿真结论改造。",
                        "patch": {"device_params": {device.id: {"cycle_sec": new}}},
                        "applicable": True,
                        "metrics": base,
                    }
                )

    if kpis["defect_rate"] > 0.02:
        inspection = next((d for d in scene.devices if d.simulation_type == "inspection"), None)
        if inspection is not None:
            old = float(inspection.params.get("pass_rate", 0.95))
            new = round(min(1.0, old + 0.01), 4)
            recommendations.append(
                {
                    "recipe_id": "SCENE-QUALITY-SENSITIVITY",
                    "title": f"进行质量敏感性实验：{inspection.name} 通过率 {old:.3f} → {new:.3f}",
                    "category": "质量假设",
                    "rationale": f"本次仿真缺陷率 {kpis['defect_rate'] * 100:.2f}%，高于 2% 演示阈值；通过率是质量假设参数。",
                    "evidence": analysis["evidence"][:4],
                    "expected_effect": "量化质量假设变化对良品数与一次通过率的影响。",
                    "risk": "这是敏感性分析，不是真实质量改进；不能替代工艺验证。",
                    "patch": {"device_params": {inspection.id: {"pass_rate": new}}},
                    "applicable": True,
                    "metrics": {"defect_rate": kpis["defect_rate"]},
                }
            )

    if analysis["failures"]:
        failure = analysis["failures"][0]
        device = _device(scene, failure["device_id"])
        if device is not None:
            old = float(device.params.get("failure_prob_per_hour", 0.0))
            if old > 0:
                new = round(old * 0.7, 4)
                recommendations.append(
                    {
                        "recipe_id": "SCENE-FAILURE-REDUCE",
                        "title": f"维护 {device.name}：故障率 {old:.2f} → {new:.2f} 次/小时",
                        "category": "可靠性",
                        "rationale": f"{device.name} 在本次仿真中发生 {failure['failure_count']} 次故障，累计停机 {failure['failed_sec']:.0f} 秒。",
                        "evidence": analysis["evidence"][-1:],
                        "expected_effect": "降低停机时间与可用率损失。",
                        "risk": "故障率下调是维护假设，需真实维护记录支撑。",
                        "patch": {"device_params": {device.id: {"failure_prob_per_hour": new}}},
                        "applicable": True,
                        "metrics": failure,
                    }
                )

    if analysis["wip_hotspots"]:
        hotspot = analysis["wip_hotspots"][0]
        device = _device(scene, hotspot["device_id"])
        if device is not None and device.simulation_type == "buffer":
            old = int(device.params.get("capacity", 20))
            new = min(200, max(old + 1, int(math.ceil(old * 1.25))))
            recommendations.append(
                {
                    "recipe_id": "SCENE-BUFFER-CAPACITY",
                    "title": f"扩大 {device.name} 容量 {old} → {new}",
                    "category": "缓冲优化",
                    "rationale": f"{device.name} 当前队列 {hotspot.get('queue')}，是 WIP 积压点。",
                    "evidence": analysis["evidence"][:3],
                    "expected_effect": "缓解上游阻塞；可能增加在制品库存。",
                    "risk": "扩容会增加 WIP 与占地，需权衡。",
                    "patch": {"device_params": {device.id: {"capacity": new}}},
                    "applicable": True,
                    "metrics": hotspot,
                }
            )

    if not recommendations:
        recommendations.append(
            {
                "recipe_id": "SCENE-BASELINE",
                "title": "维持当前配置并延长仿真窗口",
                "category": "稳定性",
                "rationale": "本次仿真未发现明显产能、质量或可靠性瓶颈；建议延长窗口以覆盖更多扰动。",
                "evidence": analysis["evidence"],
                "expected_effect": "获得更稳定的统计结论。",
                "risk": "无参数变更。",
                "patch": {},
                "applicable": False,
                "metrics": {},
            }
        )
    return recommendations[:max_items]


def _metrics(record: dict[str, Any]) -> dict[str, float]:
    kpis = scene_kpis(record)
    return {
        "good_units": float(kpis["good_units"]),
        "throughput_uph": float(kpis["throughput_uph"]),
        "oee": float(kpis["oee"]),
        "wip": float(kpis["wip"]),
        "avg_lead_time_sec": float(kpis["avg_lead_time_sec"]),
        "defect_rate": float(kpis["defect_rate"]),
        "energy_kwh_total": float(kpis["energy_kwh_total"]),
    }


def evaluate_scene_recommendations(
    scene: SceneDocument,
    record: dict[str, Any],
    recommendations: list[dict[str, Any]],
    *,
    duration_sec: Optional[float] = None,
    persist: bool = True,
    max_candidates: int = 3,
) -> dict[str, Any]:
    """真实重跑每个候选参数，返回前后对比；不是预设改善幅度。"""
    config = record.get("config") or {}
    duration = float(duration_sec or config.get("duration_sec") or scene.simulation_settings.get("duration_sec") or 1800)
    # 实时实验/很短的手动实验不足以做优化基线：至少 60 秒，避免 0 产量导致无意义评分。
    if record.get("source") == "scene_engine_live" or duration < 60:
        duration = float(scene.simulation_settings.get("duration_sec") or 1800)
    duration = max(60.0, duration)
    seed = int(config.get("seed") or scene.simulation_settings.get("seed", 42))
    baseline = run_scene_simulation(scene, duration_sec=duration, seed=seed, persist=False, label="优化基线")
    baseline_metrics = _metrics(baseline)
    evaluations = []
    best: Optional[dict[str, Any]] = None
    for rec in recommendations[:max_candidates]:
        patch = rec.get("patch") or {}
        if not patch:
            evaluations.append({"recipe_id": rec["recipe_id"], "status": "skipped", "message": "该建议没有可执行参数变更。"})
            continue
        try:
            patched_scene = apply_scene_patch(scene, patch)
            candidate = run_scene_simulation(
                patched_scene,
                duration_sec=duration,
                seed=seed,
                persist=False,
                label=f"优化候选 {rec['recipe_id']}",
            )
        except Exception as exc:  # noqa: BLE001 - 单条建议失败不影响其他
            evaluations.append({"recipe_id": rec["recipe_id"], "status": "error", "message": f"候选仿真失败：{exc}"})
            continue
        metrics = _metrics(candidate)
        deltas = {key: metrics[key] - baseline_metrics[key] for key in metrics}
        pct = {key: (deltas[key] / baseline_metrics[key] * 100.0 if baseline_metrics[key] else 0.0) for key in metrics}
        score = pct["throughput_uph"] + 0.5 * pct["oee"] - max(0.0, pct["avg_lead_time_sec"]) - max(0.0, pct["wip"]) * 0.5
        evaluation = {
            "recipe_id": rec["recipe_id"],
            "title": rec["title"],
            "status": "ok",
            "patch": patch,
            "baseline": baseline_metrics,
            "candidate": metrics,
            "deltas": {k: round(v, 4) for k, v in deltas.items()},
            "delta_pct": {k: round(v, 4) for k, v in pct.items()},
            "score": round(score, 3),
            "candidate_record": candidate,
        }
        evaluations.append(evaluation)
        if best is None or score > best["score"]:
            best = evaluation

    optimization_id = new_experiment_id("SOPT")
    summary = {
        "schema_version": "2.0",
        "experiment_id": optimization_id,
        "project_id": scene.project_id,
        "scene_id": scene.scene_id,
        "scene_version": scene.version,
        "kind": "factory",
        "source": "scene_rule_optimizer",
        "factory_engine": "scene_discrete_event",
        "created_at": baseline.get("created_at"),
        "label": f"场景优化实验 {optimization_id}",
        "baseline_experiment_id": baseline.get("experiment_id"),
        "baseline_metrics": baseline_metrics,
        "evaluations": [
            {k: v for k, v in e.items() if k != "candidate_record"} for e in evaluations
        ],
        "best": {k: v for k, v in best.items() if k != "candidate_record"} if best else None,
        "duration_sec": duration,
        "seed": seed,
        "assumptions": [
            "候选方案均由场景引擎以相同 seed/时长真实重跑，未使用预设改善幅度。",
            "优化建议为规则式，不是大模型生成；参数变更需人工确认后应用。",
            "所有指标为软件仿真结果，不是真实工厂实测。",
        ],
    }
    if persist:
        save_experiment(scene.project_id, summary)
    return {
        "optimization_id": optimization_id,
        "baseline": baseline,
        "baseline_metrics": baseline_metrics,
        "evaluations": evaluations,
        "best": best,
        "summary": summary,
    }


def compare_scene_records(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base = _metrics(baseline)
    cand = _metrics(candidate)
    rows = []
    for key, label in (
        ("good_units", "良品数（件）"),
        ("throughput_uph", "平均产能（件/小时）"),
        ("oee", "OEE"),
        ("wip", "WIP"),
        ("avg_lead_time_sec", "平均生产周期（秒）"),
        ("defect_rate", "缺陷率"),
        ("energy_kwh_total", "能耗（kWh）"),
    ):
        delta = cand[key] - base[key]
        rows.append(
            {
                "metric": key,
                "label": label,
                "baseline": base[key],
                "candidate": cand[key],
                "delta": round(delta, 4),
                "delta_pct": round(delta / base[key] * 100.0, 4) if base[key] else None,
            }
        )
    comparable = (
        (baseline.get("scene_id") == candidate.get("scene_id"))
        and (baseline.get("config") or {}).get("duration_sec") == (candidate.get("config") or {}).get("duration_sec")
        and (baseline.get("config") or {}).get("seed") == (candidate.get("config") or {}).get("seed")
    )
    tp = next(r for r in rows if r["metric"] == "throughput_uph")["delta_pct"] or 0.0
    lead = next(r for r in rows if r["metric"] == "avg_lead_time_sec")["delta_pct"] or 0.0
    if not comparable:
        verdict = "口径不一致（场景/时长/种子不同）"
    elif tp >= 0.5 and lead <= 0.5:
        verdict = "候选更优"
    elif tp <= -0.5 and lead >= -0.5:
        verdict = "基线更优"
    elif abs(tp) < 0.5 and abs(lead) < 0.5:
        verdict = "基本持平"
    else:
        verdict = "各有优劣"
    return {
        "baseline_experiment_id": baseline.get("experiment_id"),
        "candidate_experiment_id": candidate.get("experiment_id"),
        "rows": rows,
        "verdict": verdict,
        "comparable": comparable,
        "criteria": "候选更优 = 产能不降且平均周期不升（容差 0.5%），结论由实际重跑数据计算。",
    }


def apply_and_rerun(
    scene: SceneDocument,
    patch: dict[str, Any],
    *,
    duration_sec: Optional[float] = None,
    seed: Optional[int] = None,
) -> tuple[SceneDocument, dict[str, Any]]:
    """应用参数补丁到场景副本并重跑（不自动保存场景）。"""
    patched = apply_scene_patch(scene, patch)
    record = run_scene_simulation(patched, duration_sec=duration_sec, seed=seed, persist=False, label="应用优化后的重跑")
    return patched, record
