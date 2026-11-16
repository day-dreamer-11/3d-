"""代码生成与验证模块测试。"""

from __future__ import annotations

import pytest

from generator.template_generator import TASK_TYPES, generate_service
from tester.validator import run_generated_tests, static_check


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_templates_pass_static_check(task_type):
    service = generate_service(task_type, "接收检测任务，检查输入参数，并返回检测结果。")
    result = static_check(service.code)
    assert result.syntax_ok
    assert result.passed, result.errors
    assert service.inputs
    assert service.outputs
    assert service.assumptions
    assert "handle_task" in result.info["functions"]


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES))
def test_generated_tests_really_pass(task_type):
    service = generate_service(task_type)
    run = run_generated_tests(service.code, service.tests_code, timeout_seconds=90)
    assert run.success, run.stdout + "\n" + run.stderr
    assert run.passed >= 5
    assert run.failed == 0
    assert run.errors == 0
    assert run.timed_out is False


def test_unknown_task_type_raises():
    with pytest.raises(ValueError):
        generate_service("unknown_service")


def test_requirement_becomes_comment_only():
    requirement = (
        "生成一个服务\n"
        '"""\n'
        "import os\n"
        'os.system("del /f /q C:\\\\")\n'
    )
    service = generate_service("defect_detection", requirement)
    result = static_check(service.code)
    assert result.passed, result.errors
    assert "仅作注释展示" in service.code
    injection_lines = [
        line for line in service.code.splitlines() if "os.system" in line
    ]
    assert injection_lines
    assert all(line.lstrip().startswith("#") for line in injection_lines)


def test_requirement_is_truncated():
    service = generate_service("quality_statistics", "A" * 500)
    assert len(service.requirement_note) == 300


def test_static_check_rejects_forbidden_import_and_call():
    code = (
        "import os\n"
        "\n"
        "def handle_task(payload):\n"
        '    os.system("echo unsafe")\n'
        "    return {}\n"
    )
    result = static_check(code)
    assert result.syntax_ok is True
    assert result.passed is False
    assert any("os" in error for error in result.errors)
    assert any("system" in error for error in result.errors)


def test_static_check_rejects_eval():
    code = "def handle_task(payload):\n    return eval(payload)\n"
    result = static_check(code)
    assert result.passed is False
    assert any("eval" in error for error in result.errors)


def test_static_check_reports_syntax_error():
    result = static_check("def handle_task(:\n    pass\n")
    assert result.passed is False
    assert result.syntax_ok is False
    assert result.errors


def test_run_tests_reports_failures():
    code = "def handle_task(payload):\n    return {'ok': True}\n"
    tests = (
        "import importlib.util\n"
        "from pathlib import Path\n"
        "\n"
        "_spec = importlib.util.spec_from_file_location(\n"
        '    "service", Path(__file__).with_name("service.py")\n'
        ")\n"
        "service = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(service)\n"
        "\n"
        "\n"
        "def test_wrong_expectation():\n"
        "    assert service.handle_task({})['ok'] is False\n"
    )
    run = run_generated_tests(code, tests, timeout_seconds=60)
    assert run.success is False
    assert run.failed == 1
    assert "1 failed" in run.stdout
