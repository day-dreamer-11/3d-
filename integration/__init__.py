"""EdgeCoder × RFTwin 统一工业数字工厂平台集成层。

- project_store: 工程项目与实验的 JSON 持久化
- factory_adapter: 复用 RFTwin 确定性引擎，把项目配置映射为仿真参数
- edge_mapping: 工厂检测事件 → EdgeCoder 计算任务 → 调度策略实验
- analysis: 基于真实仿真结果的瓶颈分析与规则式优化建议
- factory_client / factory_service: 三维 Live 服务的 REST 客户端与进程管理
"""

from integration.project_store import (
    ProjectError,
    create_project,
    default_project,
    list_projects,
    load_project,
    save_project,
)
from integration.factory_adapter import (
    FactoryExperimentError,
    build_factory_params,
    run_factory_experiment,
)

__all__ = [
    "ProjectError",
    "FactoryExperimentError",
    "create_project",
    "default_project",
    "list_projects",
    "load_project",
    "save_project",
    "build_factory_params",
    "run_factory_experiment",
]
