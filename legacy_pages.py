"""已从主导航移除的 RFTwin / EdgeCoder 旧页面（保留用于回滚）。

这些页面不再挂载到 `PAGE_FUNCS`；如需回滚，可在 factory_app.py 中重新导入并注册。
共享 helper 来自 `ui_shared` / `ui_cache`，避免反向依赖入口文件。
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from integration.analysis import (
    analyze_record,
    apply_project_patch,
    compare_factory_records,
    evaluate_recommendations,
    experiment_kpis,
    generate_recommendations,
    oee_breakdown,
    strategy_ranking,
    target_attainment,
)
from integration.edge_mapping import EdgeMappingError, build_compute_tasks, run_edge_experiment
from integration.factory_adapter import FactoryExperimentError, run_factory_experiment
from integration.factory_client import RFTwinClient, RFTwinClientError, capture_live_record
from integration.factory_service import FactoryServiceError, get_service_manager, project_factory_signature
from integration.paths import DATA_DIR, RFTWIN_FRONTEND_DIST, RFTWIN_LAYOUT, RFTWIN_ROOT
from integration.project_store import BUFFER_LABELS, CELL_LABELS, ProjectError, list_experiments, save_project
from models.schemas import PRIORITY_LABELS, STRATEGY_ORDER
from generator.template_generator import TASK_TYPES, generate_service
from tester.validator import run_generated_tests, static_check
from ui_cache import invalidate_data_cache as _invalidate_data_cache
from ui_shared import current_project, goto_page, hero, package_version as _package_version

SEGMENT_COLORS = {
    "上行": "#1F7A8C",
    "排队": "#C08A3E",
    "计算": "#2F6B57",
    "回传": "#5B6B7F",
    "拒绝": "#A65246",
}


STRATEGY_NAMES = {
    "fixed_local": "固定本地",
    "fixed_cloud": "固定云端",
    "dynamic": "动态调度",
}


STRATEGY_COLORS = {
    "固定本地": "#5B6B7F",
    "固定云端": "#C08A3E",
    "动态调度": "#1F7A8C",
}


NODE_COLORS = {
    "本地工控机": "#5B6B7F",
    "边缘服务器": "#1F7A8C",
    "云服务器": "#C08A3E",
}


STATUS_LABELS = {
    "success": "准时完成",
    "timeout": "超时完成",
    "rejected": "已拒绝",
    "failed": "失败",
}


STATION_STATE_LABELS = {
    "RUNNING": "运行",
    "PROCESSING": "加工中",
    "READY": "就绪",
    "IDLE": "空闲",
    "BLOCKED": "阻塞",
    "STARVED": "缺料",
    "FAULT": "故障",
    "ERROR": "异常",
    "MAINTENANCE": "维护",
    "WAITING": "等待",
}


FAULT_LABELS = {
    "tool_failure": "工具故障",
    "conveyor_jam": "输送机卡料",
    "minor_stop": "短暂停机",
    "safety_gate_open": "安全门打开",
    "amr_fault": "AMR 故障",
    "zone_obstacle": "区域障碍",
    "sensor_stop": "传感器急停",
    "emergency_stop": "紧急停机",
    "light_curtain": "光幕触发",
}


EVENT_TYPE_LABELS = {
    "STATION_STATE": "工位状态变化",
    "STATION_READY": "工位就绪",
    "STATION_STARTED": "工位启动",
    "STATION_BLOCKED": "工位阻塞",
    "STATION_STARVED": "工位缺料",
    "CELL_BLOCKED": "单元阻塞",
    "CELL_STARVED": "单元缺料",
    "CELL_RECOVERED": "单元恢复",
    "CYCLE_COMPLETED": "加工完成",
    "TASK_STARTED": "任务开始",
    "TASK_COMPLETED": "任务完成",
    "TASK_DISPATCHED": "任务派发",
    "DISPATCH_DECISION": "派工决策",
    "INSPECTION_PASS": "检测通过",
    "INSPECTION_FAIL": "检测不合格",
    "PALLET_READY": "托盘就绪",
    "TOTE_LOADED": "料箱装载",
    "TOTE_RETURNED": "料箱回收",
    "AMR_REROUTED": "AMR 改道",
    "AMR_DETOUR": "AMR 绕行",
    "CONVEYOR_JAMMED": "输送机卡料",
    "TOOL_FAILURE": "工具故障",
    "MINOR_STOP": "短暂停机",
    "SAFETY_GATE_OPEN": "安全门打开",
    "EMERGENCY_STOP": "紧急停机",
    "AMR_FAULT": "AMR 故障",
    "ZONE_OBSTACLE": "区域障碍",
    "MATERIAL_LOW": "物料不足",
}


SEVERITY_LABELS = {
    "INFO": "信息",
    "LOW": "低",
    "MEDIUM": "中",
    "WARN": "警告",
    "WARNING": "警告",
    "HIGH": "高",
    "CRITICAL": "严重",
}


TASK_STATUS_LABELS = {
    "PASS": "通过",
    "FAIL": "不合格",
    "COMPLETED": "完成",
    "RUNNING": "执行中",
    "DISPATCHED": "已派发",
}


ORDER_STATUS_LABELS = {
    "PLANNED": "计划中",
    "IN_PROGRESS": "进行中",
    "COMPLETED": "已完成",
    "BLOCKED": "阻塞",
    "CANCELLED": "已取消",
}


PRIORITY_TEXT_LABELS = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}


CHART_FONT = "Microsoft YaHei, Segoe UI, sans-serif"


TWIN_PUBLIC_BASE = os.environ.get("TWIN_PUBLIC_BASE", "").strip().rstrip("/")


def twin_public_url(status: dict[str, Any]) -> str:
    return TWIN_PUBLIC_BASE + "/" if TWIN_PUBLIC_BASE else str(status.get("url", ""))


def event_label(value: Any) -> str:
    if value is None:
        return "—"
    return EVENT_TYPE_LABELS.get(str(value), str(value))


def severity_label(value: Any) -> str:
    if value is None:
        return "—"
    return SEVERITY_LABELS.get(str(value).upper(), str(value))


def station_state_label(value: Any) -> str:
    if value is None:
        return "—"
    return STATION_STATE_LABELS.get(str(value).upper(), str(value))


def fault_label(value: Any) -> str:
    if value in (None, "", "—"):
        return "—"
    return FAULT_LABELS.get(str(value), str(value))


def fmt_num(value: Any, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return "—"
    return f"{value:.{digits}f}{suffix}"


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if math.isnan(value):
        return "—"
    return f"{value * 100:.{digits}f}%"


def style_fig(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=8, r=8, t=46, b=8),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        font=dict(family=CHART_FONT, size=12, color="#27405A"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def badge(status: str) -> str:
    css = {
        "RUNNING": "ec-success",
        "PROCESSING": "ec-success",
        "READY": "ec-success",
        "IDLE": "ec-warn",
        "STARVED": "ec-warn",
        "BLOCKED": "ec-warn",
        "FAULT": "ec-error",
        "ERROR": "ec-error",
        "MAINTENANCE": "ec-warn",
    }.get(status, "ec-warn")
    return f'<span class="ec-badge {css}">{status}</span>'


def save_updated_project(project: dict[str, Any], success_text: str) -> None:
    try:
        saved = save_project(project)
    except ProjectError as exc:
        st.error(f"保存失败：{exc}")
        return
    st.session_state.project_id = saved["project_id"]
    _invalidate_data_cache()
    st.success(success_text)


def cell_label_from_id(cell_id: Any) -> str:
    key = str(cell_id or "").replace("CELL-", "").lower()
    return CELL_LABELS.get(key, str(cell_id or "—"))


def service_status(remote: bool = False) -> dict[str, Any]:
    try:
        return get_service_manager().status(check_remote=remote)
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "healthy": False, "error": str(exc)}


def experiment_options(records: list[dict[str, Any]]) -> dict[str, str]:
    options = {}
    for record in records:
        kind_label = {"factory": "离线", "live": "实时", "edge": "边缘"}.get(record.get("kind"), "实验")
        options[record["experiment_id"]] = (
            f"[{kind_label}] {record['experiment_id']} · {record.get('created_at', '')}"
        )
    return options


def latest_record(project_id: str, kinds: tuple[str, ...]) -> Optional[dict[str, Any]]:
    records = []
    for kind in kinds:
        records.extend(list_experiments(project_id, kind=kind))
    if not records:
        return None
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return records[0]


def kpi_metrics_grid(kpis: dict[str, Any]) -> None:
    cols = st.columns(6)
    cols[0].metric("总产量（件）", kpis.get("total_output", 0))
    cols[1].metric("良品数", kpis.get("good_units", 0))
    cols[2].metric("首检不合格", kpis.get("defect_units", 0))
    cols[3].metric("在制品 WIP", kpis.get("wip", 0))
    cols[4].metric("OEE（综合效率）", pct(kpis.get("oee")))
    cols[5].metric("平均周期", fmt_num(kpis.get("avg_lead_time_sec"), 1, " s"))


def flow_history_frame(record: dict[str, Any], limit: Optional[int] = None) -> pd.DataFrame:
    history = record.get("flow_history") or []
    if limit:
        history = history[-limit:]
    rows = []
    for item in history:
        row = {
            "时间": item.get("m") or item.get("sim_minute"),
            "良品数": item.get("good_units"),
            "首检不合格": item.get("defect_units"),
            "产出(件/小时)": item.get("throughput_uph"),
            "OEE": item.get("oee"),
            "WIP": item.get("wip"),
        }
        cell_uph = item.get("cell_uph") or {}
        for cell, uph in cell_uph.items():
            row[f"uph_{cell}"] = uph
        if row["产出(件/小时)"] is None and cell_uph:
            row["产出(件/小时)"] = min(cell_uph.values())
        for cell, util in (item.get("util") or {}).items():
            row[f"util_{cell}"] = util
        for buf, qty in (item.get("buf") or {}).items():
            row[f"buf_{buf}"] = qty
        rows.append(row)
    return pd.DataFrame(rows)


def process_frames(record: dict[str, Any]) -> list[dict[str, Any]]:
    """把工厂/实时记录整理为分钟级过程帧（只用记录中真实存在的字段）。

    - 离线实验：分钟产出取自 flow_history（sim_minute/good/defect）；
      工位利用率与缓冲区取自 cell_flow_history（RFTwin 每分钟 flow 快照）。
    - 实时采集：flow_history 本身就是 RFTwin 实时服务分钟快照（m/cell_uph/util/buf），
      不含逐分钟良品数，因此 good/defect 保持 None，不补造。
    """
    output_rows = record.get("flow_history") or []
    cell_rows: dict[str, dict[str, Any]] = {}
    for row in record.get("cell_flow_history") or []:
        key = row.get("m") or row.get("sim_minute")
        if key:
            cell_rows[str(key)] = row
    for row in output_rows:
        key = row.get("m") or row.get("sim_minute")
        if key and (row.get("util") or row.get("buf") or row.get("cell_uph")):
            cell_rows.setdefault(str(key), row)

    frames: list[dict[str, Any]] = []
    for row in output_rows:
        label = str(row.get("m") or row.get("sim_minute") or "")
        cell = cell_rows.get(label) or {}
        frames.append(
            {
                "label": label,
                "good": row.get("good_units"),
                "defect": row.get("defect_units"),
                "throughput": row.get("throughput_uph"),
                "wip": row.get("wip"),
                "util": cell.get("util") or row.get("util") or {},
                "buf": cell.get("buf") or row.get("buf") or {},
                "cell_uph": cell.get("cell_uph") or row.get("cell_uph") or {},
            }
        )
    return frames


def render_process_playback(record: dict[str, Any]) -> None:
    """用 Plotly 动画帧回放一轮仿真的分钟级过程（可播放/拖动）。"""
    frames = process_frames(record)
    if not frames:
        st.info("该记录没有分钟级过程数据，无法回放。")
        return

    util_cells = sorted({cell for frame in frames for cell in frame["util"]})
    buf_keys = sorted({buf for frame in frames for buf in frame["buf"]})
    has_output = any(frame["good"] is not None for frame in frames)
    if not (util_cells or buf_keys or has_output):
        st.info("该记录没有可视化的过程字段。")
        return

    charts: list[str] = []
    if util_cells:
        charts.append("util")
    if buf_keys:
        charts.append("buf")
    if has_output:
        charts.append("output")
    titles = {"util": "工位利用率", "buf": "缓冲区占用", "output": "分钟产出"}

    fig = make_subplots(rows=1, cols=len(charts), subplot_titles=[titles[c] for c in charts])
    first = frames[0]
    trace_ids: dict[str, int] = {}
    col = 1
    if "util" in charts:
        fig.add_trace(
            go.Bar(
                x=[CELL_LABELS.get(c, c) for c in util_cells],
                y=[first["util"].get(c, 0) for c in util_cells],
                marker_color="#1F7A8C",
                name="利用率",
                hovertemplate="%{x}: %{y:.1%}<extra></extra>",
            ),
            row=1,
            col=col,
        )
        trace_ids["util"] = len(fig.data) - 1
        col += 1
    if "buf" in charts:
        fig.add_trace(
            go.Bar(
                x=[BUFFER_LABELS.get(b, b) for b in buf_keys],
                y=[first["buf"].get(b, 0) for b in buf_keys],
                marker_color="#C08A3E",
                name="占用",
            ),
            row=1,
            col=col,
        )
        trace_ids["buf"] = len(fig.data) - 1
        col += 1
    if "output" in charts:
        fig.add_trace(
            go.Bar(
                x=["良品", "首检不合格"],
                y=[first["good"] or 0, first["defect"] or 0],
                marker_color=["#2F6B57", "#A65246"],
                showlegend=False,
            ),
            row=1,
            col=col,
        )
        trace_ids["output"] = len(fig.data) - 1

    plot_frames = []
    for index, frame in enumerate(frames):
        data = []
        indices = []
        if "util" in trace_ids:
            data.append(
                go.Bar(
                    x=[CELL_LABELS.get(c, c) for c in util_cells],
                    y=[frame["util"].get(c, 0) for c in util_cells],
                )
            )
            indices.append(trace_ids["util"])
        if "buf" in trace_ids:
            data.append(
                go.Bar(
                    x=[BUFFER_LABELS.get(b, b) for b in buf_keys],
                    y=[frame["buf"].get(b, 0) for b in buf_keys],
                )
            )
            indices.append(trace_ids["buf"])
        if "output" in trace_ids:
            data.append(
                go.Bar(
                    x=["良品", "首检不合格"],
                    y=[frame["good"] or 0, frame["defect"] or 0],
                )
            )
            indices.append(trace_ids["output"])
        plot_frames.append(
            go.Frame(
                name=str(index),
                data=data,
                traces=indices,
                layout=go.Layout(title_text=f"仿真时间 {frame['label']}"),
            )
        )
    fig.frames = plot_frames

    if "util" in charts:
        fig.update_yaxes(range=[0, 1], tickformat=".0%", row=1, col=1)
    steps = [
        {
            "method": "animate",
            "label": frame["label"],
            "args": [[str(i)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}}],
        }
        for i, frame in enumerate(frames)
    ]
    fig.update_layout(
        title=f"仿真时间 {frames[0]['label']}",
        height=380,
        margin=dict(l=8, r=8, t=72, b=8),
        showlegend=False,
        sliders=[
            {
                "active": 0,
                "pad": {"t": 34},
                "currentvalue": {"prefix": "仿真时间："},
                "steps": steps,
            }
        ],
        updatemenus=[
            {
                "type": "buttons",
                "showactive": False,
                "x": 0.0,
                "y": 1.22,
                "buttons": [
                    {
                        "label": "▶ 播放",
                        "method": "animate",
                        "args": [None, {"frame": {"duration": 220, "redraw": True}, "fromcurrent": True, "transition": {"duration": 0}}],
                    },
                    {
                        "label": "❚❚ 暂停",
                        "method": "animate",
                        "args": [[None], {"frame": {"duration": 0, "redraw": True}, "mode": "immediate"}],
                    },
                ],
            }
        ],
    )
    st.plotly_chart(style_fig(fig, height=380), width="stretch", key=f"process_{record['experiment_id']}")
    if record.get("kind") == "live":
        st.caption("回放数据来自 RFTwin 实时服务每分钟快照（含启停前的预热历史）；实时快照不含逐分钟良品数，故不绘制分钟产出。")
    else:
        st.caption("回放数据来自保存的 RFTwin 确定性实验记录：分钟产出、工位利用率与缓冲区占用。")


def edge_results_frame(record: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for key in STRATEGY_ORDER:
        run = (record.get("runs") or {}).get(key)
        if not run:
            continue
        for result in run.get("results", []):
            task = result["task"]
            rows.append(
                {
                    "策略key": key,
                    "策略": run.get("strategy_name", STRATEGY_NAMES.get(key, key)),
                    "任务ID": task["task_id"],
                    "来源": task.get("source", ""),
                    "优先级": PRIORITY_LABELS.get(task.get("priority", 1), "中"),
                    "节点ID": result.get("node_id"),
                    "节点": result.get("node_name") or "—",
                    "状态": STATUS_LABELS.get(result.get("status"), result.get("status")),
                    "总时延(ms)": result.get("total_latency_ms"),
                    "时延上限(ms)": task.get("deadline_ms"),
                    "上行(ms)": result.get("upload_ms", 0.0),
                    "排队(ms)": result.get("queue_ms", 0.0),
                    "计算(ms)": result.get("compute_ms", 0.0),
                    "回传(ms)": result.get("download_ms", 0.0),
                    "决策原因": result.get("decision_reason", ""),
                    "候选详情": result.get("candidate_details", {}),
                }
            )
    return pd.DataFrame(rows)


def edge_metrics_frame(record: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for key in STRATEGY_ORDER:
        metrics = (record.get("metrics") or {}).get(key)
        if not metrics:
            continue
        cost = (record.get("costs") or {}).get(key, {})
        rows.append(
            {
                "策略": metrics.get("strategy_name", STRATEGY_NAMES.get(key, key)),
                "任务完成率(%)": round(float(metrics.get("success_rate", 0.0)) * 100, 1),
                "时延满足率(%)": round(float(metrics.get("satisfaction_rate", 0.0)) * 100, 1),
                "平均时延(ms)": fmt_num(metrics.get("avg_latency_ms")),
                "P95时延(ms)": fmt_num(metrics.get("p95_latency_ms")),
                "超时数": metrics.get("timeout_tasks", 0),
                "拒绝数": metrics.get("rejected_tasks", 0),
                "决策开销(µs)": fmt_num(float(metrics.get("avg_decision_ms", 0.0)) * 1000, 2),
                "仿真成本": fmt_num(cost.get("total_cost"), 4),
            }
        )
    return pd.DataFrame(rows)


def scheduler_gantt_rows(record: dict[str, Any], strategy_key: str) -> list[dict[str, Any]]:
    """把单策略的任务结果拆成 上行/排队/计算/回传 时间段，用于甘特时间线。"""
    run = (record.get("runs") or {}).get(strategy_key) or {}
    rows: list[dict[str, Any]] = []
    for result in run.get("results", []):
        task = result.get("task") or {}
        start = float(task.get("arrival_time_ms") or 0.0)
        node = result.get("node_name") or result.get("node_id") or "未执行"
        status = result.get("status")
        base = {
            "Task": f"#{task.get('task_id')}",
            "Node": node,
            "Status": STATUS_LABELS.get(status, status),
            "Deadline": task.get("deadline_ms"),
            "Reason": result.get("decision_reason", ""),
        }
        if status == "rejected":
            rows.append({**base, "Segment": "拒绝", "Start": start, "End": start, "Duration": 0.0})
            continue
        for segment, field in (("上行", "upload_ms"), ("排队", "queue_ms"), ("计算", "compute_ms"), ("回传", "download_ms")):
            duration = float(result.get(field) or 0.0)
            if duration <= 0:
                continue
            rows.append({**base, "Segment": segment, "Start": start, "End": start + duration, "Duration": duration})
            start += duration
    return rows


def render_scheduler_timeline(record: dict[str, Any]) -> None:
    st.markdown("### 调度过程时间线")
    st.caption("每个任务从其到达时刻开始，按 上行 → 排队 → 计算 → 回传 展开；拒绝任务以 ✕ 标出。")
    strategy_key = st.radio(
        "查看哪一策略的执行过程",
        options=list(STRATEGY_ORDER),
        format_func=lambda key: STRATEGY_NAMES.get(key, key),
        horizontal=True,
        key=f"timeline_strategy_{record['experiment_id']}",
    )
    rows = scheduler_gantt_rows(record, strategy_key)
    if not rows:
        st.info("该策略没有可展示的任务执行记录。")
    else:
        fig = go.Figure()
        for segment in ("上行", "排队", "计算", "回传"):
            subset = [row for row in rows if row["Segment"] == segment]
            if not subset:
                continue
            fig.add_trace(
                go.Bar(
                    x=[row["Duration"] for row in subset],
                    base=[row["Start"] for row in subset],
                    y=[row["Node"] for row in subset],
                    orientation="h",
                    name=segment,
                    marker_color=SEGMENT_COLORS[segment],
                    customdata=[
                        [row["Task"], row["Status"], row["Start"], row["End"], row["Deadline"], row["Reason"]]
                        for row in subset
                    ],
                    hovertemplate=(
                        "任务 %{customdata[0]} · %{customdata[1]}<br>"
                        "开始 %{customdata[2]:.1f} ms → 结束 %{customdata[3]:.1f} ms<br>"
                        "时延上限 %{customdata[4]} ms<br>%{customdata[5]}<extra>" + segment + "</extra>"
                    ),
                )
            )
        rejected = [row for row in rows if row["Segment"] == "拒绝"]
        if rejected:
            fig.add_trace(
                go.Scatter(
                    x=[row["Start"] for row in rejected],
                    y=[row["Node"] for row in rejected],
                    mode="markers",
                    name="拒绝",
                    marker=dict(symbol="x", size=9, color=SEGMENT_COLORS["拒绝"]),
                    customdata=[[row["Task"], row["Reason"]] for row in rejected],
                    hovertemplate="任务 %{customdata[0]} 被拒绝<br>%{customdata[1]}<extra></extra>",
                )
            )
        fig.update_layout(
            barmode="overlay",
            title=f"{STRATEGY_NAMES.get(strategy_key, strategy_key)} · 任务执行时间线",
            xaxis_title="任务到达后时间 (ms)",
            yaxis_title="执行节点",
            height=420,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        )
        st.plotly_chart(style_fig(fig, height=420), width="stretch", key=f"gantt_{record['experiment_id']}_{strategy_key}")

    st.markdown("### 任务完成进度")
    st.caption("各策略在相同任务集上的累计完成任务数随时间变化（成功与超时均计为完成）。")
    progress = go.Figure()
    colors = {"fixed_local": "#5B6B7F", "fixed_cloud": "#C08A3E", "dynamic": "#1F7A8C"}
    for key in STRATEGY_ORDER:
        run = (record.get("runs") or {}).get(key) or {}
        completions = []
        for result in run.get("results", []):
            if result.get("status") not in ("success", "timeout"):
                continue
            task = result.get("task") or {}
            finish = float(task.get("arrival_time_ms") or 0.0) + float(result.get("total_latency_ms") or 0.0)
            completions.append(finish)
        if not completions:
            continue
        completions.sort()
        xs = [0.0] + completions
        ys = list(range(0, len(completions) + 1))
        progress.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(shape="hv", width=2, color=colors.get(key, "#5B6B7F")),
                name=STRATEGY_NAMES.get(key, key),
            )
        )
    progress.update_layout(xaxis_title="任务到达后时间 (ms)", yaxis_title="累计完成任务数", height=320)
    st.plotly_chart(style_fig(progress, height=320), width="stretch", key=f"progress_{record['experiment_id']}")


def page_overview() -> None:
    hero(
        "AI 驱动的工业数字工厂工程平台",
        "把工厂建模、产线仿真、生产数据分析、边缘计算调度与 AI 优化放进同一个工程项目闭环。"
        "工厂引擎来自 RFTwin，边缘调度与软件生成来自 EdgeCoder。",
        ["RFTwin 三维工厂", "EdgeCoder 边缘调度", "统一项目存储", "可解释实验"],
    )
    project = current_project()
    if project is None:
        return

    factory_records = list_experiments(project["project_id"], kind="factory")
    live_records = list_experiments(project["project_id"], kind="live")
    edge_records = list_experiments(project["project_id"], kind="edge")
    latest_factory = sorted(factory_records + live_records, key=lambda r: r.get("created_at", ""), reverse=True)
    latest_factory_record = latest_factory[0] if latest_factory else None
    latest_edge = edge_records[0] if edge_records else None

    cols = st.columns(4)
    cols[0].metric("当前项目", project["name"])
    cols[1].metric("生产目标", f"{project['line']['target_quantity']} 件")
    cols[2].metric("工厂种子", project["factory"]["seed"])
    cols[3].metric("实验记录", f"{len(factory_records) + len(live_records)} / {len(edge_records)}（工厂/边缘）")

    if latest_factory_record:
        analysis = analyze_record(latest_factory_record)
        kpis = analysis["kpis"]
        c1, c2, c3, c4 = st.columns(4)
        if latest_factory_record.get("kind") == "live":
            c1.metric("最新实时采集累计产量", f"{kpis['total_output']} 件")
        else:
            c1.metric("最新实验窗口产量", f"{kpis['window_good_units']} 件")
        c2.metric("OEE（综合效率）", pct(kpis["oee"]))
        c3.metric("瓶颈工位", analysis["bottleneck_name"])
        if latest_edge:
            dynamic = (latest_edge.get("metrics") or {}).get("dynamic", {})
            c4.metric("动态调度满足率", pct(dynamic.get("satisfaction_rate")))
        else:
            c4.metric("边缘实验", "尚未运行")
    else:
        st.info("该项目还没有生产实验。请到「生产数据分析」运行第一轮工厂仿真。")

    if latest_factory_record:
        breakdown = oee_breakdown(latest_factory_record)
        st.markdown("### OEE 分解与损失结构")
        o1, o2, o3, o4 = st.columns(4)
        o1.metric("可用率", pct(breakdown["availability"]))
        o2.metric("性能", pct(breakdown["performance"]))
        o3.metric("质量", pct(breakdown["quality"]))
        o4.metric("OEE（综合效率）", pct(breakdown["oee"]))
        loss_col, att_col = st.columns([2, 1])
        with loss_col:
            losses = breakdown["losses"]
            fig = go.Figure(
                go.Bar(
                    x=list(losses.keys()),
                    y=[value * 100 for value in losses.values()],
                    marker_color=["#A65246", "#C08A3E", "#5B6B7F"],
                    text=[f"{value * 100:.1f}%" for value in losses.values()],
                    textposition="outside",
                )
            )
            fig.update_layout(title="相对损失（按 OEE 分解）", yaxis_title="%")
            st.plotly_chart(style_fig(fig, height=240), width="stretch")
        with att_col:
            attainment = target_attainment(latest_factory_record, project)
            if attainment["attainment"] is not None:
                st.metric("班次目标达成率", pct(attainment["attainment"]))
                st.caption(
                    f"累计良品 {attainment['good_units']} / 目标 {attainment['target_quantity']} 件；"
                    f"差距 {attainment['gap_units']} 件。产量为仿真值。"
                )
                st.progress(min(1.0, max(0.0, attainment["attainment"])))
        st.caption("损失占比由 RFTwin 的可用率/性能/质量分解计算，是模型输出，不是真实财务损失。")

    st.markdown("### 运行控制")
    status = service_status(remote=True)
    healthy = bool(status.get("healthy"))
    r1, r2, r3, r4 = st.columns(4)
    if r1.button("进入三维仿真"):
        goto_page("三维仿真")
    if r2.button("运行 / 查看数据分析"):
        goto_page("生产数据分析")
    if r3.button("对比调度策略"):
        goto_page("调度策略实验")
    demo_label = "继续三维演示（30×）" if healthy else "一键演示：启动三维并 30× 播放"
    if r4.button(demo_label, type="primary"):
        try:
            with st.spinner("正在启动或复用三维服务，并设置为 30× 推进..."):
                info = get_service_manager().restart_if_needed(project)
                RFTwinClient(info["url"]).set_speed(30.0)
            goto_page("三维仿真")
        except Exception as exc:  # noqa: BLE001
            st.error(f"演示启动失败：{exc}")

    st.markdown("### 端到端工作流")
    steps = [
        ("1. 工厂搭建", "配置 RFTwin 四工位产线的节拍、设备与仿真种子。"),
        ("2. 生产线配置", "设置产品、生产目标、缓冲区与故障假设。"),
        ("3. 三维仿真", "启动项目专用 RFTwin 实时服务，查看同一轮仿真的三维画面。"),
        ("4. 生产数据分析", "运行离线确定性实验或采集 三维实时状态，识别瓶颈。"),
        ("5. 边缘计算", "把视觉检测事件映射为计算任务，配置边缘节点与网络。"),
        ("6. 调度策略实验", "在相同任务集上比较固定本地、固定云端与动态调度。"),
        ("7. AI 优化建议", "基于真实指标提出候选参数，并用短时仿真验证前后差异。"),
    ]
    left, right = st.columns(2)
    with left:
        for title, desc in steps[:4]:
            st.markdown(f'<div class="ec-card" style="margin-bottom:10px"><h4>{title}</h4><p>{desc}</p></div>', unsafe_allow_html=True)
    with right:
        for title, desc in steps[4:]:
            st.markdown(f'<div class="ec-card" style="margin-bottom:10px"><h4>{title}</h4><p>{desc}</p></div>', unsafe_allow_html=True)

    st.markdown("### 系统状态")
    s1, s2, s3 = st.columns(3)
    status = service_status(remote=True)
    s1.metric("三维实时服务", "运行中" if status.get("healthy") else "未启动")
    s2.metric("项目工厂参数指纹", project_factory_signature(project))
    s3.metric("数据目录", "data/projects")
    if status.get("healthy"):
        st.caption(f"三维服务地址：{twin_public_url(status)} · run_id {status.get('run_id', '—')}")

    st.markdown(
        """
<div class="ec-note">
<b>数据诚实性：</b>工厂指标、瓶颈、工位状态来自 RFTwin 确定性引擎；边缘时延、
调度决策与成本来自 EdgeCoder 仿真器。二者都是软件仿真，不代表真实工厂或 6G 网络实测。
三维页面、数据分析页面与调度实验会明确标注数据来源与实验 ID。
</div>
""",
        unsafe_allow_html=True,
    )


def page_factory_model() -> None:
    st.markdown("## 工厂搭建")
    st.caption("复用 RFTwin 的四工位机器人产线与三维场景。参数保存后，需要重启 三维服务并重新运行实验才会生效。")
    project = current_project()
    if project is None:
        return

    with st.expander("编辑项目信息", expanded=False):
        with st.form("edit_project_meta"):
            meta_name = st.text_input("项目名称", value=project["name"])
            meta_desc = st.text_area("项目描述", value=project.get("description", ""), height=70)
            meta_goal = st.text_input("项目目标", value=project.get("goal", ""))
            if st.form_submit_button("保存项目信息"):
                updated = copy.deepcopy(project)
                updated["name"] = meta_name.strip()
                updated["description"] = meta_desc.strip()
                updated["goal"] = meta_goal.strip()
                save_updated_project(updated, "项目信息已保存。")

    layout = {}
    if RFTWIN_LAYOUT.exists():
        try:
            layout = json.loads(RFTWIN_LAYOUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            layout = {}

    c1, c2, c3 = st.columns(3)
    c1.metric("产线布局", layout.get("layout_id", "factory-layout-v1"))
    c2.metric("工位总数", sum(c["stations"] for c in project["factory"]["cells"].values()))
    c3.metric("机器人总数", sum(c["robots"] for c in project["factory"]["cells"].values()))

    st.markdown("### 固定产线拓扑")
    st.markdown(
        '<div class="ec-flow">原材料库存 → <b>焊接</b>（W-01~W-04 / R-01~R-04）→ C-01 → '
        '<b>装配</b>（A-01~A-04 / R-05~R-08）→ C-02 → <b>机加工</b>（M-01~M-02 / R-09~R-10 / CNC-01~02）'
        ' → C-03 → <b>视觉检测</b>（Q-01~Q-02 / R-11~R-12 / CAM-01~02）→ 成品 / 返修</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "RFTwin 的三维场景、AMR 走廊与工位位置由 config/factory_layout.json 固定；"
        "本阶段不支持自由拖拽布局，但节拍、缓冲、故障概率、种子等参数会真实进入仿真。"
    )

    with st.expander("设备与工位清单", expanded=False):
        equipment = [
            {"单元": "焊接", "工位": "W-01 ~ W-04", "机器人": "R-01 ~ R-04", "设备": "焊接控制器"},
            {"单元": "装配", "工位": "A-01 ~ A-04", "机器人": "R-05 ~ R-08", "设备": "夹具/拧紧工具"},
            {"单元": "机加工", "工位": "M-01 ~ M-02", "机器人": "R-09 ~ R-10", "设备": "CNC-01 ~ CNC-02"},
            {"单元": "视觉检测", "工位": "Q-01 ~ Q-02", "机器人": "R-11 ~ R-12", "设备": "CAM-01 ~ CAM-02"},
            {"单元": "物流", "工位": "AMR-01 ~ AMR-02", "机器人": "—", "设备": "RACK × 4"},
        ]
        st.dataframe(pd.DataFrame(equipment), hide_index=True, width="stretch")

    st.markdown("### 仿真与设备参数")
    with st.form("factory_model_form"):
        top1, top2, top3 = st.columns(3)
        seed = int(top1.number_input("工厂随机种子", min_value=0, max_value=2_147_483_647, value=int(project["factory"]["seed"]), step=1))
        duration = int(top2.number_input("默认仿真时长（分钟）", min_value=1, max_value=180, value=int(project["factory"]["duration_min"]), step=1))
        amr_units = int(top3.number_input("AMR（移动机器人）数量", min_value=1, max_value=2, value=int(project["factory"]["amr_units"]), step=1))

        st.markdown("**工位节拍**（秒/件，按整条单元周期计算；会按比例分配到 RFTwin 的各工序阶段）")
        cycle_values = {}
        cell_cols = st.columns(4)
        for col, (key, cell) in zip(cell_cols, project["factory"]["cells"].items()):
            with col:
                st.caption(f"{CELL_LABELS[key]} · {cell['stations']} 工位")
                cycle_values[key] = float(
                    st.number_input(
                        "单元节拍（秒）",
                        min_value=5.0,
                        max_value=300.0,
                        value=float(cell["cycle_sec"]),
                        step=1.0,
                        key=f"cycle_{key}",
                    )
                )
        submitted = st.form_submit_button("保存工厂参数")

    if submitted:
        updated = copy.deepcopy(project)
        updated["factory"]["seed"] = seed
        updated["factory"]["duration_min"] = duration
        updated["factory"]["amr_units"] = amr_units
        for key, value in cycle_values.items():
            updated["factory"]["cells"][key]["cycle_sec"] = value
        save_updated_project(updated, "工厂参数已保存。请到「三维仿真」重启服务，并重新运行实验。")

    status = service_status(remote=True)
    if status.get("healthy") and status.get("project_id") == project["project_id"]:
        if status.get("factory_signature") != project_factory_signature(project):
            st.warning("当前 三维服务使用的是旧工厂参数。请到「三维仿真」点击「重启服务并应用当前配置」。")
    st.info("自由拖拽布局与新增/移动设备暂未实现：RFTwin 的前端场景与布局文件是固定的。当前支持参数级编辑，且参数真实进入仿真。")


def page_line_config() -> None:
    st.markdown("## 生产线配置")
    st.caption("配置产品与生产目标、缓冲区、故障假设。所有参数都会写入 RFTwin 仿真参数。")
    project = current_project()
    if project is None:
        return

    line = project["line"]
    factory = project["factory"]
    with st.form("line_config_form"):
        c1, c2, c3 = st.columns(3)
        product_id = c1.text_input("产品编号", value=line["product_id"])
        product_name = c2.text_input("产品名称", value=line["product_name"])
        target = int(c3.number_input("班次生产目标（件）", min_value=1, max_value=100_000, value=int(line["target_quantity"]), step=50))
        c4, c5 = st.columns(2)
        shift_hours = int(c4.number_input("班次长度（小时）", min_value=1, max_value=24, value=int(line["shift_length_hours"]), step=1))
        c5.text_input("班次开始时间", value=line.get("shift_start", "06:00"), disabled=True)
        st.markdown("**缓冲区容量（件）**")
        buffer_cols = st.columns(5)
        buffer_values = {}
        for col, (key, buffer) in zip(buffer_cols, factory["buffers"].items()):
            buffer_values[key] = int(
                col.number_input(
                    BUFFER_LABELS[key],
                    min_value=0,
                    max_value=1000,
                    value=int(buffer["capacity"]),
                    step=1,
                    key=f"buf_{key}",
                )
            )
        st.markdown("**故障与质量假设**")
        f1, f2 = st.columns(2)
        fail_prob = float(f1.number_input("视觉检测失败概率（0~0.2）", min_value=0.0, max_value=0.2, value=float(factory["vision_fail_probability"]), step=0.001, format="%.4f"))
        minor_stop = float(f2.number_input("设备短暂停机概率/周期（0~0.2）", min_value=0.0, max_value=0.2, value=float(factory["minor_stop_probability"]), step=0.001, format="%.4f"))
        submitted = st.form_submit_button("保存生产线配置")

    if submitted:
        updated = copy.deepcopy(project)
        updated["line"]["product_id"] = product_id.strip()
        updated["line"]["product_name"] = product_name.strip()
        updated["line"]["target_quantity"] = target
        updated["line"]["shift_length_hours"] = shift_hours
        for key, value in buffer_values.items():
            updated["factory"]["buffers"][key]["capacity"] = value
        updated["factory"]["vision_fail_probability"] = fail_prob
        updated["factory"]["minor_stop_probability"] = minor_stop
        save_updated_project(updated, "生产线配置已保存。三维服务与后续实验将使用新参数。")

    st.markdown("### 理论产能（由当前节拍与工位数解析计算）")
    rows = []
    capacities = {}
    for key, cell in project["factory"]["cells"].items():
        capacity = cell["stations"] * 3600.0 / max(0.1, cell["cycle_sec"])
        capacities[CELL_LABELS[key]] = capacity
        rows.append(
            {
                "工位": CELL_LABELS[key],
                "工位数": cell["stations"],
                "节拍(秒)": cell["cycle_sec"],
                "理论产能(件/小时)": round(capacity, 1),
                "说明": "理论值，不含故障、换型与物流等待",
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")
    if capacities:
        bottleneck = min(capacities, key=capacities.get)
        st.info(f"按理论产能计算，当前最低产能工位为 **{bottleneck}**（{capacities[bottleneck]:.0f} 件/小时）。实际瓶颈以仿真结果为准。")

    st.markdown("### 调度与订单规则")
    st.markdown(
        f"""
<div class="ec-note">
RFTwin 产线订单由引擎生成：产品编号 {line['product_id']}，班次目标 {line['target_quantity']} 件，
班次 {line['shift_start']} 开始。AMR 派工规则为 {line.get('priority_policy', 'PRIORITY_THEN_FIFO')}，
工序派工顺序为 READY → LONGEST_IDLE → HIGHEST_ROBOT_HEALTH → STATION_ID_ASC（RFTwin 参数固定）。
生产订单号、计划开始/结束时间由仿真时钟推算，并保存在实验记录中。
</div>
""",
        unsafe_allow_html=True,
    )


def page_three_d() -> None:
    st.markdown("## 三维仿真")
    st.caption("三维视图由 RFTwin 官方 React Three Fiber 前端提供；页面上的指标与状态来自同一个 RFTwin 实时后端。")
    project = current_project()
    if project is None:
        return

    manager = get_service_manager()
    status = service_status(remote=True)
    healthy = bool(status.get("healthy"))

    if not (RFTWIN_FRONTEND_DIST / "index.html").exists():
        st.error("RFTwin 前端尚未构建。请在 rftwin 目录执行：npm --prefix frontend ci 和 npm --prefix frontend run build。")
        return

    if not healthy:
        st.markdown(
            """
<div class="ec-note">
点击下方按钮后，平台会以当前项目工厂参数启动一个仅监听 127.0.0.1 的 RFTwin FastAPI 服务，
并托管已构建的三维前端。启动需要约 5~15 秒（引擎预跑 4 小时产能状态）。
</div>
""",
            unsafe_allow_html=True,
        )
        if st.button("启动项目三维仿真", type="primary"):
            try:
                with st.spinner("正在启动 RFTwin 实时服务并预热引擎..."):
                    info = manager.start(project)
                st.success(f"三维服务已启动：{twin_public_url(info)}")
                st.rerun()
            except FactoryServiceError as exc:
                st.error(f"启动失败：{exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"启动异常：{exc}")
        return

    if status.get("project_id") != project["project_id"]:
        st.warning(
            f"当前 三维服务属于项目 {status.get('project_id')}，与当前项目不一致。"
            "请重启以应用当前项目配置。"
        )
    elif status.get("factory_signature") != project_factory_signature(project):
        st.warning("项目工厂参数已修改，当前 三维服务仍是旧参数。重启后会使用新配置。")

    top1, top2, top3 = st.columns([2, 2, 3])
    with top1:
        if st.button("重启服务并应用当前配置"):
            try:
                with st.spinner("正在重启 三维服务..."):
                    manager.start(project)
                st.success("服务已按当前项目配置重启。")
                st.rerun()
            except FactoryServiceError as exc:
                st.error(f"重启失败：{exc}")
    with top2:
        st.link_button("在新窗口打开三维工厂", twin_public_url(status))
    with top3:
        if st.button("停止 三维服务"):
            manager.stop()
            st.success("三维服务已停止。")
            st.rerun()

    st.caption(
        f"服务：{twin_public_url(status)} · run_id {status.get('run_id', '—')} · "
        f"引擎 {status.get('engine_version', '—')} · 参数集 {status.get('parameter_set_id', '—')}"
    )

    st.iframe(twin_public_url(status), height=800)

    client = RFTwinClient(status["url"], timeout=5.0)
    try:
        insight = client.flow_insight()
        control = client.control()
    except RFTwinClientError as exc:
        st.error(f"读取实时数据失败：{exc}")
        return

    st.markdown("### 实时运行状态（与三维画面同一实时后端）")

    @st.fragment(run_every=2.0)
    def _live_monitor() -> None:
        try:
            live_health = client.health()
            live_kpis = client.kpis()
            live_insight = client.flow_insight()
            snapshot = client.snapshot(live_health.get("run_id", "LIVE-001"))
            payload = client.events(
                live_health.get("run_id", "LIVE-001"),
                after_seq=max(0, int(snapshot.get("seq", 0)) - 200),
                limit=200,
            )
        except RFTwinClientError as exc:
            st.warning(f"实时数据暂不可用：{exc}")
            return

        live_control = client.control()
        if live_control.get("resetting"):
            state_label, state_dot = "重置中", "warn"
        elif live_control.get("paused"):
            state_label, state_dot = "已暂停", "warn"
        else:
            state_label, state_dot = "运行中", "ok"
        live_events = payload.get("events", [])
        alarms = [
            ev
            for ev in live_events
            if "FAIL" in str(ev.get("event_type", ""))
            or any(token in str(ev.get("event_type", "")) for token in ("FAULT", "JAM", "EMERGENCY"))
            or str(ev.get("severity", "")).upper() in ("HIGH", "CRITICAL")
        ]
        st.markdown(
            '<div class="ec-status">'
            f'<span><span class="dot {state_dot}"></span><b>{state_label}</b></span>'
            f'<span>仿真时间 <b>{live_health.get("sim_time", "—")[11:19]}</b></span>'
            f'<span>事件序号 <b>{live_health.get("seq", "—")}</b></span>'
            f'<span>推进速度 <b>{live_control.get("speed", 1.0)}×</b></span>'
            f'<span>最近窗口异常/报警 <b>{len(alarms)}</b></span>'
            "</div>",
            unsafe_allow_html=True,
        )

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("仿真时间", live_health.get("sim_time", "—")[11:19])
        m2.metric("事件序号", live_health.get("seq", "—"))
        m3.metric("总产量", live_kpis.get("total_output", "—"))
        m4.metric("良品数", live_kpis.get("good_units", "—"))
        m5.metric("OEE（综合效率）", pct((live_kpis.get("oee") or {}).get("oee")))
        bottleneck_id = str(live_insight.get("bottleneck") or "")
        bottleneck_key = bottleneck_id.replace("CELL-", "").lower()
        m6.metric(
            "当前瓶颈",
            CELL_LABELS.get(bottleneck_key, live_insight.get("bottleneck_name", "—")),
        )
        st.caption("每 2 秒自动刷新，与上方三维画面为同一轮仿真。")
        if alarms:
            latest_alarm = alarms[-1]
            st.markdown(
                '<div class="ec-alarm"><b>最近事件窗口内异常/报警 '
                f'{len(alarms)} 条</b> · 最近：{str(latest_alarm.get("sim_time", ""))[11:19]} '
                f'{latest_alarm.get("event_type")} {latest_alarm.get("source_id")} — '
                f'{str(latest_alarm.get("message", ""))[:80]}</div>',
                unsafe_allow_html=True,
            )

        station_rows = []
        for cell in (snapshot.get("state") or {}).get("cells", []):
            for station in cell.get("stations", []):
                station_rows.append(
                    {
                        "工位": station.get("station_id"),
                        "单元": cell_label_from_id(cell.get("cell_id")),
                        "工位状态": station_state_label(station.get("state")),
                        "当前工件": station.get("current_part_id") or "—",
                        "故障": fault_label(station.get("fault_kind")),
                    }
                )
        state_colors_zh = {
            "运行": "#E2F2E8",
            "加工中": "#E2F2E8",
            "就绪": "#EDF7F0",
            "阻塞": "#FBF0DC",
            "缺料": "#FBF0DC",
            "维护": "#EAF4F7",
            "故障": "#FBE5E2",
            "异常": "#FBE5E2",
        }

        def _station_style(row: pd.Series) -> list[str]:
            color = state_colors_zh.get(str(row["工位状态"]), "#FFFFFF")
            return [f"background-color: {color}"] * len(row)

        frame_col, event_col = st.columns([3, 2])
        with frame_col:
            st.markdown("**12 个工位实时状态**（绿=运行，黄=阻塞/缺料，红=故障，灰=空闲）")
            station_df = pd.DataFrame(station_rows)
            st.dataframe(station_df.style.apply(_station_style, axis=1), hide_index=True, width="stretch", height=300)
        with event_col:
            st.markdown("**实时事件流（最近 12 条）**")
            event_rows = [
                {
                    "时间": str(ev.get("sim_time", ""))[11:19],
                    "事件": event_label(ev.get("event_type")),
                    "级别": severity_label(ev.get("severity")),
                    "设备": ev.get("source_id"),
                }
                for ev in live_events[-12:]
            ]
            st.dataframe(pd.DataFrame(event_rows), hide_index=True, width="stretch", height=300)

    _live_monitor()

    controls = st.columns([1, 1, 1, 2, 2])
    if controls[0].button("暂停" if not control.get("paused") else "继续"):
        try:
            client.pause() if not control.get("paused") else client.start()
        except RFTwinClientError as exc:
            st.error(str(exc))
        st.rerun()
    if controls[1].button("重置仿真"):
        try:
            with st.spinner("正在按当前参数重建引擎（预跑约 3~10 秒）..."):
                client.reset()
            st.success("仿真已重置到 10:00 预热完成起点。")
            st.rerun()
        except RFTwinClientError as exc:
            st.error(str(exc))
    speed = controls[2].number_input("倍速", min_value=0.1, max_value=100.0, value=float(control.get("speed", 1.0)), step=0.5, format="%.1f")
    if controls[3].button("应用倍速"):
        try:
            client.set_speed(speed)
        except RFTwinClientError as exc:
            st.error(str(exc))
        st.rerun()

    preset_cols = st.columns([1, 1, 1, 1, 4])
    for index, value in enumerate((1, 10, 30, 60)):
        if preset_cols[index].button(f"{value}×", key=f"speed_preset_{value}"):
            try:
                client.set_speed(float(value))
                st.success(f"实时推进速度已设为 {value}×。")
                st.rerun()
            except RFTwinClientError as exc:
                st.error(str(exc))
    preset_cols[4].caption("倍速只改变实时引擎的推进速度（1× = 真实 1 秒对应仿真 1 秒），不改变仿真逻辑与随机种子。")

    with st.expander("故障注入（写入同一实时引擎，会影响三维画面）"):
        with st.form("failure_injection_form"):
            f1, f2, f3 = st.columns(3)
            failure_type = f1.selectbox(
                "故障类型",
                ["conveyor_jam", "tool_failure", "minor_stop", "safety_gate_open", "amr_fault", "zone_obstacle"],
            )
            target_id = f2.text_input("目标 ID", value="C-03")
            duration_sec = f3.number_input("持续时长（秒）", min_value=1, max_value=7200, value=120, step=10)
            if st.form_submit_button("注入故障"):
                try:
                    payload = {"failure_type": failure_type, "target_id": target_id, "duration_sec": int(duration_sec)}
                    client.inject_failure(payload)
                    st.success("故障注入成功，可在三维画面与审计记录中查看。")
                except RFTwinClientError as exc:
                    st.error(f"注入失败：{exc}")

    with st.expander("瓶颈解释与最近审计"):
        st.write(insight.get("reason", "—"))
        try:
            audit = client.audit(limit=20)
            st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch")
        except RFTwinClientError as exc:
            st.caption(f"审计记录不可用：{exc}")

    if st.button("采集当前实时状态为实验记录", type="primary"):
        try:
            record = capture_live_record(project, client)
            st.session_state.last_live_id = record["experiment_id"]
            st.success(f"已保存实时实验 {record['experiment_id']}（run_id {record['live_run_id']}，seq {record['live_seq']}）。")
        except (RFTwinClientError, ProjectError) as exc:
            st.error(f"采集失败：{exc}")


def _select_experiment(records: list[dict[str, Any]], key: str, default_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    if not records:
        return None
    options = experiment_options(records)
    ids = list(options)
    index = ids.index(default_id) if default_id in ids else 0
    selected = st.selectbox("选择实验记录", options=ids, index=index, format_func=lambda x: options[x], key=key)
    return next((r for r in records if r["experiment_id"] == selected), None)


def page_data_analysis() -> None:
    st.markdown("## 生产数据分析")
    st.caption("工厂数据来自 RFTwin 引擎。离线实验可复现，实时采集与三维画面为同一轮仿真。")
    project = current_project()
    if project is None:
        return

    tab_offline, tab_live, tab_view = st.tabs(["运行离线实验", "采集 三维实时数据", "查看与分析"])
    with tab_offline:
        with st.form("run_factory_form"):
            c1, c2, c3 = st.columns(3)
            duration = int(c1.number_input("仿真时长（分钟）", min_value=1, max_value=180, value=int(project["factory"]["duration_min"]), step=1))
            seed = int(c2.number_input("随机种子", min_value=0, max_value=2_147_483_647, value=int(project["factory"]["seed"]), step=1))
            c3.caption("相同参数与种子会得到相同结果。预热 4 小时只用于初始化，不计入实验事件窗口。")
            if st.form_submit_button("运行工厂仿真并保存", type="primary"):
                try:
                    with st.spinner("RFTwin 引擎正在预热并运行仿真..."):
                        record = run_factory_experiment(project, duration_min=duration, seed=seed)
                    st.session_state.last_factory_id = record["experiment_id"]
                    st.session_state.analysis_exp = record["experiment_id"]
                    st.success(
                        f"已保存实验 {record['experiment_id']}：窗口产量 {record['window_metrics']['good_units']} 件，"
                        f"耗时 {record['wall_time_ms']:.0f} ms。"
                    )
                    st.rerun()
                except (FactoryExperimentError, ProjectError) as exc:
                    st.error(f"实验失败：{exc}")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"实验异常：{exc}")

    with tab_live:
        status = service_status(remote=True)
        if not status.get("healthy"):
            st.info("三维实时服务未运行。请先到「三维仿真」启动服务。")
        else:
            st.caption(f"将采集 {twin_public_url(status)} 当前实时运行状态（run_id {status.get('run_id')}）并保存。")
            if st.button("采集当前 三维实时数据", type="primary"):
                try:
                    with st.spinner("正在读取实时快照、指标与事件..."):
                        record = capture_live_record(project, RFTwinClient(status["url"]))
                    st.session_state.last_live_id = record["experiment_id"]
                    st.session_state.analysis_exp = record["experiment_id"]
                    st.success(f"已保存实时记录 {record['experiment_id']}（seq {record['live_seq']}）。")
                    st.rerun()
                except (RFTwinClientError, ProjectError) as exc:
                    st.error(f"采集失败：{exc}")

    with tab_view:
        factory_records = list_experiments(project["project_id"], kind="factory")
        live_records = list_experiments(project["project_id"], kind="live")
        records = sorted(factory_records + live_records, key=lambda r: r.get("created_at", ""), reverse=True)
        if not records:
            st.info("还没有工厂实验记录。请先运行离线实验或采集 三维实时数据。")
        else:
            default_id = st.session_state.get("analysis_exp") or (records[0]["experiment_id"] if records else None)
            record = _select_experiment(records, key="analysis_exp_select", default_id=default_id)
            if record:
                _render_factory_record(record)


def _render_factory_record(record: dict[str, Any]) -> None:
    analysis = analyze_record(record)
    kpis = analysis["kpis"]
    source_label = {"factory": "离线确定性实验", "live": "实时采集"}.get(record.get("kind"), record.get("kind"))
    st.markdown(f"### {str(record.get('label', record['experiment_id'])).replace('3D 实时采集', '实时采集')}")
    st.caption(
        f"实验 ID：{record['experiment_id']} · 来源：{source_label} · 创建时间：{record.get('created_at')} · "
        f"种子：{(record.get('config') or {}).get('seed', '—')} · 参数哈希：{str(record.get('param_hash') or (record.get('provenance') or {}).get('parameter_hash', '—'))[:28]}"
    )
    if record.get("kind") == "live":
        st.caption(f"实时 run_id：{record.get('live_run_id')} · 事件序号：{record.get('live_seq')} · 仿真时间：{record.get('sim_time')}")
    with st.expander("本次实验的假设与说明"):
        for item in record.get("assumptions", []):
            st.markdown(f"- {item}")

    kpi_metrics_grid(kpis)
    window = record.get("window_metrics") or {}
    if record.get("kind") == "live":
        w1, w2, w3 = st.columns(3)
        w1.metric("累计产量（自班次开始）", f"{kpis['total_output']} 件")
        w2.metric("累计良品", f"{kpis['good_units']} 件")
        w3.metric("累计 OEE", pct(kpis["oee"]))
        if window.get("note"):
            st.caption(window["note"])
        if window.get("avg_throughput_uph") is not None:
            st.caption(f"实时分钟快照的线体产能（各单元 cell_uph 最小值均值）：{fmt_num(window['avg_throughput_uph'], 1, ' 件/小时')}")
    else:
        w1, w2, w3 = st.columns(3)
        w1.metric("实验窗口良品", f"{kpis['window_good_units']} 件")
        w2.metric("窗口平均产能", fmt_num(kpis["window_avg_throughput_uph"], 1, " 件/小时"))
        w3.metric("窗口平均 OEE", pct(kpis["window_avg_oee"]))

    breakdown = oee_breakdown(record)
    attainment = target_attainment(record)
    st.markdown("### OEE 分解与目标达成")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("可用率", pct(breakdown["availability"]))
    b2.metric("性能", pct(breakdown["performance"]))
    b3.metric("质量", pct(breakdown["quality"]))
    if attainment["attainment"] is not None:
        b4.metric("班次目标达成率", pct(attainment["attainment"]))
    else:
        b4.metric("班次目标达成率", "—")

    loss_col, rank_col = st.columns(2)
    with loss_col:
        losses = breakdown["losses"]
        fig = go.Figure(
            go.Bar(
                x=list(losses.keys()),
                y=[value * 100 for value in losses.values()],
                marker_color=["#A65246", "#C08A3E", "#5B6B7F"],
                text=[f"{value * 100:.1f}%" for value in losses.values()],
                textposition="outside",
            )
        )
        fig.update_layout(title="相对损失（按 OEE 分解）", yaxis_title="%")
        st.plotly_chart(style_fig(fig, height=260), width="stretch", key=f"loss_{record['experiment_id']}")
        if attainment["attainment"] is not None:
            st.caption(
                f"累计良品 {attainment['good_units']} / 班次目标 {attainment['target_quantity']} 件，"
                f"差距 {attainment['gap_units']} 件；目标为设定值，产量为仿真值。"
            )
    with rank_col:
        util = analysis["utilization"] or {}
        if util:
            ranked = sorted(
                ((cell_label_from_id(cell), float(value)) for cell, value in util.items()),
                key=lambda item: item[1],
                reverse=True,
            )
            labels = [item[0] for item in ranked]
            values = [item[1] for item in ranked]
            colors = ["#A65246" if label == analysis["bottleneck_name"] else "#2A7F8F" for label in labels]
            fig = go.Figure(
                go.Bar(
                    x=labels,
                    y=values,
                    marker_color=colors,
                    text=[f"{value * 100:.0f}%" for value in values],
                    textposition="outside",
                )
            )
            fig.add_hline(
                y=analysis["line_avg_util"],
                line_dash="dot",
                line_color="#5B7186",
                annotation_text="全线平均",
                annotation_position="top left",
            )
            fig.update_layout(title="工位利用率排名（红色 = 当前瓶颈）", yaxis_title="利用率")
            fig.update_yaxes(range=[0, 1], tickformat=".0%")
            st.plotly_chart(style_fig(fig, height=260), width="stretch", key=f"rank_{record['experiment_id']}")

    st.markdown("### 瓶颈诊断")
    st.markdown(f"**当前瓶颈：{analysis['bottleneck_name']}**（利用率 {pct((analysis['utilization'] or {}).get(analysis['bottleneck']))}）")
    for item in analysis["evidence"]:
        st.markdown(f"- {item}")
    if analysis["insight_reason"]:
        with st.expander("查看引擎原始说明（英文日志）"):
            st.write(analysis["insight_reason"])

    st.markdown("### 仿真过程回放")
    st.caption("按仿真分钟逐帧回放这一轮实验的过程：工位利用率、缓冲区占用与分钟产出。可用播放按钮或拖动时间轴。")
    render_process_playback(record)

    is_live = record.get("kind") == "live"
    history = flow_history_frame(record, limit=90 if is_live else None)
    if not history.empty:
        util_cols = [c for c in history.columns if c.startswith("util_")]
        uph_cols = [c for c in history.columns if c.startswith("uph_")]
        if is_live:
            st.markdown("### 各单元产能与利用率趋势（实时分钟快照）")
            c1, c2 = st.columns(2)
            with c1:
                if uph_cols:
                    uph_df = history[["时间"] + uph_cols].rename(
                        columns={c: CELL_LABELS.get(c.replace("uph_", ""), c.replace("uph_", "")) for c in uph_cols}
                    )
                    uph_long = uph_df.melt(id_vars="时间", var_name="单元", value_name="产能(件/小时)")
                    fig = px.line(uph_long, x="时间", y="产能(件/小时)", color="单元", title="各单元 cell_uph（RFTwin 实时服务）")
                    st.plotly_chart(style_fig(fig), width="stretch")
                else:
                    st.info("该实时记录没有 cell_uph 分钟快照。")
            with c2:
                if util_cols:
                    util_df = history[["时间"] + util_cols].rename(
                        columns={c: CELL_LABELS.get(c.replace("util_", ""), c.replace("util_", "")) for c in util_cols}
                    )
                    util_long = util_df.melt(id_vars="时间", var_name="工位", value_name="利用率")
                    fig = px.line(util_long, x="时间", y="利用率", color="工位", title="工位利用率随时间变化")
                    fig.update_yaxes(range=[0, 1], tickformat=".0%")
                    st.plotly_chart(style_fig(fig), width="stretch")
            buf_cols = [c for c in history.columns if c.startswith("buf_")]
            if buf_cols:
                st.markdown("### 缓冲区占用")
                buf_labels = {"buf_raw": "原材料", "buf_b1": "缓冲1", "buf_b2": "缓冲2", "buf_b3": "缓冲3", "buf_rework": "返修", "buf_fg": "成品"}
                buf_df = history[["时间"] + buf_cols].rename(columns={c: buf_labels.get(c, c) for c in buf_cols})
                buf_long = buf_df.melt(id_vars="时间", var_name="缓冲区", value_name="占用")
                fig = px.line(buf_long, x="时间", y="占用", color="缓冲区", title="缓冲区占用")
                st.plotly_chart(style_fig(fig), width="stretch")
        else:
            st.markdown("### 生产节拍与质量趋势")
            c1, c2 = st.columns(2)
            with c1:
                fig = go.Figure()
                fig.add_trace(go.Bar(x=history["时间"], y=history["良品数"], name="良品数", marker_color="#2F6B57"))
                fig.add_trace(go.Bar(x=history["时间"], y=history["首检不合格"], name="首检不合格", marker_color="#A65246"))
                fig.update_layout(barmode="stack", title="每分钟产出", xaxis_title="仿真时间")
                st.plotly_chart(style_fig(fig), width="stretch")
            with c2:
                fig = px.line(history, x="时间", y="产出(件/小时)", title="每分钟产能节拍", markers=False, color_discrete_sequence=["#1F7A8C"])
                st.plotly_chart(style_fig(fig), width="stretch")

            if util_cols:
                st.markdown("### 各工位利用率与缓冲区")
                c1, c2 = st.columns(2)
                with c1:
                    util_df = history[["时间"] + util_cols].rename(
                        columns={c: CELL_LABELS.get(c.replace("util_", ""), c.replace("util_", "")) for c in util_cols}
                    )
                    util_long = util_df.melt(id_vars="时间", var_name="工位", value_name="利用率")
                    fig = px.line(util_long, x="时间", y="利用率", color="工位", title="工位利用率随时间变化")
                    fig.update_yaxes(range=[0, 1], tickformat=".0%")
                    st.plotly_chart(style_fig(fig), width="stretch")
                with c2:
                    buf_cols = [c for c in history.columns if c.startswith("buf_")]
                    if buf_cols:
                        buf_labels = {"buf_raw": "原材料", "buf_b1": "缓冲1", "buf_b2": "缓冲2", "buf_b3": "缓冲3", "buf_fg": "成品"}
                        buf_df = history[["时间"] + buf_cols].rename(columns={c: buf_labels.get(c, c) for c in buf_cols})
                        buf_long = buf_df.melt(id_vars="时间", var_name="缓冲区", value_name="占用")
                        fig = px.line(buf_long, x="时间", y="占用", color="缓冲区", title="缓冲区占用")
                        st.plotly_chart(style_fig(fig), width="stretch")

    st.markdown("### 工位状态与等待时间")
    stations = pd.DataFrame(record.get("station_metrics") or [])
    if not stations.empty:
        c1, c2 = st.columns(2)
        with c1:
            top = stations.sort_values("waiting_time_sec", ascending=False).head(10)
            fig = go.Figure()
            fig.add_trace(go.Bar(x=top["station_id"], y=top["blocked_time_sec"], name="阻塞时间", marker_color="#C08A3E"))
            fig.add_trace(go.Bar(x=top["station_id"], y=top["starved_time_sec"], name="缺料等待", marker_color="#5B6B7F"))
            fig.update_layout(barmode="stack", title="工位阻塞/缺料等待 Top 10", yaxis_title="秒")
            st.plotly_chart(style_fig(fig), width="stretch")
        with c2:
            show_cols = ["station_id", "cell_id", "state", "processing_time_sec", "blocked_time_sec", "starved_time_sec", "current_part_id", "fault_kind"]
            station_display = stations[show_cols].copy()
            station_display["cell_id"] = station_display["cell_id"].map(cell_label_from_id)
            station_display["state"] = station_display["state"].map(station_state_label)
            station_display["fault_kind"] = station_display["fault_kind"].map(fault_label)
            station_display.columns = ["工位", "单元", "状态", "节拍(秒)", "阻塞(秒)", "缺料等待(秒)", "当前工件", "故障"]
            st.dataframe(station_display, hide_index=True, width="stretch", height=330)

    st.markdown("### 生产订单")
    orders = pd.DataFrame(record.get("orders") or [])
    if not orders.empty:
        order_display = orders.copy()
        if "priority" in order_display:
            order_display["priority"] = order_display["priority"].map(
                lambda value: PRIORITY_TEXT_LABELS.get(str(value), value)
            )
        if "status" in order_display:
            order_display["status"] = order_display["status"].map(
                lambda value: ORDER_STATUS_LABELS.get(str(value), value)
            )
        rename_map = {
            "order_id": "订单号",
            "line_id": "产线",
            "product_id": "产品编号",
            "target_quantity": "目标数量",
            "completed_quantity": "已完成",
            "defect_quantity": "不合格",
            "priority": "优先级",
            "status": "状态",
            "planned_start": "计划开始",
            "planned_end": "计划结束",
        }
        order_display = order_display.rename(columns=rename_map)
        st.dataframe(order_display, hide_index=True, width="stretch")

    st.markdown("### 事件记录")
    events = pd.DataFrame(record.get("events") or [])
    if not events.empty:
        filter_cols = st.columns([2, 2, 3])
        event_types = sorted(events["event_type"].dropna().unique().tolist())
        selected_types = filter_cols[0].multiselect("事件类型", event_types, default=event_types[:8])
        severities = sorted(events["severity"].dropna().unique().tolist())
        selected_severity = filter_cols[1].multiselect("严重程度", severities, default=severities)
        search = filter_cols[2].text_input("搜索消息/设备", value="")
        filtered = events[events["event_type"].isin(selected_types) & events["severity"].isin(selected_severity)]
        if search:
            mask = filtered["message"].astype(str).str.contains(search, case=False, na=False) | filtered["machine_id"].astype(str).str.contains(search, case=False, na=False)
            filtered = filtered[mask]
        display_cols = ["timestamp", "event_type", "severity", "machine_id", "cell_id", "task_id", "task_status", "failure_event", "message"]
        display_df = filtered[display_cols].tail(500).copy()
        display_df["event_type"] = display_df["event_type"].map(event_label)
        display_df["severity"] = display_df["severity"].map(severity_label)
        display_df["cell_id"] = display_df["cell_id"].map(cell_label_from_id)
        display_df["task_status"] = display_df["task_status"].map(
            lambda value: TASK_STATUS_LABELS.get(str(value), value) if value is not None else "—"
        )
        display_df["failure_event"] = display_df["failure_event"].map(
            lambda value: event_label(value) if value is not None else "—"
        )
        display_df.columns = ["仿真时间", "事件类型", "级别", "设备", "单元", "任务/工件", "任务状态", "故障事件", "原始消息（引擎日志）"]
        st.dataframe(display_df, hide_index=True, width="stretch", height=320)
        csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
        st.download_button("下载事件 CSV", data=csv_bytes, file_name=f"{record['experiment_id']}_events.csv", mime="text/csv")
    st.caption("工厂事件、指标与三维画面均来自 RFTwin 引擎；本页不会生成或补造任何数据。")


def page_edge_compute() -> None:
    st.markdown("## 边缘计算")
    st.caption("配置边缘节点与工厂检测事件到计算任务的映射。调度实验会使用这里的参数。")
    project = current_project()
    if project is None:
        return

    edge = project["edge"]
    st.markdown("### 计算节点与网络")
    with st.form("edge_nodes_form"):
        node_cols = st.columns(len(edge["nodes"]))
        node_values = {}
        for col, node in zip(node_cols, edge["nodes"]):
            with col:
                st.markdown(f"**{node['display_name']}**（{node['node_type']}）")
                compute = float(st.number_input("算力 GFLOPS", 0.5, 1000.0, float(node["compute_gflops"]), 1.0, key=f"e_compute_{node['node_id']}"))
                load = float(st.number_input("基础负载 0~0.9", 0.0, 0.9, float(node["base_load"]), 0.05, format="%.2f", key=f"e_load_{node['node_id']}"))
                max_task = float(st.number_input("单任务上限 GFLOP", 0.1, 500.0, float(node["max_task_gflops"]), 0.5, key=f"e_max_{node['node_id']}"))
                slots = int(st.number_input("队列容量", 1, 200, int(node["max_queue_slots"]), 1, key=f"e_slots_{node['node_id']}"))
                bw = float(st.number_input("带宽 Mbps", 0.5, 5000.0, float(node["bandwidth_mbps"]), 5.0, key=f"e_bw_{node['node_id']}"))
                latency = float(st.number_input("单向时延 ms", 0.0, 2000.0, float(node["latency_ms"]), 1.0, key=f"e_lat_{node['node_id']}"))
                congestion = float(st.number_input("拥塞 0~0.95", 0.0, 0.95, float(node["congestion"]), 0.05, format="%.2f", key=f"e_cong_{node['node_id']}"))
                cost = float(st.number_input("成本系数 / GFLOP", 0.0, 100.0, float(node["cost_per_gflop"]), 0.005, format="%.3f", key=f"e_cost_{node['node_id']}"))
                node_values[node["node_id"]] = (compute, load, max_task, slots, bw, latency, congestion, cost)

        st.markdown("**任务映射假设**")
        m = edge["mapping"]
        m1, m2, m3, m4 = st.columns(4)
        data_size = float(m1.number_input("检测数据量 MB", 0.1, 100.0, float(m["data_size_mb"]), 0.5))
        base_compute = float(m2.number_input("基础计算需求 GFLOP", 0.1, 200.0, float(m["compute_demand_gflops"]), 0.5))
        deadline = float(m3.number_input("时延上限 ms", 10.0, 10_000.0, float(m["deadline_ms"]), 50.0))
        deadline_jitter = float(m4.number_input("时延抖动 ms", 0.0, 5000.0, float(m["deadline_jitter_ms"]), 10.0))
        m5, m6, m7, m8 = st.columns(4)
        interval = float(m5.number_input("任务到达间隔 ms", 1.0, 60_000.0, float(m["arrival_interval_ms"]), 10.0))
        net_jitter = float(m6.number_input("网络抖动 ms", 0.0, 1000.0, float(m["network_jitter_ms"]), 1.0))
        result_size = float(m7.number_input("结果回传 MB", 0.001, 10.0, float(m["result_size_mb"]), 0.01, format="%.3f"))
        max_tasks = int(m8.number_input("最大任务数", 1, 500, int(m["max_tasks"]), 5))
        m9, m10 = st.columns(2)
        failed_factor = float(m9.number_input("失败任务计算放大", 1.0, 3.0, float(m["failed_compute_factor"]), 0.05, format="%.2f"))
        variation = float(m10.number_input("计算需求波动 0~0.5", 0.0, 0.5, float(m["compute_variation"]), 0.01, format="%.2f"))
        submitted = st.form_submit_button("保存边缘配置")

    if submitted:
        updated = copy.deepcopy(project)
        for node in updated["edge"]["nodes"]:
            values = node_values[node["node_id"]]
            (
                node["compute_gflops"],
                node["base_load"],
                node["max_task_gflops"],
                node["max_queue_slots"],
                node["bandwidth_mbps"],
                node["latency_ms"],
                node["congestion"],
                node["cost_per_gflop"],
            ) = values
        updated["edge"]["mapping"] = {
            "data_size_mb": data_size,
            "compute_demand_gflops": base_compute,
            "deadline_ms": deadline,
            "deadline_jitter_ms": deadline_jitter,
            "arrival_interval_ms": interval,
            "network_jitter_ms": net_jitter,
            "result_size_mb": result_size,
            "max_tasks": max_tasks,
            "failed_compute_factor": failed_factor,
            "compute_variation": variation,
        }
        save_updated_project(updated, "边缘节点与任务映射配置已保存。")

    st.markdown("### 工厂事件 → 计算任务映射预览")
    st.markdown(
        """
<div class="ec-note">
映射规则：每个视觉检测事件（INSPECTION_PASS / INSPECTION_FAIL）生成一个计算任务；
任务按配置间隔依次到达；检测失败任务为高优先级并放大计算需求；数据量、计算需求、
时延上限与网络参数均为可调仿真假设。映射不会保存，运行调度实验时才写入实验记录。
</div>
""",
        unsafe_allow_html=True,
    )
    records = sorted(
        list_experiments(project["project_id"], kind="factory")
        + list_experiments(project["project_id"], kind="live"),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )
    if not records:
        st.info("请先在「生产数据分析」运行工厂实验。")
        return
    options = experiment_options(records)
    ids = list(options)
    source_id = st.selectbox("用作任务来源的工厂实验", options=ids, format_func=lambda x: options[x])
    source = next(r for r in records if r["experiment_id"] == source_id)
    if st.button("生成任务映射预览", type="primary"):
        try:
            tasks, meta = build_compute_tasks(project, source)
            st.session_state.edge_preview = {
                "source_id": source_id,
                "tasks": tasks,
                "meta": meta,
            }
        except EdgeMappingError as exc:
            st.error(str(exc))

    preview = st.session_state.get("edge_preview")
    if preview and preview.get("source_id") == source_id:
        meta = preview["meta"]
        tasks = preview["tasks"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("检测事件", meta["source_events"])
        c2.metric("映射任务", meta["mapped_tasks"])
        c3.metric("失败检测任务", meta["failed_tasks"])
        c4.metric("到达间隔", f"{meta['arrival_interval_ms']:.0f} ms")
        df = pd.DataFrame(
            [
                {
                    "任务ID": t.task_id,
                    "来源": t.source,
                    "到达(ms)": t.arrival_time_ms,
                    "数据量(MB)": t.data_size_mb,
                    "计算需求(GFLOP)": t.compute_demand_gflops,
                    "时延上限(ms)": t.deadline_ms,
                    "优先级": PRIORITY_LABELS.get(t.priority, ""),
                }
                for t in tasks
            ]
        )
        st.dataframe(df, hide_index=True, width="stretch", height=300)
        st.info("映射预览已生成。请到「调度策略实验」运行三种策略。")
    elif preview and preview.get("source_id") != source_id:
        st.caption("实验来源已变化，请重新生成预览。")


def page_scheduling() -> None:
    st.markdown("## 调度策略实验")
    st.caption("三种策略共享同一任务集、网络条件与随机种子；唯一变量是调度策略。")
    project = current_project()
    if project is None:
        return

    records = sorted(
        list_experiments(project["project_id"], kind="factory")
        + list_experiments(project["project_id"], kind="live"),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )
    if not records:
        st.info("请先在「生产数据分析」运行工厂实验，再到「边缘计算」检查任务映射。")
        return
    options = experiment_options(records)
    ids = list(options)
    source_id = st.selectbox("工厂实验（任务来源）", options=ids, format_func=lambda x: options[x], key="schedule_source")
    source = next(r for r in records if r["experiment_id"] == source_id)
    inspection_count = (source.get("event_stats") or {}).get("inspection_events", 0)
    st.caption(f"该实验采集到检测事件 {inspection_count} 条；映射时最多取前配置的 {project['edge']['mapping']['max_tasks']} 条。")

    if st.button("运行三种调度策略并保存", type="primary"):
        try:
            with st.spinner("正在生成任务集并运行固定本地 / 固定云端 / 动态调度..."):
                record = run_edge_experiment(project, source)
            st.session_state.last_edge_id = record["experiment_id"]
            st.success(f"已保存调度实验 {record['experiment_id']}（{len(record['tasks'])} 个任务）。")
            st.rerun()
        except EdgeMappingError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"调度实验失败：{exc}")

    edge_records = list_experiments(project["project_id"], kind="edge")
    if not edge_records:
        st.info("还没有调度实验记录。")
        return
    default_id = st.session_state.get("last_edge_id") or edge_records[0]["experiment_id"]
    record = _select_experiment(edge_records, key="edge_exp_select", default_id=default_id)
    if record is None:
        return
    _render_edge_record(record)


def _render_edge_record(record: dict[str, Any]) -> None:
    st.markdown(f"### {str(record.get('label', record['experiment_id'])).replace('3D 实时采集', '实时采集')}")
    meta = record.get("mapping") or {}
    st.caption(
        f"实验 ID：{record['experiment_id']} · 来源工厂实验：{record.get('factory_experiment_id')} · "
        f"任务 {len(record.get('tasks', []))} 个 · 配置指纹：{record.get('config_signature', '—')} · "
        f"创建：{record.get('created_at')}"
    )
    if meta:
        st.caption(
            f"映射：检测事件 {meta.get('source_events')} → 任务 {meta.get('mapped_tasks')} "
            f"（失败 {meta.get('failed_tasks')}）· 到达间隔 {meta.get('arrival_interval_ms')} ms · "
            f"数据量 {meta.get('data_size_mb')} MB"
        )
    with st.expander("公平性与假设"):
        for item in record.get("assumptions", []):
            st.markdown(f"- {item}")

    metrics = record.get("metrics") or {}
    cols = st.columns(3)
    for col, key in zip(cols, STRATEGY_ORDER):
        m = metrics.get(key, {})
        with col:
            col.markdown(f"**{m.get('strategy_name', STRATEGY_NAMES[key])}**")
            col.metric("时延满足率", pct(m.get("satisfaction_rate")))
            col.caption(
                f"平均时延 {fmt_num(m.get('avg_latency_ms'), 0, ' ms')} · "
                f"超时 {m.get('timeout_tasks', 0)} · 拒绝 {m.get('rejected_tasks', 0)}"
            )

    st.markdown("### 指标对比")
    st.dataframe(edge_metrics_frame(record), hide_index=True, width="stretch")

    ranking = strategy_ranking(record)
    if ranking:
        best = ranking[0]
        st.markdown("### 策略评价")
        st.info(
            "按 **时延满足率 → 平均时延 → 仿真成本** 排序："
            + "；".join(f"{row['rank']}. {row['strategy_name']}" for row in ranking)
            + f"。本次最佳为 **{best['strategy_name']}**"
            f"（满足率 {pct(best['satisfaction_rate'])}，平均时延 {fmt_num(best['avg_latency_ms'], 1, ' ms')}）。"
            "结论由本次实验数值计算，不代表该策略在所有工作负载下都最优。"
        )
        rank_df = pd.DataFrame(
            [
                {
                    "排名": row["rank"],
                    "策略": row["strategy_name"],
                    "时延满足率(%)": round(row["satisfaction_rate"] * 100, 1),
                    "任务完成率(%)": round(row["success_rate"] * 100, 1),
                    "平均时延(ms)": round(row["avg_latency_ms"], 1),
                    "仿真成本": round(row["total_cost"], 4),
                }
                for row in ranking
            ]
        )
        st.dataframe(rank_df, hide_index=True, width="stretch")
        st.caption(
            f"公平性：三策略使用同一任务集（{len(record.get('tasks') or [])} 个任务）、"
            f"同一配置指纹 {record.get('config_signature', '—')}，来源实验 "
            f"{record.get('factory_experiment_id')}；唯一变量是调度策略。"
        )

    render_scheduler_timeline(record)

    df = edge_results_frame(record)
    if df.empty:
        st.warning("该实验没有可展示的任务结果。")
        return

    c1, c2 = st.columns(2)
    with c1:
        latency_rows = []
        for key in STRATEGY_ORDER:
            m = metrics.get(key)
            if not m:
                continue
            latency_rows.append(
                {
                    "策略": m.get("strategy_name", STRATEGY_NAMES[key]),
                    "平均时延": m.get("avg_latency_ms"),
                    "P95时延": m.get("p95_latency_ms"),
                }
            )
        latency_df = pd.DataFrame(latency_rows).melt(id_vars="策略", var_name="指标", value_name="时延(ms)")
        fig = px.bar(
            latency_df,
            x="策略",
            y="时延(ms)",
            color="指标",
            barmode="group",
            text_auto=".0f",
            title="平均时延与 P95 时延",
            color_discrete_sequence=["#1F7A8C", "#A9C1CC"],
        )
        st.plotly_chart(style_fig(fig), width="stretch")
    with c2:
        rate_rows = []
        for key in STRATEGY_ORDER:
            m = metrics.get(key)
            if not m:
                continue
            rate_rows.append(
                {
                    "策略": m.get("strategy_name", STRATEGY_NAMES[key]),
                    "任务完成率": float(m.get("success_rate", 0)) * 100,
                    "时延满足率": float(m.get("satisfaction_rate", 0)) * 100,
                }
            )
        rate_df = pd.DataFrame(rate_rows).melt(id_vars="策略", var_name="指标", value_name="比例(%)")
        fig = px.bar(
            rate_df,
            x="策略",
            y="比例(%)",
            color="指标",
            barmode="group",
            text_auto=".1f",
            title="任务完成率与时延满足率",
            color_discrete_sequence=["#2F6B57", "#C08A3E"],
        )
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(style_fig(fig), width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        dist_rows = []
        for key in STRATEGY_ORDER:
            m = metrics.get(key) or {}
            for node_id, count in (m.get("node_distribution") or {}).items():
                dist_rows.append({"策略": m.get("strategy_name", key), "节点": node_id, "任务数": count})
        dist_df = pd.DataFrame(dist_rows)
        if not dist_df.empty:
            fig = px.bar(dist_df, x="策略", y="任务数", color="节点", barmode="group", text_auto=True, title="节点任务分布")
            st.plotly_chart(style_fig(fig), width="stretch")
    with c2:
        util_rows = []
        for key in STRATEGY_ORDER:
            m = metrics.get(key) or {}
            for node_id, value in (m.get("node_utilization") or {}).items():
                util_rows.append({"策略": m.get("strategy_name", key), "节点": node_id, "利用率(%)": float(value) * 100})
        util_df = pd.DataFrame(util_rows)
        if not util_df.empty:
            fig = px.bar(util_df, x="策略", y="利用率(%)", color="节点", barmode="group", text_auto=".0f", title="节点资源利用率")
            fig.update_yaxes(range=[0, 100])
            st.plotly_chart(style_fig(fig), width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        completed = df[df["总时延(ms)"].notna()]
        if not completed.empty:
            fig = px.box(completed, x="策略", y="总时延(ms)", color="策略", points="outliers", title="任务完成时延分布", color_discrete_map=STRATEGY_COLORS)
            fig.update_layout(showlegend=False)
            st.plotly_chart(style_fig(fig), width="stretch")
    with c2:
        status_df = df.groupby(["策略", "状态"], as_index=False)["任务ID"].count().rename(columns={"任务ID": "任务数"})
        fig = px.bar(
            status_df,
            x="策略",
            y="任务数",
            color="状态",
            barmode="stack",
            text_auto=True,
            title="任务状态构成",
            color_discrete_map={"准时完成": "#2F6B57", "超时完成": "#C08A3E", "已拒绝": "#A65246", "失败": "#7A4E4E"},
        )
        st.plotly_chart(style_fig(fig), width="stretch")

    st.markdown("### 决策开销与仿真成本")
    c1, c2 = st.columns(2)
    with c1:
        overhead = []
        for key in STRATEGY_ORDER:
            m = metrics.get(key)
            if m:
                overhead.append({"策略": m.get("strategy_name", key), "平均决策开销(µs)": float(m.get("avg_decision_ms", 0)) * 1000})
        odf = pd.DataFrame(overhead)
        fig = px.bar(odf, x="策略", y="平均决策开销(µs)", color="策略", text_auto=".1f", title="每任务平均调度决策耗时（本机实测）", color_discrete_map=STRATEGY_COLORS)
        fig.update_layout(showlegend=False)
        st.plotly_chart(style_fig(fig, height=300), width="stretch")
    with c2:
        cost_rows = []
        for key in STRATEGY_ORDER:
            c = (record.get("costs") or {}).get(key)
            if c:
                cost_rows.append({"策略": STRATEGY_NAMES.get(key, key), "总仿真成本": c.get("total_cost", 0), "已执行任务": c.get("executed_tasks", 0)})
        cdf = pd.DataFrame(cost_rows)
        if not cdf.empty:
            fig = px.bar(cdf, x="策略", y="总仿真成本", color="策略", text_auto=".2f", title="任务处理成本（GFLOP × 成本系数）", color_discrete_map=STRATEGY_COLORS)
            fig.update_layout(showlegend=False)
            st.plotly_chart(style_fig(fig, height=300), width="stretch")
        st.caption((record.get("costs") or {}).get("dynamic", {}).get("cost_note", "成本为仿真假设。"))

    st.markdown("### 单任务调度详情")
    task_ids = sorted({int(t) for t in df["任务ID"].tolist()})
    selected_task = st.selectbox("任务", task_ids, format_func=lambda tid: f"#{tid:03d}")
    task_rows = df[df["任务ID"] == selected_task]
    compare = task_rows[["策略", "节点", "状态", "总时延(ms)", "时延上限(ms)", "排队(ms)", "计算(ms)", "决策原因"]]
    st.dataframe(compare, hide_index=True, width="stretch")
    for key in STRATEGY_ORDER:
        detail = task_rows[task_rows["策略key"] == key]
        if detail.empty:
            continue
        candidates = detail.iloc[0]["候选详情"]
        if candidates:
            rows = []
            for node_id, info in candidates.items():
                rows.append(
                    {
                        "策略": detail.iloc[0]["策略"],
                        "节点": node_id,
                        "预测总时延(ms)": fmt_num(info.get("total")),
                        "评分": fmt_num(info.get("score"), 3),
                        "满足时延": "是" if info.get("within_deadline") else "否",
                        "算力可执行": "否" if info.get("over_capacity") else "是",
                        "队列可用": "否" if info.get("queue_full") else "是",
                        "已选中": "是" if node_id == detail.iloc[0]["节点ID"] else "",
                    }
                )
            with st.expander(f"{detail.iloc[0]['策略']} 的候选节点评估"):
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    csv_bytes = df.drop(columns=["候选详情"]).to_csv(index=False).encode("utf-8-sig")
    st.download_button("下载调度结果 CSV", data=csv_bytes, file_name=f"{record['experiment_id']}_scheduling.csv", mime="text/csv")


def page_ai_optimize() -> None:
    st.markdown("## AI 优化建议")
    st.caption("第一阶段使用可解释规则分析真实实验指标，并用 RFTwin 短时重放验证建议，不调用外部大模型 API。")
    project = current_project()
    if project is None:
        return

    records = sorted(
        list_experiments(project["project_id"], kind="factory")
        + list_experiments(project["project_id"], kind="live"),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )
    if not records:
        st.info("请先运行工厂实验。")
        return
    options = experiment_options(records)
    ids = list(options)
    exp_id = st.selectbox("分析对象", options=ids, format_func=lambda x: options[x])
    record = next(r for r in records if r["experiment_id"] == exp_id)
    analysis = analyze_record(record)

    st.markdown(f"### 实验诊断（{record['experiment_id']}）")
    for item in analysis["evidence"]:
        st.markdown(f"- {item}")
    if analysis["insight_reason"]:
        with st.expander("查看引擎原始说明（英文日志）"):
            st.write(analysis["insight_reason"])

    cache = st.session_state.get("optimizer")
    if not cache or cache.get("exp_id") != exp_id:
        if st.button("生成优化建议", type="primary"):
            recs = generate_recommendations(project, record)
            st.session_state.optimizer = {"exp_id": exp_id, "recs": recs, "eval": None}
            st.rerun()
        return

    recs = cache.get("recs") or []
    st.markdown("### 规则建议（引用实际指标）")
    for rec in recs:
        with st.expander(f"{rec['title']} · {rec.get('category', '')}", expanded=True):
            st.write(rec["rationale"])
            st.markdown("**证据**")
            for item in rec.get("evidence", []):
                st.markdown(f"- {item}")
            st.markdown(f"**预期影响：** {rec.get('expected_effect', '—')}")
            st.markdown(f"**风险/边界：** {rec.get('risk', '—')}")
            if rec.get("rftwin_overrides"):
                st.caption("候选参数变更（RFTwin 参数）：" + json.dumps(rec["rftwin_overrides"], ensure_ascii=False))

    st.markdown("### 用短时仿真验证建议")
    vcols = st.columns([2, 2, 3])
    duration = int(vcols[0].number_input("验证时长（分钟）", min_value=3, max_value=30, value=10, step=1))
    if vcols[1].button("运行验证", type="primary"):
        try:
            with st.spinner("正在重放基线与候选参数（相同种子）..."):
                evaluation = evaluate_recommendations(project, record, recs, duration_min=duration, max_candidates=3)
            st.session_state.optimizer = {"exp_id": exp_id, "recs": recs, "eval": evaluation}
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"验证失败：{exc}")
    vcols[2].caption("基线、候选方案均重新运行 RFTwin 引擎；表中所有差异均为实际仿真结果。")

    evaluation = (st.session_state.get("optimizer") or {}).get("eval")
    if not evaluation:
        return
    st.markdown(f"### 验证结果（种子 {evaluation['seed']}，时长 {evaluation['duration_min']} 分钟）")
    rows = []
    for item in evaluation["evaluations"]:
        if item.get("status") != "ok":
            rows.append({"建议": item["recipe_id"], "状态": item["status"], "说明": item.get("message", "")})
            continue
        p = item["delta_pct"]
        rows.append(
            {
                "建议": item["recipe_id"],
                "状态": "成功",
                "产量变化(%)": round(p.get("window_good_units", 0), 2),
                "产能变化(%)": round(p.get("window_avg_throughput_uph", 0), 2),
                "OEE变化(%)": round(p.get("window_avg_oee", 0), 2),
                "平均周期变化(%)": round(p.get("avg_lead_time_sec", 0), 2),
                "缺陷率变化(%)": round(p.get("defect_rate", 0), 2),
                "综合评分": item.get("score", 0),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    ok_items = [item for item in evaluation["evaluations"] if item.get("status") == "ok"]
    if ok_items:
        delta_rows = []
        labels = {
            "window_avg_throughput_uph": "产能变化",
            "avg_lead_time_sec": "生产周期变化",
            "window_avg_oee": "OEE 变化",
            "defect_rate": "缺陷率变化",
        }
        for item in ok_items:
            for key, label in labels.items():
                value = (item.get("delta_pct") or {}).get(key)
                if value is not None:
                    delta_rows.append({"建议": item["recipe_id"], "指标": label, "变化率(%)": value})
        if delta_rows:
            fig = px.bar(
                pd.DataFrame(delta_rows),
                x="建议",
                y="变化率(%)",
                color="指标",
                barmode="group",
                text_auto=".1f",
                title="候选方案相对基线的实测变化（同种子、同时长重放）",
            )
            fig.add_hline(y=0, line_color="#8798A8", line_width=1)
            st.plotly_chart(style_fig(fig, height=300), width="stretch")
            st.caption("变化率来自 RFTwin 引擎重放的实际计算结果；生产周期为负值表示缩短，缺陷率为负值表示改善。")
    best = evaluation.get("best")
    if best:
        best_rec = next((r for r in recs if r["recipe_id"] == best["recipe_id"]), None)
        st.success(f"最佳候选：{best_rec['title'] if best_rec else best['recipe_id']}（综合评分 {best['score']}）")
        if best_rec and best_rec.get("project_patch"):
            st.markdown("**将应用的参数修改：** " + json.dumps(best_rec["project_patch"], ensure_ascii=False))
            if st.button("应用最佳建议到项目", type="primary"):
                try:
                    updated = apply_project_patch(project, best_rec["project_patch"])
                    save_updated_project(updated, "建议参数已写入项目。请重新运行工厂实验验证效果，并重启 三维服务。")
                    st.session_state.optimizer = None
                except ProjectError as exc:
                    st.error(f"应用失败：{exc}")
    st.caption("建议为规则式分析；候选方案的“改善”必须由新一轮仿真确认，不能视为真实产线改造结论。")

    st.markdown(
        """
<div class="ec-note">
RFTwin 本身在三维界面中提供 Copilot 与 What-if（隔离引擎、12 项指标、第一分歧事件）。
本页的规则优化由统一平台实现，可与 RFTwin Copilot 并存，互不替代。
</div>
""",
        unsafe_allow_html=True,
    )


def page_history() -> None:
    st.markdown("## 实验历史")
    st.caption("所有实验保存在项目目录下，刷新或重启后可重新加载。")
    project = current_project()
    if project is None:
        return

    records = list_experiments(project["project_id"])
    if not records:
        st.info("还没有实验记录。")
        return

    rows = []
    for record in records:
        row = {
            "实验ID": record["experiment_id"],
            "类型": {"factory": "工厂离线", "live": "实时", "edge": "边缘调度"}.get(record.get("kind"), record.get("kind")),
            "创建时间": record.get("created_at"),
            "种子": (record.get("config") or {}).get("seed", "—"),
            "任务/时长": (
                f"{len(record.get('tasks', []))} 个任务"
                if record.get("kind") == "edge"
                else f"{(record.get('config') or {}).get('duration_min') or '实时'} 分钟"
            ),
        }
        if record.get("kind") == "edge":
            row["动态调度满足率(%)"] = round(float((record.get("metrics") or {}).get("dynamic", {}).get("satisfaction_rate", 0)) * 100, 1)
        elif record.get("kind") == "live":
            row["累计产量"] = (record.get("kpis") or {}).get("total_output", 0)
            row["OEE(%)"] = round(float((record.get("kpis") or {}).get("oee", {}).get("oee", 0)) * 100, 1)
        else:
            row["窗口良品数"] = (record.get("window_metrics") or {}).get("good_units", 0)
            row["OEE(%)"] = round(float((record.get("kpis") or {}).get("oee", {}).get("oee", 0)) * 100, 1)
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.markdown("### 实验对比")
    factory_records = [r for r in records if r.get("kind") in ("factory", "live")]
    if len(factory_records) >= 2:
        comp_rows = []
        for record in reversed(factory_records[-8:]):
            kpis = experiment_kpis(record)
            comp_rows.append(
                {
                    "实验": record["experiment_id"],
                    "产量": (
                        kpis["window_good_units"]
                        if record.get("kind") == "factory"
                        else kpis["total_output"]
                    ),
                    "平均产能": kpis["window_avg_throughput_uph"],
                    "OEE": kpis["oee"],
                    "平均周期": kpis["avg_lead_time_sec"],
                }
            )
        comp = pd.DataFrame(comp_rows)
        fig = px.bar(comp, x="实验", y="产量", color="实验", title="工厂实验产量对比（离线=窗口良品 / 实时=累计产量）")
        fig.update_layout(showlegend=False)
        st.plotly_chart(style_fig(fig), width="stretch")

    offline_records = [r for r in records if r.get("kind") == "factory"]
    st.markdown("### 场景并排对比")
    if len(offline_records) < 2:
        st.info("至少需要两轮离线工厂实验才能并排对比。请到「生产数据分析」运行不同参数的实验。")
    else:
        compare_options = experiment_options(offline_records)
        compare_ids = list(compare_options)
        newest_cfg = offline_records[0].get("config") or {}
        baseline_default = 1
        for index, record in enumerate(offline_records[1:], start=1):
            cfg = record.get("config") or {}
            if (
                cfg.get("duration_min") == newest_cfg.get("duration_min")
                and cfg.get("seed") == newest_cfg.get("seed")
            ):
                baseline_default = index
                break
        select_cols = st.columns(2)
        baseline_id = select_cols[0].selectbox(
            "基线实验",
            compare_ids,
            format_func=lambda x: compare_options[x],
            index=baseline_default,
            key="cmp_base",
        )
        candidate_id = select_cols[1].selectbox(
            "候选实验", compare_ids, format_func=lambda x: compare_options[x], index=0, key="cmp_cand"
        )
        baseline_record = next(r for r in offline_records if r["experiment_id"] == baseline_id)
        candidate_record = next(r for r in offline_records if r["experiment_id"] == candidate_id)
        if baseline_id == candidate_id:
            st.warning("基线与候选选择了同一实验，请选择不同实验。")
        else:
            comparison = compare_factory_records(baseline_record, candidate_record)
            verdict_css = {
                "候选更优": "ec-verdict-ok",
                "基线更优": "ec-verdict-warn",
                "基本持平": "ec-verdict-neutral",
                "各有优劣": "ec-verdict-neutral",
                "口径不一致（时长或种子不同）": "ec-verdict-warn",
            }.get(comparison["verdict"], "ec-verdict-neutral")
            st.markdown(
                f'<div class="{verdict_css}">结论：{comparison["verdict"]}</div>',
                unsafe_allow_html=True,
            )
            if comparison["comparable"]:
                st.caption(
                    comparison["criteria"]
                    + f" 两轮实验时长均为 {comparison['baseline_duration_min']} 分钟、"
                    f"种子均为 {comparison['baseline_seed']}，指标口径为实验窗口。"
                )
            else:
                st.warning(
                    "两轮实验的仿真时长或随机种子不同：窗口良品数不可直接比较，"
                    f"基线 {comparison['baseline_duration_min']} 分钟/种子 {comparison['baseline_seed']}，"
                    f"候选 {comparison['candidate_duration_min']} 分钟/种子 {comparison['candidate_seed']}。"
                    "请选择同时长同种子的实验以获得可比较的结论。"
                )
            rows = []
            for row in comparison["rows"]:
                is_ratio = row["metric"] in ("oee", "defect_rate")
                rows.append(
                    {
                        "指标": row["label"],
                        "基线": pct(row["baseline"]) if is_ratio else f"{row['baseline']:.2f}",
                        "候选": pct(row["candidate"]) if is_ratio else f"{row['candidate']:.2f}",
                        "变化": f"{row['delta'] * 100:+.2f} pp" if is_ratio else f"{row['delta']:+.2f}",
                        "变化率": (
                            f"{row['delta_pct']:+.1f}%"
                            if row["delta_pct"] is not None
                            else "—"
                        ),
                    }
                )
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption(
                f"基线瓶颈：{comparison['baseline']['bottleneck']}；候选瓶颈：{comparison['candidate']['bottleneck']}。"
                "结论仅适用于这两组参数与工作负载。"
            )

    st.markdown("### 查看与导出")
    options = experiment_options(records)
    ids = list(options)
    selected_id = st.selectbox("实验记录", ids, format_func=lambda x: options[x])
    record = next(r for r in records if r["experiment_id"] == selected_id)
    if record.get("kind") in ("factory", "live"):
        kpi_metrics_grid(experiment_kpis(record))
        st.caption(f"瓶颈：{(record.get('flow_insight') or {}).get('bottleneck_name', '—')} · 事件 {(record.get('event_stats') or {}).get('total', 0)} 条")
    else:
        st.dataframe(edge_metrics_frame(record), hide_index=True, width="stretch")
    st.download_button(
        "下载实验 JSON",
        data=json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"{record['experiment_id']}.json",
        mime="application/json",
    )


def page_ai_service() -> None:
    st.markdown("## AI 工业软件生成")
    st.caption("保留 EdgeCoder 的本地可信模板生成、静态检查与自动化测试能力；第一阶段不调用大模型 API。")
    left, right = st.columns([3, 2])
    with left:
        task_type = st.selectbox(
            "任务类型",
            options=list(TASK_TYPES.keys()),
            format_func=lambda key: f"{TASK_TYPES[key]}（{key}）",
            key="factory_gen_type",
        )
        requirement = st.text_area(
            "需求描述",
            value="生成一个工业产品缺陷检测服务，接收检测任务，检查输入参数，并返回检测结果。",
            height=100,
            key="factory_gen_requirement",
        )
        if st.button("生成示例服务", type="primary"):
            try:
                service = generate_service(task_type, requirement)
                st.session_state.gen_service = service
                st.session_state.gen_check = static_check(service.code)
                st.session_state.gen_test = None
            except Exception as exc:  # noqa: BLE001
                st.error(f"生成失败：{exc}")
    with right:
        st.markdown(
            '<div class="ec-note"><b>安全边界：</b>只执行仓库维护的可信模板；用户需求仅作为注释；'
            '生成代码经过 AST 静态检查；测试在临时目录的独立子进程中运行。不是强安全沙箱。</div>',
            unsafe_allow_html=True,
        )

    service = st.session_state.get("gen_service")
    if service is None:
        return
    st.divider()
    st.markdown(f"### {service.title}")
    st.caption(f"后端：{service.backend} · 模板版本 {service.template_version}")
    st.write(service.description)
    tab_in, tab_out, tab_code, tab_tests = st.tabs(["输入说明", "输出说明", "代码", "测试代码"])
    with tab_in:
        st.dataframe(
            pd.DataFrame(
                [{"字段": f.name, "类型": f.field_type, "必填": "是" if f.required else "否", "约束": f.constraint, "说明": f.description} for f in service.inputs]
            ),
            hide_index=True,
            width="stretch",
        )
        for item in service.assumptions:
            st.markdown(f"- {item}")
    with tab_out:
        st.dataframe(
            pd.DataFrame(
                [{"字段": f.name, "类型": f.field_type, "约束": f.constraint, "说明": f.description} for f in service.outputs]
            ),
            hide_index=True,
            width="stretch",
        )
    with tab_code:
        st.code(service.code, language="python", line_numbers=True)
    with tab_tests:
        st.code(service.tests_code, language="python", line_numbers=True)

    check = st.session_state.get("gen_check")
    if check:
        if check.passed:
            st.success(f"静态检查通过：{check.info.get('lines')} 行，函数 {', '.join(check.info.get('functions', []))}。")
        else:
            st.error("静态检查未通过：" + "；".join(check.errors))

    if st.button("运行自动化测试", type="primary"):
        try:
            with st.spinner("正在隔离子进程中执行 pytest ..."):
                result = run_generated_tests(service.code, service.tests_code, timeout_seconds=60)
            st.session_state.gen_test = result
        except Exception as exc:  # noqa: BLE001
            st.error(f"测试执行异常：{exc}")
    test = st.session_state.get("gen_test")
    if test:
        if test.success:
            st.success("自动化测试全部通过。")
        else:
            st.error("自动化测试存在失败或错误。")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("通过", test.passed)
        c2.metric("失败", test.failed)
        c3.metric("错误", test.errors)
        c4.metric("耗时", f"{test.duration_ms:.0f} ms")
        with st.expander("测试原始输出", expanded=not test.success):
            st.code(test.stdout or "（无输出）", language="text")


def page_system() -> None:
    st.markdown("## 系统设置与说明")
    project = current_project(required=False)

    if os.environ.get("AIC_DESKTOP") == "1":
        st.markdown("### 本地运行管理")
        st.caption(f"桌面版 v{os.environ.get('AIC_DESKTOP_VERSION', '1.0.0')} · 数据目录：{os.environ.get('AIC_DATA_DIR', '—')}（SQLite）")
        if st.button("停止本地平台服务"):
            data_value = os.environ.get("AIC_DATA_DIR", "")
            if data_value:
                data_dir = Path(data_value)
                (data_dir / "runtime").mkdir(parents=True, exist_ok=True)
                (data_dir / "runtime" / "stop.request").write_text("stop", encoding="utf-8")
                st.success("已发送停止请求；服务将在数秒内退出，可直接关闭浏览器。")
            else:
                st.error("未找到数据目录，无法停止。请使用开始菜单中的“停止”快捷方式。")

    st.markdown("### 运行环境")
    versions = {
        "Python": sys.version.split()[0],
        "Streamlit": _package_version("streamlit"),
        "pandas": _package_version("pandas"),
        "Plotly": _package_version("plotly"),
        "FastAPI": _package_version("fastapi"),
        "Pydantic": _package_version("pydantic"),
        "pytest": _package_version("pytest"),
        "httpx": _package_version("httpx"),
    }
    st.dataframe(pd.DataFrame([versions]), hide_index=True, width="stretch")
    dist_ok = (RFTWIN_FRONTEND_DIST / "index.html").exists()
    st.caption(
        f"RFTwin 目录：{RFTWIN_ROOT} · 前端构建产物：{'已构建' if dist_ok else '缺失（需 npm build）'} · "
        f"许可证：MIT（rftwin/LICENSE）"
    )

    st.markdown("### 三维服务管理")
    status = service_status(remote=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("服务状态", "运行中" if status.get("healthy") else "未启动")
    c2.metric("地址", twin_public_url(status) if status.get("running") else "—")
    c3.metric("进程 PID", status.get("pid", "—"))
    if status.get("running"):
        if st.button("停止 三维服务"):
            get_service_manager().stop()
            st.success("已停止。")
            st.rerun()
        st.caption(f"日志：{status.get('log_path', '—')}")

    st.markdown("### 数据目录")
    st.code(
        f"项目与实验：{DATA_DIR / 'projects'}\n运行时状态：{DATA_DIR / 'runtime'}\n服务日志：{DATA_DIR / 'logs'}",
        language="text",
    )

    st.markdown("### 启动命令")
    st.code(
        "cd C:\\Users\\Administrator\\Desktop\\aic\n"
        ".venv\\Scripts\\Activate.ps1   # 如使用虚拟环境\n"
        "streamlit run factory_app.py --server.port 8502",
        language="powershell",
    )
    st.caption("三维服务由页面内按钮按需启动，无需手工执行 uvicorn。原 EdgeCoder 入口仍可用：streamlit run app.py。")

    st.markdown("### 架构与职责")
    st.markdown(
        """
| 子系统 | 职责 |
| --- | --- |
| RFTwin 引擎 (`rftwin/packages/domain_factory`) | 确定性工厂仿真：工位节拍、故障、AMR、缓冲区、能耗、OEE |
| RFTwin 后端 (`rftwin/apps/factory_backend`) | REST/WebSocket、静态托管三维前端、What-if/Copilot |
| 集成层 (`integration/`) | 项目存储、参数映射、实验归一化、边缘任务映射、规则优化、服务管理 |
| EdgeCoder 核心 (`network_sim/`, `scheduler/`, `experiments/`) | 边缘节点、网络、三种调度策略与指标 |
| 统一界面 (`factory_app.py`) | 项目工作台、三维嵌入、分析、调度、AI、历史 |
"""
    )

    st.markdown("### 关键假设与局限")
    st.markdown(
        "- 工厂产能、OEE、故障、AMR 等全部为 RFTwin 仿真结果，不是真实工厂实测；\n"
        "- 三维布局固定，平台当前只支持参数级编辑，不支持自由拖拽/新增设备；\n"
        "- 工位数量固定为 RFTwin 场景的 4/4/2/2；AMR（移动机器人）数量仅支持 1~2；\n"
        "- 检测事件到计算任务的映射是平台假设，任务到达间隔、数据量、计算需求、时延上限均可调；\n"
        "- 边缘网络模型为带宽/时延/拥塞简化模型，不含丢包与无线信道；\n"
        "- 决策开销在本机实测，含 Python 解释器开销；\n"
        "- AI 优化为规则式方法，候选方案通过确定性重放验证，未接入外部大模型 API。\n"
    )

    st.markdown(
        """
<div class="ec-note">
原 EdgeCoder 的独立入口、35 项测试与中文界面保持不变；统一平台通过复用其核心模块接入，
而不是复制一套调度实现。RFTwin 代码保持原样，仅增加 TWIN_PARAMS_PATH / TWIN_SEED
两个向后兼容的环境变量钩子，用于按项目配置启动实时服务。
</div>
""",
        unsafe_allow_html=True,
    )
    if project:
        st.caption(f"当前项目：{project['name']}（{project['project_id']}）")
