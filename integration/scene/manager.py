"""实时场景仿真的线程管理（暂停/恢复/停止/状态快照）。

实时运行由 wall-clock 驱动，步数与真实时间相关，因此实时结果不承诺逐位可复现；
离线实验（engine.run）才是确定性、可复现的权威结果。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Optional

from integration.project_store import new_experiment_id

from .engine import SceneFactoryEngine, SceneSimulationError
from .models import SceneDocument
from .store import save_scene_experiment


class LiveSimulation:
    def __init__(self, scene: SceneDocument, *, duration_sec: float, seed: int, speed: float = 1.0):
        self.engine = SceneFactoryEngine(scene, duration_sec=duration_sec, seed=seed, sim_id=new_experiment_id("SIM"))
        self.sim_id = self.engine.sim_id
        # 引擎内部已经 deepcopy；这里复用同一份，避免每个实例多占一份场景（含 OIP 工程）。
        self.scene = self.engine.scene
        self.duration_sec = float(duration_sec)
        self.speed = max(0.1, min(100.0, float(speed)))
        self.status = "created"
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.started_at: Optional[str] = None
        self.stopped_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._record: Optional[dict[str, Any]] = None

    def start(self) -> None:
        with self._lock:
            if self.status in ("running", "completed"):
                return
            if self.status == "stopped":
                raise SceneSimulationError("已停止的仿真实例不能再次启动，请新建实例")
            self.status = "running"
            self.started_at = self.started_at or datetime.now().isoformat(timespec="seconds")
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name=f"scene-sim-{self.sim_id}", daemon=True)
                self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self.status == "running":
                self.status = "paused"

    def resume(self) -> None:
        with self._lock:
            if self.status == "paused":
                self.status = "running"

    def stop(self) -> None:
        with self._lock:
            if self.status in ("running", "paused", "created"):
                self.status = "stopped"
                self.stopped_at = datetime.now().isoformat(timespec="seconds")
                self._stop_event.set()
                self._finalize()

    def _finalize(self) -> None:
        if self._record is not None:
            return
        try:
            record = self.engine.build_record(persist=False, label="实时场景仿真")
            record["source"] = "scene_engine_live"
            record["live"] = True
            save_scene_experiment(self.scene.project_id, record)
            self._record = record
        except Exception as exc:  # noqa: BLE001 - 保留错误供 API/UI 查看
            self.last_error = str(exc)

    def _run(self) -> None:
        tick = self.engine.tick_sec
        accumulator = 0.0
        last = time.monotonic()
        while not self._stop_event.is_set():
            time.sleep(0.1)
            now = time.monotonic()
            elapsed = max(0.0, now - last)
            last = now
            with self._lock:
                if self.status == "paused":
                    continue
                if self.status != "running":
                    break
                accumulator += elapsed * self.speed
                steps = 0
                while accumulator >= tick and steps < 200:
                    try:
                        self.engine.step(tick)
                    except Exception as exc:  # noqa: BLE001
                        self.last_error = str(exc)
                        self.status = "failed"
                        self._stop_event.set()
                        break
                    accumulator -= tick
                    steps += 1
                if self.engine.t + 1e-9 >= self.duration_sec:
                    self.status = "completed"
                    self.stopped_at = datetime.now().isoformat(timespec="seconds")
                    self._finalize()
                    break
        if self.status == "stopped":
            self._finalize()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = self.engine.snapshot()
            snap.update(
                {
                    "sim_id": self.sim_id,
                    "status": self.status,
                    "speed": self.speed,
                    "created_at": self.created_at,
                    "started_at": self.started_at,
                    "stopped_at": self.stopped_at,
                    "last_error": self.last_error,
                    "finalized": self._record is not None,
                }
            )
            return snap

    def result(self) -> Optional[dict[str, Any]]:
        with self._lock:
            if self._record is None and self.status in ("completed", "stopped"):
                self._finalize()
            return self._record

    def finished(self) -> bool:
        with self._lock:
            return self.status in ("completed", "stopped", "failed")


class SimulationManager:
    def __init__(self, *, max_finished: int = 20) -> None:
        self._lock = threading.RLock()
        self._sims: dict[str, LiveSimulation] = {}
        self.max_finished = max(0, int(max_finished))

    def _prune_locked(self) -> None:
        """只保留最近 max_finished 个已结束实例，避免长会话内存持续增长。"""
        finished = [(sim_id, sim) for sim_id, sim in self._sims.items() if sim.finished()]
        excess = len(finished) - self.max_finished
        if excess <= 0:
            return
        finished.sort(key=lambda item: item[1].stopped_at or item[1].created_at)
        for sim_id, _ in finished[:excess]:
            self._sims.pop(sim_id, None)

    def create(self, scene: SceneDocument, *, duration_sec: float, seed: int, speed: float = 1.0) -> LiveSimulation:
        sim = LiveSimulation(scene, duration_sec=duration_sec, seed=seed, speed=speed)
        with self._lock:
            self._sims[sim.sim_id] = sim
            self._prune_locked()
        return sim

    def get(self, sim_id: str) -> LiveSimulation:
        with self._lock:
            sim = self._sims.get(sim_id)
        if sim is None:
            raise SceneSimulationError(f"仿真实例不存在：{sim_id}")
        return sim

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            return [sim.snapshot() for sim in self._sims.values()]

    def stop_all(self) -> None:
        with self._lock:
            sims = list(self._sims.values())
        for sim in sims:
            try:
                sim.stop()
            except Exception:  # noqa: BLE001
                pass


_MANAGER = SimulationManager()


def get_simulation_manager() -> SimulationManager:
    return _MANAGER
