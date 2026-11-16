"""基于真实仿真结果的瓶颈分析与规则式 AI 辅助优化。

原则：
- 所有结论引用实验记录中的实际数值（利用率、产能、缓冲区占用、故障事件）；
- 建议分为“可直接应用的项目参数修改”和“仅用于敏感性验证的候选参数”；
- 候选方案必须通过重新运行 RFTwin 引擎获得前后对比，不使用预设的改善幅度；
- 第一阶段不调用任何外部大模型 API；规则式分析在无网络时也可完整运行。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from integration.factory_adapter import build_factory_params, run_short_replay

CELL_LABELS = {
    "CELL-WELDING": "焊接",
    "CELL-ASSEMBLY": "装配",
    "CELL-MACHINE_TENDING": "机加工",
    "CELL-VISION_INSPECTION": "视觉检测",
    "welding": "焊接",
    "assembly": "装配",
    "machine_tending": "机加工",
    "vision_inspection": "视觉检测",
}
INPUT_BUFFER_BY_CELL = {
    "welding": "raw_material",
    "assembly": "welding_to_assembly",
    "machine_tending": "assembly_to_machining",
    "vision_inspection": "machining_to_inspection",
}
CELL_KEY_FROM_ID = {
    "CELL-WELDING": "welding",
    "CELL-ASSEMBLY": "assembly",
    "CELL-MACHINE_TENDING": "machine_tending",
    "CELL-VISION_INSPECTION": "vision_inspection",
}


def _deep_merge(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def experiment_kpis(record: dict[str, Any]) -> dict[str, Any]:
    """把工厂/实时实验记录中的关键指标整理为统一字段。"""
    kpis = record.get("kpis") or {}
    window = record.get("window_metrics") or {}
    oee = kpis.get("oee") or {}
    return {
        "total_output": kpis.get("total_output", 0),
        "good_units": kpis.get("good_units", 0),
        "defect_units": kpis.get("defect_units", 0),
        "wip": kpis.get("wip", 0),
        "throughput_uph": kpis.get("throughput_uph", 0.0),
        "oee": oee.get("oee", 0.0),
        "target_oee": oee.get("target_oee", 0.0),
        "first_pass_yield": kpis.get("first_pass_yield", 0.0),
        "defect_rate": kpis.get("defect_rate", 0.0),
        "avg_lead_time_sec": kpis.get("avg_lead_time_sec", 0.0),
        "runtime_sec": kpis.get("runtime_sec", 0.0),
        "downtime_sec": kpis.get("downtime_sec", 0.0),
        "energy_kwh_total": kpis.get("energy_kwh_total", 0.0),
        "window_minutes": window.get("minutes", 0),
        "window_good_units": window.get("good_units", 0),
        "window_avg_throughput_uph": window.get("avg_throughput_uph", 0.0),
        "window_avg_oee": window.get("avg_oee", 0.0),
    }


def analyze_record(record: dict[str, Any]) -> dict[str, Any]:
    """瓶颈与产线状态分析（全部来自记录内的引擎输出）。"""
    insight = record.get("flow_insight") or {}
    cells = record.get("cell_metrics") or []
    utilization = insight.get("utilization") or {}
    rates = insight.get("rates_uph") or {}
    bottleneck_id = insight.get("bottleneck")
    bottleneck_key = CELL_KEY_FROM_ID.get(bottleneck_id)
    util_values = [float(v) for v in utilization.values()] or [0.0]
    line_avg_util = sum(util_values) / len(util_values)
    buffer_pressure = []
    for cell in cells:
        capacity = cell.get("input_buffer_capacity")
        occupancy = cell.get("input_buffer_occupancy")
        if capacity:
            ratio = occupancy / capacity
            buffer_pressure.append(
                {
                    "cell_id": cell["cell_id"],
                    "name": cell["name"],
                    "occupancy": occupancy,
                    "capacity": capacity,
                    "ratio": ratio,
                }
            )
    buffer_pressure.sort(key=lambda x: x["ratio"], reverse=True)
    stations = sorted(
        record.get("station_metrics") or [],
        key=lambda s: s.get("waiting_time_sec", 0.0),
        reverse=True,
    )[:5]
    events = record.get("events") or []
    if events:
        event_stats = {
            "total": len(events),
            "failures": sum(
                1
                for e in events
                if any(token in str(e.get("event_type", "")) for token in ("FAIL", "FAULT", "JAM", "EMERGENCY_STOP"))
            ),
            "inspection_events": sum(
                1 for e in events if e.get("event_type") in ("INSPECTION_PASS", "INSPECTION_FAIL")
            ),
            "blocked_events": sum(1 for e in events if "BLOCKED" in str(e.get("event_type", ""))),
            "starved_events": sum(1 for e in events if "STARVED" in str(e.get("event_type", ""))),
        }
    else:
        event_stats = record.get("event_stats") or {}
    kpis = experiment_kpis(record)
    if record.get("kind") == "live":
        production_line = (
            f"当前累计产量 {kpis['total_output']} 件（实时采集；分钟快照不含逐分钟良品数）"
        )
    else:
        production_line = (
            f"实验窗口产量 {kpis['window_good_units']} 件，"
            f"平均节拍 {kpis['window_avg_throughput_uph']:.1f} 件/小时"
        )
    return {
        "bottleneck": bottleneck_id,
        "bottleneck_name": CELL_LABELS.get(bottleneck_id or "", insight.get("bottleneck_name", "—")),
        "bottleneck_key": bottleneck_key,
        "utilization": utilization,
        "rates_uph": rates,
        "line_avg_util": line_avg_util,
        "buffer_pressure": buffer_pressure,
        "top_waiting_stations": stations,
        "event_stats": event_stats,
        "kpis": kpis,
        "insight_reason": insight.get("reason", ""),
        "evidence": [
            f"瓶颈工位：{CELL_LABELS.get(bottleneck_id or '', bottleneck_id or '—')}，利用率 {utilization.get(bottleneck_id, 0) * 100:.1f}%",
            f"全线平均利用率 {line_avg_util * 100:.1f}%",
            production_line,
            f"OEE {kpis['oee'] * 100:.1f}%，一次通过率 {kpis['first_pass_yield'] * 100:.1f}%",
            f"采集事件 {event_stats.get('total', 0)} 条：故障 {event_stats.get('failures', 0)}、"
            f"阻塞 {event_stats.get('blocked_events', 0)}、缺料等待 {event_stats.get('starved_events', 0)}",
        ],
    }


def _cycle_override(params_cycle: dict[str, float], factor: float) -> dict[str, float]:
    return {key: round(float(value) * factor, 4) for key, value in params_cycle.items()}


def generate_recommendations(
    project: dict[str, Any], record: dict[str, Any]
) -> list[dict[str, Any]]:
    """生成可解释、可验证的规则式优化建议。"""
    analysis = analyze_record(record)
    kpis = analysis["kpis"]
    insight = record.get("flow_insight") or {}
    utilization = insight.get("utilization") or {}
    bottleneck_id = insight.get("bottleneck")
    bottleneck_key = CELL_KEY_FROM_ID.get(bottleneck_id)
    recommendations: list[dict[str, Any]] = []

    if bottleneck_key:
        util = float(utilization.get(bottleneck_id, 0.0))
        label = CELL_LABELS.get(bottleneck_id, bottleneck_key)
        current_cycle = float(project["factory"]["cells"][bottleneck_key]["cycle_sec"])
        factor = 0.9
        new_cycle = round(max(5.0, current_cycle * factor), 2)
        rec = {
            "recipe_id": "REC-CYCLE",
            "title": f"缩短{label}工位节拍 10%（{current_cycle:.1f}s → {new_cycle:.1f}s）",
            "category": "产能提升",
            "rationale": (
                f"{label}工位利用率为 {util * 100:.1f}%，是当前产线约束；"
                f"全线平均利用率为 {analysis['line_avg_util'] * 100:.1f}%。"
                "在工位数量不变的条件下，缩短该工位节拍可直接提高理论产能。"
            ),
            "evidence": analysis["evidence"][:3]
            + [f"当前{label}节拍 {current_cycle:.1f} 秒/件"],
            "expected_effect": "提升瓶颈工位理论产能，可能降低在制品等待与平均生产周期。",
            "risk": "节拍缩短属于仿真假设；真实产线需评估工艺可行性与设备能力，不得直接作为改造依据。",
            "project_patch": {
                "factory": {"cells": {bottleneck_key: {"cycle_sec": new_cycle}}}
            },
        }
        # RFTwin 参数中的节拍键需要从基准参数生成，而不是从实验记录猜测。
        base_params = build_factory_params(project)
        rec["rftwin_overrides"] = {
            "cells": {
                bottleneck_key: {
                    "station_cycle_sec": _cycle_override(
                        base_params["cells"][bottleneck_key]["station_cycle_sec"], factor
                    )
                }
            }
        }
        rec["applicable"] = True
        recommendations.append(rec)

    # 缓冲区压力
    if analysis["buffer_pressure"]:
        pressure = analysis["buffer_pressure"][0]
        cell_key = CELL_KEY_FROM_ID.get(pressure["cell_id"])
        buffer_key = INPUT_BUFFER_BY_CELL.get(cell_key or "")
        if cell_key and buffer_key and pressure["ratio"] >= 0.85:
            capacity = int(project["factory"]["buffers"][buffer_key]["capacity"])
            new_capacity = min(1000, max(capacity + 1, int(math.ceil(capacity * 1.25))))
            label = CELL_LABELS.get(pressure["cell_id"], cell_key)
            recommendations.append(
                {
                    "recipe_id": "REC-BUFFER",
                    "title": f"将{label}前缓冲区容量从 {capacity} 提升到 {new_capacity} 件",
                    "category": "缓冲优化",
                    "rationale": (
                        f"{label}前缓冲区占用 {pressure['occupancy']}/{pressure['capacity']}"
                        f"（{pressure['ratio'] * 100:.0f}%），已接近饱和，上游阻塞风险较高。"
                    ),
                    "evidence": [
                        f"缓冲区占用率 {pressure['ratio'] * 100:.1f}%",
                        f"当前瓶颈：{analysis['bottleneck_name']}",
                    ],
                    "expected_effect": "缓解上游阻塞，降低因缓冲区满导致的等待；可能增加在制品。",
                    "risk": "扩大缓冲会增加在制品库存，需权衡库存成本。",
                    "project_patch": {
                        "factory": {"buffers": {buffer_key: {"capacity": new_capacity}}}
                    },
                    "rftwin_overrides": {
                        "buffers": {buffer_key: {"capacity": new_capacity}}
                    },
                    "applicable": True,
                }
            )

    # 质量
    if kpis.get("defect_rate", 0.0) > 0.02:
        current_fail = float(project["factory"]["vision_fail_probability"])
        new_fail = round(max(0.0, current_fail * 0.8), 5)
        recommendations.append(
            {
                "recipe_id": "REC-QUALITY",
                "title": "进行视觉检测缺陷概率敏感性实验（下调 20%）",
                "category": "质量假设",
                "rationale": (
                    f"当前实验缺陷率 {kpis['defect_rate'] * 100:.2f}%，高于 2% 的演示阈值；"
                    "该缺陷率来自视觉检测失败概率假设。"
                ),
                "evidence": [
                    f"缺陷率 {kpis['defect_rate'] * 100:.2f}%",
                    f"一次通过率 {kpis['first_pass_yield'] * 100:.1f}%",
                    f"当前视觉失败概率假设 {current_fail}",
                ],
                "expected_effect": "量化质量假设变化对产量与一次通过率的影响。",
                "risk": "下调失败概率只是假设敏感性分析，不能替代真实质量改进。",
                "project_patch": {
                    "factory": {"vision_fail_probability": new_fail}
                },
                "rftwin_overrides": {
                    "cells": {"vision_inspection": {"fail_probability": new_fail}}
                },
                "applicable": True,
            }
        )

    if not recommendations:
        recommendations.append(
            {
                "recipe_id": "REC-BASELINE",
                "title": "维持当前配置并扩大样本窗口",
                "category": "稳定性",
                "rationale": (
                    "当前实验未发现明显的产能、缓冲或质量瓶颈；"
                    "建议延长仿真时长以覆盖更多扰动事件后再评估。"
                ),
                "evidence": analysis["evidence"],
                "expected_effect": "获得更稳定的统计结论。",
                "risk": "无参数变更。",
                "project_patch": {},
                "rftwin_overrides": {},
                "applicable": False,
            }
        )
    return recommendations


def _comparison_metrics(record: dict[str, Any]) -> dict[str, float]:
    kpis = experiment_kpis(record)
    insight = record.get("flow_insight") or {}
    utilization = insight.get("utilization") or {}
    bottleneck = insight.get("bottleneck")
    return {
        "window_good_units": float(kpis["window_good_units"]),
        "window_avg_throughput_uph": float(kpis["window_avg_throughput_uph"]),
        "window_avg_oee": float(kpis["window_avg_oee"]),
        "avg_lead_time_sec": float(kpis["avg_lead_time_sec"]),
        "defect_rate": float(kpis["defect_rate"]),
        "bottleneck_utilization": float(utilization.get(bottleneck, 0.0)),
    }


def evaluate_recommendations(
    project: dict[str, Any],
    record: dict[str, Any],
    recommendations: list[dict[str, Any]],
    duration_min: int = 10,
    max_candidates: int = 3,
) -> dict[str, Any]:
    """用短时确定性重放验证候选建议，返回真实的参数前后对比。"""
    seed = int((record.get("config") or {}).get("seed", project["factory"]["seed"]))
    baseline = run_short_replay(
        project, overrides=None, duration_min=duration_min, seed=seed
    )
    baseline_metrics = _comparison_metrics(baseline)
    evaluations = []
    for rec in recommendations[:max_candidates]:
        overrides = rec.get("rftwin_overrides") or {}
        if not overrides:
            evaluations.append(
                {
                    "recipe_id": rec["recipe_id"],
                    "status": "skipped",
                    "message": "该建议没有可执行的参数变更，未运行候选仿真。",
                }
            )
            continue
        try:
            candidate = run_short_replay(
                project, overrides=overrides, duration_min=duration_min, seed=seed
            )
        except Exception as exc:  # noqa: BLE001 - 单条建议失败不影响其他建议
            evaluations.append(
                {
                    "recipe_id": rec["recipe_id"],
                    "status": "error",
                    "message": f"候选仿真失败：{exc}",
                }
            )
            continue
        metrics = _comparison_metrics(candidate)
        deltas = {
            key: metrics[key] - baseline_metrics[key] for key in baseline_metrics
        }
        pct = {
            key: (deltas[key] / baseline_metrics[key] * 100.0 if baseline_metrics[key] else 0.0)
            for key in baseline_metrics
        }
        score = pct["window_avg_throughput_uph"] - max(0.0, pct["avg_lead_time_sec"])
        evaluations.append(
            {
                "recipe_id": rec["recipe_id"],
                "status": "ok",
                "baseline": baseline_metrics,
                "candidate": metrics,
                "deltas": deltas,
                "delta_pct": pct,
                "score": round(score, 3),
                "candidate_experiment_id": candidate["experiment_id"],
            }
        )
    successful = [e for e in evaluations if e.get("status") == "ok"]
    best = max(successful, key=lambda e: e["score"]) if successful else None
    return {
        "baseline": baseline_metrics,
        "baseline_record": baseline,
        "evaluations": evaluations,
        "best": best,
        "duration_min": duration_min,
        "seed": seed,
    }


def apply_project_patch(project: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """把建议的项目参数修改合并进项目副本（不自动保存）。"""
    return _deep_merge(project, patch)


def oee_breakdown(record: dict[str, Any]) -> dict[str, Any]:
    """OEE 的可用率/性能/质量分解与相对损失（全部来自引擎 KPI）。"""
    oee = ((record.get("kpis") or {}).get("oee") or {})
    availability = float(oee.get("availability", 0) or 0)
    performance = float(oee.get("performance", 0) or 0)
    quality = float(oee.get("quality", 0) or 0)
    losses = {
        "可用率损失": max(0.0, 1.0 - availability),
        "性能损失": max(0.0, availability * (1.0 - performance)),
        "质量损失": max(0.0, availability * performance * (1.0 - quality)),
    }
    return {
        "availability": availability,
        "performance": performance,
        "quality": quality,
        "oee": availability * performance * quality,
        "losses": losses,
    }


def target_attainment(record: dict[str, Any], project: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """累计良品与班次目标的达成情况（目标取自实验配置或当前项目）。"""
    kpis = experiment_kpis(record)
    line = (record.get("config") or {}).get("line") or {}
    target = (project or {}).get("line", {}).get("target_quantity") or line.get("target_quantity")
    target = int(target or 0)
    good = int(kpis.get("good_units", 0) or 0)
    ratio = (good / target) if target else None
    return {
        "target_quantity": target,
        "good_units": good,
        "attainment": ratio,
        "gap_units": max(0, target - good) if target else None,
    }


def strategy_ranking(record: dict[str, Any]) -> list[dict[str, Any]]:
    """按“满足率 → 平均时延 → 仿真成本”排序三种策略；结果完全来自实验记录。"""
    metrics = record.get("metrics") or {}
    costs = record.get("costs") or {}
    rows: list[dict[str, Any]] = []
    for key, metric in metrics.items():
        rows.append(
            {
                "strategy_key": key,
                "strategy_name": metric.get("strategy_name", key),
                "satisfaction_rate": float(metric.get("satisfaction_rate", 0.0) or 0.0),
                "avg_latency_ms": float(metric.get("avg_latency_ms", 0.0) or 0.0),
                "success_rate": float(metric.get("success_rate", 0.0) or 0.0),
                "total_cost": float((costs.get(key) or {}).get("total_cost", 0.0) or 0.0),
            }
        )
    rows.sort(
        key=lambda row: (
            -row["satisfaction_rate"],
            row["avg_latency_ms"],
            row["total_cost"],
        )
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def compare_factory_records(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """并排比较两轮离线工厂实验（同一工作负载口径），给出计算得到的结论。"""

    def metricset(record: dict[str, Any]) -> dict[str, Any]:
        kpis = experiment_kpis(record)
        insight = record.get("flow_insight") or {}
        return {
            "window_good_units": float(
                kpis["window_good_units"] if record.get("kind") == "factory" else kpis["total_output"]
            ),
            "window_avg_throughput_uph": float(kpis["window_avg_throughput_uph"] or 0.0),
            "oee": float(kpis["oee"] or 0.0),
            "avg_lead_time_sec": float(kpis["avg_lead_time_sec"] or 0.0),
            "defect_rate": float(kpis["defect_rate"] or 0.0),
            "bottleneck": insight.get("bottleneck_name") or insight.get("bottleneck") or "—",
        }

    base_cfg = baseline.get("config") or {}
    cand_cfg = candidate.get("config") or {}
    base_duration = base_cfg.get("duration_min")
    cand_duration = cand_cfg.get("duration_min")
    base_seed = base_cfg.get("seed")
    cand_seed = cand_cfg.get("seed")
    comparable = (
        baseline.get("kind") == "factory"
        and candidate.get("kind") == "factory"
        and base_duration == cand_duration
        and base_seed == cand_seed
    )

    base_metrics = metricset(baseline)
    cand_metrics = metricset(candidate)
    rows = []
    for key, label in (
        ("window_good_units", "窗口良品（件）"),
        ("window_avg_throughput_uph", "平均产能（件/小时）"),
        ("oee", "OEE"),
        ("avg_lead_time_sec", "平均生产周期（秒）"),
        ("defect_rate", "缺陷率"),
    ):
        base_value = base_metrics[key]
        cand_value = cand_metrics[key]
        delta = cand_value - base_value
        pct = (delta / base_value * 100.0) if base_value else None
        rows.append(
            {
                "metric": key,
                "label": label,
                "baseline": base_value,
                "candidate": cand_value,
                "delta": delta,
                "delta_pct": pct,
            }
        )

    throughput_pct = rows[1]["delta_pct"] or 0.0
    lead_pct = rows[3]["delta_pct"] or 0.0
    oee_pct = rows[2]["delta_pct"] or 0.0
    tolerance = 0.5
    if not comparable:
        verdict = "口径不一致（时长或种子不同）"
    elif throughput_pct >= tolerance and lead_pct <= tolerance and oee_pct >= -tolerance:
        verdict = "候选更优"
    elif throughput_pct <= -tolerance and lead_pct >= -tolerance and oee_pct <= tolerance:
        verdict = "基线更优"
    elif abs(throughput_pct) < tolerance and abs(lead_pct) < tolerance and abs(oee_pct) < tolerance:
        verdict = "基本持平"
    else:
        verdict = "各有优劣"
    return {
        "baseline_experiment_id": baseline.get("experiment_id"),
        "candidate_experiment_id": candidate.get("experiment_id"),
        "rows": rows,
        "baseline": base_metrics,
        "candidate": cand_metrics,
        "verdict": verdict,
        "comparable": comparable,
        "baseline_duration_min": base_duration,
        "candidate_duration_min": cand_duration,
        "baseline_seed": base_seed,
        "candidate_seed": cand_seed,
        "criteria": "候选更优 = 产能不降、周期不升、OEE 不降（容差 0.5%）；结论由实验数值计算。",
    }
