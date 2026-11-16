"""AIC Factory Studio 只读公开接口（供 ChatGPT 网页版 / 自动化读取）。

为什么需要它：
- Streamlit 页面是前端渲染 + WebSocket 应用，外部抓取器往往只能拿到空壳；
- ChatGPT 网页版也无法访问 localhost。
因此这里提供只读的 JSON 接口和**服务端渲染的 HTML 报告**，可经隧道暴露后直接抓取。

安全边界：
- 只读，不提供任何修改/运行仿真的接口；
- 默认只监听 127.0.0.1；设置 PUBLIC_API_TOKEN 后，/api/* 与 /report 需要令牌；
- 不要把 Streamlit（8502）直接暴露到公网；本服务也不接触密钥或真实工厂数据。
"""

from __future__ import annotations

import hmac
import html
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from fastapi.openapi.utils import get_openapi

from integration.analysis import (
    CELL_LABELS,
    analyze_record,
    experiment_kpis,
    oee_breakdown,
    strategy_ranking,
    target_attainment,
)
from integration.factory_service import get_service_manager
from integration.project_store import (
    ProjectError,
    list_experiments,
    list_projects,
    load_experiment,
    load_project,
)

app = FastAPI(
    title="AIC Factory Studio 只读接口",
    version="1.0.0",
    openapi_version="3.0.3",
    description=(
        "工业数字工厂平台的只读数据接口：项目、实验、KPI、瓶颈与调度对比。"
        "所有指标均为仿真结果，不是真实工厂实测。"
    ),
)

_bearer = HTTPBearer(auto_error=False)


def _configured_token() -> str:
    return os.environ.get("PUBLIC_API_TOKEN", "").strip()


def require_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    token: Optional[str] = Query(default=None, description="未使用 Bearer 头时的令牌"),
) -> None:
    expected = _configured_token()
    if not expected:
        return
    supplied = credentials.credentials if credentials else token
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="无效或缺失的访问令牌")


@app.get("/health", summary="健康检查")
def health() -> dict[str, Any]:
    projects = list_projects()
    return {
        "status": "ok",
        "service": "aic-factory-studio-public-api",
        "token_required": bool(_configured_token()),
        "projects": len(projects),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "全部指标为仿真结果，不是真实工厂实测。",
    }


def _bottleneck_label(insight: dict[str, Any]) -> Optional[str]:
    bottleneck_id = insight.get("bottleneck")
    if bottleneck_id and bottleneck_id in CELL_LABELS:
        return CELL_LABELS[bottleneck_id]
    return insight.get("bottleneck_name") or bottleneck_id


def _experiment_summary(record: dict[str, Any]) -> dict[str, Any]:
    kind = record.get("kind")
    summary: dict[str, Any] = {
        "experiment_id": record.get("experiment_id"),
        "project_id": record.get("project_id"),
        "kind": kind,
        "label": record.get("label"),
        "created_at": record.get("created_at"),
        "source": record.get("source"),
        "seed": (record.get("config") or {}).get("seed"),
    }
    if kind in ("factory", "live"):
        kpis = experiment_kpis(record)
        insight = record.get("flow_insight") or {}
        summary.update(
            {
                "duration_min": (record.get("config") or {}).get("duration_min"),
                "total_output": kpis.get("total_output"),
                "good_units": kpis.get("good_units"),
                "wip": kpis.get("wip"),
                "oee": kpis.get("oee"),
                "bottleneck": _bottleneck_label(insight),
                "window_metrics": record.get("window_metrics"),
            }
        )
        if kind == "live":
            summary["live_run_id"] = record.get("live_run_id")
            summary["live_seq"] = record.get("live_seq")
    elif kind == "edge":
        summary.update(
            {
                "factory_experiment_id": record.get("factory_experiment_id"),
                "task_count": len(record.get("tasks") or []),
                "metrics": {
                    key: {
                        "strategy_name": value.get("strategy_name"),
                        "satisfaction_rate": value.get("satisfaction_rate"),
                        "avg_latency_ms": value.get("avg_latency_ms"),
                        "timeout_tasks": value.get("timeout_tasks"),
                        "rejected_tasks": value.get("rejected_tasks"),
                    }
                    for key, value in (record.get("metrics") or {}).items()
                },
            }
        )
    return summary


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    project_id = project["project_id"]
    records = list_experiments(project_id)
    latest_factory = next((r for r in records if r.get("kind") in ("factory", "live")), None)
    latest_edge = next((r for r in records if r.get("kind") == "edge"), None)
    summary: dict[str, Any] = {
        "project_id": project_id,
        "name": project.get("name"),
        "description": project.get("description"),
        "goal": project.get("goal"),
        "updated_at": project.get("updated_at"),
        "target_quantity": (project.get("line") or {}).get("target_quantity"),
        "experiment_counts": {
            "factory": sum(1 for r in records if r.get("kind") == "factory"),
            "live": sum(1 for r in records if r.get("kind") == "live"),
            "edge": sum(1 for r in records if r.get("kind") == "edge"),
        },
        "latest_factory": _experiment_summary(latest_factory) if latest_factory else None,
        "latest_edge": _experiment_summary(latest_edge) if latest_edge else None,
    }
    if latest_factory:
        analysis = analyze_record(latest_factory)
        summary["latest_factory"].update(
            {
                "oee_breakdown": oee_breakdown(latest_factory),
                "target_attainment": target_attainment(latest_factory, project),
                "bottleneck_evidence": analysis.get("evidence", []),
                "utilization": analysis.get("utilization", {}),
            }
        )
    if latest_edge:
        summary["latest_edge"]["ranking"] = strategy_ranking(latest_edge)
    return summary


@app.get(
    "/api/projects",
    summary="列出工程项目",
    dependencies=[Depends(require_token)],
)
def projects() -> list[dict[str, Any]]:
    return [_project_summary(project) for project in list_projects()]


@app.get(
    "/api/projects/{project_id}",
    summary="项目摘要（含最新工厂/边缘实验）",
    dependencies=[Depends(require_token)],
)
def project_detail(project_id: str) -> dict[str, Any]:
    try:
        project = load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _project_summary(project)


@app.get(
    "/api/projects/{project_id}/experiments",
    summary="实验列表（简要）",
    dependencies=[Depends(require_token)],
)
def experiments(
    project_id: str,
    kind: Optional[str] = Query(default=None, pattern="^(factory|live|edge)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    try:
        load_project(project_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    records = list_experiments(project_id, kind=kind)
    return [_experiment_summary(record) for record in records[:limit]]


@app.get(
    "/api/projects/{project_id}/experiments/{experiment_id}",
    summary="单条实验完整记录",
    dependencies=[Depends(require_token)],
)
def experiment_detail(project_id: str, experiment_id: str) -> dict[str, Any]:
    try:
        load_project(project_id)
        return load_experiment(project_id, experiment_id)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/live",
    summary="当前三维实时仿真的 KPI 摘要",
    dependencies=[Depends(require_token)],
)
def live() -> dict[str, Any]:
    try:
        status = get_service_manager().status()
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "error": str(exc)}
    if not status.get("healthy"):
        return {"running": False, "note": "三维实时服务未运行；可在工作台「三维仿真」页启动。"}
    from integration.factory_client import RFTwinClient

    client = RFTwinClient(status["url"], timeout=5.0)
    try:
        health = client.health()
        kpis = client.kpis()
        insight = client.flow_insight()
    except Exception as exc:  # noqa: BLE001
        return {"running": True, "healthy": False, "error": str(exc)}
    finally:
        client.close()
    return {
        "running": True,
        "healthy": True,
        "run_id": health.get("run_id"),
        "sim_time": health.get("sim_time"),
        "seq": health.get("seq"),
        "total_output": kpis.get("total_output"),
        "good_units": kpis.get("good_units"),
        "oee": (kpis.get("oee") or {}).get("oee"),
        "bottleneck": _bottleneck_label(insight),
        "note": "实时数值与三维画面为同一轮仿真；不是真实工厂实测。",
    }


@app.get("/report", response_class=HTMLResponse, summary="服务端渲染的只读 HTML 报告")
def report(
    project_id: Optional[str] = Query(default=None),
    _: None = Depends(require_token),
) -> str:
    projects_list = list_projects()
    if not projects_list:
        return _html_page("暂无工程项目", "<p>还没有工程项目。请先在工作台创建项目并运行仿真。</p>")
    if project_id:
        try:
            project = load_project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        project = projects_list[0]
    summary = _project_summary(project)
    return _report_html(summary)


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:'Microsoft YaHei',system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 18px;color:#1c3348;background:#f7fafc}}
h1{{font-size:24px}} h2{{font-size:18px;margin-top:28px;border-left:4px solid #2a7f8f;padding-left:10px}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}}
th,td{{border:1px solid #dce5ec;padding:7px 9px;text-align:left}} th{{background:#edf3f7}}
.note{{background:#f3f8fb;border:1px solid #d9e5ec;border-radius:8px;padding:10px 14px;font-size:13px;color:#35566d}}
.muted{{color:#6a7f92;font-size:12px}}
</style></head><body>{body}</body></html>"""


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _report_html(summary: dict[str, Any]) -> str:
    rows = []
    factory = summary.get("latest_factory")
    edge = summary.get("latest_edge")
    if factory:
        breakdown = factory.get("oee_breakdown") or {}
        losses = breakdown.get("losses") or {}
        attainment = factory.get("target_attainment") or {}
        rows.append(("实验", factory.get("experiment_id")))
        rows.append(("类型", "工厂离线实验" if factory.get("kind") == "factory" else "三维实时采集"))
        rows.append(("累计产量（件）", factory.get("total_output")))
        rows.append(("良品数", factory.get("good_units")))
        rows.append(("在制品 WIP", factory.get("wip")))
        rows.append(("OEE", _fmt_pct(factory.get("oee"))))
        rows.append(("可用率", _fmt_pct(breakdown.get("availability"))))
        rows.append(("性能", _fmt_pct(breakdown.get("performance"))))
        rows.append(("质量", _fmt_pct(breakdown.get("quality"))))
        if losses:
            rows.append(
                (
                    "相对损失",
                    "；".join(f"{name} {_fmt_pct(value)}" for name, value in losses.items()),
                )
            )
        if attainment.get("attainment") is not None:
            rows.append(
                (
                    "班次目标达成率",
                    f"{_fmt_pct(attainment.get('attainment'))}"
                    f"（良品 {attainment.get('good_units')} / 目标 {attainment.get('target_quantity')}）",
                )
            )
        rows.append(("当前瓶颈", factory.get("bottleneck")))
        for item in (factory.get("bottleneck_evidence") or [])[:4]:
            rows.append(("证据", item))

    table_html = "".join(
        f"<tr><th>{html.escape(str(label))}</th><td>{html.escape(str(value))}</td></tr>"
        for label, value in rows
    )

    edge_rows = ""
    if edge:
        edge_rows = "".join(
            f"<tr><td>{html.escape(str(item.get('strategy_name') or item.get('strategy_key') or ''))}</td>"
            f"<td>{_fmt_pct(item.get('satisfaction_rate'))}</td>"
            f"<td>{'—' if item.get('avg_latency_ms') is None else round(float(item['avg_latency_ms']), 1)}</td>"
            f"<td>{html.escape(str(item.get('total_cost')))}</td></tr>"
            for item in (edge.get("ranking") or [])
        )
    edge_html = (
        f"<h2>边缘调度实验</h2><p>实验 {html.escape(str(edge.get('experiment_id')))}，"
        f"任务 {edge.get('task_count')} 个（来源工厂实验 {html.escape(str(edge.get('factory_experiment_id')))}）。</p>"
        "<table><tr><th>策略</th><th>时延满足率</th><th>平均时延(ms)</th><th>仿真成本</th></tr>"
        f"{edge_rows}</table>"
        if edge
        else "<h2>边缘调度实验</h2><p>暂无调度实验记录。</p>"
    )

    recent = summary.get("latest_factory")
    counts = summary.get("experiment_counts") or {}
    body = f"""
<h1>{html.escape(str(summary.get('name')))} · 仿真摘要</h1>
<p class="muted">项目 {html.escape(str(summary.get('project_id')))} · 目标 {summary.get('target_quantity')} 件 ·
实验记录：工厂 {counts.get('factory', 0)} / 实时 {counts.get('live', 0)} / 边缘 {counts.get('edge', 0)} ·
生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p>{html.escape(str(summary.get('description') or ''))}</p>
<h2>最新工厂实验 / 实时采集</h2>
{("<table>" + table_html + "</table>") if rows else "<p>暂无工厂实验记录。</p>"}
{edge_html}
<div class="note">所有数值均由 RFTwin 确定性引擎和 EdgeCoder 调度仿真器计算，属于<strong>仿真结果</strong>，
不是真实工厂或 6G 网络实测。{("本页对应最新实验 " + html.escape(str(recent.get('experiment_id'))) + "。") if recent else ""}</div>
"""
    return _html_page(f"{summary.get('name')} · 仿真摘要", body)


def _custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["openapi"] = "3.0.3"
    base_url = os.environ.get("PUBLIC_API_BASE_URL", "").strip().rstrip("/")
    if base_url:
        schema["servers"] = [{"url": base_url, "description": "公开隧道地址"}]
    if _configured_token():
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
        }
        for path, methods in schema.get("paths", {}).items():
            if path == "/health":
                continue
            for method in methods.values():
                method.setdefault("security", [{"BearerAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]
