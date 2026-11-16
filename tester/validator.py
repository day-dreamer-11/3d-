"""生成代码的静态检查与自动化测试执行。

安全边界（重要）：
- 本模块只应接收 EdgeCoder 本地可信模板生成的代码；
- 绝不执行用户自由输入的代码，也不执行未经审查的模型生成代码；
- 静态检查是白名单式防护：解析 AST、禁止危险模块与内建函数；
- 自动化测试在独立临时目录、独立 Python 子进程中运行，并设置超时。
  但这**不是**强安全沙箱，不能用于执行不可信代码。后续接入真实大模型时，
  必须改用容器/虚拟机等隔离手段，而不是直接复用本模块执行生成结果。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

# 模板允许导入的标准库白名单（不含文件、网络、进程相关模块）。
ALLOWED_MODULES = {
    "__future__",
    "collections",
    "dataclasses",
    "json",
    "math",
    "random",
    "re",
    "statistics",
    "time",
    "typing",
    "unicodedata",
    "zlib",
}

# 明确禁止的模块（即使未来扩展白名单也不应放开）。
FORBIDDEN_MODULES = {
    "asyncio",
    "builtins",
    "ctypes",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "platform",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "threading",
    "urllib",
    "webbrowser",
}

# 明确禁止调用的内建函数 / 动态执行入口。
FORBIDDEN_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}


@dataclass
class StaticCheckResult:
    passed: bool
    syntax_ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestRunResult:
    success: bool
    returncode: int | None
    passed: int
    failed: int
    errors: int
    duration_ms: float
    stdout: str
    stderr: str
    timed_out: bool = False


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def static_check(code: str) -> StaticCheckResult:
    """对代码做语法检查与危险 API 白名单检查。"""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        tree = ast.parse(code)
        compile(tree, "<generated_service>", "exec")
    except SyntaxError as exc:
        return StaticCheckResult(
            passed=False,
            syntax_ok=False,
            errors=[f"语法错误（第 {exc.lineno} 行）：{exc.msg}"],
            warnings=[],
            info={"lines": len(code.splitlines())},
        )

    imports: set[str] = set()
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                imports.add(top)
                if top in FORBIDDEN_MODULES:
                    errors.append(f"禁止导入模块：{alias.name}（第 {node.lineno} 行）")
                elif top not in ALLOWED_MODULES:
                    warnings.append(
                        f"非白名单模块：{alias.name}（第 {node.lineno} 行，需人工复核）"
                    )
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if node.level:
                warnings.append(f"检测到相对导入（第 {node.lineno} 行）")
            imports.add(top or "<relative>")
            if top in FORBIDDEN_MODULES:
                errors.append(
                    f"禁止从模块 {node.module} 导入（第 {node.lineno} 行）"
                )
            elif top and top not in ALLOWED_MODULES:
                warnings.append(
                    f"非白名单模块：{node.module}（第 {node.lineno} 行，需人工复核）"
                )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            short = name.split(".")[-1]
            if name in FORBIDDEN_CALLS or short in FORBIDDEN_CALLS:
                errors.append(f"禁止调用函数：{name}（第 {node.lineno} 行）")
            base = name.split(".")[0]
            if base in FORBIDDEN_MODULES:
                errors.append(
                    f"禁止调用模块 {base} 的函数：{name}（第 {node.lineno} 行）"
                )

    if "handle_task" not in functions:
        errors.append("缺少约定入口函数 handle_task(payload)")
    if ast.get_docstring(tree) is None:
        warnings.append("模块缺少说明文档字符串")
    if not code.strip():
        errors.append("代码为空")

    info = {
        "lines": len(code.splitlines()),
        "functions": functions,
        "imports": sorted(imports),
    }
    return StaticCheckResult(
        passed=not errors,
        syntax_ok=True,
        errors=errors,
        warnings=warnings,
        info=info,
    )


def _count(stdout: str, word: str) -> int:
    match = re.search(rf"(\d+) {word}", stdout)
    return int(match.group(1)) if match else 0


def run_generated_tests(
    code: str, tests_code: str, timeout_seconds: float = 60.0
) -> TestRunResult:
    """在临时目录中用独立子进程运行 pytest 测试，并返回真实结果。

    仅用于本地可信模板；非沙箱环境，不得用于任意不可信代码。
    """
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="edgecoder_test_") as tmp_dir:
        service_path = os.path.join(tmp_dir, "service.py")
        test_path = os.path.join(tmp_dir, "test_service.py")
        with open(service_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(code)
        with open(test_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(tests_code)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONPATH", None)

        command = [
            sys.executable,
            "-m",
            "pytest",
            "test_service.py",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
        ]
        try:
            proc = subprocess.run(
                command,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return TestRunResult(
                success=False,
                returncode=None,
                passed=0,
                failed=0,
                errors=0,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                stdout=stdout,
                stderr=stderr or f"测试执行超时（>{timeout_seconds:.0f} 秒）",
                timed_out=True,
            )

        duration_ms = (time.perf_counter() - started) * 1000.0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        passed = _count(stdout, "passed")
        failed = _count(stdout, "failed")
        # pytest 的摘要中 "error" 与 "errors" 使用同一计数前缀，只匹配一次。
        errors = _count(stdout, "error")
        if proc.returncode != 0 and passed == 0 and failed == 0 and errors == 0:
            errors = 1

        return TestRunResult(
            success=proc.returncode == 0,
            returncode=proc.returncode,
            passed=passed,
            failed=failed,
            errors=errors,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
        )
