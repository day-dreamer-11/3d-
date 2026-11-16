"""AIC Factory Studio：三维搭产线，模拟生产与质检（Streamlit 入口）。

启动：
    streamlit run factory_app.py

主导航五页：
- 平台总览 / 场景编辑器 / 建模分析工作台 / 实验历史 / 系统设置；
- OIP 负责三维搭建，生产 KPI 唯一权威是 `integration/scene/engine.py`；
- 旧 RFTwin/EdgeCoder 页面已移至 `legacy_pages.py`，不参与启动；
- 所有指标均为软件仿真，不宣称真实工厂或 6G 实测。
"""

from __future__ import annotations

import json
import math
import sys
import time
from typing import Any, Optional
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from integration.analysis import strategy_ranking
from integration.paths import DATA_DIR, ensure_data_dirs
from integration.project_bundle import (
    ProjectBundleError,
    export_project_bytes,
    import_project_bytes,
)
from integration.project_store import ProjectError, create_project
from integration.scene.client import SceneClient, SceneClientError
from ui_cache import invalidate_data_cache as _invalidate_data_cache
from ui_cache import list_projects
from ui_shared import current_project, goto_page, hero, package_version as _package_version

PAGES = [
    "平台总览",
    "场景编辑器",
    "建模分析工作台",
    "实验历史",
    "系统设置",
]


# 工作流四阶段：成功案例普遍采用“概览 → 仿真 → 分析 → 优化”的单一主线，
# 避免把十几个功能平铺成同等重要。
WORKFLOW_STEPS = [
    ("① 场景搭建", "OIP 三维编辑器"),
    ("② 生产仿真", "生产过程模拟"),
    ("③ 数据分析", "瓶颈、在制品、设备效率"),
    ("④ 优化归档", "AI 优化、实验、报告"),
]
PAGE_STEPS = {
    "平台总览": 0,
    "场景编辑器": 0,
    "建模分析工作台": 1,
    "实验历史": 3,
    "系统设置": 3,
}

st.set_page_config(
    page_title="AIC Factory Studio · 工业数字工厂平台",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# 样式与通用工具
# ---------------------------------------------------------------------------


def inject_css() -> None:
    st.markdown(
        """
<style>
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { display: none; }
.block-container { padding-top: 3.4rem; padding-bottom: 3rem; max-width: 1380px; }
h1, h2, h3, h4 { color: #16324F; letter-spacing: .2px; }
.ec-hero {
    background: linear-gradient(135deg, #16324F 0%, #23536B 55%, #2A7F8F 100%);
    color: #F5F7FA; padding: 24px 30px; border-radius: 14px; margin-bottom: 18px;
    box-shadow: 0 8px 24px rgba(22, 50, 79, .16);
}
.ec-hero h1 { color: #FFFFFF; margin: 0 0 8px 0; font-size: 28px; font-weight: 700; }
.ec-hero p { margin: 0; color: #D8E4EA; font-size: 13.5px; line-height: 1.7; }
.ec-tag {
    display: inline-block; padding: 3px 11px; border-radius: 999px;
    background: rgba(255,255,255,.14); font-size: 12px; margin: 8px 8px 0 0;
    color: #EAF2F6;
}
div[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #DCE5EC; border-left: 4px solid #2A7F8F;
    border-radius: 10px; padding: 11px 14px; box-shadow: 0 1px 3px rgba(16,50,79,.06);
}
div[data-testid="stMetricLabel"] p { font-size: 12.5px; color: #5B7186; }
.ec-note {
    background: #F3F8FB; border: 1px solid #D9E5EC; border-radius: 10px;
    padding: 12px 16px; color: #35566D; font-size: 13px; line-height: 1.75;
}
.ec-badge {
    display: inline-block; padding: 3px 11px; border-radius: 6px;
    font-size: 12.5px; font-weight: 600;
}
.ec-success { background: #E2F2E8; color: #1C6B3A; }
.ec-warn { background: #FBF0DC; color: #8A5A12; }
.ec-error { background: #FBE5E2; color: #8E2D22; }
.ec-logo { font-size: 21px; font-weight: 800; color: #16324F; letter-spacing: 1.2px; }
.ec-logo span { color: #2A7F8F; }
.ec-card {
    background: #FFFFFF; border: 1px solid #DCE5EC; border-radius: 12px;
    padding: 16px 18px; height: 100%;
}
.ec-card h4 { margin: 0 0 6px 0; color: #1F4E6B; font-size: 15px; }
.ec-card p { margin: 0; color: #5B7186; font-size: 12.8px; line-height: 1.7; }
.ec-flow {
    background: #FFFFFF; border: 1px solid #DCE5EC; border-radius: 10px;
    padding: 14px 16px; color: #27405A; font-size: 13px; line-height: 1.9;
}
.ec-steps {
    display: flex; gap: 8px; margin: 2px 0 16px 0; flex-wrap: wrap;
}
.ec-step {
    flex: 1; min-width: 140px; background: #FFFFFF; border: 1px solid #DCE5EC;
    border-radius: 10px; padding: 9px 12px; color: #6A7F92; font-size: 12.5px;
}
.ec-step b { display: block; color: #35566D; font-size: 13.5px; margin-bottom: 2px; }
.ec-step.active {
    background: #EAF4F7; border-color: #2A7F8F; box-shadow: inset 0 0 0 1px #2A7F8F;
}
.ec-step.active b { color: #1F6273; }
.ec-step.done { border-left: 4px solid #2F6B57; }
.ec-status {
    display: flex; gap: 22px; align-items: center; flex-wrap: wrap;
    border-radius: 10px; padding: 11px 16px; margin: 6px 0 12px 0;
    font-size: 13px; border: 1px solid #DCE5EC; background: #FFFFFF;
}
.ec-status .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.ec-status .ok { background: #2F6B57; }
.ec-status .warn { background: #C08A3E; }
.ec-status .bad { background: #A65246; }
.ec-alarm {
    background: #FBE5E2; border: 1px solid #E7B9B2; color: #8E2D22;
    border-radius: 10px; padding: 10px 14px; font-size: 13px; margin: 6px 0 10px 0;
}
.ec-verdict-ok { background: #E2F2E8; border: 1px solid #B9DCC7; color: #1C6B3A;
    border-radius: 10px; padding: 12px 16px; font-weight: 600; }
.ec-verdict-warn { background: #FBF0DC; border: 1px solid #E8CFA0; color: #8A5A12;
    border-radius: 10px; padding: 12px 16px; font-weight: 600; }
.ec-verdict-neutral { background: #F3F8FB; border: 1px solid #D9E5EC; color: #35566D;
    border-radius: 10px; padding: 12px 16px; font-weight: 600; }
</style>
""",
        unsafe_allow_html=True,
    )


def init_state() -> None:
    defaults = {
        "platform_page": PAGES[0],
        "project_id": None,
        "last_factory_id": None,
        "last_live_id": None,
        "last_edge_id": None,
        "analysis_exp": None,
        "gen_service": None,
        "gen_check": None,
        "gen_test": None,
        "edge_preview": None,
        "optimizer": None,
        # 每个会话使用唯一的 iframe 版本号，避免浏览器沿用旧版 OIP 页面/桥接脚本。
        "scene_editor_nonce": time.time(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if not st.session_state.get("scene_editor_nonce"):
        st.session_state["scene_editor_nonce"] = time.time()


def render_workflow_stepper(page: str) -> None:
    current = PAGE_STEPS.get(page, 0)
    parts = []
    for index, (title, desc) in enumerate(WORKFLOW_STEPS):
        css = "ec-step active" if index == current else ("ec-step done" if index < current else "ec-step")
        parts.append(f'<div class="{css}"><b>{title}</b>{desc}</div>')
    st.markdown('<div class="ec-steps">' + "".join(parts) + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 侧栏
# ---------------------------------------------------------------------------


def render_sidebar() -> str:
    pending_page = st.session_state.pop("_pending_page", None)
    if pending_page in PAGES:
        st.session_state.platform_page = pending_page
    if st.session_state.get("platform_page") not in PAGES:
        st.session_state.platform_page = PAGES[0]
    with st.sidebar:
        st.markdown('<div class="ec-logo">AIC FACTORY<span> STUDIO</span></div>', unsafe_allow_html=True)
        st.caption("三维搭产线，模拟生产与质检")

        projects = list_projects()
        if projects:
            ids = [p["project_id"] for p in projects]
            if st.session_state.get("project_id") not in ids:
                # OIP 场景平台优先选择“已有场景”的项目，避免默认落到空项目。
                from integration.demo_seed import preferred_demo_project_id

                demo_id = preferred_demo_project_id()
                preferred = demo_id if demo_id in ids else None
                try:
                    probe = SceneClient()
                    for candidate in projects:
                        if preferred:
                            break
                        scenes = probe.list_scenes(candidate["project_id"])
                        if any(int(scene.get("devices") or 0) > 0 for scene in scenes):
                            preferred = candidate["project_id"]
                            break
                    probe.close()
                except Exception:  # noqa: BLE001
                    preferred = demo_id if demo_id in ids else None
                st.session_state.project_id = preferred or ids[0]
            labels = {p["project_id"]: p["name"] for p in projects}
            selected = st.selectbox(
                "当前项目",
                options=ids,
                index=ids.index(st.session_state.project_id),
                format_func=lambda pid: labels.get(pid, pid),
            )
            if selected != st.session_state.project_id:
                st.session_state.project_id = selected
                st.session_state.analysis_exp = None
                st.session_state.optimizer = None
        else:
            st.warning("还没有工程项目，请先创建一个。")

        with st.expander("新建工程项目", expanded=not projects):
            with st.form("create_project_form", clear_on_submit=True):
                new_name = st.text_input("项目名称", value="智能工厂项目")
                new_desc = st.text_area("项目描述", value="", height=68)
                new_goal = st.text_input("项目目标", value="优化产线瓶颈与检测任务调度")
                if st.form_submit_button("创建项目"):
                    try:
                        project = create_project(new_name, new_desc, new_goal)
                    except ProjectError as exc:
                        st.error(f"创建失败：{exc}")
                    else:
                        st.session_state.project_id = project["project_id"]
                        st.session_state.analysis_exp = None
                        st.session_state.optimizer = None
                        _invalidate_data_cache()
                        st.rerun()

        if st.button("▶ 一键演示（自动仿真并跳转）", type="primary", width="stretch", key="one_click_demo"):
            _one_click_demo()

        with st.expander("项目文件：整包导出 / 导入", expanded=False):
            notice = st.session_state.pop("_bundle_notice", None)
            if notice:
                st.success(notice)
            current_pid = st.session_state.get("project_id")
            if current_pid:
                st.caption("导出包含场景、OIP 工程、实验与报告；导入会创建一个新项目。")
                if st.button("保存项目文件（.aicproj）", key="bundle_pack"):
                    try:
                        with st.spinner("正在打包…"):
                            filename, data = export_project_bytes(current_pid)
                    except (ProjectBundleError, ProjectError, OSError) as exc:
                        st.error(f"打包失败：{exc}")
                    else:
                        st.session_state["_bundle_ready"] = {
                            "pid": current_pid,
                            "filename": filename,
                            "data": data,
                        }
                ready = st.session_state.get("_bundle_ready")
                if ready and ready.get("pid") == current_pid:
                    st.download_button(
                        "下载项目备份",
                        ready["data"],
                        file_name=ready["filename"],
                        mime="application/zip",
                        key="bundle_download",
                    )
                    st.caption(f"文件大小 {len(ready['data']) / 1024 / 1024:.1f} MB")
            upload = st.file_uploader(
                "导入项目文件（.aicproj / .zip）",
                type=["aicproj", "zip"],
                key="bundle_upload",
            )
            if upload is not None and st.button("导入为新项目", key="bundle_import"):
                try:
                    with st.spinner("正在导入…"):
                        summary = import_project_bytes(upload.getvalue())
                except (ProjectBundleError, ProjectError, OSError, json.JSONDecodeError) as exc:
                    st.error(f"导入失败：{exc}")
                else:
                    st.session_state.project_id = summary["project_id"]
                    st.session_state.analysis_exp = None
                    st.session_state.optimizer = None
                    st.session_state.pop("ws_scene_prev", None)
                    st.session_state["_bundle_notice"] = (
                        f"已导入为项目「{summary['name']}」：{summary['scenes']} 个场景 / "
                        f"{summary['experiments']} 条实验（含 OIP 工程 {summary['oip_projects']} 个）"
                    )
                    _invalidate_data_cache()
                    st.rerun()

        page = st.radio("功能导航", PAGES, key="platform_page")

        st.divider()
        try:
            from integration.scene.api_service import get_scene_api_service
            from integration.scene.oip_service import get_oip_manager

            scene_api = get_scene_api_service().status()
            oip = get_oip_manager().status(check_remote=False)
            st.caption(f"场景服务：{'运行中' if scene_api.get('healthy') else '未启动'}")
            st.caption(f"OIP 编辑器：{'运行中' if oip.get('running') else '未启动'}")
            if not scene_api.get("healthy"):
                st.warning("场景服务未运行，请点击修复。")
                if st.button("启动 / 修复场景服务", key="fix_scene_api"):
                    ensure_services()
                    st.rerun()
        except Exception:  # noqa: BLE001 - 状态展示失败不影响主流程
            pass
        project_id = st.session_state.get("project_id")
        if project_id:
            try:
                sidebar_client = SceneClient()
                scene_count = len(sidebar_client.list_scenes(project_id))
                sidebar_client.close()
                st.caption(f"当前项目场景：{scene_count} 个")
            except Exception:  # noqa: BLE001
                pass
        st.divider()
        st.caption("v2.0 · OIP 场景平台 · 全部指标为仿真结果")
    return page


# ---------------------------------------------------------------------------
# 页面 1：平台总览
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 2：工厂搭建
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 3：生产线配置
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 4：三维仿真
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 5：生产数据分析
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 6：边缘计算
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 7：调度策略实验
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 8：AI 优化建议
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 9：实验历史
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 10：AI 服务生成（保留 EdgeCoder 能力）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 页面 11：系统设置与说明
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 自建工厂场景：OIP 编辑器 → Python 离散事件仿真 → 分析/优化
# ---------------------------------------------------------------------------

SCENE_STATE_COLORS = {
    "idle": "#8794a3",
    "running": "#1f8a4c",
    "processing": "#1f8a4c",
    "blocked": "#c47b13",
    "failed": "#c0392b",
    "maintenance": "#2a6f9e",
}
SCENE_STATE_LABELS = {
    "idle": "空闲",
    "running": "运行",
    "processing": "加工中",
    "blocked": "阻塞",
    "failed": "故障",
    "maintenance": "维护",
}
DEVICE_TYPE_ZH = {
    "conveyor": "输送机",
    "inspection": "检测装置",
    "robot": "机器人",
    "spawner": "物料源",
    "despawner": "物料汇",
    "display_only": "仅三维展示",
    "decoration": "装饰（仅三维）",
    "unknown": "未映射",
    "building": "建筑",
    "group": "分组",
    "annotation": "注释",
}
DEVICE_NAME_ZH = {
    "Spawner": "物料源",
    "Conveyor A": "输送机 A",
    "Inspection B": "检测工位 B",
    "Robot C": "机器人 C",
    "Conveyor D": "输送机 D",
    "Roller Conveyor": "滚筒输送机",
    "Belt Conveyor": "皮带输送机",
    "Curved Belt Conveyor": "弯道皮带输送机",
    "Curved Roller Conveyor": "弯道滚筒输送机",
    "Flex Conveyor": "柔性输送机",
    "Spur Belt Conveyor": "支线皮带输送机",
    "Spur Roller Conveyor": "支线滚筒输送机",
    "Turntable Conveyor": "转盘输送机",
    "Photo Eye": "光电传感器",
    "Laser Sensor": "激光传感器",
    "Color Sensor": "颜色传感器",
    "Barcode Scanner": "条码扫描器",
    "Light Curtain": "安全光幕",
    "Six-Axis Robot": "六轴机器人",
    "Gantry": "龙门架",
    "AGV": "自动导引车",
    "Box Spawner": "箱体物料源",
    "Despawner": "物料出口",
    "Box": "箱子",
    "Pallet": "托盘",
    "Unit Load": "单元负载",
    "Drum": "油桶",
    "IBC tote": "吨桶",
    "Tote": "周转箱",
}
CONNECTION_TYPE_ZH = {
    "material_flow": "物料流",
    "control": "控制",
    "data": "数据",
    "power": "电源",
}
CONNECTION_SOURCE_ZH = {
    "manual": "手工",
    "manual_ui": "界面添加",
    "geometry_derived": "几何推导",
    "geometry_derived_initial": "几何推导（初始）",
    "oip_mapping": "OIP 导入",
}


SIMULATION_STATUS_ZH = {
    "running": "运行中", "paused": "已暂停", "completed": "已完成",
    "stopped": "已停止", "failed": "运行失败",
}
STRATEGY_COLUMNS = {
    "rank": "排名", "strategy_name": "任务分配方案",
    "satisfaction_rate": "按时完成比例", "avg_latency_ms": "平均耗时（毫秒）",
    "success_rate": "完成比例", "total_cost": "模拟成本",
}
COMPARISON_COLUMNS = {
    "label": "指标", "baseline": "基准方案", "candidate": "对比方案",
    "delta": "变化量", "delta_pct": "变化幅度（%）",
}


def zh_device_type(value: Any) -> str:
    return DEVICE_TYPE_ZH.get(str(value), str(value or "—"))


def zh_device_name(value: Any) -> str:
    return DEVICE_NAME_ZH.get(str(value), str(value or "—"))


def zh_connection_type(value: Any) -> str:
    return CONNECTION_TYPE_ZH.get(str(value), str(value or "—"))


def ensure_services() -> dict[str, Any]:
    """确保场景 API 与 OIP 编辑器可用；返回状态摘要。用于现场自检/一键修复。"""
    result: dict[str, Any] = {}
    try:
        from integration.scene.api_service import get_scene_api_service

        api = get_scene_api_service()
        if not api.status().get("healthy"):
            api.ensure()
        result["api"] = api.status()
    except Exception as exc:  # noqa: BLE001
        result["api"] = {"healthy": False, "error": str(exc)}
    try:
        from integration.scene.oip_service import get_oip_manager

        oip = get_oip_manager()
        status = oip.status(check_remote=False)
        if not status.get("running"):
            status = oip.start()
        result["oip"] = status
    except Exception as exc:  # noqa: BLE001
        result["oip"] = {"running": False, "error": str(exc)}
    return result


@st.cache_resource(show_spinner=False)
def _scene_client_for(base_url: str) -> SceneClient:
    return SceneClient(base_url=base_url, on_write=_invalidate_data_cache)


def _scene_client() -> Optional[SceneClient]:
    try:
        from integration.scene.api_service import ensure_scene_api

        return _scene_client_for(ensure_scene_api())
    except Exception:  # noqa: BLE001
        try:
            ensure_services()
            from integration.scene.api_service import ensure_scene_api

            return _scene_client_for(ensure_scene_api())
        except Exception as exc:  # noqa: BLE001
            st.error(f"场景服务不可用：{exc}")
            if st.button("启动 / 修复场景服务", type="primary"):
                ensure_services()
                st.rerun()
            return None


def _pick_demo_scene(client: SceneClient) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    """选择演示项目/场景：优先当前项目；否则设备最多（同分偏好“可动画”）的场景。"""
    projects = list_projects()
    if not projects:
        return None
    current = st.session_state.get("project_id")
    ordered = sorted(projects, key=lambda p: p["project_id"] != current)
    best: Optional[tuple[int, int, dict[str, Any], dict[str, Any]]] = None
    for project in ordered:
        try:
            scenes = client.list_scenes(project["project_id"])
        except Exception:  # noqa: BLE001
            continue
        usable = [s for s in scenes if int(s.get("devices") or 0) > 0]
        for scene in usable:
            score = (0 if project["project_id"] == current else 1,
                     -int(scene.get("devices") or 0),
                     0 if "可动画" in str(scene.get("name")) else 1)
            if best is None or score < best[:3]:
                best = (*score, project, scene)
    if best is None:
        return None
    return best[3], best[4]


def _one_click_demo() -> None:
    client = _scene_client()
    if client is None:
        return
    picked = _pick_demo_scene(client)
    if picked is None:
        st.warning("没有可演示的场景，请先在「场景编辑器」搭建或导入场景。")
        return
    project, scene = picked
    try:
        detail = client.get_scene(project["project_id"], scene["scene_id"])
        settings = detail.get("simulation_settings") or {}
        preset = project.get("demo_preset") or {}
        with st.spinner("正在运行离线仿真（约几秒）…"):
            record = client.run_simulation(
                project["project_id"],
                scene["scene_id"],
                duration_sec=float(preset.get("duration_sec") or settings.get("duration_sec") or 600),
                seed=int(preset.get("seed", settings.get("seed", 42))),
            )
    except SceneClientError as exc:
        st.error(f"一键演示失败：{exc}")
        return
    exp_id = record.get("experiment_id")
    st.session_state.project_id = project["project_id"]
    st.session_state.analysis_exp = exp_id
    st.session_state.optimizer = None
    st.session_state["scene_editor_scene"] = scene["scene_id"]
    st.session_state.pop("ws_scene_prev", None)
    st.session_state["ws_demo_notice"] = (
        f"一键演示：已在「{project['name']} / {scene['name']}」运行离线仿真 {exp_id}，已跳转到分析结果。"
    )
    goto_page("建模分析工作台")


def _apply_pending_scene_selection() -> None:
    pending = st.session_state.pop("_pending_scene_id", None)
    if pending is None:
        return
    for key in ("scene_editor_scene", "scene_sim_scene", "scene_analysis_scene"):
        if pending == "":
            st.session_state.pop(key, None)
        else:
            st.session_state[key] = pending


def _scene_pick(project_id: str, client: SceneClient, key: str) -> Optional[dict[str, Any]]:
    _apply_pending_scene_selection()
    try:
        scenes = client.list_scenes(project_id)
    except SceneClientError as exc:
        st.error(str(exc))
        return None
    if not scenes:
        st.info("当前项目还没有自建场景。请先在「建模分析工作台」新建空白场景并搭建产线。")
        return None
    options = [s["scene_id"] for s in scenes]
    labels = {
        s["scene_id"]: f"{s['name']} · v{s['version']} · {s['devices']} 设备 · {s['connections']} 连接"
        for s in scenes
    }
    default_index = max(range(len(scenes)), key=lambda i: int(scenes[i].get("devices") or 0))
    index_kwargs = {} if key in st.session_state else {"index": default_index}
    selected = st.selectbox("选择场景", options, format_func=lambda sid: labels.get(sid, sid), key=key, **index_kwargs)
    try:
        return client.get_scene(project_id, selected)
    except SceneClientError as exc:
        st.error(str(exc))
        return None


def _render_scene_3d(scene: dict[str, Any], states: list[dict[str, Any]], key_prefix: str = "") -> None:
    state_map = {s.get("device_id"): s for s in (states or [])}
    xs, ys, zs, colors, names, hover = [], [], [], [], [], []
    line_x, line_y, line_z = [], [], []
    for device in scene.get("devices", []):
        device_id = device["id"]
        state = (state_map.get(device_id) or {}).get("state", "idle")
        position = device.get("position") or [0, 0, 0]
        xs.append(float(position[0]))
        ys.append(-float(position[2]))
        zs.append(float(position[1]))
        colors.append(SCENE_STATE_COLORS.get(state, "#8794a3"))
        name = zh_device_name(device.get("name") or device_id)
        names.append(name)
        info = state_map.get(device_id) or {}
        hover.append(
            f"{name}<br>类型 {zh_device_type(device.get('simulation_type'))}<br>"
            f"状态 {SCENE_STATE_LABELS.get(state, state)}<br>队列 {info.get('queue', '—')} · "
            f"利用率 {float(info.get('utilization', 0)) * 100:.1f}%"
        )
        if device.get("simulation_type") == "conveyor":
            length = float((device.get("params") or {}).get("length_m", 2.0))
            yaw = float((device.get("rotation") or {}).get("yaw", 0.0))
            dx = length / 2 * math.cos(yaw)
            dz = -length / 2 * math.sin(yaw)
            line_x += [float(position[0]) - dx, float(position[0]) + dx, None]
            line_y += [-float(position[2]) + dz, -float(position[2]) - dz, None]
            line_z += [float(position[1]), float(position[1]), None]
    fig = go.Figure()
    if line_x:
        fig.add_trace(go.Scatter3d(x=line_x, y=line_y, z=line_z, mode="lines", line=dict(color="#a8b4c2", width=4), hoverinfo="skip", showlegend=False))
    if xs:
        fig.add_trace(
            go.Scatter3d(
                x=xs,
                y=ys,
                z=zs,
                mode="markers+text",
                text=names,
                textposition="top center",
                marker=dict(size=8, color=colors, line=dict(color="#ffffff", width=1)),
                hovertext=hover,
                hoverinfo="text",
                showlegend=False,
            )
        )
    fig.update_layout(
        height=470,
        margin=dict(l=0, r=0, t=8, b=0),
        scene=dict(
            xaxis_title="X (m)",
            yaxis_title="-Z (m)",
            zaxis_title="Y (m)",
            aspectmode="data",
            dragmode="orbit",
        ),
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch", key=f"{key_prefix or 'scene3d'}_{scene.get('scene_id')}")
    st.caption("三维状态来自本次 Python 仿真快照；颜色：绿=运行/加工，橙=阻塞，红=故障，灰=空闲，蓝=维护。")


def _render_scene_record(record: dict[str, Any], scene: dict[str, Any]) -> None:
    kpis = record.get("kpis") or {}
    oee = kpis.get("oee") or {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("良品数", kpis.get("good_units", 0))
    c2.metric("总产出", kpis.get("total_output", 0))
    c3.metric("在制品", kpis.get("wip", 0))
    c4.metric("平均产能", f"{float(kpis.get('throughput_uph', 0)):.1f} 件/小时")
    c5.metric("设备综合效率", f"{float(oee.get('oee', 0)) * 100:.1f}%")
    bottleneck = (record.get("flow_insight") or {}).get("bottleneck_name") or "—"
    c6.metric("瓶颈", zh_device_name(bottleneck))
    st.caption(
        f"实验 {record.get('experiment_id')} · 引擎 {record.get('factory_engine')} · "
        f"时长 {record.get('duration_min')} 分钟 · 种子 {(record.get('config') or {}).get('seed')} · "
        "全部指标来自实际仿真，不是真实工厂实测。"
    )
    left, right = st.columns([2, 3])
    with left:
        st.markdown("**设备综合效率分解**")
        st.plotly_chart(
            go.Figure(
                go.Bar(
                    x=["可用率", "性能", "质量", "设备综合效率"],
                    y=[oee.get("availability", 0), oee.get("performance", 0), oee.get("quality", 0), oee.get("oee", 0)],
                    marker_color=["#2a7f8f", "#4f9d6b", "#c08a3e", "#1f4b7a"],
                )
            ).update_layout(height=300, yaxis_range=[0, 1], margin=dict(l=10, r=10, t=10, b=10)),
            width="stretch",
            key=f"oee_{record.get('experiment_id')}",
        )
    with right:
        st.markdown("**设备利用率与状态**")
        metrics = record.get("device_metrics") or []
        if metrics:
            frame = pd.DataFrame(
                [
                    {
                        "设备": m.get("device_id"),
                        "名称": zh_device_name(m.get("name")),
                        "类型": zh_device_type(m.get("simulation_type")),
                        "状态": SCENE_STATE_LABELS.get(m.get("state"), m.get("state")),
                        "利用率": round(float(m.get("utilization", 0)) * 100, 1),
                        "队列": m.get("queue"),
                        "处理数": m.get("processed"),
                        "故障次数": m.get("failure_count"),
                        "能耗 kWh": m.get("energy_kwh"),
                    }
                    for m in metrics
                ]
            )
            st.dataframe(frame, hide_index=True, width="stretch", height=300)
    st.markdown("**仿真中的设备状态**")
    _render_scene_3d(scene, record.get("device_states") or [], key_prefix=f"record_{record.get('experiment_id')}")
    history = record.get("flow_history") or []
    if history:
        st.markdown("**产量与在制品变化**")
        frame = pd.DataFrame(history)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=frame["minute"], y=frame["good_units"], name="累计良品", line=dict(color="#1f8a4c")), secondary_y=False)
        fig.add_trace(go.Scatter(x=frame["minute"], y=frame["wip"], name="在制品", line=dict(color="#c47b13")), secondary_y=True)
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10), xaxis_title="仿真分钟")
        st.plotly_chart(fig, width="stretch", key=f"flow_{record.get('experiment_id')}")
    with st.expander("事件日志（最近 200 条）"):
        events = (record.get("events") or [])[-200:]
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "仿真时间": e.get("sim_time"),
                        "事件": e.get("event_type"),
                        "级别": e.get("severity"),
                        "设备": e.get("source_id"),
                        "说明": e.get("message"),
                    }
                    for e in events
                ]
            ),
            hide_index=True,
            width="stretch",
            height=280,
        )


def page_scene_editor() -> None:
    st.markdown("## 场景编辑器 · OIP 三维建模")
    st.caption(
        "在平台内直接打开 OIP 三维编辑器，完成设备的放置、移动、旋转、吸附与属性设置；"
        "Ctrl+S 或左下角「保存」会把工程同步为统一场景。全部指标仍以 Python 仿真为准。"
    )
    project = current_project()
    if project is None:
        return
    client = _scene_client()
    if client is None:
        return
    scene = _scene_pick(project["project_id"], client, "scene_editor_page_scene")
    if scene is None:
        return
    _render_editor_section(project, client, scene, expand_editor=True)
    st.divider()
    if st.button("进入采集与仿真", type="primary"):
        goto_page("建模分析工作台")


def _version_diff_summary(
    current_devices: list[dict[str, Any]], old_devices: list[dict[str, Any]]
) -> tuple[list[str], list[str], list[tuple[str, list[str]]]]:
    cur = {d.get("id"): d for d in current_devices if d.get("id")}
    old = {d.get("id"): d for d in old_devices if d.get("id")}
    added = [cur[key].get("name") or key for key in sorted(cur.keys() - old.keys())]
    removed = [old[key].get("name") or key for key in sorted(old.keys() - cur.keys())]
    changed: list[tuple[str, list[str]]] = []
    for key in sorted(cur.keys() & old.keys()):
        before = cur[key].get("params") or {}
        after = old[key].get("params") or {}
        diffs = [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
        if diffs:
            changed.append((cur[key].get("name") or key, diffs))
    return added, removed, changed


def _render_version_history(project_id: str, scene_id: str, client: SceneClient) -> None:
    with st.expander("版本历史（查看差异 / 恢复）", expanded=False):
        try:
            versions = client.versions(project_id, scene_id)
        except SceneClientError as exc:
            st.error(f"读取版本失败：{exc}")
            return
        if not versions:
            st.caption("还没有历史版本。保存场景后会自动生成。")
            return
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "版本": f"v{v.get('version')}",
                        "时间": v.get("created_at"),
                        "设备": v.get("devices"),
                        "连接": v.get("connections"),
                        "哈希": str(v.get("content_hash") or "")[:10],
                    }
                    for v in versions
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        numbers = [int(v["version"]) for v in versions]
        chosen = st.selectbox("查看版本", numbers, format_func=lambda v: f"v{v}", key=f"ver_pick_{scene_id}")
        try:
            old = client.version_detail(project_id, scene_id, chosen)
            current_devices = client.devices(project_id, scene_id)
        except SceneClientError as exc:
            st.error(f"读取版本详情失败：{exc}")
            return
        added, removed, changed = _version_diff_summary(current_devices, old.get("devices") or [])
        st.markdown(
            f"**当前场景相比 v{chosen}**：新增 {len(added)} 台 / 移除 {len(removed)} 台 / 参数变化 {len(changed)} 台"
        )
        if added:
            st.caption("新增：" + "、".join(added[:8]))
        if removed:
            st.caption("移除：" + "、".join(removed[:8]))
        for name, keys in changed[:6]:
            st.caption(f"参数变化：{name} → {', '.join(keys[:8])}")
        confirm = st.checkbox(
            f"确认恢复到 v{chosen}（会生成新版本，历史不丢失）",
            key=f"ver_confirm_{scene_id}_{chosen}",
        )
        if confirm and st.button("恢复此版本", type="primary", key=f"ver_restore_{scene_id}"):
            try:
                saved = client.restore_version(project_id, scene_id, chosen)
            except SceneClientError as exc:
                st.error(f"恢复失败：{exc}")
            else:
                st.success(f"已从 v{chosen} 恢复，当前 v{saved.get('version')}")
                st.session_state["scene_editor_nonce"] = time.time()
                st.rerun()


def _render_editor_section(
    project: dict[str, Any],
    client: SceneClient,
    scene: dict[str, Any],
    expand_editor: Optional[bool] = None,
    show_editor: bool = True,
) -> None:
    if show_editor:
        st.caption(
            "在三维编辑器里从空白地面搭建工厂：目录放置、移动/旋转、吸附对齐、属性参数、保存。"
            "在编辑器内按 Ctrl+S 或点击左下角「保存」，即可将当前布局和设备设置保存到平台。"
        )
        st.caption(
            "提示：编辑器打开后会自动运行三维物理动画（箱子沿皮带流动），可用顶部暂停/停止控制；"
            "该动画仅作视觉演示，不代表生产 KPI，生产数据以 Python 仿真为准。"
            "「六设备产线（可动画演示）」场景的皮带首尾相接，动画效果最好。"
        )
        st.caption("「装饰」分类中是外部导入模型（工具车、人字梯等），仅用于三维观感，不参与生产仿真；放置后平台标记为“装饰（仅三维）”。")
    else:
        st.caption("三维建模统一在「场景编辑器」页完成；这里管理场景、查看设备/连接与校验结果。")
    with st.expander("新建 / 管理场景", expanded=False):
        with st.form("scene_create_form", clear_on_submit=True):
            new_name = st.text_input("场景名称", value="自建工厂场景")
            if st.form_submit_button("新建空白场景", type="primary"):
                try:
                    created = client.create_scene(project["project_id"], new_name)
                    st.session_state["_pending_scene_id"] = created["scene_id"]
                    st.session_state["scene_editor_nonce"] = time.time()
                    st.success(f"已创建场景 {created['scene_id']}")
                    st.rerun()
                except SceneClientError as exc:
                    st.error(str(exc))
    project_id = project["project_id"]
    scene_id = scene["scene_id"]
    with st.expander("导入 OIP 工程 JSON（示例场景 / 备份）", expanded=False):
        upload = st.file_uploader("OIP 工程 JSON 文件", type=["json"], key=f"scene_upload_{scene_id}")
        if upload is not None and st.button("导入并转换", key=f"scene_import_{scene_id}"):
            try:
                payload = json.loads(upload.getvalue().decode("utf-8"))
                result = client.import_oip(project_id, scene_id, payload)
                st.success(f"已导入：v{result['version']} · {len(result['devices'])} 设备 · {len(result['connections'])} 连接")
                st.session_state["scene_editor_nonce"] = time.time()
                st.rerun()
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(f"文件不是有效 JSON：{exc}")
            except SceneClientError as exc:
                st.error(str(exc))
        st.download_button(
            "下载当前统一场景 JSON",
            json.dumps(scene, ensure_ascii=False, indent=2),
            file_name=f"{scene_id}-v{scene.get('version')}.json",
            mime="application/json",
            key=f"scene_download_{scene_id}",
        )
    controls = st.columns([1.2, 1.2, 1.4, 3])
    if controls[0].button("重载编辑器", type="primary"):
        st.session_state["scene_editor_nonce"] = time.time()
    if controls[1].button("刷新场景列表"):
        st.rerun()
    confirm_delete = controls[3].checkbox("确认删除当前场景（不可恢复）", key=f"scene_del_{scene_id}")
    if confirm_delete and controls[3].button("删除场景"):
        try:
            client.delete_scene(project_id, scene_id)
            st.session_state["_pending_scene_id"] = ""
            st.session_state.pop("scene_editor_nonce", None)
            st.rerun()
        except SceneClientError as exc:
            st.error(str(exc))

    if show_editor:
        try:
            oip = client.oip_url(project_id, scene_id)
        except SceneClientError as exc:
            st.error(f"OIP 编辑器启动失败：{exc}")
            return
        nonce = st.session_state.get("scene_editor_nonce", 0)
        hook_param = "" if "hook=1" in oip["url"] else "&hook=1"
        app_url = f"{oip['url']}{hook_param}&_r={nonce}&autoplay=1&live=1"
        # 先经过同源 reset.html：注销 OIP 旧 Service Worker 并清缓存，避免一直加载旧版桥接脚本。
        oip_base = oip["url"].split("/app/")[0]
        editor_url = f"{oip_base}/reset.html?to={quote(app_url, safe='')}"
        expanded_flag = not bool(scene.get("devices")) if expand_editor is None else expand_editor
        with st.expander("三维产线编辑器", expanded=expanded_flag):
            st.caption(f"当前场景版本 v{scene.get('version')}。打开后自动播放三维动画。")
            st.iframe(editor_url, height=780)
            st.caption("「保存」更新当前场景。「另存为」将当前编辑内容保存成同一项目下的新场景。保存后点「刷新场景列表」查看。")
    else:
        if st.button("打开场景编辑器", type="primary", key=f"ws_open_editor_{scene_id}"):
            goto_page("场景编辑器")

    if show_editor:
        _render_version_history(project_id, scene_id, client)

    try:
        scene = client.get_scene(project_id, scene_id)
        devices = client.devices(project_id, scene_id)
        conns = client.connections(project_id, scene_id)
    except SceneClientError as exc:
        st.error(str(exc))
        return
    errors = (scene.get("validation") or {}).get("errors", [])
    warnings = (scene.get("validation") or {}).get("warnings", [])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("场景版本", f"v{scene.get('version')}")
    m2.metric("设备", len(devices))
    m3.metric("设备连接", len(conns.get("connections") or []))
    m4.metric("校验问题", f"{len(errors)} 错误 / {len(warnings)} 警告")

    tab_devices, tab_connections, tab_validation = st.tabs(["设备清单", "物料连接", "校验结果"])
    with tab_devices:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "编号": d["id"],
                        "名称": zh_device_name(d.get("name")),
                        "类型": zh_device_type(d.get("type")),
                        "仿真类型": zh_device_type(d.get("simulation_type")),
                        "位置": ", ".join(f"{float(v):.2f}" for v in d.get("position", [])),
                        "旋转角Y": round(float((d.get("rotation") or {}).get("yaw", 0)), 4),
                        "参数摘要": ", ".join(f"{k}={v}" for k, v in list((d.get("params") or {}).items())[:6]),
                    }
                    for d in devices
                ]
            ),
            hide_index=True,
            width="stretch",
            height=320,
        )
        selected_device = st.selectbox("查看设备完整参数", [d["id"] for d in devices], key=f"scene_dev_{scene_id}") if devices else None
        if selected_device:
            device = next(d for d in devices if d["id"] == selected_device)
            with st.expander("查看原始参数（进阶）"):
                st.json(device.get("params") or {})
    with tab_connections:
        st.markdown("连接决定产品依次经过哪些设备。移动设备位置后，请检查连接是否需要调整。")
        current = conns.get("connections") or []
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "连接": c["id"],
                        "源": f"{c['source_device']}.{c['source_port']}",
                        "目标": f"{c['target_device']}.{c['target_port']}",
                        "类型": zh_connection_type(c["connection_type"]),
                        "来源": CONNECTION_SOURCE_ZH.get((c.get("metadata") or {}).get("source", "manual"), (c.get("metadata") or {}).get("source", "manual")),
                    }
                    for c in current
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        if len(devices) >= 2:
            with st.form("scene_add_connection", clear_on_submit=True):
                device_labels = {d["id"]: f"{zh_device_name(d.get('name'))} ({d['id']})" for d in devices}
                c1, c2, c3, c4 = st.columns(4)
                source_id = c1.selectbox("源设备", list(device_labels), format_func=lambda i: device_labels[i])
                source_ports = [p["id"] for p in next(d for d in devices if d["id"] == source_id).get("ports", []) if p.get("direction") == "output"]
                target_id = c2.selectbox("目标设备", list(device_labels), format_func=lambda i: device_labels[i], index=min(1, len(device_labels) - 1))
                target_ports = [p["id"] for p in next(d for d in devices if d["id"] == target_id).get("ports", []) if p.get("direction") == "input"]
                source_port = c3.selectbox("源端口", source_ports or ["out"])
                target_port = c4.selectbox("目标端口", target_ports or ["in"])
                conn_type = st.selectbox("连接类型", ["material_flow", "control", "data", "power"], format_func=zh_connection_type)
                if st.form_submit_button("添加连接"):
                    payload = current + [
                        {
                            "source_device": source_id,
                            "source_port": source_port,
                            "target_device": target_id,
                            "target_port": target_port,
                            "connection_type": conn_type,
                            "metadata": {"source": "manual_ui"},
                        }
                    ]
                    try:
                        result = client.set_connections(project_id, scene_id, payload)
                        st.success(f"连接已保存；校验错误 {len((result.get('validation') or {}).get('errors', []))} 条。")
                        st.rerun()
                    except SceneClientError as exc:
                        st.error(str(exc))
        if current:
            delete_id = st.selectbox("删除连接", [c["id"] for c in current], key=f"scene_delconn_{scene_id}")
            if st.button("删除所选连接"):
                try:
                    client.set_connections(project_id, scene_id, [c for c in current if c["id"] != delete_id])
                    st.rerun()
                except SceneClientError as exc:
                    st.error(str(exc))
        c1, c2 = st.columns(2)
        if c1.button("重新推导几何候选（不采用）"):
            try:
                client.derive_connections(project_id, scene_id, adopt=False)
                st.success("已更新几何候选；设备连接保持不变。")
                st.rerun()
            except SceneClientError as exc:
                st.error(str(exc))
        if c2.button("采用几何候选为初始连接"):
            try:
                result = client.derive_connections(project_id, scene_id, adopt=True)
                st.success(f"已采用 {len(result.get('candidates') or [])} 条候选连接。")
                st.rerun()
            except SceneClientError as exc:
                st.error(str(exc))
    with tab_validation:
        if not errors and not warnings:
            st.success("连接校验通过：设备/端口存在、方向兼容、从 Spawner 可到达 Despawner、无物料流环路。")
        for issue in errors:
            st.error(f"[{issue.get('code')}] {issue.get('message')}")
        for issue in warnings:
            st.warning(f"[{issue.get('code')}] {issue.get('message')}")


def page_scene_simulation() -> None:
    st.markdown("## 场景生产仿真")
    project = current_project()
    if project is None:
        return
    client = _scene_client()
    if client is None:
        return
    scene = _scene_pick(project["project_id"], client, "scene_sim_scene")
    if scene is None:
        return
    _render_simulation_section(project, client, scene)
    st.divider()
    if st.button("进入数据分析", type="primary"):
        goto_page("建模分析工作台")


def _render_simulation_section(project: dict[str, Any], client: SceneClient, scene: dict[str, Any]) -> None:
    project_id, scene_id = project["project_id"], scene["scene_id"]
    errors = (scene.get("validation") or {}).get("errors", [])
    if errors:
        st.error("场景存在连接/设备校验错误，仿真引擎会拒绝运行：")
        for issue in errors:
            st.error(f"[{issue.get('code')}] {issue.get('message')}")
        return
    tab_offline, tab_live = st.tabs(["快速仿真", "实时仿真"])
    with tab_offline:
        with st.form("scene_offline_run"):
            c1, c2 = st.columns(2)
            duration_min = c1.number_input("仿真时长（分钟）", min_value=1, max_value=180, value=10, step=1)
            seed = c2.number_input("随机种子", min_value=0, max_value=2_147_483_647, value=42, step=1)
            if st.form_submit_button("运行离线仿真", type="primary"):
                with st.spinner("正在使用 生产过程模拟运行..."):
                    try:
                        record = client.run_simulation(project_id, scene_id, duration_sec=float(duration_min) * 60, seed=int(seed))
                        st.session_state[f"scene_record_{scene_id}"] = record
                        st.session_state[f"scene_exp_{scene_id}"] = record["experiment_id"]
                        st.rerun()
                    except SceneClientError as exc:
                        st.error(str(exc))
        record = st.session_state.get(f"scene_record_{scene_id}")
        if not record:
            try:
                candidates = [
                    r
                    for r in client.experiments(project_id, scene_id)
                    if r.get("kind") == "factory" and r.get("source") == "scene_engine" and not r.get("live")
                ]
                latest = max(candidates, key=lambda r: r.get("created_at") or "", default=None)
                if latest:
                    record = client.experiment(project_id, latest["experiment_id"])
                    st.session_state[f"scene_record_{scene_id}"] = record
            except SceneClientError:
                record = None
        if record:
            _render_scene_record(record, scene)
        else:
            st.info("尚未运行实验。参数变化后重新运行，结果会真实变化。")
    with tab_live:
        sim_key = f"scene_live_{scene_id}"
        sim_id = st.session_state.get(sim_key)
        if not sim_id:
            with st.form("scene_live_start"):
                c1, c2, c3 = st.columns(3)
                duration_min = c1.number_input("实时仿真时长（分钟）", min_value=1, max_value=120, value=10, step=1)
                speed = c2.number_input("倍速", min_value=0.5, max_value=20.0, value=2.0, step=0.5)
                seed = c3.number_input("随机种子", min_value=0, max_value=2_147_483_647, value=42, step=1)
                if st.form_submit_button("启动实时仿真", type="primary"):
                    try:
                        snap = client.start_live(project_id, scene_id, duration_sec=float(duration_min) * 60, seed=int(seed), speed=float(speed))
                        st.session_state[sim_key] = snap["sim_id"]
                        st.rerun()
                    except SceneClientError as exc:
                        st.error(str(exc))
        else:
            status = client.sim(sim_id)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("状态", SIMULATION_STATUS_ZH.get(status.get("status"), status.get("status")))
            c2.metric("仿真时间", f"{float(status.get('sim_time_sec', 0)):.1f}s")
            c3.metric("良品", status.get("kpis", {}).get("good_units"))
            c4.metric("在制品", status.get("kpis", {}).get("wip"))
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("暂停"):
                client.sim_action(sim_id, "pause"); st.rerun()
            if b2.button("继续"):
                client.sim_action(sim_id, "resume"); st.rerun()
            if b3.button("停止"):
                client.sim_action(sim_id, "stop"); st.rerun()
            if b4.button("清除并新建"):
                st.session_state.pop(sim_key, None); st.rerun()

            @st.fragment(run_every=2.0)
            def _live_scene_panel() -> None:
                try:
                    snap = client.sim(sim_id)
                except SceneClientError as exc:
                    st.warning(str(exc))
                    return
                st.caption(
                    f"sim {sim_id} · 状态 {snap.get('status')} · 仿真时间 {float(snap.get('sim_time_sec', 0)):.1f}s · "
                    f"倍速 {snap.get('speed')} · 更新时间 {snap.get('timestamp')}"
                )
                _render_scene_3d(scene, snap.get("device_states") or [], key_prefix=f"live_{sim_id}")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "设备": d.get("device_id"),
                                "状态": SCENE_STATE_LABELS.get(d.get("state"), d.get("state")),
                                "队列": d.get("queue"),
                                "处理数": d.get("processed"),
                                "利用率": f"{float(d.get('utilization', 0)) * 100:.1f}%",
                                "时间戳": d.get("timestamp"),
                            }
                            for d in snap.get("device_states") or []
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                    height=260,
                )
                if snap.get("status") in ("completed", "stopped") and snap.get("finalized"):
                    st.success("实时仿真已结束，完整结果已保存到实验历史。")

            _live_scene_panel()


def page_scene_analysis() -> None:
    st.markdown("## 场景数据分析与 AI 优化")
    project = current_project()
    if project is None:
        return
    client = _scene_client()
    if client is None:
        return
    scene = _scene_pick(project["project_id"], client, "scene_analysis_scene")
    if scene is None:
        return
    result = _render_analysis_section(project, client, scene)
    if result is None:
        return
    st.divider()
    _render_optimization_section(project, client, scene, *result)


def _render_analysis_section(
    project: dict[str, Any], client: SceneClient, scene: dict[str, Any], *, allow_run: bool = True
) -> Optional[tuple[str, dict[str, Any]]]:
    project_id, scene_id = project["project_id"], scene["scene_id"]
    try:
        experiments = client.experiments(project_id, scene_id)
    except SceneClientError as exc:
        st.error(str(exc))
        return None
    factory_exps = [
        r
        for r in experiments
        if r.get("kind") == "factory"
        and r.get("factory_engine") == "scene_discrete_event"
        and r.get("source") == "scene_engine"
        and not r.get("live")
        and "实时" not in str(r.get("label") or "")
    ]
    if not factory_exps:
        st.info("该场景还没有生产实验数据。运行一次仿真即可在本页直接进入分析。")
        if allow_run and st.button(
            "运行一次仿真采集数据（10 分钟 / 种子 42）", type="primary", key=f"ws_collect_{scene_id}"
        ):
            with st.spinner("正在使用 生产过程模拟运行..."):
                try:
                    record = client.run_simulation(project_id, scene_id, duration_sec=600.0, seed=42)
                    st.session_state[f"scene_record_{scene_id}"] = record
                    st.session_state[f"scene_exp_{scene_id}"] = record["experiment_id"]
                    st.rerun()
                except SceneClientError as exc:
                    st.error(str(exc))
        return None
    exp_ids = [r["experiment_id"] for r in factory_exps]
    exp_labels = {r["experiment_id"]: f"{r['created_at']} · v{r.get('scene_version')} · 良品 {(r.get('kpis') or {}).get('good_units')}" for r in factory_exps}
    selected = st.selectbox("选择仿真记录", exp_ids, format_func=lambda i: exp_labels.get(i, i), key=f"scene_exp_{scene_id}")
    try:
        analysis = client.analysis(project_id, scene_id, selected)
    except SceneClientError as exc:
        st.error(str(exc))
        return None
    kpis = analysis.get("kpis") or {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("瓶颈", zh_device_name(analysis.get("bottleneck_name") or analysis.get("bottleneck")))
    c2.metric("良品", kpis.get("good_units"))
    c3.metric("在制品", kpis.get("wip"))
    c4.metric("产能", f"{float(kpis.get('throughput_uph') or 0):.1f} 件/小时")
    c5.metric("设备综合效率", f"{float(kpis.get('oee') or 0) * 100:.1f}%")
    c6.metric("缺陷率", f"{float(kpis.get('defect_rate') or 0) * 100:.2f}%")
    for line in analysis.get("evidence") or []:
        st.caption(f"· {line}")
    ranking = analysis.get("ranking") or []
    if ranking:
        frame = pd.DataFrame(
            [
                {"设备": d.get("device_id"), "名称": zh_device_name(d.get("name")), "利用率": round(float(d.get("utilization", 0)) * 100, 1), "队列": d.get("queue"), "阻塞秒": d.get("blocked_sec")}
                for d in ranking
            ]
        )
        left, right = st.columns([3, 2])
        with left:
            st.plotly_chart(
                go.Figure(go.Bar(x=frame["名称"], y=frame["利用率"], marker_color="#2a7f8f")).update_layout(height=300, yaxis_title="利用率 %", margin=dict(l=10, r=10, t=10, b=10)),
                width="stretch",
                key=f"util_{selected}",
            )
        with right:
            st.dataframe(frame, hide_index=True, width="stretch", height=300)

    record = next((r for r in factory_exps if r["experiment_id"] == selected), {})
    st.caption(
        f"数据链路：场景 {scene.get('name')} v{scene.get('version')} → 实验 {selected}"
        f"（采集于 {record.get('created_at') or '—'}，来源 {record.get('source') or 'scene_engine'}）"
    )
    st.markdown("**本次仿真的设备状态**")
    _render_scene_3d(
        scene,
        [
            {
                "device_id": m.get("device_id"),
                "state": m.get("state"),
                "queue": m.get("queue"),
                "utilization": m.get("utilization"),
            }
            for m in analysis.get("device_metrics") or []
        ],
        key_prefix=f"analysis_{selected}",
    )
    return selected, kpis


def _render_optimization_section(
    project: dict[str, Any], client: SceneClient, scene: dict[str, Any], selected: str, kpis: dict[str, Any]
) -> None:
    project_id, scene_id = project["project_id"], scene["scene_id"]
    try:
        exp_records = [
            r
            for r in client.experiments(project_id, scene_id)
            if r.get("kind") == "factory"
            and r.get("factory_engine") == "scene_discrete_event"
            and r.get("source") == "scene_engine"
            and not r.get("live")
        ]
    except SceneClientError:
        exp_records = []
    exp_ids = [r["experiment_id"] for r in exp_records]
    st.markdown("### 质检任务分配")
    edge_key = f"scene_edge_{selected}"
    if st.button("运行三种调度策略对比"):
        with st.spinner("正在生成任务并比较三种分配方案…"):
            try:
                st.session_state[edge_key] = client.run_edge(project_id, scene_id, selected)
            except SceneClientError as exc:
                st.error(str(exc))
    edge = st.session_state.get(edge_key)
    if edge:
        mapping = edge.get("mapping") or {}
        st.caption(f"共 {mapping.get('task_count')} 项任务，三种方案使用同一批任务。")
        if edge.get("metrics"):
            rows = strategy_ranking(edge)
            st.dataframe(pd.DataFrame(rows, columns=STRATEGY_COLUMNS).rename(columns=STRATEGY_COLUMNS), hide_index=True, width="stretch")

    st.divider()
    st.markdown("### 改进建议（逐个方案试跑）")
    opt_key = f"scene_opt_{selected}"
    c1, c2 = st.columns([1, 3])
    if c1.button("生成并评估优化建议", type="primary"):
        with st.spinner("正在重跑基线/候选参数..."):
            try:
                st.session_state[opt_key] = client.optimize(
                    project_id, scene_id, experiment_id=selected, duration_sec=float(kpis.get("runtime_sec") or 600), max_candidates=3
                )
            except SceneClientError as exc:
                st.error(str(exc))
    opt = st.session_state.get(opt_key)
    if opt:
        best = opt.get("best")
        if best:
            st.success(f"最佳候选：{best.get('title')}（评分 {best.get('score')}）")
        for ev in opt.get("evaluations") or []:
            if ev.get("status") != "ok":
                st.warning(f"{ev.get('recipe_id')}：{ev.get('message')}")
                continue
            with st.expander(ev.get("title") or ev.get("recipe_id"), expanded=ev is best):
                pct = ev.get("delta_pct") or {}
                st.write(
                    f"产能 {pct.get('throughput_uph', 0):+.2f}% · OEE {pct.get('oee', 0):+.2f}% · "
                    f"周期 {pct.get('avg_lead_time_sec', 0):+.2f}% · WIP {pct.get('wip', 0):+.2f}%"
                )
                st.json(ev.get("patch") or {})
        if best and st.button("应用最佳建议为新的场景版本（并重跑）", type="primary"):
            try:
                applied = client.apply_optimization(
                    project_id,
                    scene_id,
                    patch=best["patch"],
                    duration_sec=float(kpis.get("runtime_sec") or 600),
                    seed=42,
                )
                st.session_state[f"scene_record_{scene_id}"] = applied["experiment"]
                st.session_state[f"scene_exp_{scene_id}"] = applied["experiment"]["experiment_id"]
                st.session_state["ws_optimized"] = (
                    f"已应用优化：场景 v{applied['scene']['version']}，新实验 {applied['experiment']['experiment_id']}。"
                )
                st.rerun()
            except SceneClientError as exc:
                st.error(str(exc))

    st.divider()
    st.markdown("### 试改设备参数")
    devices = client.devices(project_id, scene_id)
    numeric_devices = [d for d in devices if any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (d.get("params") or {}).values())]
    if numeric_devices:
        with st.form("scene_whatif"):
            device_labels = {d["id"]: f"{zh_device_name(d.get('name'))} ({d['id']})" for d in numeric_devices}
            c1, c2, c3 = st.columns(3)
            dev_id = c1.selectbox("设备", list(device_labels), format_func=lambda i: device_labels[i])
            params = (next(d for d in numeric_devices if d["id"] == dev_id).get("params") or {})
            numeric_keys = [k for k, v in params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
            param_key = c2.selectbox("参数", numeric_keys)
            value = c3.number_input("新值", value=float(params.get(param_key, 0)), format="%.4f")
            if st.form_submit_button("试运行"):
                try:
                    result = client.what_if(
                        project_id,
                        scene_id,
                        patch={"device_params": {dev_id: {param_key: value}}},
                        experiment_id=selected,
                        duration_sec=float(kpis.get("runtime_sec") or 600),
                        seed=42,
                    )
                    st.session_state[f"scene_whatif_{selected}"] = result
                except SceneClientError as exc:
                    st.error(str(exc))
        whatif = st.session_state.get(f"scene_whatif_{selected}")
        if whatif:
            comp = whatif.get("comparison") or {}
            st.caption(f"结论：{comp.get('verdict')}（{comp.get('criteria')}）")
            st.dataframe(pd.DataFrame(comp.get("rows") or [], columns=COMPARISON_COLUMNS).rename(columns=COMPARISON_COLUMNS), hide_index=True, width="stretch")

    st.divider()
    st.markdown("### 报告与实验对比")
    edge_id = (edge or {}).get("experiment_id")
    opt_id = (opt or {}).get("optimization_id")
    if st.button("生成场景仿真报告"):
        try:
            report = client.report(
                project_id, scene_id, experiment_id=selected, edge_experiment_id=edge_id, optimization_id=opt_id
            )
            st.session_state[f"scene_report_{selected}"] = report
        except SceneClientError as exc:
            st.error(str(exc))
    report = st.session_state.get(f"scene_report_{selected}")
    if report:
        st.success(f"报告已生成：{report.get('report_id')}")
        d1, d2 = st.columns(2)
        d1.download_button("下载 Markdown", report.get("markdown") or "", file_name=f"{report.get('report_id')}.md")
        d2.download_button("下载 HTML", report.get("html") or "", file_name=f"{report.get('report_id')}.html", mime="text/html")
        with st.expander("报告预览", expanded=True):
            st.markdown(report.get("markdown") or "")
    if len(exp_ids) >= 2:
        c1, c2 = st.columns(2)
        baseline_id = c1.selectbox("基线实验", exp_ids, key=f"cmp_a_{scene_id}")
        candidate_id = c2.selectbox("对比实验", exp_ids, index=1, key=f"cmp_b_{scene_id}")
        if st.button("比较两个实验"):
            try:
                comparison = client.compare(project_id, scene_id, baseline_id, candidate_id)
                st.caption(f"结论：{comparison.get('verdict')}")
                st.dataframe(pd.DataFrame(comparison.get("rows") or [], columns=COMPARISON_COLUMNS).rename(columns=COMPARISON_COLUMNS), hide_index=True, width="stretch")
            except SceneClientError as exc:
                st.error(str(exc))


def _ws_cleanup_scene(scene_id: str, client: Optional[SceneClient] = None) -> None:
    sim_id = st.session_state.pop(f"scene_live_{scene_id}", None)
    if sim_id and client is not None:
        try:
            client.sim_action(sim_id, "stop")
        except Exception:  # noqa: BLE001 - 清理旧实时仿真失败不影响主流程
            pass
    for key in [
        f"scene_record_{scene_id}",
        f"scene_exp_{scene_id}",
        f"scene_upload_{scene_id}",
        f"scene_dev_{scene_id}",
        f"scene_del_{scene_id}",
        f"scene_delconn_{scene_id}",
    ]:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if key.startswith(("scene_edge_", "scene_opt_", "scene_whatif_", "scene_report_", "cmp_a_", "cmp_b_")):
            st.session_state.pop(key, None)


def _render_ws_steps(
    has_devices: bool,
    has_experiment: bool,
    has_optimization: bool = False,
    has_report: bool = False,
) -> None:
    active = 4 if has_report else (3 if has_optimization else (2 if has_experiment else (1 if has_devices else 0)))
    steps = [
        ("① 建模", "场景编辑器 / 设备"),
        ("② 采集数据", "Python 离散事件仿真"),
        ("③ 数据分析", "瓶颈 / 利用率 / OEE"),
        ("④ 优化归档", "EdgeCoder / AI 建议"),
        ("⑤ 报告", "HTML / Markdown"),
    ]
    parts = []
    for index, (title, desc) in enumerate(steps):
        css = "ec-step active" if index == active else ("ec-step done" if index < active else "ec-step")
        parts.append(f'<div class="{css}"><b>{title}</b>{desc}</div>')
    st.markdown('<div class="ec-steps">' + "".join(parts) + "</div>", unsafe_allow_html=True)


def _workspace_scene(project: dict[str, Any], client: SceneClient) -> Optional[dict[str, Any]]:
    _apply_pending_scene_selection()
    project_id = project["project_id"]
    try:
        scenes = client.list_scenes(project_id)
    except SceneClientError as exc:
        st.error(str(exc))
        return None
    if not scenes:
        st.info("当前项目还没有自建场景。先新建一个空白场景，再进入「场景编辑器」页搭建产线。")
        with st.form("ws_scene_create_form", clear_on_submit=True):
            new_name = st.text_input("场景名称", value="自建工厂场景")
            if st.form_submit_button("新建空白场景", type="primary"):
                try:
                    created = client.create_scene(project_id, new_name)
                except SceneClientError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["_pending_scene_id"] = created["scene_id"]
                    st.session_state.pop("ws_scene_prev", None)
                    st.rerun()
        return None

    options = [s["scene_id"] for s in scenes]
    labels = {
        s["scene_id"]: f"{s['name']} · v{s['version']} · {s['devices']} 设备 · {s['connections']} 连接"
        for s in scenes
    }
    previous = st.session_state.get("ws_scene_prev")
    default_index = max(range(len(scenes)), key=lambda i: int(scenes[i].get("devices") or 0))
    index_kwargs = {} if "scene_editor_scene" in st.session_state else {"index": default_index}
    selected = st.selectbox("当前场景", options, format_func=lambda sid: labels.get(sid, sid), key="scene_editor_scene", **index_kwargs)
    if previous and previous != selected:
        _ws_cleanup_scene(previous, client)
    st.session_state["ws_scene_prev"] = selected
    try:
        return client.get_scene(project_id, selected)
    except SceneClientError as exc:
        st.error(str(exc))
        return None


def page_workspace() -> None:
    st.markdown("## 建模分析工作台")
    st.caption(
        "在同一个页面完成整条链路：场景与设备 → 仿真采集数据 → 数据分析 → 优化归档。"
        "三维建模在「场景编辑器」页完成，这里不再内嵌编辑器；所有指标来自 Python 离散事件仿真，不是真实工厂实测。"
    )
    project = current_project()
    if project is None:
        return
    client = _scene_client()
    if client is None:
        return
    project_id = project["project_id"]

    if st.session_state.get("ws_project") != project_id:
        for key in list(st.session_state.keys()):
            if key.startswith("scene_live_"):
                sim_id = st.session_state.pop(key, None)
                if sim_id:
                    try:
                        client.sim_action(sim_id, "stop")
                    except Exception:  # noqa: BLE001
                        pass
        st.session_state["ws_project"] = project_id
        st.session_state.pop("ws_scene_prev", None)

    scene = _workspace_scene(project, client)
    if scene is None:
        return
    scene_id = scene["scene_id"]
    try:
        devices = client.devices(project_id, scene_id)
        conns = client.connections(project_id, scene_id)
        experiments = client.experiments(project_id, scene_id)
    except SceneClientError as exc:
        st.error(str(exc))
        return
    factory_exps = [
        r
        for r in experiments
        if r.get("kind") == "factory"
        and r.get("factory_engine") == "scene_discrete_event"
        and r.get("source") == "scene_engine"
        and not r.get("live")
    ]
    latest = max(factory_exps, key=lambda r: r.get("created_at") or "", default=None)
    has_optimization = any("优化" in str(r.get("label") or "") for r in factory_exps)
    try:
        reports = client.reports(project_id, scene_id)
    except SceneClientError:
        reports = []
    _render_ws_steps(bool(devices), latest is not None, has_optimization, bool(reports))

    errors = (scene.get("validation") or {}).get("errors", [])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("场景版本", f"v{scene.get('version')}")
    m2.metric("设备", len(devices))
    m3.metric("设备连接", len(conns.get("connections") or []))
    m4.metric("校验问题", f"{len(errors)} 错误")
    chain = f"数据链路：场景 {scene.get('name')} v{scene.get('version')}"
    if latest:
        chain += f" → 实验 {latest['experiment_id']}（采集于 {latest.get('created_at') or '—'}）"
    else:
        chain += " → 尚未采集数据"
    st.caption(chain)
    demo_notice = st.session_state.pop("ws_demo_notice", None)
    if demo_notice:
        st.success(demo_notice)
    notice = st.session_state.pop("ws_optimized", None)
    if notice:
        st.success(notice)

    st.divider()
    st.markdown("### ① 建模 · 场景与设备")
    _render_editor_section(project, client, scene, show_editor=False)

    st.divider()
    st.markdown("### ② 采集数据 · 生产仿真")
    _render_simulation_section(project, client, scene)

    st.divider()
    st.markdown("### ③ 数据分析 · 瓶颈 / 利用率 / OEE")
    result = _render_analysis_section(project, client, scene)

    st.divider()
    st.markdown("### ④ 比较方案、生成报告")
    if result is None:
        st.info("完成 ② 采集数据后，这里会显示边缘调度、规则式优化、What-if 与报告。")
    else:
        _render_optimization_section(project, client, scene, *result)


def page_scene_overview() -> None:
    hero(
        "OIP 工厂场景平台",
        "在网页里搭好产线后，可以模拟生产过程，"
        "查看瓶颈、比较质检任务分配方案，并保存实验结果。",
        ["OIP 三维编辑", "Python 生产仿真", "任务分配", "统一实验与报告"],
    )
    project = current_project()
    if project is None:
        return
    if project.get("demo_preset"):
        st.info(
            "已为您准备好视频中的演示案例。直接进入「场景编辑器」查看产线，"
            "或进入「建模分析工作台」查看已保存的仿真结果。"
            "点击侧栏「一键演示」可按视频参数重新仿真（10 分钟，种子 42）。"
        )
    client = _scene_client()
    if client is None:
        return
    try:
        scenes = client.list_scenes(project["project_id"])
    except SceneClientError as exc:
        st.error(str(exc))
        return

    records: list[dict[str, Any]] = []
    for scene in scenes:
        try:
            for record in client.experiments(project["project_id"], scene["scene_id"]):
                records.append({**record, "scene_name": scene["name"]})
        except SceneClientError:
            continue
    offline = [
        r for r in records
        if r.get("kind") == "factory" and r.get("factory_engine") == "scene_discrete_event" and r.get("source") == "scene_engine"
    ]
    edges = [r for r in records if r.get("kind") == "edge"]
    latest = max(offline, key=lambda r: r.get("created_at") or "", default=None)
    latest_edge = max(edges, key=lambda r: r.get("created_at") or "", default=None)
    if latest_edge:
        try:
            latest_edge = client.experiment(project["project_id"], latest_edge["experiment_id"])
        except SceneClientError:
            pass

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("场景", len(scenes))
    m2.metric("设备", sum(int(s.get("devices", 0)) for s in scenes))
    m3.metric("设备连接", sum(int(s.get("connections", 0)) for s in scenes))
    if latest:
        kpis = latest.get("kpis") or {}
        m4.metric("最新良品", kpis.get("good_units", 0))
        m5.metric("最新产能", f"{float(kpis.get('throughput_uph') or 0):.1f} 件/小时")
    else:
        m4.metric("最新实验", "未运行")
        m5.metric("最新产能", "—")

    if not scenes:
        st.info("当前项目还没有工厂场景。先进入「建模分析工作台」创建空白场景，再在 OIP 中搭建产线。")
        if st.button("进入建模分析工作台", type="primary"):
            goto_page("建模分析工作台")
        return

    if latest:
        kpis = latest.get("kpis") or {}
        oee = (kpis.get("oee") or {}).get("oee", 0)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("最新实验", latest.get("experiment_id"))
        c2.metric("在制品", kpis.get("wip", 0))
        c3.metric("设备综合效率", f"{float(oee) * 100:.1f}%")
        c4.metric("缺陷率", f"{float(kpis.get('defect_rate') or 0) * 100:.2f}%")
        if latest_edge:
            dynamic = (latest_edge.get("metrics") or {}).get("dynamic", {})
            c5.metric("动态调度满足率", f"{float(dynamic.get('satisfaction_rate') or 0) * 100:.1f}%")
        else:
            c5.metric("边缘实验", "未运行")
        st.caption(
            f"场景 {latest.get('scene_name')} · v{latest.get('scene_version')} · "
            f"全部指标来自本次 Python 仿真，不是真实工厂实测。"
        )
    else:
        st.info("场景已创建，但还没有生产实验。到「建模分析工作台」② 采集数据运行第一轮仿真。")

    st.markdown("### 工厂场景")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "场景": s["name"],
                    "版本": f"v{s['version']}",
                    "设备": s["devices"],
                    "连接": s["connections"],
                    "校验": f"{s.get('errors', 0)} 错误 / {s.get('warnings', 0)} 警告",
                    "更新时间": s.get("updated_at"),
                }
                for s in scenes
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    a1, a2 = st.columns(2)
    if a1.button("进入建模分析工作台", type="primary"):
        goto_page("建模分析工作台")
    if a2.button("实验历史"):
        goto_page("实验历史")

    st.markdown("### 工作流")
    steps = [
        ("1. 场景搭建", "摆放并连接设备，按 Ctrl+S 保存场景。"),
        ("2. 数据转换", "设置产品依次经过哪些设备，检查连接是否完整。"),
        ("3. 生产仿真", "按设备设置和连接运行仿真，查看产量与设备状态。"),
        ("4. 数据分析", "查看哪里拖慢生产，以及设备利用率、故障和能耗。"),
        ("5. 边缘调度", "把检测和告警转成计算任务，比较交给哪台电脑更合适。"),
        ("6. 优化与归档", "试改参数，比较结果，把选定方案和报告保存下来。"),
    ]
    left, right = st.columns(2)
    for column, group in ((left, steps[:3]), (right, steps[3:])):
        with column:
            for title, desc in group:
                st.markdown(
                    f'<div class="ec-card" style="margin-bottom:10px"><h4>{title}</h4><p>{desc}</p></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("### 平台状态")
    try:
        from integration.scene.api_service import get_scene_api_service
        from integration.scene.oip_service import get_oip_manager

        api_status = get_scene_api_service().status()
        oip_status = get_oip_manager().status(check_remote=False)
        s1, s2, s3 = st.columns(3)
        s1.metric("场景 API", "运行中" if api_status.get("healthy") else "未启动")
        s2.metric("OIP 编辑器", "运行中" if oip_status.get("running") else "未启动")
        s3.metric("数据目录", "data/projects")
        st.caption("OIP 上游提交 dcebf53 · 编辑器静态托管 · 场景与实验保存在项目目录")
    except Exception:  # noqa: BLE001
        pass
    st.caption(
        "点击左侧「一键演示」可试跑示例产线。"
        "页面上的生产数据来自软件仿真。"
    )


def page_scene_history() -> None:
    st.markdown("## 实验历史")
    st.caption("全部实验来自 OIP 自建场景；记录保存在项目目录下，关联场景版本与参数快照。")
    project = current_project()
    if project is None:
        return
    client = _scene_client()
    if client is None:
        return
    try:
        scenes = client.list_scenes(project["project_id"])
    except SceneClientError as exc:
        st.error(str(exc))
        return
    if not scenes:
        st.info("还没有场景。请先到「建模分析工作台」搭建工厂。")
        return

    rows: list[dict[str, Any]] = []
    full_records: dict[str, dict[str, Any]] = {}
    scene_by_id = {s["scene_id"]: s for s in scenes}
    for scene in scenes:
        try:
            records = client.experiments(project["project_id"], scene["scene_id"])
        except SceneClientError:
            continue
        for record in records:
            record = {**record, "scene_id": scene["scene_id"]}
            if record.get("kind") == "edge":
                try:
                    record = {**client.experiment(project["project_id"], record["experiment_id"]), "scene_id": scene["scene_id"]}
                except SceneClientError:
                    pass
            kpis = record.get("kpis") or {}
            kind = record.get("kind")
            is_edge = kind == "edge"
            row = {
                "时间": record.get("created_at"),
                "场景": scene["name"],
                "版本": f"v{record.get('scene_version')}" if record.get("scene_version") else "—",
                "类型": "边缘调度" if is_edge else ("实时仿真" if record.get("source") == "scene_engine_live" else "生产仿真"),
                "实验ID": record.get("experiment_id"),
                "良品": None if is_edge else kpis.get("good_units"),
                "产能/h": None if is_edge else kpis.get("throughput_uph"),
                "OEE%": None if is_edge else round(float((kpis.get("oee") or {}).get("oee") or 0) * 100, 2),
                "任务数": (record.get("mapping") or {}).get("task_count") if is_edge else None,
                "动态满足率%": (
                    round(float(((record.get("metrics") or {}).get("dynamic") or {}).get("satisfaction_rate") or 0) * 100, 2)
                    if is_edge
                    else None
                ),
            }
            rows.append(row)
            full_records[record["experiment_id"]] = record
    if not rows:
        st.info("还没有实验。请到「建模分析工作台」运行一次生产仿真。")
        return
    rows.sort(key=lambda r: r.get("时间") or "", reverse=True)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=360)

    experiment_ids = [r["实验ID"] for r in rows]
    selected = st.selectbox("查看实验详情", experiment_ids, key="scene_history_detail")
    record = full_records.get(selected)
    if record:
        try:
            record = client.experiment(project["project_id"], selected)
        except SceneClientError:
            pass
        kpis = record.get("kpis") or {}
        if kpis:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("良品", kpis.get("good_units", 0))
            c2.metric("在制品", kpis.get("wip", 0))
            c3.metric("产能/h", f"{float(kpis.get('throughput_uph') or 0):.1f}")
            c4.metric("设备综合效率", f"{float((kpis.get('oee') or {}).get('oee') or 0) * 100:.1f}%")
            c5.metric("缺陷率", f"{float(kpis.get('defect_rate') or 0) * 100:.2f}%")
        if record.get("mapping"):
            mapping = record["mapping"]
            st.caption(f"共 {mapping.get('task_count')} 项任务。")
            if record.get("metrics"):
                st.dataframe(pd.DataFrame(strategy_ranking(record), columns=STRATEGY_COLUMNS).rename(columns=STRATEGY_COLUMNS), hide_index=True, width="stretch")
        if record.get("device_metrics"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "设备": d.get("device_id"),
                            "名称": zh_device_name(d.get("name")),
                            "类型": zh_device_type(d.get("simulation_type")),
                            "状态": SCENE_STATE_LABELS.get(d.get("state"), d.get("state")),
                            "利用率%": round(float(d.get("utilization", 0)) * 100, 1),
                            "队列": d.get("queue"),
                            "处理数": d.get("processed"),
                            "故障次数": d.get("failure_count"),
                        }
                        for d in record["device_metrics"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        with st.expander("完整实验 JSON"):
            st.json(record)

    # 同场景、同口径的两轮生产实验对比
    factory_by_scene: dict[str, list[dict[str, Any]]] = {}
    for record in full_records.values():
        if record.get("kind") == "factory" and record.get("factory_engine") == "scene_discrete_event" and record.get("source") == "scene_engine":
            factory_by_scene.setdefault(record.get("scene_id"), []).append(record)
    comparable_scenes = [sid for sid, items in factory_by_scene.items() if len(items) >= 2]
    st.markdown("### 实验对比")
    if comparable_scenes:
        sid = st.selectbox(
            "选择场景",
            comparable_scenes,
            format_func=lambda x: scene_by_id.get(x, {}).get("name", x),
            key="scene_history_compare_scene",
        )
        items = sorted(factory_by_scene[sid], key=lambda r: r.get("created_at") or "", reverse=True)[:8]
        ids = [r["experiment_id"] for r in items]
        labels = {r["experiment_id"]: f"{r['created_at']} · 良品 {(r.get('kpis') or {}).get('good_units')}" for r in items}
        b = st.selectbox("基线实验", ids, format_func=lambda x: labels.get(x, x), key="scene_cmp_base")
        c = st.selectbox("对比实验", ids, index=min(1, len(ids) - 1), format_func=lambda x: labels.get(x, x), key="scene_cmp_cand")
        if st.button("比较", type="primary"):
            try:
                comparison = client.compare(project["project_id"], sid, b, c)
                st.caption(f"结论：{comparison.get('verdict')} · {comparison.get('criteria')}")
                st.dataframe(pd.DataFrame(comparison.get("rows") or [], columns=COMPARISON_COLUMNS).rename(columns=COMPARISON_COLUMNS), hide_index=True, width="stretch")
            except SceneClientError as exc:
                st.error(str(exc))
    else:
        st.info("同一场景至少需要两轮生产实验才能对比。")
    if st.button("进入数据分析"):
        goto_page("建模分析工作台")


def page_scene_system() -> None:
    st.markdown("## 系统设置")
    project = current_project(required=False)
    st.markdown("### 运行环境")
    versions = {
        "Python": sys.version.split()[0],
        "Streamlit": _package_version("streamlit"),
        "pandas": _package_version("pandas"),
        "Plotly": _package_version("plotly"),
        "FastAPI": _package_version("fastapi"),
        "httpx": _package_version("httpx"),
        "pytest": _package_version("pytest"),
    }
    st.dataframe(pd.DataFrame([versions]), hide_index=True, width="stretch")

    st.markdown("### 场景服务")
    try:
        from integration.scene.api_service import get_scene_api_service
        from integration.scene.oip_service import get_oip_manager

        api_status = get_scene_api_service().status()
        oip_status = get_oip_manager().status()
        c1, c2, c3 = st.columns(3)
        c1.metric("场景 API", "运行中" if api_status.get("healthy") else "未启动")
        c2.metric("OIP 编辑器", "运行中" if oip_status.get("healthy") else "未启动")
        c3.metric("OIP 模式", {"static": "静态托管", "vite-dev": "开发模式"}.get(oip_status.get("mode"), oip_status.get("mode", "静态托管")))
        if api_status.get("url"):
            st.caption(f"场景 API：{api_status['url']}")
        if oip_status.get("url"):
            st.caption(f"OIP：{oip_status['url']}/app/ · 上游 {oip_status.get('commit', 'dcebf53')}")
        b1, b2 = st.columns(2)
        if b1.button("启动 / 复用 OIP 编辑器"):
            try:
                info = get_oip_manager().start()
                st.success(f"OIP 已启动：{info['url']}/app/")
            except Exception as exc:  # noqa: BLE001
                st.error(f"启动失败：{exc}")
        if b2.button("停止 OIP 编辑器"):
            get_oip_manager().stop()
            st.success("OIP 已停止；下次进入场景编辑页会自动启动。")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"服务状态读取失败：{exc}")

    st.markdown("### 演示数据")
    with st.expander("重置演示项目（从 demo_seed 恢复）", expanded=False):
        st.caption(
            "把安装包/仓库自带的演示项目恢复到数据目录；现有同名项目会先移动到 "
            "data/archive/demo-reset-<时间>，不会直接删除。适合评委演示前恢复初始状态。"
        )
        confirm_reset = st.checkbox("确认重置演示项目（同名项目会被归档）", key="reset_demo_confirm")
        if confirm_reset and st.button("重置演示项目", type="primary", key="reset_demo_button"):
            try:
                from integration.demo_seed import DemoSeedError, reset_demo_projects

                reset_result = reset_demo_projects()
            except (DemoSeedError, OSError, ValueError) as exc:
                st.error(f"重置失败：{exc}")
            else:
                _invalidate_data_cache()
                restored = reset_result.get("restored") or []
                st.success(f"已恢复 {len(restored)} 个演示项目。")
                if reset_result.get("archive_dir"):
                    st.caption(f"原同名项目已归档到：{reset_result['archive_dir']}")
                if reset_result.get("errors"):
                    st.warning("部分项目恢复失败：" + "；".join(reset_result["errors"][:5]))
                projects = list_projects()
                if projects:
                    st.session_state.project_id = next(
                        (p["project_id"] for p in projects if p["project_id"] in restored),
                        projects[0]["project_id"],
                    )
                st.session_state.pop("analysis_exp", None)
                st.session_state.pop("optimizer", None)
                st.session_state.pop("_pending_scene_id", None)
                st.session_state.pop("scene_editor_scene", None)
                st.session_state.pop("ws_scene_prev", None)
                st.rerun()

    st.markdown("### 数据目录")
    st.code(
        f"项目与场景：{DATA_DIR / 'projects'}\n"
        f"运行时状态：{DATA_DIR / 'runtime'}\n"
        f"服务日志：{DATA_DIR / 'logs'}\n"
        f"OIP 编辑器：{DATA_DIR.parent / 'frontend' / 'oip'}",
        language="text",
    )
    st.markdown("### 启动命令")
    st.code(
        "cd C:\\Users\\Administrator\\Desktop\\aic\n"
        "start_platform.bat            # 或 python run_platform.py\n"
        "python stop_platform.py       # 停止场景 API / OIP\n",
        language="powershell",
    )

    st.markdown("### 当前架构")
    st.markdown(
        "| 子系统 | 职责 |\n| --- | --- |\n"
        "| OIP 编辑器 (`frontend/oip`) | 三维场景搭建、设备库、吸附、属性编辑、工程保存 |\n"
        "| 场景 API (`integration/scene/api.py`) | 场景转换、版本、仿真、分析、EdgeCoder、报告 |\n"
        "| Python 引擎 (`integration/scene/engine.py`) | 用户自建工厂的离散事件生产仿真（KPI 权威） |\n"
        "| EdgeCoder (`network_sim/`、`scheduler/`) | 边缘任务、三种调度策略、指标 |\n"
        "| Streamlit (`factory_app.py`) | 统一入口：场景编辑/仿真/数据分析/历史/设置 |"
    )
    st.markdown("### 边界说明")
    st.markdown(
        "- 所有 KPI、OEE、能耗、边缘时延均为软件仿真，不是真实工厂或 6G 实测；\n"
        "- OIP 只负责三维编辑，不参与生产仿真；\n"
        "- 未映射 OIP 设备标记为“仅三维展示”，不能混入物料流；\n"
        "- 当前优先支持 material_flow 连接；control/data/power 可保存但未仿真；\n"
        "- 实时实验为 wall-clock 推进，离线实验才是确定性权威；\n"
        "- 原有 RFTwin/EdgeCoder 核心代码保留，但固定产线页面已从主导航隐藏。"
    )
    if project:
        st.caption(f"当前项目：{project['name']}（{project['project_id']}）")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

PAGE_FUNCS = {
    "平台总览": page_scene_overview,
    "场景编辑器": page_scene_editor,
    "建模分析工作台": page_workspace,
    "实验历史": page_scene_history,
    "系统设置": page_scene_system,
}


def main() -> None:
    ensure_data_dirs()
    init_state()
    inject_css()
    page = render_sidebar()
    try:
        if page != "建模分析工作台":
            render_workflow_stepper(page)
        PAGE_FUNCS[page]()
    except Exception as exc:  # noqa: BLE001 - 页面层兜底，避免白屏
        st.error(f"页面渲染出现异常：{exc}")
        st.exception(exc)


main()
