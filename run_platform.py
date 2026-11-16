"""AIC Factory Studio 一键启动器。

用法：
    python run_platform.py

行为：
1. 检查 Python 依赖与 RFTwin 前端构建产物；
2. 选择一个空闲端口（默认 8502）启动 Streamlit 统一工作台；
3. 等待服务可用后在默认浏览器打开。

三维 Live 服务由平台的「三维仿真」页面按需启动/停止，不需要手工运行 uvicorn。
"""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND_INDEX = ROOT / "rftwin" / "frontend" / "dist" / "index.html"

REQUIRED_MODULES = [
    ("streamlit", "streamlit"),
    ("plotly", "plotly"),
    ("pandas", "pandas"),
    ("yaml", "pyyaml"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("httpx", "httpx"),
    ("wsproto", "wsproto"),
]


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_and_open(url: str, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    if os.environ.get("AIC_NO_BROWSER") != "1":
                        webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.8)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    missing = [pip_name for module, pip_name in REQUIRED_MODULES if importlib.util.find_spec(module) is None]
    if missing:
        print("缺少依赖：" + "、".join(missing))
        print("请先执行：pip install -r requirements.txt")
        return 1
    if not FRONTEND_INDEX.exists():
        print("未找到 RFTwin 前端构建产物：rftwin/frontend/dist/index.html")
        print("请执行：")
        print("  npm --prefix rftwin/frontend ci")
        print("  npm --prefix rftwin/frontend run build")
        return 1

    port = next((p for p in range(8502, 8530) if _port_free(p)), None)
    if port is None:
        print("8502~8529 端口均被占用，无法启动。")
        return 1
    url = f"http://localhost:{port}"
    print("=" * 68)
    print("AIC Factory Studio · EdgeCoder × RFTwin 统一工业数字工厂平台")
    print(f"工作台地址：{url}")
    print("三维 Live 服务：在网页「三维仿真」页面点击按钮启动（本机 127.0.0.1）")
    print("按 Ctrl+C 停止工作台。")
    print("=" * 68)

    try:
        sys.path.insert(0, str(ROOT))
        from integration.scene.api_service import ensure_scene_api

        print(f"场景 API（OIP → Python 仿真）：{ensure_scene_api()}")
    except Exception as exc:  # noqa: BLE001 - 工作台仍可启动，场景页会重试
        print(f"场景 API 启动失败（工作台仍会启动）：{exc}")

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "factory_app.py",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]
    proc = subprocess.Popen(command, cwd=str(ROOT))
    threading.Thread(target=_wait_and_open, args=(url,), daemon=True).start()
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
