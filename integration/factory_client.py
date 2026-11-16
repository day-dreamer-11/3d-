"""RFTwin 三维 Live 服务的 REST 客户端与实时数据采集。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from integration.factory_adapter import normalize_event, station_metrics_from_snapshot
from integration.project_store import new_experiment_id, save_experiment


class RFTwinClientError(RuntimeError):
    """访问 RFTwin 服务失败。"""


class RFTwinClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # 复用连接池：Windows 上每次新建 httpx.Client 约有 1 秒连接建立开销。
        # trust_env=False：本地 127.0.0.1 请求不经过系统代理。
        self._client = httpx.Client(timeout=timeout, trust_env=False)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        request_timeout = kwargs.pop("timeout", None)
        try:
            if request_timeout is None:
                response = self._client.request(method, url, **kwargs)
            else:
                response = self._client.request(method, url, timeout=request_timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise RFTwinClientError(f"RFTwin 服务不可达：{exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            raise RFTwinClientError(f"RFTwin 返回 {response.status_code}：{detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise RFTwinClientError("RFTwin 返回了非 JSON 响应") from exc

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health", timeout=3.0 if self.timeout > 3 else self.timeout)

    def runs(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/runs")

    def snapshot(self, run_id: str = "LIVE-001") -> dict[str, Any]:
        return self._request("GET", f"/api/runs/{run_id}/snapshot")

    def events(self, run_id: str, after_seq: int = 0, limit: int = 2000) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/runs/{run_id}/events",
            params={"after_seq": after_seq, "limit": limit},
        )

    def kpis(self) -> dict[str, Any]:
        return self._request("GET", "/api/kpis")

    def flow_insight(self) -> dict[str, Any]:
        return self._request("GET", "/api/flow/insight")

    def flow_history(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/flow/history")

    def amr(self) -> dict[str, Any]:
        return self._request("GET", "/api/amr")

    def inspections(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._request("GET", "/api/inspection/recent", params={"limit": limit})

    def control(self) -> dict[str, Any]:
        return self._request("GET", "/api/simulation/control")

    def pause(self) -> dict[str, Any]:
        return self._request("POST", "/api/simulation/pause")

    def start(self) -> dict[str, Any]:
        return self._request("POST", "/api/simulation/start")

    def set_speed(self, value: float) -> dict[str, Any]:
        return self._request("POST", "/api/simulation/speed", params={"value": value})

    def reset(self) -> dict[str, Any]:
        return self._request("POST", "/api/simulation/reset", timeout=120.0)

    def inject_failure(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/failures/inject", json=payload)

    def maintenance(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/maintenance")

    def audit(self, limit: int = 40) -> list[dict[str, Any]]:
        return self._request("GET", "/api/audit", params={"limit": limit})


def capture_live_record(
    project: dict[str, Any],
    client: RFTwinClient,
    experiment_id: Optional[str] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """采集当前三维实时服务的状态与指标，保存为可追溯的实时实验快照。

    记录中的 live_run_id / live_seq / sim_time 明确对应三维画面正在显示的那一轮仿真，
    不是另起引擎的重放。
    """
    experiment_id = experiment_id or new_experiment_id("LEXP")
    health = client.health()
    run_id = health["run_id"]
    snapshot = client.snapshot(run_id)
    kpis = client.kpis()
    insight = client.flow_insight()
    history = client.flow_history()
    amr_kpis = client.amr()
    inspections = client.inspections(limit=50)
    provenance_list = client.runs()
    provenance = next((p for p in provenance_list if p.get("run_id") == run_id), {})
    after_seq = max(0, int(snapshot.get("seq", 0)) - 2000)
    event_payload = client.events(run_id, after_seq=after_seq, limit=2000)
    events = [
        normalize_event(ev, project, experiment_id)
        for ev in event_payload.get("events", [])
    ]
    stations, cells = station_metrics_from_snapshot(snapshot)

    window_history = history[-30:] if history else []
    # RFTwin 的实时 /api/flow/history 是每分钟 cell_uph/util/buf 快照，
    # 不含逐分钟良品数；不能把缺失字段当成 0 产量。
    has_output_fields = any("good_units" in row for row in window_history)
    line_rates = [
        min(float(v) for v in row["cell_uph"].values())
        for row in window_history
        if row.get("cell_uph")
    ]
    if has_output_fields:
        window_metrics = {
            "minutes": len(window_history),
            "good_units": sum(int(h.get("good_units", 0)) for h in window_history),
            "defect_units": sum(int(h.get("defect_units", 0)) for h in window_history),
            "avg_throughput_uph": (
                round(sum(float(h.get("throughput_uph", 0.0)) for h in window_history) / len(window_history), 2)
                if window_history
                else 0.0
            ),
            "avg_oee": (
                round(sum(float(h.get("oee", 0.0)) for h in window_history) / len(window_history), 4)
                if window_history
                else 0.0
            ),
            "basis": "per_minute_output",
            "note": "逐分钟良品/不良数来自引擎分钟快照。",
        }
    else:
        window_metrics = {
            "minutes": len(window_history),
            "good_units": None,
            "defect_units": None,
            "avg_throughput_uph": round(sum(line_rates) / len(line_rates), 2) if line_rates else None,
            "avg_oee": (kpis.get("oee") or {}).get("oee"),
            "basis": "live_cell_uph",
            "oee_basis": "cumulative",
            "note": (
                "RFTwin 实时服务分钟快照只提供各单元 cell_uph/util/buf，不含逐分钟良品数；"
                "此处不推算窗口良品数，产量请看累计指标。平均产能取各单元 cell_uph 的最小值。"
            ),
        }
    record = {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "project_id": project["project_id"],
        "kind": "live",
        "source": "rftwin_live_capture",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "label": f"实时采集 {experiment_id}",
        "live_run_id": run_id,
        "live_seq": snapshot.get("seq"),
        "sim_time": snapshot.get("sim_time"),
        "config": {
            "factory": project["factory"],
            "line": project.get("line", {}),
            "seed": project["factory"].get("seed"),
            "duration_min": None,
        },
        "provenance": provenance,
        "kpis": kpis,
        "window_metrics": window_metrics,
        "flow_history": history,
        "flow_insight": insight,
        "station_metrics": stations,
        "cell_metrics": cells,
        "orders": snapshot.get("state", {}).get("orders", []),
        "amr_kpis": amr_kpis,
        "recent_inspections": inspections,
        "events": events,
        "event_stats": {
            "total": len(events),
            "failures": sum(1 for e in events if e.get("failure_event")),
            "inspection_events": sum(
                1 for e in events if e.get("event_type") in ("INSPECTION_PASS", "INSPECTION_FAIL")
            ),
            "blocked_events": sum(1 for e in events if "BLOCKED" in str(e.get("event_type", ""))),
            "starved_events": sum(1 for e in events if "STARVED" in str(e.get("event_type", ""))),
        },
        "snapshot": snapshot,
        "assumptions": [
            "本记录采集自 RFTwin 三维实时服务的当前运行状态，与画面为同一轮仿真。",
            "事件窗口为最近 2000 个事件序号的子集，受 RFTwin 事件保留窗口限制。",
            "全部数值为仿真结果，不是真实工厂实测数据。",
        ],
        "wall_time_ms": 0.0,
    }
    if persist:
        save_experiment(project["project_id"], record)
    return record
