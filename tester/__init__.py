"""生成代码的静态检查与自动化测试包。"""

from tester.validator import (
    StaticCheckResult,
    TestRunResult,
    run_generated_tests,
    static_check,
)

__all__ = [
    "StaticCheckResult",
    "TestRunResult",
    "run_generated_tests",
    "static_check",
]
