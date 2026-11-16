"""AI 工业软件生成包。"""

from generator.template_generator import (
    TASK_TYPES,
    GeneratedService,
    IOSpecField,
    LLMBackend,
    LocalTemplateBackend,
    generate_service,
)

__all__ = [
    "TASK_TYPES",
    "GeneratedService",
    "IOSpecField",
    "LLMBackend",
    "LocalTemplateBackend",
    "generate_service",
]
