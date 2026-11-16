"""停止 AIC 平台由本仓库启动的服务（场景 API、OIP 编辑器、RFTwin Live）。

只处理 data/runtime 中记录的 PID；不触碰其他进程。
用法：python stop_platform.py
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "data" / "runtime"
FILES = ("scene_api.json", "oip_service.json", "service.json")


def _stop(pid: int) -> str:
    if not pid:
        return "no pid"
    try:
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=15)
            return (result.stdout or result.stderr or "").strip().splitlines()[-1] if (result.stdout or result.stderr) else "killed"
        os.kill(pid, 15)
        return "killed"
    except Exception as exc:  # noqa: BLE001
        return f"skip: {exc}"


def main() -> int:
    for name in FILES:
        path = RUNTIME / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = int(data.get("pid") or 0)
        print(f"{name}: {data.get('url') or data.get('path') or ''} PID {pid} -> {_stop(pid)}")
        try:
            path.unlink()
        except OSError:
            pass
    print("完成。Streamlit 由启动终端 Ctrl+C 停止；若仍运行可关闭其窗口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
