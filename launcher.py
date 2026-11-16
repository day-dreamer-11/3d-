"""AI Industrial Factory · Windows 本地 Web 启动器。

职责（不包含任何业务逻辑）：
- 单实例：同一用户目录只允许一个运行实例；
- 动态端口：为 Streamlit 与 RFTwin 三维服务选择空闲的本机端口；
- 自动启动：SQLite 数据目录、演示项目、RFTwin 三维服务、Streamlit 工作台；
- 健康检查：等待两个服务真正可用后再打开默认浏览器；
- 运行管理：监控子进程、异常退出/看门狗、退出时清理自己启动的进程；
- 日志：写入 %LOCALAPPDATA%/AIIndustrialFactory/logs/launcher.log；
- 命令行：--stop 停止当前实例；--status 输出运行状态；--no-open 不打开浏览器。

评委使用方式：双击桌面快捷方式 → 浏览器自动打开 http://127.0.0.1:<动态端口>。
"""

from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
APP_NAME = "AIIndustrialFactory"
LAUNCHER_VERSION = "1.1.1"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def user_data_dir() -> Path:
    if os.environ.get("AIC_DATA_DIR"):
        return Path(os.environ["AIC_DATA_DIR"]).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


DATA_DIR = user_data_dir()
RUNTIME_DIR = DATA_DIR / "runtime"
LOG_DIR = DATA_DIR / "logs"
STATE_PATH = RUNTIME_DIR / "launcher.json"
LOCK_PATH = RUNTIME_DIR / "launcher.lock"
STOP_PATH = RUNTIME_DIR / "stop.request"


def _message(title: str, text: str) -> None:
    try:
        if os.name == "nt" and sys.stdout is None:  # pythonw：没有控制台
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:  # noqa: BLE001
        pass


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("aic-launcher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(LOG_DIR / "launcher.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger


def _port_in_use(port: int) -> bool:
    # Windows 上别的进程监听 0.0.0.0:port 时，仍可能成功 bind 127.0.0.1:port，
    # 因此必须先尝试连接，再尝试绑定。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _free_port(preferred: int = 0, low: int = 8502, high: int = 8599) -> int:
    candidates = [preferred] if preferred else []
    candidates.extend(range(low, high + 1))
    for port in candidates:
        if not _port_in_use(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_owner_pid(port: int) -> int | None:
    """返回 Windows 上监听该端口的 PID（用于确认服务确实由我们启动）。"""
    if os.name != "nt":
        return None
    try:
        output = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def _http_ok(host: str, port: int, path: str, timeout: float = 1.5) -> bool:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read(200)
        conn.close()
        return response.status == 200 and (b"ok" in body.lower() or b"status" in body.lower() or body)
    except OSError:
        return False


def _wait_http(host: str, port: int, path: str, timeout_s: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _http_ok(host, port, path):
            return True
        time.sleep(0.6)
    return False


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid: int, logger: logging.Logger) -> None:
    if not pid or not _pid_alive(pid):
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
        else:
            os.kill(pid, 15)
        logger.info("已终止进程树 PID=%s", pid)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("终止 PID=%s 失败：%s", pid, exc)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(state: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _acquire_lock(logger: logging.Logger):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "a+")
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return None
    return handle


def _release_lock(handle) -> None:
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        handle.close()


def _prepare_environment(twin_port: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AIC_DATA_DIR", str(DATA_DIR))
    os.environ["AIC_DATA_DIR"] = str(DATA_DIR)
    os.environ["AIC_STORAGE"] = "sqlite"
    os.environ.setdefault("AIC_SQLITE_PATH", str(DATA_DIR / "aic_factory.db"))
    os.environ["TWIN_INTERNAL_PORT"] = str(twin_port)
    os.environ["TWIN_RATE_LIMIT"] = "0"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["AIC_DESKTOP_VERSION"] = LAUNCHER_VERSION
    os.environ["AIC_DESKTOP"] = "1"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"


def _seed_demo_projects(logger: logging.Logger) -> None:
    """首次启动时从安装目录的 demo_seed 复制演示项目（已存在的不覆盖）。"""
    seed_root = APP_ROOT / "demo_seed" / "projects"
    if not seed_root.is_dir():
        return
    target_root = DATA_DIR / "projects"
    target_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(seed_root.iterdir()):
        if not source.is_dir() or not (source / "project.json").exists():
            continue
        target = target_root / source.name
        if target.exists():
            continue
        shutil.copytree(source, target)
        logger.info("已载入演示项目：%s", source.name)

    # 升级用户的 SQLite 已完成过首次导入；新预置项目仍需进入索引。
    # INSERT OR IGNORE 保留用户修改，也可补齐上次中断后遗漏的导入。
    if os.environ.get("AIC_STORAGE") == "sqlite":
        from integration.project_store import migrate_json_to_sqlite

        migrate_json_to_sqlite()


def _ensure_project(logger: logging.Logger) -> dict:
    from integration.project_store import create_project, list_projects

    projects = list_projects()
    if projects:
        return projects[0]
    project = create_project(
        "演示工厂",
        "首次启动自动创建。可直接运行生产仿真、三维查看与边缘调度实验。",
        "演示完整数字工厂闭环：建模 → 仿真 → 分析 → 优化。",
    )
    logger.info("已创建默认演示项目：%s", project["project_id"])
    return project


def _open_state_browser(state: dict, no_open: bool, logger: logging.Logger) -> None:
    url = str(state.get("url", ""))
    if not url:
        return
    if not no_open:
        webbrowser.open(url)
        logger.info("已请求打开浏览器：%s", url)


def _try_existing_instance(no_open: bool, logger: logging.Logger) -> bool:
    state = _read_json(STATE_PATH)
    if not state:
        return False
    port = int(state.get("streamlit_port") or 0)
    streamlit_pid = int(state.get("streamlit_pid") or 0)
    owner = _port_owner_pid(port)
    if (
        port
        and _http_ok("127.0.0.1", port, "/_stcore/health")
        and _pid_alive(streamlit_pid)
        and (owner is None or owner == streamlit_pid)
    ):
        logger.info("已有运行实例：%s", state.get("url"))
        _open_state_browser(state, no_open, logger)
        return True
    return False


def stop_instance(logger: logging.Logger) -> int:
    state = _read_json(STATE_PATH)
    if not state:
        logger.info("没有检测到运行中的实例")
        return 0
    streamlit_pid = int(state.get("streamlit_pid") or 0)
    port = int(state.get("streamlit_port") or 0)
    # 先写停止标记，让正在运行的启动器走“正常停止”分支，而不是把子进程退出当成崩溃。
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STOP_PATH.write_text("stop", encoding="utf-8")
    except OSError:
        pass
    time.sleep(0.8)
    if port and _http_ok("127.0.0.1", port, "/_stcore/health"):
        _kill_tree(streamlit_pid, logger)
    try:
        _prepare_environment(int(state.get("twin_port") or _free_port(8020, 8020, 8099)))
        from integration.factory_service import get_service_manager

        get_service_manager().stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("停止三维服务失败：%s", exc)
        twin_pid = int(state.get("twin_pid") or 0)
        _kill_tree(twin_pid, logger)
    for path in (STATE_PATH, RUNTIME_DIR / "service.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    logger.info("本地平台已停止")
    return 0


def status_instance() -> int:
    state = _read_json(STATE_PATH)
    if not state:
        print(json.dumps({"running": False}, ensure_ascii=False))
        return 1
    streamlit_ok = _http_ok("127.0.0.1", int(state.get("streamlit_port") or 0), "/_stcore/health")
    twin_ok = _http_ok("127.0.0.1", int(state.get("twin_port") or 0), "/api/health")
    print(
        json.dumps(
            {
                "running": bool(streamlit_ok),
                "url": state.get("url"),
                "streamlit_port": state.get("streamlit_port"),
                "twin_port": state.get("twin_port"),
                "streamlit_health": streamlit_ok,
                "twin_health": twin_ok,
                "data_dir": str(DATA_DIR),
                "log": str(LOG_DIR / "launcher.log"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run(no_open: bool, streamlit_port: int | None) -> int:
    logger = _setup_logging()
    logger.info("启动器 v%s，目录 %s", LAUNCHER_VERSION, APP_ROOT)
    logger.info("Python: %s (%s)", sys.executable, sys.version.split()[0])
    logger.info(
        "前端构建产物：OIP=%s RFTwin=%s",
        (APP_ROOT / "frontend" / "oip" / "dist" / "index.html").exists(),
        (APP_ROOT / "rftwin" / "frontend" / "dist" / "index.html").exists(),
    )

    lock = _acquire_lock(logger)
    if lock is None:
        if _try_existing_instance(no_open, logger):
            return 0
        logger.error("另一个实例正在启动或运行，请稍后重试")
        _message("AI 数字工厂", "另一个实例正在启动或运行。请稍后重试。")
        return 2

    streamlit_proc = None
    manager = None
    try:
        if _try_existing_instance(no_open, logger):
            return 0
        try:
            STOP_PATH.unlink()
        except FileNotFoundError:
            pass

        twin_port = _free_port(8020, 8020, 8099)
        _prepare_environment(twin_port)
        logger.info("数据目录：%s", DATA_DIR)
        _seed_demo_projects(logger)

        project = _ensure_project(logger)
        from integration.factory_service import get_service_manager

        manager = get_service_manager()
        twin_info = None
        try:
            twin_info = manager.start(project, timeout_s=120)
            logger.info("三维服务已就绪：%s（PID %s）", twin_info.get("url"), twin_info.get("pid"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("三维服务启动失败：%s", exc)

        port = _free_port(streamlit_port or 0, 8502, 8599)
        log_path = LOG_DIR / "streamlit.log"
        log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP_ROOT / "factory_app.py"),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--server.fileWatcherType",
            "none",
            "--server.enableCORS",
            "false",
            "--browser.gatherUsageStats",
            "false",
        ]
        env = os.environ.copy()
        streamlit_proc = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        logger.info("Streamlit 启动中：PID %s，端口 %s", streamlit_proc.pid, port)
        if not _wait_http("127.0.0.1", port, "/_stcore/health", timeout_s=120):
            raise RuntimeError(f"Streamlit 健康检查超时。日志：{log_path}")
        owner = _port_owner_pid(port)
        if owner is not None and owner != streamlit_proc.pid:
            raise RuntimeError(
                f"端口 {port} 被其他进程（PID {owner}）占用，未能启动本实例。"
                "请重试或关闭占用该端口的程序。"
            )

        url = f"http://127.0.0.1:{port}"
        state = {
            "version": LAUNCHER_VERSION,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "url": url,
            "streamlit_port": port,
            "streamlit_pid": streamlit_proc.pid,
            "twin_port": int(twin_info["port"]) if twin_info else None,
            "twin_pid": int(twin_info["pid"]) if twin_info else None,
            "project_id": project.get("project_id"),
            "data_dir": str(DATA_DIR),
        }
        _write_state(state)
        logger.info("平台已就绪：%s", url)
        _open_state_browser(state, no_open, logger)

        consecutive_twin_failures = 0
        while True:
            if STOP_PATH.exists():
                logger.info("收到网页停止请求")
                break
            if streamlit_proc.poll() is not None:
                raise RuntimeError(f"Streamlit 已退出（返回码 {streamlit_proc.returncode}）")
            if manager is not None:
                twin_ok = bool(twin_info and _http_ok("127.0.0.1", int(twin_info["port"]), "/api/health"))
                if twin_ok:
                    consecutive_twin_failures = 0
                else:
                    consecutive_twin_failures += 1
                    if consecutive_twin_failures >= 3:
                        logger.warning("三维服务无响应，尝试重启")
                        try:
                            twin_info = manager.start(project, timeout_s=120)
                            state["twin_port"] = int(twin_info["port"])
                            state["twin_pid"] = int(twin_info["pid"])
                            _write_state(state)
                            consecutive_twin_failures = 0
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("三维服务重启失败：%s", exc)
                            consecutive_twin_failures = 0
            time.sleep(3)
    except KeyboardInterrupt:
        logger.info("收到退出信号")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("启动失败：%s", exc)
        _message("AI 数字工厂启动失败", f"{exc}\n\n日志：{LOG_DIR / 'launcher.log'}")
        return 1
    finally:
        try:
            if streamlit_proc is not None:
                _kill_tree(streamlit_proc.pid, logger)
            if manager is not None:
                manager.stop()
        finally:
            for path in (STATE_PATH, STOP_PATH):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            _release_lock(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Industrial Factory 本地启动器")
    parser.add_argument("--stop", action="store_true", help="停止当前实例")
    parser.add_argument("--status", action="store_true", help="显示运行状态")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器（测试用）")
    parser.add_argument("--port", type=int, default=None, help="指定 Streamlit 端口（默认动态）")
    args = parser.parse_args()
    if args.stop:
        return stop_instance(_setup_logging())
    if args.status:
        return status_instance()
    return run(args.no_open, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
