"""Streamlit → 场景 API 的轻量 HTTP 客户端。"""

from __future__ import annotations

from typing import Any, Callable, Optional

import httpx

from .api_service import ensure_scene_api


class SceneClientError(RuntimeError):
    pass


class SceneClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        on_write: Optional[Callable[[], None]] = None,
    ) -> None:
        self.base_url = (base_url or ensure_scene_api()).rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False)
        self._on_write = on_write

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SceneClientError(f"场景 API 不可用：{exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise SceneClientError(f"{response.status_code}: {detail}")
        if self._on_write is not None and method.upper() not in ("GET", "HEAD", "OPTIONS"):
            try:
                self._on_write()
            except Exception:  # noqa: BLE001 - 缓存失效失败不应影响业务调用
                pass
        if response.status_code == 204:
            return None
        return response.json()

    # scenes
    def health(self) -> dict[str, Any]:
        return self._call("GET", "/api/health")

    def list_scenes(self, project_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/projects/{project_id}/scenes")

    def create_scene(self, project_id: str, name: str, *, oip_project: Optional[dict] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if oip_project is not None:
            payload["oip_project"] = oip_project
        return self._call("POST", f"/api/projects/{project_id}/scenes", json=payload)

    def get_scene(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}")

    def update_scene(self, project_id: str, scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("PUT", f"/api/projects/{project_id}/scenes/{scene_id}", json=payload)

    def delete_scene(self, project_id: str, scene_id: str) -> None:
        self._call("DELETE", f"/api/projects/{project_id}/scenes/{scene_id}")

    def import_oip(self, project_id: str, scene_id: str, oip_project: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/api/projects/{project_id}/scenes/{scene_id}/oip-import", json={"oip_project": oip_project})

    def devices(self, project_id: str, scene_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/devices")

    def connections(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/connections")

    def set_connections(self, project_id: str, scene_id: str, connections: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("PUT", f"/api/projects/{project_id}/scenes/{scene_id}/connections", json={"connections": connections})

    def derive_connections(self, project_id: str, scene_id: str, adopt: bool = False) -> dict[str, Any]:
        return self._call("POST", f"/api/projects/{project_id}/scenes/{scene_id}/derive-connections", json={"adopt": adopt})

    def validate(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return self._call("POST", f"/api/projects/{project_id}/scenes/{scene_id}/validate")

    def versions(self, project_id: str, scene_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/versions")

    def version_detail(self, project_id: str, scene_id: str, version: int) -> dict[str, Any]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/versions/{int(version)}")

    def restore_version(self, project_id: str, scene_id: str, version: int) -> dict[str, Any]:
        return self._call("POST", f"/api/projects/{project_id}/scenes/{scene_id}/versions/{int(version)}/restore")

    # simulation
    def run_simulation(self, project_id: str, scene_id: str, *, duration_sec: float, seed: int) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/api/projects/{project_id}/scenes/{scene_id}/simulations",
            json={"duration_sec": duration_sec, "seed": seed, "persist": True},
        )

    def start_live(self, project_id: str, scene_id: str, *, duration_sec: float, seed: int, speed: float) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/api/projects/{project_id}/scenes/{scene_id}/simulations/live",
            json={"duration_sec": duration_sec, "seed": seed, "speed": speed},
        )

    def sim(self, sim_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/simulations/{sim_id}")

    def sim_action(self, sim_id: str, action: str) -> dict[str, Any]:
        return self._call("POST", f"/api/simulations/{sim_id}/{action}")

    def sim_result(self, sim_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/simulations/{sim_id}/result")

    # analysis / edge / optimize / report
    def experiments(self, project_id: str, scene_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/experiments")

    def experiment(self, project_id: str, experiment_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/projects/{project_id}/experiments/{experiment_id}")

    def compare(self, project_id: str, scene_id: str, baseline: str, candidate: str) -> dict[str, Any]:
        return self._call(
            "GET",
            f"/api/projects/{project_id}/scenes/{scene_id}/compare",
            params={"baseline": baseline, "candidate": candidate},
        )

    def analysis(self, project_id: str, scene_id: str, experiment_id: Optional[str] = None) -> dict[str, Any]:
        params = {"experiment_id": experiment_id} if experiment_id else {}
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/analysis", params=params)

    def run_edge(self, project_id: str, scene_id: str, experiment_id: Optional[str] = None) -> dict[str, Any]:
        return self._call("POST", f"/api/projects/{project_id}/scenes/{scene_id}/edge", json={"experiment_id": experiment_id})

    def optimize(self, project_id: str, scene_id: str, *, experiment_id: Optional[str], duration_sec: float, max_candidates: int = 3) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/api/projects/{project_id}/scenes/{scene_id}/optimize",
            json={"experiment_id": experiment_id, "duration_sec": duration_sec, "max_candidates": max_candidates},
        )

    def apply_optimization(self, project_id: str, scene_id: str, *, patch: dict[str, Any], duration_sec: float, seed: int) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/api/projects/{project_id}/scenes/{scene_id}/optimize/apply",
            json={"patch": patch, "duration_sec": duration_sec, "seed": seed},
        )

    def what_if(self, project_id: str, scene_id: str, *, patch: dict[str, Any], experiment_id: Optional[str], duration_sec: float, seed: int) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/api/projects/{project_id}/scenes/{scene_id}/what-if",
            json={"patch": patch, "experiment_id": experiment_id, "duration_sec": duration_sec, "seed": seed},
        )

    def reports(self, project_id: str, scene_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/projects/{project_id}/scenes/{scene_id}/reports")

    def report(self, project_id: str, scene_id: str, *, experiment_id: str, edge_experiment_id: Optional[str], optimization_id: Optional[str]) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/api/projects/{project_id}/scenes/{scene_id}/reports",
            json={"experiment_id": experiment_id, "edge_experiment_id": edge_experiment_id, "optimization_id": optimization_id},
        )

    # OIP
    def oip_status(self) -> dict[str, Any]:
        return self._call("GET", "/api/oip/status")

    def oip_start(self) -> dict[str, Any]:
        return self._call("POST", "/api/oip/start")

    def oip_url(self, project_id: str, scene_id: str) -> dict[str, Any]:
        return self._call("GET", "/api/oip/url", params={"project_id": project_id, "scene_id": scene_id})
