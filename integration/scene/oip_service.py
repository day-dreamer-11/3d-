"""OIP 编辑器子项目进程管理。

- OIP 源码固定在 frontend/oip（commit dcebf53），不复制到 rftwin/；
- 以独立 Vite 服务运行，只监听 127.0.0.1；平台通过 iframe 嵌入；
- 健康检查成功后才返回 URL；支持页面进程重启后附着；
- 退出时只清理本进程启动的编辑器。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from integration.paths import PROJECT_ROOT, RUNTIME_DIR, LOGS_DIR, ensure_data_dirs

OIP_ROOT = PROJECT_ROOT / "frontend" / "oip"
STATE_PATH = RUNTIME_DIR / "oip_service.json"
OIP_COMMIT = "dcebf53"
_HTTP = httpx.Client(timeout=3.0, trust_env=False)


class OipServiceError(RuntimeError):
    pass


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(start: int = 8610, attempts: int = 60) -> int:
    fixed = os.environ.get("AIC_OIP_PORT", "").strip()
    if fixed:
        port = int(fixed)
        if _port_available(port):
            return port
        raise OipServiceError(f"AIC_OIP_PORT={port} 已被占用")
    for port in range(start, start + attempts):
        if _port_available(port):
            return port
    raise OipServiceError("没有可用端口启动 OIP 编辑器")


def _node_executable() -> str:
    node = shutil.which("node")
    if node:
        return node
    for candidate in (
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
    ):
        if Path(candidate).exists():
            return candidate
    raise OipServiceError("未找到 Node.js（OIP 编辑器需要 Node 24+）")


def _vite_entry() -> Path:
    path = OIP_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
    if not path.exists():
        raise OipServiceError(
            "OIP 依赖未安装：请在 frontend/oip 执行 npm ci（或复用已安装的 node_modules）"
        )
    return path


class OipServiceManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._info: Optional[dict[str, Any]] = None
        self._log_handle = None
        self._log_path: Optional[Path] = None
        self._load_state()

    def _load_state(self) -> None:
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if data.get("url"):
                    data["attached"] = True
                    self._info = data
        except (OSError, json.JSONDecodeError):
            self._info = None

    def _write_state(self) -> None:
        ensure_data_dirs()
        if self._info is None:
            try:
                STATE_PATH.unlink()
            except FileNotFoundError:
                pass
            return
        STATE_PATH.write_text(json.dumps(self._info, ensure_ascii=False, indent=2), encoding="utf-8")

    def _health(self, url: str, timeout: float = 2.0) -> bool:
        try:
            response = _HTTP.get(f"{url}/app/", timeout=timeout)
            return response.status_code == 200 and "Open Industry Project" in response.text or response.status_code == 200
        except (httpx.HTTPError, ValueError):
            return False

    def status(self, check_remote: bool = True) -> dict[str, Any]:
        with self._lock:
            if self._info is None:
                # 另一个进程（场景 API）可能已经启动 OIP 并写了状态文件；重新读取以显示真实状态。
                self._load_state()
            info = self._info
            if info is None:
                return {"running": False, "healthy": False, "commit": OIP_COMMIT, "root": str(OIP_ROOT)}
            running = bool(self._proc and self._proc.poll() is None) or bool(info.get("attached"))
            healthy = self._health(info["url"], timeout=1.5) if check_remote else False
            if self._proc is not None and self._proc.poll() is not None and not healthy:
                running = False
            return {
                "running": running,
                "healthy": healthy,
                **(info or {}),
                "commit": OIP_COMMIT,
                "root": str(OIP_ROOT),
            }

    def start(self, timeout_s: float = 90.0) -> dict[str, Any]:
        ensure_data_dirs()
        with self._lock:
            current = self.status()
            if current.get("running") and current.get("healthy"):
                return current
            if not (OIP_ROOT / "package.json").exists():
                raise OipServiceError(f"未找到 OIP 子项目：{OIP_ROOT}")
            self._stop_locked()
            port = find_free_port()
            url = f"http://127.0.0.1:{port}"
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOGS_DIR / f"oip-{datetime.now():%Y%m%d-%H%M%S}.log"
            self._log_path = log_path
            self._log_handle = open(log_path, "a", encoding="utf-8", newline="\n")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            dist_index = OIP_ROOT / "dist" / "index.html"
            static_mode = dist_index.exists() and os.environ.get("AIC_OIP_DEV", "").strip() != "1"
            if static_mode:
                # 生产/离线模式：Python 静态服务器，不需要 Node 运行时。
                command = [
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                    "--directory",
                    str(OIP_ROOT / "dist"),
                ]
            else:
                node = _node_executable()
                vite = _vite_entry()
                command = [node, str(vite), "--port", str(port), "--strictPort", "--host", "127.0.0.1"]
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=str(OIP_ROOT),
                    env=env,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                if self._log_handle:
                    self._log_handle.close()
                raise OipServiceError(f"无法启动 OIP 编辑器：{exc}") from exc
            self._proc = proc
            info = {
                "url": url,
                "port": port,
                "pid": proc.pid,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "log_path": str(log_path),
                "commit": OIP_COMMIT,
                "mode": "static" if static_mode else "vite-dev",
                "dist": str(OIP_ROOT / "dist") if static_mode else None,
                "attached": False,
            }
            self._info = info
            started = time.monotonic()
            while time.monotonic() - started < timeout_s:
                if proc.poll() is not None:
                    break
                if self._health(url, timeout=2.0):
                    self._write_state()
                    return dict(info)
                time.sleep(0.6)
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            except OSError:
                pass
            self._stop_locked()
            if proc.poll() is not None:
                raise OipServiceError(f"OIP 编辑器提前退出（{proc.returncode}）。日志：\n{tail}")
            raise OipServiceError(f"OIP 编辑器启动超时。日志：\n{tail}")

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
            if self._health(info["url"], timeout=1.5):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(info["pid"]), "/F"], capture_output=True, timeout=10)
                    else:
                        os.kill(int(info["pid"]), 15)
                except (OSError, subprocess.SubprocessError):
                    pass
        self._info = None
        self._write_state()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def editor_url(self, *, api_base: str, project_id: Optional[str] = None, scene_id: Optional[str] = None) -> str:
        info = self.status()
        if not info.get("running") or not info.get("healthy"):
            info = self.start()
        # hook=1: OIP's supported gate that exposes window.__oip (store/sceneFileText/loadSceneText)
        # in a production build; the AIC bridge needs it to snapshot/load the project.
        params = {"api": api_base.rstrip("/"), "hook": "1"}
        if project_id:
            params["projectId"] = project_id
        if scene_id:
            params["sceneId"] = scene_id
        return f"{info['url']}/app/?{urlencode(params)}"


_MANAGER: Optional[OipServiceManager] = None
_MANAGER_LOCK = threading.Lock()


def _stop_owned(manager: OipServiceManager) -> None:
    with manager._lock:
        if manager._proc is not None:
            manager._stop_locked()


def get_oip_manager() -> OipServiceManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = OipServiceManager()
            atexit.register(_stop_owned, _MANAGER)
        return _MANAGER
