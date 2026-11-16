"""场景仿真报告：JSON + Markdown + 可独立打开的 HTML。"""

from __future__ import annotations

import html
import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from integration.analysis import strategy_ranking

from .models import SceneDocument
from .store import scene_dir


def _fmt(value: Any, digits: int = 2, pct: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number * 100:.{digits}f}%" if pct else f"{number:.{digits}f}"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def generate_scene_report(
    project: dict[str, Any],
    scene: SceneDocument,
    record: dict[str, Any],
    analysis: dict[str, Any],
    *,
    edge_record: Optional[dict[str, Any]] = None,
    optimization: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    report_id = f"RPT-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}"
    kpis = analysis.get("kpis") or record.get("kpis") or {}
    oee = record.get("kpis", {}).get("oee", {}) or {}
    device_rows = []
    for d in (record.get("device_metrics") or []):
        device_rows.append(
            [
                d.get("device_id"),
                d.get("name"),
                d.get("simulation_type"),
                d.get("state"),
                _fmt(d.get("utilization"), 1, True),
                d.get("queue"),
                d.get("processed"),
                _fmt(d.get("energy_kwh"), 3),
            ]
        )
    conn_rows = [
        [c.id, c.source_device, c.source_port, c.target_device, c.target_port, c.connection_type, c.metadata.get("source", "manual")]
        for c in scene.connections
    ]
    edge_rows = []
    if edge_record:
        for row in strategy_ranking(edge_record):
            edge_rows.append(
                [
                    row["strategy_name"],
                    _fmt(row["satisfaction_rate"], 1, True),
                    _fmt(row["avg_latency_ms"], 1),
                    _fmt(row["success_rate"], 1, True),
                    _fmt(row["total_cost"], 3),
                ]
            )
    opt_rows = []
    if optimization:
        for item in optimization.get("evaluations") or []:
            if item.get("status") != "ok":
                continue
            pct = item.get("delta_pct") or {}
            opt_rows.append(
                [
                    item.get("title") or item.get("recipe_id"),
                    _fmt(pct.get("throughput_uph"), 2) + "%",
                    _fmt(pct.get("oee"), 2) + "%",
                    _fmt(pct.get("avg_lead_time_sec"), 2) + "%",
                    _fmt(pct.get("wip"), 2) + "%",
                    item.get("score"),
                ]
            )

    md_parts = [
        f"# 场景仿真报告 · {scene.name}",
        "",
        f"- 项目：{project.get('name')}（{scene.project_id}）",
        f"- 场景：{scene.scene_id} · 版本 v{scene.version}",
        f"- 实验：{record.get('experiment_id')} · 引擎 `{record.get('factory_engine')}` · 种子 {record.get('config', {}).get('seed')}",
        f"- 仿真时长：{record.get('duration_min')} 分钟",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "> 全部指标由 Python 离散事件仿真计算，属于软件仿真结果，不是真实工厂或 6G 网络实测。",
        "",
        "## 关键指标",
        "",
        _md_table(
            ["指标", "数值"],
            [
                ["良品数", kpis.get("good_units")],
                ["不良数", kpis.get("defect_units")],
                ["总产出", kpis.get("total_output")],
                ["WIP", kpis.get("wip")],
                ["平均产能（件/小时）", _fmt(kpis.get("throughput_uph"), 2)],
                ["OEE", _fmt(oee.get("oee"), 2, True)],
                ["可用率", _fmt(oee.get("availability"), 2, True)],
                ["性能", _fmt(oee.get("performance"), 2, True)],
                ["质量", _fmt(oee.get("quality"), 2, True)],
                ["平均生产周期（秒）", _fmt(kpis.get("avg_lead_time_sec"), 2)],
                ["能耗（kWh）", _fmt(kpis.get("energy_kwh_total"), 4)],
                ["瓶颈", analysis.get("bottleneck_name") or analysis.get("bottleneck")],
            ],
        ),
        "",
        "## 设备状态与利用率",
        "",
        _md_table(["设备", "名称", "类型", "末态", "利用率", "队列", "处理数", "能耗 kWh"], device_rows),
        "",
        "## 显式物料连接",
        "",
        _md_table(["连接", "源", "源端口", "目标", "目标端口", "类型", "来源"], conn_rows),
        "",
        "## 瓶颈证据",
        "",
        "\n".join(f"- {item}" for item in (analysis.get("evidence") or [])),
    ]
    if edge_rows:
        md_parts += [
            "",
            "## EdgeCoder 调度实验结果",
            "",
            f"任务数：{edge_record.get('mapping', {}).get('task_count')}；任务类型：{edge_record.get('mapping', {}).get('task_type_counts')}",
            "",
            _md_table(["策略", "时延满足率", "平均时延 ms", "完成率", "仿真成本"], edge_rows),
        ]
    if opt_rows:
        md_parts += [
            "",
            "## 规则式优化候选（真实重跑对比）",
            "",
            _md_table(["建议", "产能变化", "OEE 变化", "周期变化", "WIP 变化", "评分"], opt_rows),
        ]
    md_parts += ["", "## 假设与边界", ""] + [f"- {a}" for a in (record.get("assumptions") or [])]
    markdown = "\n".join(md_parts) + "\n"

    rows_html = lambda pairs: "".join(f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>" for k, v in pairs)
    html_body = f"""
<h1>场景仿真报告 · {html.escape(scene.name)}</h1>
<p class="muted">项目 {html.escape(str(project.get('name')))}（{html.escape(scene.project_id)}） · 场景 {html.escape(scene.scene_id)} v{scene.version} ·
实验 {html.escape(str(record.get('experiment_id')))} · 引擎 {html.escape(str(record.get('factory_engine')))} · 时长 {record.get('duration_min')} 分钟</p>
<div class="note">全部指标由 Python 离散事件仿真计算，属于软件仿真结果，不是真实工厂或 6G 网络实测。</div>
<h2>关键指标</h2><table>{rows_html([
    ("良品数", kpis.get("good_units")), ("不良数", kpis.get("defect_units")), ("总产出", kpis.get("total_output")),
    ("WIP", kpis.get("wip")), ("平均产能（件/小时）", _fmt(kpis.get("throughput_uph"), 2)),
    ("OEE", _fmt(oee.get("oee"), 2, True)), ("可用率", _fmt(oee.get("availability"), 2, True)),
    ("性能", _fmt(oee.get("performance"), 2, True)), ("质量", _fmt(oee.get("quality"), 2, True)),
    ("平均生产周期（秒）", _fmt(kpis.get("avg_lead_time_sec"), 2)), ("能耗（kWh）", _fmt(kpis.get("energy_kwh_total"), 4)),
    ("瓶颈", analysis.get("bottleneck_name") or analysis.get("bottleneck")),
])}</table>
<h2>设备状态与利用率</h2>{_html_table(["设备", "名称", "类型", "末态", "利用率", "队列", "处理数", "能耗 kWh"], device_rows)}
<h2>显式物料连接</h2>{_html_table(["连接", "源", "源端口", "目标", "目标端口", "类型", "来源"], conn_rows)}
<h2>瓶颈证据</h2><ul>{''.join(f'<li>{html.escape(str(x))}</li>' for x in (analysis.get('evidence') or []))}</ul>
{('<h2>EdgeCoder 调度实验</h2><p>任务数 ' + str(edge_record.get('mapping', {}).get('task_count')) + '，任务类型 ' + html.escape(str(edge_record.get('mapping', {}).get('task_type_counts'))) + '</p>' + _html_table(["策略", "时延满足率", "平均时延 ms", "完成率", "仿真成本"], edge_rows)) if edge_rows else ''}
{('<h2>规则式优化候选（真实重跑）</h2>' + _html_table(["建议", "产能变化", "能耗", "周期变化", "WIP 变化", "评分"], opt_rows)) if opt_rows else ''}
<h2>假设与边界</h2><ul>{''.join(f'<li>{html.escape(str(x))}</li>' for x in (record.get('assumptions') or []))}</ul>
"""
    html_document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>场景仿真报告</title>
<style>
body{{font-family:'Microsoft YaHei',system-ui,sans-serif;max-width:1100px;margin:28px auto;padding:0 20px;color:#1c3348;background:#f7fafc;line-height:1.6}}
h1{{font-size:25px}} h2{{font-size:18px;margin-top:28px;border-left:4px solid #2a7f8f;padding-left:10px}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}} th,td{{border:1px solid #dce5ec;padding:7px 9px;text-align:left}}
th{{background:#edf3f7}} .muted{{color:#6a7f92;font-size:12px}} .note{{background:#f3f8fb;border:1px solid #d9e5ec;border-radius:8px;padding:10px 14px;font-size:13px;color:#35566d}}
</style></head><body>{html_body}</body></html>"""

    directory = scene_dir(scene.project_id, scene.scene_id) / "reports"
    json_path = directory / f"{report_id}.json"
    md_path = directory / f"{report_id}.md"
    html_path = directory / f"{report_id}.html"
    payload = {
        "report_id": report_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_id": scene.project_id,
        "project_name": project.get("name"),
        "scene_id": scene.scene_id,
        "scene_version": scene.version,
        "experiment_id": record.get("experiment_id"),
        "edge_experiment_id": (edge_record or {}).get("experiment_id"),
        "optimization_id": (optimization or {}).get("optimization_id"),
        "kpis": kpis,
        "analysis": analysis,
        "device_metrics": record.get("device_metrics"),
        "connections": [c.to_dict() for c in scene.connections],
        "assumptions": record.get("assumptions"),
    }
    _write_text_atomic(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_text_atomic(md_path, markdown)
    _write_text_atomic(html_path, html_document)
    return {
        "report_id": report_id,
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "markdown": markdown,
        "html": html_document,
    }
