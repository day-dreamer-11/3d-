"""场景 FastAPI 服务的进程管理（Streamlit / 一键启动共用）。

- 环境变量 AIC_SCENE_API_URL 指定已有服务时直接附着；
- 否则读取 data/runtime/scene_api.json，健康则复用；
- 否则启动 uvicorn integration.scene.api:app（127.0.0.1 随机空闲端口）。
"""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from integration.paths import LOGS_DIR, PROJECT_ROOT, RUNTIME_DIR, ensure_data_dirs

STATE_PATH = RUNTIME_DIR / "scene_api.json"
_HTTP = httpx.Client(timeout=2.0, trust_env=False)


class SceneApiError(RuntimeError):
    pass


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _free_port(start: int = 8701, attempts: int = 80) -> int:
    for port in range(start, start + attempts):
        if _port_available(port):
            return port
    raise SceneApiError("没有可用端口启动场景 API 服务")


def _healthy(url: str) -> bool:
    try:
        response = _HTTP.get(f"{url.rstrip('/')}/api/health", timeout=1.5)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def _read_state() -> Optional[dict[str, Any]]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if data.get("url"):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_state(data: Optional[dict[str, Any]]) -> None:
    ensure_data_dirs()
    if data is None:
        try:
            STATE_PATH.unlink()
        except FileNotFoundError:
            pass
        return
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class SceneApiService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._info: Optional[dict[str, Any]] = None
        self._log_handle = None
        self._log_path: Optional[Path] = None

    def ensure(self, timeout_s: float = 30.0) -> str:
        with self._lock:
            explicit = os.environ.get("AIC_SCENE_API_URL", "").strip()
            if explicit:
                if _healthy(explicit):
                    return explicit.rstrip("/")
                raise SceneApiError(f"AIC_SCENE_API_URL 不可用：{explicit}")
            state = _read_state()
            if state and _healthy(state["url"]):
                self._info = {**state, "attached": True}
                return state["url"].rstrip("/")
            if self._proc is not None and self._proc.poll() is None and self._info:
                if _healthy(self._info["url"]):
                    return self._info["url"].rstrip("/")
            self._stop_locked()
            ensure_data_dirs()
            port = _free_port()
            url = f"http://127.0.0.1:{port}"
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            self._log_path = LOGS_DIR / "scene_api.log"
            self._log_handle = open(self._log_path, "a", encoding="utf-8", newline="\n")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["AIC_SCENE_API_PORT"] = str(port)
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                "integration.scene.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ]
            proc = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
            self._proc = proc
            info = {
                "url": url,
                "port": port,
                "pid": proc.pid,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "log_path": str(self._log_path),
                "attached": False,
            }
            self._info = info
            started = time.monotonic()
            while time.monotonic() - started < timeout_s:
                if proc.poll() is not None:
                    break
                if _healthy(url):
                    _write_state(info)
                    return url
                time.sleep(0.4)
            tail = ""
            try:
                tail = self._log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                pass
            self._stop_locked()
            if proc.poll() is not None:
                raise SceneApiError(f"场景 API 启动失败（{proc.returncode}）：\n{tail}")
            raise SceneApiError(f"场景 API 启动超时：\n{tail}")

    def status(self) -> dict[str, Any]:
        with self._lock:
            info = self._info or _read_state()
            if not info:
                return {"running": False, "healthy": False}
            return {"running": True, "healthy": _healthy(info["url"]), **info}

    def _stop_locked(self) -> None:
        proc = self._proc
        info = self._info
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=8)
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
        elif info and info.get("pid") and info.get("url"):
            if _healthy(info["url"]):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(info["pid"]), "/F"], capture_output=True, timeout=10)
                    else:
                        os.kill(int(info["pid"]), 15)
                except (OSError, subprocess.SubprocessError):
                    pass
        self._info = None
        _write_state(None)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()


_SERVICE: Optional[SceneApiService] = None
_LOCK = threading.Lock()


def _stop_owned(service: SceneApiService) -> None:
    with service._lock:
        if service._proc is not None:
            service._stop_locked()


def get_scene_api_service() -> SceneApiService:
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            _SERVICE = SceneApiService()
            atexit.register(_stop_owned, _SERVICE)
        return _SERVICE


def ensure_scene_api() -> str:
    return get_scene_api_service().ensure()
