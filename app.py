"""EdgeCoder：AI 驱动的工业边缘智能仿真与软件自动生成平台（Streamlit 界面）。

运行方式（项目根目录）：
    streamlit run app.py

说明：界面中的所有实验指标均由本项目的仿真程序实时计算，不含预设结果；
所有网络与服务器行为均为可解释的软件仿真，不代表真实设备实测。
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from experiments.runner import (
    ConfigError,
    ExperimentResult,
    config_signature,
    run_experiment,
    validate_config,
)
from generator.template_generator import (
    TASK_TYPES,
    GeneratedService,
    generate_service,
)
from models.defaults import default_config
from models.schemas import (
    PRIORITY_LABELS,
    STRATEGY_ORDER,
    NetworkLink,
    SimulationConfig,
)
from tester.validator import (
    StaticCheckResult,
    TestRunResult,
    run_generated_tests,
    static_check,
)

PAGES = ["项目总览", "场景配置", "AI 软件生成", "仿真实验", "任务详情", "项目说明"]

STATUS_LABELS = {
    "success": "准时完成",
    "timeout": "超时完成",
    "rejected": "已拒绝",
    "failed": "失败",
}

STRATEGY_COLORS = {
    "固定本地": "#5B6B7F",
    "固定云端": "#C08A3E",
    "动态调度": "#1F7A8C",
}

NODE_COLORS = {
    "local": "#5B6B7F",
    "edge": "#1F7A8C",
    "cloud": "#C08A3E",
}

CHART_FONT = "Microsoft YaHei, Segoe UI, sans-serif"

st.set_page_config(
    page_title="EdgeCoder 工业边缘智能仿真平台",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# 通用辅助函数
# ---------------------------------------------------------------------------


def inject_css() -> None:
    st.markdown(
        """
<style>
.block-container { padding-top: 1.8rem; padding-bottom: 3rem; max-width: 1320px; }
h1, h2, h3, h4 { color: #16324F; letter-spacing: .2px; }
.ec-hero {
    background: linear-gradient(135deg, #16324F 0%, #23536B 58%, #2A7F8F 100%);
    color: #F5F7FA; padding: 26px 32px; border-radius: 14px; margin-bottom: 20px;
    box-shadow: 0 8px 24px rgba(22, 50, 79, .16);
}
.ec-hero h1 { color: #FFFFFF; margin: 0 0 8px 0; font-size: 30px; font-weight: 700; }
.ec-hero p { margin: 0; color: #D8E4EA; font-size: 14px; line-height: 1.7; }
.ec-tag {
    display: inline-block; padding: 3px 11px; border-radius: 999px;
    background: rgba(255,255,255,.14); font-size: 12px; margin: 8px 8px 0 0;
    color: #EAF2F6;
}
div[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #DCE5EC; border-left: 4px solid #2A7F8F;
    border-radius: 10px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(16,50,79,.06);
}
div[data-testid="stMetricLabel"] p { font-size: 13px; color: #5B7186; }
.ec-note {
    background: #F3F8FB; border: 1px solid #D9E5EC; border-radius: 10px;
    padding: 13px 17px; color: #35566D; font-size: 13.5px; line-height: 1.75;
}
.ec-badge {
    display: inline-block; padding: 4px 12px; border-radius: 6px;
    font-size: 13px; font-weight: 600;
}
.ec-success { background: #E2F2E8; color: #1C6B3A; }
.ec-warn { background: #FBF0DC; color: #8A5A12; }
.ec-error { background: #FBE5E2; color: #8E2D22; }
.ec-card {
    background: #FFFFFF; border: 1px solid #DCE5EC; border-radius: 12px;
    padding: 18px 20px; height: 100%;
}
.ec-card h4 { margin: 0 0 8px 0; color: #1F4E6B; }
.ec-card p { margin: 0; color: #5B7186; font-size: 13px; line-height: 1.7; }
.ec-logo { font-size: 22px; font-weight: 800; color: #16324F; letter-spacing: 1.5px; }
.ec-logo span { color: #2A7F8F; }
.ec-section { margin: 10px 0 6px 0; }
</style>
""",
        unsafe_allow_html=True,
    )


def init_state() -> None:
    ss = st.session_state
    if "config" not in ss:
        ss.config = default_config()
    if "experiment" not in ss:
        ss.experiment = None
    if "generated" not in ss:
        ss.generated = None
    if "static_result" not in ss:
        ss.static_result = None
    if "test_run" not in ss:
        ss.test_run = None
    if "nav" not in ss:
        ss.nav = PAGES[0]


def current_experiment() -> Optional[ExperimentResult]:
    experiment = st.session_state.get("experiment")
    if experiment is None:
        return None
    if experiment.signature != config_signature(st.session_state.config):
        return None
    return experiment


def stale_experiment() -> bool:
    experiment = st.session_state.get("experiment")
    return experiment is not None and experiment.signature != config_signature(
        st.session_state.config
    )


def node_display_map(config: SimulationConfig) -> dict[str, str]:
    return {node.node_id: node.display_name for node in config.nodes}


def style_fig(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=8, r=8, t=46, b=8),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        font=dict(family=CHART_FONT, size=12, color="#27405A"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def fmt_num(value: Optional[float], digits: int = 1, suffix: str = "") -> str:
    """格式化数值；NaN / None 显示为破折号，避免界面上出现 nan。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:.{digits}f}{suffix}"


def status_badge(status: str) -> str:
    label = STATUS_LABELS.get(status, status)
    css = {
        "success": "ec-success",
        "timeout": "ec-warn",
        "rejected": "ec-error",
        "failed": "ec-error",
    }.get(status, "ec-warn")
    return f'<span class="ec-badge {css}">{label}</span>'


def results_dataframe(experiment: ExperimentResult) -> pd.DataFrame:
    rows = []
    for key in STRATEGY_ORDER:
        run = experiment.runs[key]
        for result in run.results:
            task = result.task
            rows.append(
                {
                    "策略key": key,
                    "策略": run.strategy_name,
                    "任务ID": task.task_id,
                    "优先级": PRIORITY_LABELS.get(task.priority, str(task.priority)),
                    "数据量(MB)": task.data_size_mb,
                    "计算需求(GFLOP)": task.compute_demand_gflops,
                    "时延上限(ms)": task.deadline_ms,
                    "到达时间(ms)": round(task.arrival_time_ms, 1),
                    "目标节点": result.node_name or "—",
                    "状态": STATUS_LABELS.get(result.status, result.status),
                    "总时延(ms)": (
                        round(result.total_latency_ms, 1)
                        if result.total_latency_ms is not None
                        else None
                    ),
                    "上行(ms)": round(result.upload_ms, 1),
                    "排队(ms)": round(result.queue_ms, 1),
                    "计算(ms)": round(result.compute_ms, 1),
                    "回传(ms)": round(result.download_ms, 1),
                    "决策原因": result.decision_reason,
                }
            )
    return pd.DataFrame(rows)


def config_summary_dataframe(config: SimulationConfig) -> pd.DataFrame:
    rows = []
    type_labels = {"local": "本地", "edge": "边缘", "cloud": "云端"}
    for node in config.nodes:
        rows.append(
            {
                "节点": node.display_name,
                "类型": type_labels[node.node_type],
                "算力(GFLOPS)": node.compute_gflops,
                "基础负载": f"{node.base_load * 100:.0f}%",
                "有效算力(GFLOPS)": round(node.effective_compute_gflops(), 2),
                "单任务上限(GFLOP)": node.max_task_gflops,
                "队列容量": node.max_queue_slots,
                "带宽(Mbps)": node.link.bandwidth_mbps,
                "单向时延(ms)": node.link.latency_ms,
                "拥塞系数": node.link.congestion,
            }
        )
    return pd.DataFrame(rows)


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown('<div class="ec-logo">EDGE<span>CODER</span></div>', unsafe_allow_html=True)
        st.caption("AI 驱动的工业边缘智能仿真平台")
        page = st.radio("功能导航", PAGES, key="nav")
        st.divider()
        config = st.session_state.config
        st.caption(f"当前场景：{config.num_tasks} 个任务 · 种子 {config.seed}")
        experiment = st.session_state.get("experiment")
        if experiment is not None:
            suffix = "（配置已变更）" if stale_experiment() else ""
            st.caption(f"上次实验：{experiment.generated_at} {suffix}")
        st.divider()
        st.caption("v1.0 · 纯软件仿真")
        st.caption("所有结果为仿真计算，非真实网络实测")
    return page


# ---------------------------------------------------------------------------
# 页面 1：项目总览
# ---------------------------------------------------------------------------


def page_overview() -> None:
    st.markdown(
        """
<div class="ec-hero">
  <h1>EdgeCoder 工业边缘智能仿真与软件自动生成平台</h1>
  <p>面向 AI 与 6G 融合创新场景的纯软件仿真平台：模拟智能工厂的工业视觉缺陷检测任务，
  对比本地、云端与动态边缘调度策略，并用本地可信模板自动生成可测试的工业服务示例。</p>
  <span class="ec-tag">工业场景仿真</span>
  <span class="ec-tag">AI 软件生成</span>
  <span class="ec-tag">边缘任务调度</span>
  <span class="ec-tag">可解释指标</span>
</div>
""",
        unsafe_allow_html=True,
    )

    config = st.session_state.config
    experiment = current_experiment()

    cols = st.columns(4)
    if experiment is None:
        cols[0].metric("仿真状态", "待运行")
        cols[1].metric("当前任务数量", f"{config.num_tasks}")
        cols[2].metric("计算节点数", f"{len(config.nodes)}")
        cols[3].metric("随机种子", f"{config.seed}")
    else:
        metrics = experiment.metrics
        dynamic = metrics["dynamic"]
        cols[0].metric("任务数量", f"{len(experiment.tasks)}")
        cols[1].metric("动态调度时延满足率", f"{dynamic.satisfaction_rate * 100:.1f}%")
        cols[2].metric("动态调度平均时延", fmt_num(dynamic.avg_latency_ms, 0, " ms"))
        cols[3].metric("超时 / 拒绝任务", f"{dynamic.timeout_tasks} / {dynamic.rejected_tasks}")

    st.markdown("### 平台能力")
    left, middle, right = st.columns(3)
    with left:
        st.markdown(
            """
<div class="ec-card">
  <h4>工业场景仿真</h4>
  <p>任务生成器模拟工业相机连续产生检测任务，数据量、计算需求、时延上限与优先级均随机生成；
  网络时延、带宽、拥塞与节点算力、负载可交互调整。</p>
</div>
""",
            unsafe_allow_html=True,
        )
    with middle:
        st.markdown(
            """
<div class="ec-card">
  <h4>AI 软件生成</h4>
  <p>基于本地可信模板生成工业服务 Python 示例，展示输入输出说明、静态安全检查与
  真实执行的 pytest 测试结果。第一阶段不调用任何付费大模型 API。</p>
</div>
""",
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
<div class="ec-card">
  <h4>可解释调度实验</h4>
  <p>固定本地、固定云端与动态调度共享同一任务集与网络条件。动态调度按
  “预测时延 / 时延上限 + 排队压力”的加权评分选点，并记录每个任务的决策原因。</p>
</div>
""",
            unsafe_allow_html=True,
        )

    st.markdown("### 快速开始")
    st.markdown(
        "1. 在 **场景配置** 中调整任务规模、网络条件与节点参数；\n"
        "2. 在 **仿真实验** 页面点击「运行仿真实验」，查看三种策略的真实对比指标；\n"
        "3. 在 **任务详情** 页面选择一个任务，查看目标节点、时延构成与调度原因；\n"
        "4. 在 **AI 软件生成** 页面生成可信模板服务，并运行自动化测试。"
    )

    if experiment is not None:
        st.markdown("### 最近一次实验结果")
        metrics = experiment.metrics
        summary = pd.DataFrame(
            [
                {
                    "策略": metrics[key].strategy_name,
                    "时延满足率(%)": round(metrics[key].satisfaction_rate * 100, 1),
                    "平均时延(ms)": round(metrics[key].avg_latency_ms, 1),
                    "超时任务数": metrics[key].timeout_tasks,
                    "拒绝任务数": metrics[key].rejected_tasks,
                }
                for key in STRATEGY_ORDER
            ]
        )
        st.dataframe(summary, hide_index=True, width="stretch")
    elif stale_experiment():
        st.warning("场景配置已修改，此前的实验结果已过期，请到「仿真实验」重新运行。")

    st.markdown(
        """
<div class="ec-note">
<b>公平性声明：</b>三种调度策略在每次实验中共享完全相同的任务集、网络条件、节点参数与随机种子，
只有调度策略不同。所有指标均由仿真程序实时计算得出；决策开销由 Python 计时器实测，
包含解释器开销，不能与真实网络时延直接比较。本平台不连接真实 6G 设备，
仿真结果不得表述为真实网络测试结果。
</div>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 页面 2：场景配置
# ---------------------------------------------------------------------------


def page_scene_config() -> None:
    st.markdown("## 场景配置")
    st.caption("调整任务生成、网络条件与节点参数。保存后需重新运行实验，结果才会更新。")

    config = st.session_state.config
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("任务数量", config.num_tasks)
    m2.metric("到达频率", f"{config.arrival_rate_per_sec:.1f} 任务/秒")
    m3.metric("随机种子", config.seed)
    m4.metric("计算节点", len(config.nodes))

    with st.form("scene_config_form"):
        tab_tasks, tab_network, tab_nodes = st.tabs(
            ["任务与随机种子", "网络条件", "节点参数"]
        )
        with tab_tasks:
            c1, c2, c3 = st.columns(3)
            num_tasks = int(
                c1.number_input(
                    "任务数量",
                    min_value=10,
                    max_value=500,
                    value=int(config.num_tasks),
                    step=10,
                    help="一次仿真实验生成的任务总数。",
                )
            )
            arrival_rate = float(
                c2.number_input(
                    "任务到达频率（任务/秒）",
                    min_value=0.2,
                    max_value=30.0,
                    value=float(config.arrival_rate_per_sec),
                    step=0.5,
                    format="%.1f",
                    help="相邻任务到达间隔服从指数分布，均值为 1/到达频率。",
                )
            )
            seed = int(
                c3.number_input(
                    "随机种子",
                    min_value=0,
                    max_value=999_999,
                    value=int(config.seed),
                    step=1,
                    help="相同种子生成完全相同的任务集，保证实验可复现。",
                )
            )
            data_range = st.slider(
                "任务数据量范围（MB）",
                min_value=0.1,
                max_value=20.0,
                value=(
                    float(config.data_size_range_mb[0]),
                    float(config.data_size_range_mb[1]),
                ),
                step=0.1,
                help="工业相机图像数据量的均匀分布范围。",
            )
            compute_range = st.slider(
                "任务计算需求范围（GFLOP）",
                min_value=0.1,
                max_value=30.0,
                value=(
                    float(config.compute_range_gflops[0]),
                    float(config.compute_range_gflops[1]),
                ),
                step=0.1,
                help="单个检测任务所需计算量；约 12% 的任务会翻倍以模拟高负载场景。",
            )
            deadline_range = st.slider(
                "任务时延上限范围（ms）",
                min_value=20.0,
                max_value=3000.0,
                value=(
                    float(config.deadline_range_ms[0]),
                    float(config.deadline_range_ms[1]),
                ),
                step=10.0,
                help="超过该时延上限的任务会被标记为超时。",
            )

        with tab_network:
            c1, c2 = st.columns(2)
            jitter = float(
                c1.number_input(
                    "网络抖动上限（ms）",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(config.network_jitter_ms),
                    step=1.0,
                    help="为每个任务随机采样一次抖动，三种策略使用相同抖动序列。",
                )
            )
            result_size = float(
                c2.number_input(
                    "结果回传数据量（MB）",
                    min_value=0.001,
                    max_value=5.0,
                    value=float(config.result_size_mb),
                    step=0.01,
                    format="%.3f",
                    help="检测结果由节点回传至产线系统时的数据量。",
                )
            )
            st.caption("各节点链路参数：任务源到节点的上行/下行链路使用同一组带宽与时延参数。")
            net_cols = st.columns(len(config.nodes))
            network_inputs: dict[str, tuple[float, float, float]] = {}
            for col, node in zip(net_cols, config.nodes):
                with col:
                    st.markdown(f"**{node.display_name}**")
                    bandwidth = float(
                        st.number_input(
                            "带宽（Mbps）",
                            min_value=1.0,
                            max_value=2000.0,
                            value=float(node.link.bandwidth_mbps),
                            step=5.0,
                            key=f"net_bw_{node.node_id}",
                        )
                    )
                    latency = float(
                        st.number_input(
                            "单向时延（ms）",
                            min_value=0.0,
                            max_value=500.0,
                            value=float(node.link.latency_ms),
                            step=1.0,
                            key=f"net_lat_{node.node_id}",
                        )
                    )
                    congestion = float(
                        st.number_input(
                            "拥塞系数（0~0.95）",
                            min_value=0.0,
                            max_value=0.95,
                            value=float(node.link.congestion),
                            step=0.05,
                            format="%.2f",
                            key=f"net_cong_{node.node_id}",
                            help="有效带宽 = 带宽 × (1 − 拥塞系数)。",
                        )
                    )
                    network_inputs[node.node_id] = (bandwidth, latency, congestion)

        with tab_nodes:
            node_cols = st.columns(len(config.nodes))
            node_inputs: dict[str, tuple[float, float, float, int]] = {}
            for col, node in zip(node_cols, config.nodes):
                with col:
                    st.markdown(f"**{node.display_name}**")
                    compute = float(
                        st.number_input(
                            "算力（GFLOPS）",
                            min_value=1.0,
                            max_value=500.0,
                            value=float(node.compute_gflops),
                            step=5.0,
                            key=f"node_compute_{node.node_id}",
                        )
                    )
                    base_load = float(
                        st.number_input(
                            "基础负载（0~0.9）",
                            min_value=0.0,
                            max_value=0.9,
                            value=float(node.base_load),
                            step=0.05,
                            format="%.2f",
                            key=f"node_load_{node.node_id}",
                            help="与实验无关的常驻业务占用；有效算力 = 算力 × (1 − 基础负载)。",
                        )
                    )
                    max_task = float(
                        st.number_input(
                            "单任务算力上限（GFLOP）",
                            min_value=0.5,
                            max_value=200.0,
                            value=float(node.max_task_gflops),
                            step=1.0,
                            key=f"node_max_{node.node_id}",
                        )
                    )
                    slots = int(
                        st.number_input(
                            "队列容量（含正在计算的任务）",
                            min_value=1,
                            max_value=100,
                            value=int(node.max_queue_slots),
                            step=1,
                            key=f"node_slots_{node.node_id}",
                        )
                    )
                    node_inputs[node.node_id] = (
                        compute,
                        base_load,
                        max_task,
                        slots,
                    )

        submitted = st.form_submit_button("保存场景配置")

    if submitted:
        new_nodes = []
        for node in config.nodes:
            bandwidth, latency, congestion = network_inputs[node.node_id]
            compute, base_load, max_task, slots = node_inputs[node.node_id]
            link = NetworkLink(
                bandwidth_mbps=bandwidth,
                latency_ms=latency,
                congestion=congestion,
            )
            new_nodes.append(
                type(node)(
                    node_id=node.node_id,
                    display_name=node.display_name,
                    node_type=node.node_type,
                    compute_gflops=compute,
                    base_load=base_load,
                    max_task_gflops=max_task,
                    max_queue_slots=slots,
                    link=link,
                )
            )
        new_config = SimulationConfig(
            num_tasks=num_tasks,
            arrival_rate_per_sec=arrival_rate,
            seed=seed,
            data_size_range_mb=(float(data_range[0]), float(data_range[1])),
            compute_range_gflops=(float(compute_range[0]), float(compute_range[1])),
            deadline_range_ms=(float(deadline_range[0]), float(deadline_range[1])),
            network_jitter_ms=jitter,
            result_size_mb=result_size,
            nodes=tuple(new_nodes),
        )
        try:
            validate_config(new_config)
        except ConfigError as exc:
            st.error(f"配置校验未通过：{exc}")
        else:
            st.session_state.config = new_config
            st.success("场景配置已保存。请到「仿真实验」重新运行以获取新结果。")

    st.markdown("### 当前节点与网络参数")
    st.dataframe(config_summary_dataframe(config), hide_index=True, width="stretch")
    st.markdown(
        """
<div class="ec-note">
<b>参数影响提示：</b>降低带宽或提高拥塞系数会增大传输时延；提高基础负载会降低有效算力；
调紧时延上限或提高到达频率会增加排队与超时。以上影响均由仿真模型计算，
可修改参数后重新运行实验进行验证。
</div>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 页面 3：AI 软件生成
# ---------------------------------------------------------------------------


def page_generate() -> None:
    st.markdown("## AI 工业软件生成")
    st.caption(
        "第一阶段使用本地可信模板生成可运行的 Python 服务示例，"
        "不调用任何大模型 API，也不把模板结果伪装成大模型生成。"
    )

    left, right = st.columns([3, 2])
    with left:
        task_type = st.selectbox(
            "任务类型",
            options=list(TASK_TYPES.keys()),
            format_func=lambda key: f"{TASK_TYPES[key]}（{key}）",
            key="gen_task_type",
        )
        requirement = st.text_area(
            "需求描述",
            value="生成一个工业产品缺陷检测服务，接收检测任务，检查输入参数，并返回检测结果。",
            height=110,
            key="gen_requirement",
            help="需求文本仅以注释形式附加到生成代码头部，不参与代码拼接或执行。",
        )
        generate_clicked = st.button("生成示例服务", type="primary")
    with right:
        st.markdown(
            """
<div class="ec-note">
<b>安全边界：</b>系统只执行本仓库维护的可信模板及其配套测试。
用户需求不会作为代码执行；生成的代码在临时目录的独立子进程中运行，
并设置超时。该机制不是强安全沙箱，接入真实大模型后必须使用容器等隔离手段。
</div>
""",
            unsafe_allow_html=True,
        )

    if generate_clicked:
        try:
            service = generate_service(task_type, requirement)
            check = static_check(service.code)
            st.session_state.generated = service
            st.session_state.static_result = check
            st.session_state.test_run = None
        except Exception as exc:  # noqa: BLE001 - 界面层兜底
            st.error(f"生成失败：{exc}")

    service: Optional[GeneratedService] = st.session_state.get("generated")
    check: Optional[StaticCheckResult] = st.session_state.get("static_result")
    if service is None:
        st.info("请选择任务类型并点击「生成示例服务」。")
        return

    st.divider()
    st.markdown(f"### {service.title}")
    st.caption(
        f"生成后端：{service.backend} · 模板版本 {service.template_version} · "
        f"任务类型 {service.task_type_label}"
    )
    st.write(service.description)
    if service.requirement_note:
        st.caption(f"用户需求（仅注释）：{service.requirement_note}")

    tab_inputs, tab_outputs, tab_code, tab_tests = st.tabs(
        ["输入说明", "输出说明", "服务代码", "自动生成测试"]
    )
    with tab_inputs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "字段": field.name,
                        "类型": field.field_type,
                        "必填": "是" if field.required else "否",
                        "约束": field.constraint,
                        "说明": field.description,
                    }
                    for field in service.inputs
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.markdown("**仿真假设**")
        for item in service.assumptions:
            st.markdown(f"- {item}")
    with tab_outputs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "字段": field.name,
                        "类型": field.field_type,
                        "约束": field.constraint,
                        "说明": field.description,
                    }
                    for field in service.outputs
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with tab_code:
        st.code(service.code, language="python", line_numbers=True)

    with tab_tests:
        st.code(service.tests_code, language="python", line_numbers=True)

    st.markdown("### 静态安全检查")
    if check is None:
        st.info("尚未执行静态检查。")
    elif check.passed:
        st.success(
            f"静态检查通过：语法正确，入口函数 handle_task 存在，未发现危险模块或调用。"
            f"代码行数 {check.info.get('lines', '—')}，导入模块："
            f"{'、'.join(check.info.get('imports', [])) or '无'}。"
        )
    else:
        st.error("静态检查未通过：")
        for error in check.errors:
            st.markdown(f"- {error}")
    if check and check.warnings:
        with st.expander(f"静态检查警告（{len(check.warnings)} 条）"):
            for warning in check.warnings:
                st.markdown(f"- {warning}")

    st.markdown("### 自动化测试")
    st.caption(
        "测试在系统临时目录中的独立 Python 子进程执行，超时 60 秒。"
        "以下结果为测试程序的真实输出。"
    )
    if st.button("运行自动化测试", type="primary"):
        try:
            with st.spinner("正在隔离子进程中执行 pytest ..."):
                test_result = run_generated_tests(
                    service.code, service.tests_code, timeout_seconds=60.0
                )
            st.session_state.test_run = test_result
        except Exception as exc:  # noqa: BLE001 - 界面层兜底
            st.error(f"测试执行异常：{exc}")

    test_result: Optional[TestRunResult] = st.session_state.get("test_run")
    if test_result is None:
        st.info("尚未运行自动化测试。")
    else:
        if test_result.timed_out:
            st.error("测试执行超时，已终止子进程。")
        elif test_result.success:
            st.success("自动化测试全部通过。")
        else:
            st.error("自动化测试存在失败或错误，请查看下方原始输出。")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("通过", test_result.passed)
        m2.metric("失败", test_result.failed)
        m3.metric("错误", test_result.errors)
        m4.metric("耗时", f"{test_result.duration_ms:.0f} ms")

        with st.expander("测试原始输出（stdout）", expanded=not test_result.success):
            st.code(test_result.stdout or "（无输出）", language="text")
        if test_result.stderr:
            with st.expander("测试错误输出（stderr）", expanded=not test_result.success):
                st.code(test_result.stderr, language="text")

    st.markdown(
        """
<div class="ec-note">
<b>后续接入真实大模型的预留接口：</b>代码中提供 <code>LLMBackend</code> 协议，
实现 <code>generate_service(requirement, task_type)</code> 即可替换生成后端。
接入时必须同时启用静态检查与强隔离执行环境，并在界面中如实标注代码来源。
</div>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 页面 4：仿真实验
# ---------------------------------------------------------------------------


def render_experiment(experiment: ExperimentResult) -> None:
    metrics = experiment.metrics
    config = experiment.config
    names = node_display_map(config)

    st.markdown("### 实验参数")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("任务数量", len(experiment.tasks))
    p2.metric("到达频率", f"{config.arrival_rate_per_sec:.1f} 任务/秒")
    p3.metric("随机种子", config.seed)
    p4.metric("仿真总耗时", f"{experiment.wall_time_ms:.1f} ms")
    st.caption(
        f"实验生成时间：{experiment.generated_at} · 配置指纹：{experiment.signature} · "
        f"任务集与网络条件由三种策略共享"
    )

    with st.expander("本次实验的仿真假设与公式"):
        st.markdown(
            "- 任务到达间隔服从指数分布，均值 = 1000 / 到达频率（毫秒）；\n"
            "- 数据量、计算需求、时延上限在设定范围内均匀分布；约 12% 的高计算量任务计算需求 ×2；\n"
            "- 上行传输时延 = 数据量(MB) × 8 × 1000 / 有效带宽(Mbps) + 单向时延 + 任务抖动；\n"
            "- 有效带宽 = 带宽 × (1 − 拥塞系数)；\n"
            "- 计算时延 = 计算需求(GFLOP) / [算力(GFLOPS) × (1 − 基础负载)]；\n"
            "- 节点采用 FIFO 单服务台排队；总时延 = 上行 + 排队 + 计算 + 回传；\n"
            "- 总时延 ≤ 时延上限记为准时完成，否则记为超时；队列满或超过单任务算力上限记为拒绝。"
        )

    dynamic = metrics["dynamic"]
    local = metrics["fixed_local"]
    cloud = metrics["fixed_cloud"]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "动态时延满足率",
        f"{dynamic.satisfaction_rate * 100:.1f}%",
        f"{(dynamic.satisfaction_rate - local.satisfaction_rate) * 100:+.1f}% vs 固定本地",
    )
    k2.metric(
        "动态平均完成时延",
        fmt_num(dynamic.avg_latency_ms, 0, " ms"),
        (
            f"{dynamic.avg_latency_ms - local.avg_latency_ms:+.0f} ms vs 固定本地"
            if not math.isnan(dynamic.avg_latency_ms)
            and not math.isnan(local.avg_latency_ms)
            else None
        ),
        delta_color="inverse",
    )
    k3.metric(
        "动态超时任务",
        dynamic.timeout_tasks,
        f"{dynamic.timeout_tasks - local.timeout_tasks:+d} vs 固定本地",
        delta_color="inverse",
    )
    k4.metric(
        "动态拒绝任务",
        dynamic.rejected_tasks,
        f"{dynamic.rejected_tasks - local.rejected_tasks:+d} vs 固定本地",
        delta_color="inverse",
    )

    st.markdown("### 三种调度策略指标对比")
    table = pd.DataFrame(
        [
            {
                "策略": metrics[key].strategy_name,
                "任务完成率(%)": round(metrics[key].success_rate * 100, 1),
                "时延满足率(%)": round(metrics[key].satisfaction_rate * 100, 1),
                "平均时延(ms)": fmt_num(metrics[key].avg_latency_ms),
                "P95时延(ms)": fmt_num(metrics[key].p95_latency_ms),
                "超时任务数": metrics[key].timeout_tasks,
                "拒绝任务数": metrics[key].rejected_tasks,
                "平均决策开销(µs)": round(metrics[key].avg_decision_ms * 1000, 2),
                "吞吐(任务/秒)": round(metrics[key].throughput_per_sec, 2),
            }
            for key in STRATEGY_ORDER
        ]
    )
    st.dataframe(table, hide_index=True, width="stretch")

    st.markdown("### 时延与成功率")
    c1, c2 = st.columns(2)
    with c1:
        latency_rows = []
        for key in STRATEGY_ORDER:
            latency_rows.append(
                {
                    "策略": metrics[key].strategy_name,
                    "平均时延": metrics[key].avg_latency_ms,
                    "P95时延": metrics[key].p95_latency_ms,
                }
            )
        latency_df = pd.DataFrame(latency_rows).melt(
            id_vars="策略", var_name="指标", value_name="时延(ms)"
        )
        fig = px.bar(
            latency_df,
            x="策略",
            y="时延(ms)",
            color="指标",
            barmode="group",
            text_auto=".0f",
            title="平均时延与 P95 时延（仅完成的任务）",
            color_discrete_sequence=["#1F7A8C", "#A9C1CC"],
        )
        st.plotly_chart(style_fig(fig), width="stretch")
    with c2:
        rate_rows = []
        for key in STRATEGY_ORDER:
            rate_rows.append(
                {
                    "策略": metrics[key].strategy_name,
                    "任务完成率": metrics[key].success_rate * 100,
                    "时延满足率": metrics[key].satisfaction_rate * 100,
                }
            )
        rate_df = pd.DataFrame(rate_rows).melt(
            id_vars="策略", var_name="指标", value_name="比例(%)"
        )
        fig = px.bar(
            rate_df,
            x="策略",
            y="比例(%)",
            color="指标",
            barmode="group",
            text_auto=".1f",
            title="任务完成率与时延约束满足率",
            color_discrete_sequence=["#2F6B57", "#C08A3E"],
        )
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(style_fig(fig), width="stretch")

    st.markdown("### 节点分布与资源利用率")
    c1, c2 = st.columns(2)
    with c1:
        dist_rows = []
        for key in STRATEGY_ORDER:
            for node_id, count in metrics[key].node_distribution.items():
                dist_rows.append(
                    {
                        "策略": metrics[key].strategy_name,
                        "节点": names.get(node_id, node_id),
                        "任务数": count,
                    }
                )
        dist_df = pd.DataFrame(dist_rows)
        if not dist_df.empty:
            fig = px.bar(
                dist_df,
                x="策略",
                y="任务数",
                color="节点",
                barmode="group",
                text_auto=True,
                title="各节点承接的任务数量",
                color_discrete_map={
                    names.get("local", "本地工控机"): NODE_COLORS["local"],
                    names.get("edge", "边缘服务器"): NODE_COLORS["edge"],
                    names.get("cloud", "云服务器"): NODE_COLORS["cloud"],
                },
            )
            st.plotly_chart(style_fig(fig), width="stretch")
    with c2:
        util_rows = []
        for key in STRATEGY_ORDER:
            for node_id, value in metrics[key].node_utilization.items():
                util_rows.append(
                    {
                        "策略": metrics[key].strategy_name,
                        "节点": names.get(node_id, node_id),
                        "利用率(%)": value * 100,
                    }
                )
        util_df = pd.DataFrame(util_rows)
        fig = px.bar(
            util_df,
            x="策略",
            y="利用率(%)",
            color="节点",
            barmode="group",
            text_auto=".0f",
            title="节点计算资源利用率（按仿真跨度计算）",
            color_discrete_map={
                names.get("local", "本地工控机"): NODE_COLORS["local"],
                names.get("edge", "边缘服务器"): NODE_COLORS["edge"],
                names.get("cloud", "云服务器"): NODE_COLORS["cloud"],
            },
        )
        fig.update_yaxes(range=[0, max(100, util_df["利用率(%)"].max() if not util_df.empty else 100)])
        st.plotly_chart(style_fig(fig), width="stretch")
    st.caption("利用率只统计实验任务带来的计算忙碌时间，不含节点基础负载；基础负载已体现在有效算力中。")

    st.markdown("### 任务时延分布与状态构成")
    results_df = results_dataframe(experiment)
    c1, c2 = st.columns(2)
    with c1:
        completed_df = results_df[results_df["总时延(ms)"].notna()]
        if not completed_df.empty:
            fig = px.box(
                completed_df,
                x="策略",
                y="总时延(ms)",
                color="策略",
                points="outliers",
                title="单个任务完成时延分布（仅完成的任务）",
                color_discrete_map=STRATEGY_COLORS,
            )
            fig.update_layout(showlegend=False)
            st.plotly_chart(style_fig(fig), width="stretch")
    with c2:
        status_df = (
            results_df.groupby(["策略", "状态"], as_index=False)["任务ID"]
            .count()
            .rename(columns={"任务ID": "任务数"})
        )
        fig = px.bar(
            status_df,
            x="策略",
            y="任务数",
            color="状态",
            barmode="stack",
            text_auto=True,
            title="任务状态构成",
            color_discrete_map={
                "准时完成": "#2F6B57",
                "超时完成": "#C08A3E",
                "已拒绝": "#A65246",
                "失败": "#7A4E4E",
            },
        )
        st.plotly_chart(style_fig(fig), width="stretch")

    st.markdown("### 调度决策开销（实测）")
    overhead_df = pd.DataFrame(
        [
            {
                "策略": metrics[key].strategy_name,
                "平均决策开销(µs)": metrics[key].avg_decision_ms * 1000,
                "总决策开销(ms)": metrics[key].total_decision_ms,
            }
            for key in STRATEGY_ORDER
        ]
    )
    fig = px.bar(
        overhead_df,
        x="策略",
        y="平均决策开销(µs)",
        text_auto=".1f",
        title="每个任务的平均调度决策耗时（perf_counter 实测）",
        color="策略",
        color_discrete_map=STRATEGY_COLORS,
    )
    fig.update_layout(showlegend=False)
    st.plotly_chart(style_fig(fig, height=300), width="stretch")
    st.caption(
        "决策开销在本机 Python 环境中实测，包含解释器与数据拷贝开销，存在微秒级波动；"
        "与毫秒级的仿真时延不可直接比较，仅用于策略间相对比较。"
    )

    st.markdown("### 逐任务结果")
    display_columns = [
        "策略",
        "任务ID",
        "优先级",
        "目标节点",
        "状态",
        "总时延(ms)",
        "时延上限(ms)",
        "上行(ms)",
        "排队(ms)",
        "计算(ms)",
        "回传(ms)",
    ]
    st.dataframe(
        results_df[display_columns].sort_values(["策略", "任务ID"]),
        hide_index=True,
        width="stretch",
        height=360,
    )
    csv_bytes = results_df.to_csv(index=False).encode("utf-8-sig")
    down1, down2 = st.columns([2, 3])
    with down1:
        st.download_button(
            "下载逐任务结果 CSV",
            data=csv_bytes,
            file_name="edgecoder_task_results.csv",
            mime="text/csv",
        )
    with down2:
        st.caption("需要查看单个任务的决策原因与时延构成？请前往「任务详情」页面。")


def page_experiment() -> None:
    st.markdown("## 仿真实验")
    st.caption(
        "点击按钮后，系统使用当前场景配置运行三种调度策略，所有指标由仿真程序实时计算。"
    )

    config = st.session_state.config
    with st.expander("当前实验参数与仿真假设", expanded=True):
        st.dataframe(config_summary_dataframe(config), hide_index=True, width="stretch")
        st.caption(
            f"任务数量 {config.num_tasks} · 到达频率 {config.arrival_rate_per_sec:.1f} 任务/秒 · "
            f"随机种子 {config.seed} · 网络抖动上限 {config.network_jitter_ms:.1f} ms · "
            f"结果回传 {config.result_size_mb:.3f} MB"
        )

    if stale_experiment():
        st.warning("场景配置已修改，上次实验结果已过期。请重新运行实验。")

    if st.button("运行仿真实验", type="primary"):
        try:
            with st.spinner("正在生成统一任务集并运行三种调度策略..."):
                experiment = run_experiment(config)
            st.session_state.experiment = experiment
            st.success(f"实验完成：{len(experiment.tasks)} 个任务，耗时 {experiment.wall_time_ms:.1f} ms。")
        except ConfigError as exc:
            st.error(f"配置不合法，无法运行实验：{exc}")
        except Exception as exc:  # noqa: BLE001 - 界面层兜底
            st.error(f"实验运行失败：{exc}")

    experiment = current_experiment()
    if experiment is None:
        st.info("当前没有与配置匹配的实验结果。请点击「运行仿真实验」。")
        return
    render_experiment(experiment)


# ---------------------------------------------------------------------------
# 页面 5：任务详情
# ---------------------------------------------------------------------------


def page_task_detail() -> None:
    st.markdown("## 任务详情")
    st.caption("查看单个任务的目标节点、时延构成、候选节点评分与调度决策原因。")

    experiment = current_experiment()
    if experiment is None:
        st.info("当前没有可用的实验结果。请先到「仿真实验」页面运行实验。")
        return

    config = experiment.config
    names = node_display_map(config)
    strategy_key = st.selectbox(
        "调度策略",
        options=list(STRATEGY_ORDER),
        format_func=lambda key: experiment.runs[key].strategy_name,
    )
    run = experiment.runs[strategy_key]

    result_by_id = {result.task.task_id: result for result in run.results}
    task_id = st.selectbox(
        "任务",
        options=[result.task.task_id for result in run.results],
        format_func=lambda tid: (
            f"#{tid:03d} · {PRIORITY_LABELS.get(result_by_id[tid].task.priority, '')}优先级"
            f" · {STATUS_LABELS.get(result_by_id[tid].status, '')}"
        ),
    )
    result = next(r for r in run.results if r.task.task_id == task_id)
    task = result.task

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("目标节点", result.node_name or "无")
    c2.metric("总完成时延", f"{result.total_latency_ms:.1f} ms" if result.total_latency_ms is not None else "—")
    c3.metric("任务时延上限", f"{task.deadline_ms:.0f} ms")
    with c4:
        st.markdown("**任务状态**")
        st.markdown(status_badge(result.status), unsafe_allow_html=True)

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("优先级", PRIORITY_LABELS.get(task.priority, str(task.priority)))
    d2.metric("数据量", f"{task.data_size_mb:.2f} MB")
    d3.metric("计算需求", f"{task.compute_demand_gflops:.2f} GFLOP")
    d4.metric("到达时间", f"{task.arrival_time_ms:.0f} ms")

    st.markdown("### 调度决策原因")
    st.info(result.decision_reason)
    st.caption(f"决策耗时：{result.decision_cost_ms * 1000:.1f} µs（本机实测，含解释器开销）")

    st.markdown("### 时延构成")
    if result.total_latency_ms is None:
        st.warning("该任务未进入执行阶段，没有时延构成数据。")
    else:
        breakdown = result.delay_breakdown()
        colors = {
            "上行传输": "#7FA6B2",
            "排队等待": "#C08A3E",
            "计算处理": "#1F7A8C",
            "结果回传": "#8A9AA8",
        }
        fig = go.Figure()
        for name, value in breakdown.items():
            fig.add_trace(
                go.Bar(
                    y=["时延构成"],
                    x=[value],
                    name=name,
                    orientation="h",
                    marker_color=colors[name],
                    text=f"{name} {value:.1f} ms",
                    textposition="inside",
                    insidetextanchor="middle",
                )
            )
        fig.add_vline(
            x=task.deadline_ms,
            line_dash="dash",
            line_color="#8E2D22",
            annotation_text=f"时延上限 {task.deadline_ms:.0f} ms",
            annotation_position="top",
        )
        fig.update_layout(
            barmode="stack",
            title=f"任务 #{task.task_id} 的完成时延分解（总计 {result.total_latency_ms:.1f} ms）",
            xaxis_title="毫秒（ms）",
        )
        st.plotly_chart(style_fig(fig, height=280), width="stretch")

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("上行传输", f"{result.upload_ms:.1f} ms")
        q2.metric("排队等待", f"{result.queue_ms:.1f} ms")
        q3.metric("计算处理", f"{result.compute_ms:.1f} ms")
        q4.metric("结果回传", f"{result.download_ms:.1f} ms")

        if result.status == "timeout":
            over = result.total_latency_ms - task.deadline_ms
            st.warning(f"该任务超出时延上限 {over:.1f} ms。")
        elif result.status == "success":
            st.success(
                f"该任务在时延上限内完成，剩余余量 {task.deadline_ms - result.total_latency_ms:.1f} ms。"
            )

    st.markdown("### 候选节点评估")
    if result.candidate_details:
        candidate_rows = []
        for node_id, detail in result.candidate_details.items():
            candidate_rows.append(
                {
                    "节点": names.get(node_id, node_id),
                    "预测总时延(ms)": round(detail.get("total", math.nan), 1),
                    "评分": (
                        round(detail["score"], 3)
                        if detail.get("score") is not None
                        else "—（固定策略不评分）"
                    ),
                    "上行(ms)": round(detail.get("upload", 0.0), 1),
                    "排队(ms)": round(detail.get("wait", 0.0), 1),
                    "计算(ms)": round(detail.get("compute", 0.0), 1),
                    "回传(ms)": round(detail.get("download", 0.0), 1),
                    "满足时延": "是" if detail.get("within_deadline") else "否",
                    "算力可执行": "否" if detail.get("over_capacity") else "是",
                    "队列可用": "否" if detail.get("queue_full") else "是",
                    "已选中": "✔" if node_id == result.node_id else "",
                }
            )
        st.dataframe(pd.DataFrame(candidate_rows), hide_index=True, width="stretch")
        st.caption(
            "预测值由调度器在任务到达时刻根据节点状态估算；实际值可能因执行期间的其他任务而不同。"
        )
    else:
        st.info("该任务没有候选节点评估记录。")

    st.markdown("### 同一任务在三种策略下的对比")
    compare_rows = []
    for key in STRATEGY_ORDER:
        other = next(
            (r for r in experiment.runs[key].results if r.task.task_id == task_id), None
        )
        if other is None:
            continue
        compare_rows.append(
            {
                "策略": experiment.runs[key].strategy_name,
                "目标节点": other.node_name or "—",
                "状态": STATUS_LABELS.get(other.status, other.status),
                "总时延(ms)": (
                    round(other.total_latency_ms, 1)
                    if other.total_latency_ms is not None
                    else "—"
                ),
                "决策原因": other.decision_reason,
            }
        )
    st.dataframe(pd.DataFrame(compare_rows), hide_index=True, width="stretch")

    st.markdown("### 本策略的逐任务记录")
    filter_cols = st.columns([2, 2, 3])
    status_filter = filter_cols[0].multiselect(
        "按状态筛选",
        options=list(STATUS_LABELS.values()),
        default=list(STATUS_LABELS.values()),
    )
    all_node_names = [node.display_name for node in config.nodes]
    node_filter = filter_cols[1].multiselect(
        "按节点筛选",
        options=all_node_names,
        default=all_node_names,
    )
    search_text = filter_cols[2].text_input("按决策原因搜索", value="")

    rows = []
    for r in run.results:
        status_label = STATUS_LABELS.get(r.status, r.status)
        node_label = r.node_name or "—"
        if status_label not in status_filter:
            continue
        if node_label not in node_filter:
            continue
        if search_text and search_text not in r.decision_reason:
            continue
        rows.append(
            {
                "任务ID": r.task.task_id,
                "优先级": PRIORITY_LABELS.get(r.task.priority, ""),
                "节点": node_label,
                "状态": status_label,
                "总时延(ms)": (
                    round(r.total_latency_ms, 1)
                    if r.total_latency_ms is not None
                    else None
                ),
                "时延上限(ms)": r.task.deadline_ms,
                "决策原因": r.decision_reason,
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        height=320,
    )


# ---------------------------------------------------------------------------
# 页面 6：项目说明
# ---------------------------------------------------------------------------


def page_about() -> None:
    st.markdown("## 项目说明")

    st.markdown("### 系统架构")
    st.markdown(
        """
| 模块 | 职责 |
| --- | --- |
| `models/` | 数据模型：任务、节点、链路、仿真配置、执行结果 |
| `network_sim/` | 可解释的离散事件仿真器：传输、排队、计算、回传与容量拒绝 |
| `scheduler/` | 固定本地、固定云端、动态加权评分三种调度策略 |
| `experiments/` | 统一任务集生成、策略对比编排、指标计算 |
| `generator/` | 本地可信模板生成工业服务代码与输入输出说明，预留 LLM 后端协议 |
| `tester/` | 生成代码的静态安全检查与隔离子进程自动化测试 |
| `app.py` | Streamlit 中文交互界面 |
| `tests/` | 真实执行单元测试与集成测试 |
"""
    )

    st.markdown("### 仿真模型与公式")
    st.markdown("**上行传输时延**（任务源到目标节点）：")
    st.latex(
        r"t_{\mathrm{up}} = \frac{d_{\mathrm{MB}} \times 8}{B \times (1 - c)} + L + J"
    )
    st.markdown(
        "其中 $d_{\\mathrm{MB}}$ 为数据量（MB），$B$ 为带宽（Mbps），$c$ 为拥塞系数，"
        "$L$ 为单向时延（ms），$J$ 为该任务采样得到的抖动（ms）。"
    )
    st.markdown("**排队时延**（FIFO 单服务台）：")
    st.latex(
        r"t_{\mathrm{queue}} = \max\left(0,\; T_{\mathrm{free}} - (t_{\mathrm{arrive}} + t_{\mathrm{up}})\right)"
    )
    st.markdown("**计算时延**：")
    st.latex(
        r"t_{\mathrm{compute}} = \frac{C_{\mathrm{GFLOP}}}{F_{\mathrm{GFLOPS}} \times (1 - l)} \times 1000"
    )
    st.markdown(
        "其中 $C$ 为任务计算需求（GFLOP），$F$ 为节点算力（GFLOPS），$l$ 为基础负载。"
    )
    st.markdown("**总完成时延**：")
    st.latex(
        r"t_{\mathrm{total}} = t_{\mathrm{up}} + t_{\mathrm{queue}} + t_{\mathrm{compute}} + t_{\mathrm{down}}"
    )
    st.markdown(
        "结果回传时延与上行同链路公式（不含抖动）。总时延不超过任务时延上限记为准时完成。"
    )

    st.markdown("### 动态调度算法")
    st.markdown(
        "对每个候选节点，调度器使用当前状态（下一次空闲时刻、排队数）预测完成时延，并按以下可解释评分选择："
    )
    st.latex(
        r"\mathrm{score} = w_p \cdot \frac{\hat{t}_{\mathrm{total}}}{D} + 0.3 \cdot \frac{q}{Q}"
    )
    st.markdown(
        "- $w_p = 1.0 + 0.5 \\times (2 - \\text{priority})/2$：高优先级任务对时延比值更敏感；\n"
        "- $D$：任务时延上限；$q$：决策时刻排队任务数；$Q$：节点队列容量；\n"
        "- 可行节点要求：单任务算力不超限、预测时延不超过上限、队列未满；\n"
        "- 若没有可行节点，退化为预测完成时延最小的可执行节点，并在原因中说明预计超时。"
    )

    st.markdown("### 指标定义")
    st.markdown(
        "- **任务完成率**：完成执行的任务（准时 + 超时）/ 总任务数；\n"
        "- **时延约束满足率**：准时完成的任务 / 总任务数；\n"
        "- **平均 / P95 完成时延**：仅统计完成执行的任务；\n"
        "- **节点资源利用率**：节点累计计算忙碌时间 / 该策略仿真跨度（不含基础负载）；\n"
        "- **调度决策开销**：`time.perf_counter_ns` 实测的每次决策调用耗时，包含 Python 解释器开销，"
        "不同机器与不同运行时刻会有波动，仅适合做策略间的相对比较。"
    )

    st.markdown("### 安全设计")
    st.markdown(
        "- 只执行本仓库维护的可信模板代码，绝不执行用户自由输入的代码；\n"
        "- 用户需求仅作为注释附加到生成代码头部，不参与代码拼接或执行；\n"
        "- 生成代码先经过 AST 静态检查：语法校验、模块白名单、危险调用拦截；\n"
        "- 自动化测试在临时目录的独立子进程中运行，并设置执行超时；\n"
        "- 该子进程隔离不是强安全沙箱。接入真实大模型后，必须使用容器/虚拟机等隔离环境执行生成代码。"
    )

    st.markdown("### 局限性与后续计划")
    st.markdown(
        "- 当前网络模型为简化的带宽/时延/拥塞模型，未建模无线信道、丢包重传与路由；\n"
        "- 节点采用 FIFO 单服务台，未建模多核并行、任务拆分与优先级抢占；\n"
        "- 动态调度为可解释的规则加权算法，尚未引入强化学习；\n"
        "- 代码生成使用本地可信模板，尚未接入真实大语言模型 API；\n"
        "- 后续计划：实现 LLMBackend、增加能耗与成本指标、支持多工厂与异构任务、"
        "引入基于历史数据的调度策略评估。"
    )

    st.markdown(
        """
<div class="ec-note">
<b>免责声明：</b>EdgeCoder 是用于教学、演示与算法对比的软件仿真平台，
不连接真实 6G 网络、工业设备或云服务器。界面中的所有指标均为仿真模型输出，
不得作为真实网络性能或设备能力的依据。
</div>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

PAGE_FUNCS = {
    "项目总览": page_overview,
    "场景配置": page_scene_config,
    "AI 软件生成": page_generate,
    "仿真实验": page_experiment,
    "任务详情": page_task_detail,
    "项目说明": page_about,
}


def main() -> None:
    init_state()
    inject_css()
    page = render_sidebar()
    try:
        PAGE_FUNCS[page]()
    except Exception as exc:  # noqa: BLE001 - 避免界面直接崩溃
        st.error(f"页面渲染出现异常：{exc}")
        st.exception(exc)


main()
