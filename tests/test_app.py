"""Streamlit 界面的冒烟测试（使用官方 AppTest，真实执行页面逻辑）。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=120)


def test_overview_page_loads():
    at = _app()
    at.run()
    assert not at.exception
    assert at.sidebar.radio[0].value == "项目总览"


def test_generate_page_produces_service():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("AI 软件生成").run()
    assert not at.exception
    buttons = [b for b in at.button if "生成示例服务" in b.label]
    assert buttons
    buttons[0].click().run()
    assert not at.exception
    generated = at.session_state["generated"]
    assert generated is not None
    assert generated.code
    assert generated.tests_code
    assert at.session_state["static_result"].passed


def test_experiment_page_runs_via_ui():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("仿真实验").run()
    assert not at.exception
    buttons = [b for b in at.button if "运行仿真实验" in b.label]
    assert buttons
    buttons[0].click().run()
    assert not at.exception
    experiment = at.session_state["experiment"]
    assert experiment is not None
    assert set(experiment.runs) == {"fixed_local", "fixed_cloud", "dynamic"}
    assert experiment.metrics["dynamic"].total_tasks == len(experiment.tasks)


def test_scene_config_form_changes_experiment_inputs():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("场景配置").run()
    num_tasks = next(w for w in at.number_input if w.label == "任务数量")
    num_tasks.set_value(30)
    seed = next(w for w in at.number_input if w.label == "随机种子")
    seed.set_value(7)
    submit = next(b for b in at.button if "保存场景配置" in b.label)
    submit.click().run()
    assert not at.error
    config = at.session_state["config"]
    assert config.num_tasks == 30
    assert config.seed == 7

    at.sidebar.radio[0].set_value("仿真实验").run()
    run_button = next(b for b in at.button if "运行仿真实验" in b.label)
    run_button.click().run()
    assert not at.error
    experiment = at.session_state["experiment"]
    assert len(experiment.tasks) == 30
    assert experiment.config.seed == 7


def test_task_detail_page_loads_after_experiment():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("仿真实验").run()
    buttons = [b for b in at.button if "运行仿真实验" in b.label]
    buttons[0].click().run()
    at.sidebar.radio[0].set_value("任务详情").run()
    assert not at.exception
