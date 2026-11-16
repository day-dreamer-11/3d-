"""集成层路径常量。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RFTWIN_ROOT = PROJECT_ROOT / "rftwin"
RFTWIN_PACKAGES = RFTWIN_ROOT / "packages"
RFTWIN_CONFIG = RFTWIN_ROOT / "config"
RFTWIN_FRONTEND_DIST = RFTWIN_ROOT / "frontend" / "dist"
RFTWIN_BASE_PARAMS = RFTWIN_CONFIG / "simulation_parameters.yaml"
RFTWIN_LAYOUT = RFTWIN_CONFIG / "factory_layout.json"

DATA_DIR = Path(os.environ.get("AIC_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()
PROJECTS_DIR = DATA_DIR / "projects"
RUNTIME_DIR = DATA_DIR / "runtime"
LOGS_DIR = DATA_DIR / "logs"


def ensure_data_dirs() -> None:
    for directory in (PROJECTS_DIR, RUNTIME_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
