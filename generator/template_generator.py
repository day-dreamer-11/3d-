"""第一阶段：基于本地可信模板的工业软件生成器。

设计原则：
- 不调用任何付费大模型 API，不伪装成“真实大模型生成”；
- 用户输入的自然语言需求只作为注释附加到代码头部，不参与任何代码拼接；
- 生成结果来自本仓库维护、经过静态审查的可信模板；
- 预留 ``LLMBackend`` 协议，后续接入真实大模型时只需实现该协议，
  并应配合 ``tester.validator`` 的静态检查与隔离执行策略使用。
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Optional, Protocol

from generator.templates import TEMPLATES

TEMPLATE_VERSION = "1.0.0"

TASK_TYPES: dict[str, str] = {
    key: spec["label"] for key, spec in TEMPLATES.items()
}


@dataclass
class IOSpecField:
    name: str
    field_type: str
    required: bool
    constraint: str
    description: str


@dataclass
class GeneratedService:
    task_type: str
    task_type_label: str
    title: str
    description: str
    code: str
    tests_code: str
    inputs: list[IOSpecField]
    outputs: list[IOSpecField]
    assumptions: list[str]
    backend: str = "本地可信模板"
    template_version: str = TEMPLATE_VERSION
    requirement_note: str = ""


class LLMBackend(Protocol):
    """预留的真实大模型生成接口（第一阶段未实现、未调用）。

    后续实现时要求：
    1. 生成代码必须通过 tester.validator.static_check 的语法与安全检查；
    2. 未经隔离的生成代码不得在宿主环境中直接执行；
    3. 界面必须如实标注代码来源（模板 / 大模型 / 人工）。
    """

    def generate_service(self, requirement: str, task_type: str) -> GeneratedService:
        ...


def _sanitize_requirement(text: str, limit: int = 300) -> str:
    """把用户需求压缩为单行注释文本，去除控制字符并限制长度。"""
    if not text:
        return ""
    cleaned = " ".join(str(text).replace("\x00", " ").split())
    return cleaned[:limit]


def _attach_requirement_note(code: str, note: str) -> str:
    if not note:
        return code
    lines = textwrap.wrap(note, width=72, break_long_words=False)
    header = "# 用户需求说明（仅作注释展示，不参与执行）：\n"
    header += "\n".join(f"# {line}" for line in lines)
    return header + "\n" + code


def _to_fields(raw: list[dict]) -> list[IOSpecField]:
    return [
        IOSpecField(
            name=item["name"],
            field_type=item["type"],
            required=bool(item["required"]),
            constraint=item["constraint"],
            description=item["description"],
        )
        for item in raw
    ]


class LocalTemplateBackend:
    """默认后端：从经过审查的本地模板生成服务代码。"""

    name = "本地可信模板"

    def generate_service(
        self, requirement: str, task_type: str
    ) -> GeneratedService:
        if task_type not in TEMPLATES:
            supported = "、".join(f"{key}（{label}）" for key, label in TASK_TYPES.items())
            raise ValueError(f"不支持的任务类型 {task_type!r}，可选：{supported}")

        spec = TEMPLATES[task_type]
        note = _sanitize_requirement(requirement)
        code = _attach_requirement_note(spec["code"], note)
        return GeneratedService(
            task_type=task_type,
            task_type_label=spec["label"],
            title=spec["title"],
            description=spec["description"],
            code=code,
            tests_code=spec["tests"],
            inputs=_to_fields(spec["inputs"]),
            outputs=_to_fields(spec["outputs"]),
            assumptions=list(spec["assumptions"]),
            backend=self.name,
            template_version=TEMPLATE_VERSION,
            requirement_note=note,
        )


def generate_service(
    task_type: str,
    requirement: str = "",
    backend: Optional[object] = None,
) -> GeneratedService:
    """生成工业服务示例代码。

    第一阶段始终使用本地可信模板；``backend`` 参数保留给未来的大模型实现。
    """
    impl = backend if backend is not None else LocalTemplateBackend()
    return impl.generate_service(requirement, task_type)
