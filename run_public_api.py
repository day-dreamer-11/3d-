"""启动只读公开接口（供 ChatGPT 网页版 / 自动化读取）。

用法：
    python run_public_api.py                 # 127.0.0.1:8600
    PUBLIC_API_PORT=8700 python run_public_api.py
    PUBLIC_API_TOKEN=your-token python run_public_api.py

默认只监听本机；要供 ChatGPT 读取，请用隧道（如 cloudflared）暴露该端口，
不要直接暴露 Streamlit 8502。
"""

from __future__ import annotations

import os
import socket
import sys

import uvicorn


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    port = int(os.environ.get("PUBLIC_API_PORT", "8600"))
    if not _free(port):
        print(f"端口 {port} 已被占用，可通过 PUBLIC_API_PORT 指定其他端口。")
        return 1
    token = os.environ.get("PUBLIC_API_TOKEN", "").strip()
    print("=" * 68)
    print("AIC Factory Studio · 只读公开接口")
    print(f"本机地址：http://127.0.0.1:{port}")
    print(f"HTML 报告：http://127.0.0.1:{port}/report")
    print(f"接口文档：http://127.0.0.1:{port}/docs")
    print("访问令牌：" + ("已启用（Bearer 或 ?token=）" if token else "未设置（本机调试模式）"))
    print("提示：对外暴露请使用隧道，并设置 PUBLIC_API_TOKEN；不要暴露 Streamlit 8502。")
    print("=" * 68)
    uvicorn.run("public_api:app", host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
