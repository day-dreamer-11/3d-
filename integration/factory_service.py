"""RFTwin 三维 Live 服务的进程管理。

- 由统一平台按项目配置启动一个本地 RFTwin FastAPI 服务（静态托管已构建的 React 前端）；
- 通过 TWIN_PARAMS_PATH / TWIN_SEED 把项目参数注入 RFTwin 引擎（向后兼容钩子）；
- 服务信息写入 data/runtime/service.json，页面刷新后可重新附着；
- 只监听 127.0.0.1，不主动对公网开放。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from integration.factory_adapter import build_factory_params
from integration.paths import (
    LOGS_DIR,
    RFTWIN_FRONTEND_DIST,
    RFTWIN_ROOT,
    RUNTIME_DIR,
    ensure_data_dirs,
)
from integration.project_store import write_project_params_yaml

SERVICE_STATE_PATH = RUNTIME_DIR / "service.json"


class FactoryServiceError(RuntimeError):
    """三维仿真服务启动或停止失败。"""


# 复用健康检查连接，避免每次 status() 新建 httpx.Client（Windows 上约 1 秒开销）。
_HEALTH_HTTP = httpx.Client(timeout=3.0, trust_env=False)


def project_factory_signature(project: dict[str, Any]) -> str:
    """只对影响 3D 仿真的工厂/产线配置做指纹，用于判断是否需要重启服务。"""
    payload = {
        "factory": project.get("factory", {}),
        "line": project.get("line", {}),
        "project_id": project.get("project_id"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(start: int = 8020, attempts: int = 60) -> int:
    # 容器部署时 nginx 固定把 /twin/ 代理到 TWIN_INTERNAL_PORT，因此必须使用固定端口。
    fixed = os.environ.get("TWIN_INTERNAL_PORT", "").strip()
    if fixed:
        port = int(fixed)
        if _port_available(port):
            return port
        raise FactoryServiceError(f"TWIN_INTERNAL_PORT={port} 已被占用，无法启动三维服务")
    for port in range(start, start + attempts):
        if _port_available(port):
            return port
    raise FactoryServiceError("没有可用的本地端口来启动三维仿真服务")


class FactoryServiceManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None
        self._info: Optional[dict[str, Any]] = None
        self._load_runtime_state()

    def _load_runtime_state(self) -> None:
        try:
            if SERVICE_STATE_PATH.exists():
                with open(SERVICE_STATE_PATH, encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict) and data.get("url"):
                    data["attached"] = True    # 页面进程重启后附着到已有服务
                    self._info = data
        except (OSError, json.JSONDecodeError):
            self._info = None

    def _write_runtime_state(self) -> None:
        ensure_data_dirs()
        if self._info is None:
            try:
                SERVICE_STATE_PATH.unlink()
            except FileNotFoundError:
                pass
            return
        with open(SERVICE_STATE_PATH, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(self._info, handle, ensure_ascii=False, indent=2)

    def _health(self, url: str, timeout: float = 2.0) -> Optional[dict[str, Any]]:
        try:
            response = _HEALTH_HTTP.get(f"{url}/api/health", timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    return data
        except (httpx.HTTPError, ValueError):
            return None
        return None

    @property
    def info(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._info) if self._info else None

    def status(self, check_remote: bool = True) -> dict[str, Any]:
        """返回服务状态：running/healthy/url/project_id 等。"""
        with self._lock:
            info = self._info
            if info is None:
                return {"running": False, "healthy": False}
            running = bool(self._proc and self._proc.poll() is None) or bool(
                info.get("attached")
            )
            healthy = False
            health = None
            if check_remote:
                health = self._health(info["url"], timeout=2.0)
                healthy = health is not None
            if self._proc is not None and self._proc.poll() is not None and not healthy:
                running = False
            if not running and not healthy:
                return {"running": False, "healthy": False, **(info or {})}
            result = {
                "running": running,
                "healthy": healthy,
                "pid": self._proc.pid if self._proc else info.get("pid"),
                **(info or {}),
            }
            if health:
                result["health"] = health
            return result

    def _wait_until_healthy(
        self, url: str, proc: subprocess.Popen, timeout_s: float = 120.0
    ) -> dict[str, Any]:
        started = time.monotonic()
        last_error = "等待服务响应超时"
        while time.monotonic() - started < timeout_s:
            if proc.poll() is not None:
                break
            health = self._health(url, timeout=2.0)
            if health is not None:
                return health
            time.sleep(0.8)
        log_tail = ""
        log_path = getattr(self, "_log_path", None)
        if log_path and Path(log_path).exists():
            try:
                log_tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-3000:]
            except OSError:
                log_tail = ""
        if proc.poll() is not None:
            raise FactoryServiceError(
                f"三维服务进程提前退出（返回码 {proc.returncode}）。日志尾部：\n{log_tail or '（无日志）'}"
            )
        raise FactoryServiceError(f"三维服务启动超时：{last_error}\n日志尾部：\n{log_tail or '（无日志）'}")

    def start(self, project: dict[str, Any], timeout_s: float = 120.0) -> dict[str, Any]:
        """按项目配置启动 3D Live 服务（会先停止已有实例）。"""
        ensure_data_dirs()
        index_html = RFTWIN_FRONTEND_DIST / "index.html"
        if not index_html.exists():
            raise FactoryServiceError(
                "未找到 RFTwin 前端构建产物（rftwin/frontend/dist）。"
                "请先在 rftwin 目录执行：npm --prefix frontend ci 与 npm --prefix frontend run build"
            )
        with self._lock:
            self._stop_locked()
            params = build_factory_params(project)
            params_path = write_project_params_yaml(project, params)
            port = find_free_port()
            url = f"http://127.0.0.1:{port}"
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path = LOGS_DIR / f"rftwin-{project['project_id']}-{timestamp}.log"
            env = os.environ.copy()
            env["TWIN_PARAMS_PATH"] = str(params_path)
            env["TWIN_SEED"] = str(int(project["factory"]["seed"]))
            env["TWIN_RATE_LIMIT"] = "0"
            env["TWIN_HEALTH_STALL_S"] = "30"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            env.pop("TWIN_CORS_ORIGINS", None)
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.factory_backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--ws",
                "wsproto",
                "--log-level",
                "warning",
            ]
            self._log_handle = open(log_path, "a", encoding="utf-8", newline="\n")
            self._log_path = log_path
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=str(RFTWIN_ROOT),
                    env=env,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                if self._log_handle:
                    self._log_handle.close()
                raise FactoryServiceError(f"无法启动三维服务：{exc}") from exc

            self._proc = proc
            info = {
                "url": url,
                "port": port,
                "pid": proc.pid,
                "project_id": project["project_id"],
                "project_name": project.get("name"),
                "factory_signature": project_factory_signature(project),
                "params_path": str(params_path),
                "log_path": str(log_path),
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "seed": int(project["factory"]["seed"]),
                "attached": False,
            }
            self._info = info
            try:
                health = self._wait_until_healthy(url, proc, timeout_s=timeout_s)
            except Exception:
                self._stop_locked()
                raise
            info["run_id"] = health.get("run_id")
            info["schema_version"] = health.get("schema_version")
            info["engine_version"] = health.get("engine_version")
            info["parameter_set_id"] = health.get("parameter_set_id")
            self._info = info
            self._write_runtime_state()
            return dict(info)

    def _stop_locked(self) -> None:
        info = self._info
        proc = self._proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
            finally:
                self._proc = None
                if self._log_handle is not None:
                    try:
                        self._log_handle.close()
                    except OSError:
                        pass
                    self._log_handle = None
        elif info and info.get("pid"):
            # 页面进程重启后重新附着到旧服务：仅在健康检查确认是本服务时终止
            if self._health(info.get("url", ""), timeout=2.0) is not None:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(info["pid"]), "/F"],
                            capture_output=True,
                            timeout=10,
                        )
                    else:
                        os.kill(int(info["pid"]), 15)
                except (OSError, subprocess.SubprocessError):
                    pass
        self._info = None
        self._write_runtime_state()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def restart_if_needed(self, project: dict[str, Any]) -> dict[str, Any]:
        """项目工厂参数变化时重启服务；否则复用。"""
        current = self.status()
        desired = project_factory_signature(project)
        if (
            current.get("running")
            and current.get("healthy")
            and current.get("project_id") == project.get("project_id")
            and current.get("factory_signature") == desired
        ):
            return current
        return self.start(project)


_manager: Optional[FactoryServiceManager] = None
_manager_lock = threading.Lock()


def _stop_owned_process(manager: FactoryServiceManager) -> None:
    """进程退出时只清理本进程启动的 3D 服务；附着的服务由用户显式停止。"""
    with manager._lock:
        if manager._proc is not None:
            manager._stop_locked()


def get_service_manager() -> FactoryServiceManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = FactoryServiceManager()
            atexit.register(_stop_owned_process, _manager)
        return _manager
