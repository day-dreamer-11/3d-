"""本地可信代码模板库。

每个模板包含：
- 可独立运行的 Python 服务源码（纯标准库、纯函数入口 handle_task）；
- 针对该服务自动生成的 pytest 测试源码；
- 输入/输出说明、仿真假设。

重要：模板内容全部由本仓库维护、经过静态审查，属于“可信代码”。
用户输入的自然语言需求只会以注释形式附加到源码头部，绝不参与代码拼接或执行。
"""

from __future__ import annotations

DEFECT_CODE = r'''"""EdgeCoder 可信模板：工业产品缺陷检测服务（仿真演示）。

说明：
1. 本文件由 EdgeCoder 的本地可信模板生成，未调用任何大模型 API。
2. 检测逻辑是可解释的伪随机仿真，用于演示边缘侧服务接口与参数校验，
   不代表真实计算机视觉模型的精度，也不连接任何工业设备。
3. 服务入口为纯函数 handle_task(payload: dict) -> dict，便于被调度器调用。
"""

from __future__ import annotations

import random
import time
import zlib
from typing import Any


class ValidationError(ValueError):
    """表示调用方传入的任务参数不合法。"""


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValidationError(f"缺少必填字段: {key}")
    return payload[key]


def _as_str(payload: dict[str, Any], key: str) -> str:
    value = _require(payload, key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"字段 {key} 必须为非空字符串")
    return value.strip()


def _as_int(payload: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = _require(payload, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"字段 {key} 必须为整数")
    if value < minimum or value > maximum:
        raise ValidationError(f"字段 {key} 必须在 [{minimum}, {maximum}] 范围内")
    return value


def _as_float(
    payload: dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"字段 {key} 必须为数值")
    value = float(value)
    if value < minimum or value > maximum:
        raise ValidationError(f"字段 {key} 必须在 [{minimum}, {maximum}] 范围内")
    return value


def validate_task(payload: Any) -> dict[str, Any]:
    """校验输入参数并返回规范化后的字典。"""
    if not isinstance(payload, dict):
        raise ValidationError("任务参数必须是 JSON 对象（dict）")
    order_id = _as_str(payload, "order_id")
    product_id = _as_str(payload, "product_id")
    width = _as_int(payload, "image_width", 64, 8192)
    height = _as_int(payload, "image_height", 64, 8192)
    threshold = _as_float(payload, "confidence_threshold", 0.5, 0.0, 1.0)
    defect_rate = _as_float(payload, "simulated_defect_rate", 0.08, 0.0, 1.0)
    seed = payload.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValidationError("字段 seed 必须为整数或省略")
    return {
        "order_id": order_id,
        "product_id": product_id,
        "image_width": width,
        "image_height": height,
        "confidence_threshold": threshold,
        "simulated_defect_rate": defect_rate,
        "seed": seed,
    }


def handle_task(payload: dict[str, Any]) -> dict[str, Any]:
    """处理一个工业缺陷检测任务并返回检测结果（仿真逻辑）。"""
    started = time.perf_counter()
    task = validate_task(payload)
    seed = task["seed"]
    if seed is None:
        seed = zlib.crc32(
            f"{task['order_id']}|{task['product_id']}".encode("utf-8")
        )
    rng = random.Random(seed)
    confidence = round(rng.uniform(0.55, 0.99), 4)
    detected = rng.random() < task["simulated_defect_rate"]
    defects: list[dict[str, Any]] = []
    if detected and confidence >= task["confidence_threshold"]:
        defects.append(
            {
                "type": rng.choice(["划痕", "凹坑", "污渍", "裂纹"]),
                "x": rng.randint(0, task["image_width"] - 1),
                "y": rng.randint(0, task["image_height"] - 1),
                "area_ratio": round(rng.uniform(0.002, 0.05), 4),
            }
        )
    processing_ms = (time.perf_counter() - started) * 1000.0
    return {
        "order_id": task["order_id"],
        "product_id": task["product_id"],
        "defect_found": bool(defects),
        "confidence": confidence,
        "defects": defects,
        "processing_time_ms": round(processing_ms, 3),
    }
'''

DEFECT_TESTS = r'''"""自动生成的 pytest 测试：工业产品缺陷检测服务。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SERVICE_PATH = Path(__file__).with_name("service.py")
_spec = importlib.util.spec_from_file_location("edgecoder_service", _SERVICE_PATH)
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)


def valid_payload(**overrides):
    payload = {
        "order_id": "ORD-0001",
        "product_id": "PART-42",
        "image_width": 1280,
        "image_height": 960,
        "confidence_threshold": 0.5,
        "simulated_defect_rate": 1.0,
        "seed": 7,
    }
    payload.update(overrides)
    return payload


def test_valid_payload_returns_result():
    result = service.handle_task(valid_payload())
    assert result["order_id"] == "ORD-0001"
    assert result["product_id"] == "PART-42"
    assert isinstance(result["defect_found"], bool)
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["processing_time_ms"] >= 0.0


def test_defect_rate_one_always_detects():
    result = service.handle_task(valid_payload(simulated_defect_rate=1.0))
    assert result["defect_found"] is True
    assert len(result["defects"]) == 1


def test_defect_rate_zero_never_detects():
    result = service.handle_task(valid_payload(simulated_defect_rate=0.0))
    assert result["defect_found"] is False
    assert result["defects"] == []


def test_missing_required_field_raises():
    payload = valid_payload()
    del payload["order_id"]
    with pytest.raises(service.ValidationError):
        service.handle_task(payload)


def test_invalid_image_size_raises():
    with pytest.raises(service.ValidationError):
        service.handle_task(valid_payload(image_width=10))


def test_non_dict_payload_raises():
    with pytest.raises(service.ValidationError):
        service.handle_task(["not", "a", "dict"])


def test_seed_makes_result_deterministic():
    first = service.handle_task(valid_payload(seed=123))
    second = service.handle_task(valid_payload(seed=123))
    assert first["defect_found"] == second["defect_found"]
    assert first["confidence"] == second["confidence"]
    assert first["defects"] == second["defects"]
'''

MAINTENANCE_CODE = r'''"""EdgeCoder 可信模板：设备预测性维护评估服务（仿真演示）。

说明：
1. 本文件由 EdgeCoder 的本地可信模板生成，未调用任何大模型 API。
2. 健康度由振动、温度相对额定值的超限比例计算，是可解释的规则模型，
   不代表真实设备的故障诊断结论。
3. 服务入口为纯函数 handle_task(payload: dict) -> dict。
"""

from __future__ import annotations

import time
from typing import Any


class ValidationError(ValueError):
    """表示调用方传入的任务参数不合法。"""


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValidationError(f"缺少必填字段: {key}")
    return payload[key]


def _number(value: Any, key: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"字段 {key} 必须为数值")
    value = float(value)
    if value < minimum or value > maximum:
        raise ValidationError(f"字段 {key} 必须在 [{minimum}, {maximum}] 范围内")
    return value


def validate_task(payload: Any) -> dict[str, Any]:
    """校验输入参数并返回规范化后的字典。"""
    if not isinstance(payload, dict):
        raise ValidationError("任务参数必须是 JSON 对象（dict）")
    device_id = _require(payload, "device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValidationError("字段 device_id 必须为非空字符串")
    vibration = _number(_require(payload, "vibration_mm_s"), "vibration_mm_s", 0.0, 100.0)
    temperature = _number(_require(payload, "temperature_c"), "temperature_c", -40.0, 300.0)
    rated_vibration = _number(
        payload.get("rated_vibration_mm_s", 4.5), "rated_vibration_mm_s", 0.1, 50.0
    )
    rated_temperature = _number(
        payload.get("rated_temperature_c", 75.0), "rated_temperature_c", 0.0, 300.0
    )
    return {
        "device_id": device_id.strip(),
        "vibration_mm_s": vibration,
        "temperature_c": temperature,
        "rated_vibration_mm_s": rated_vibration,
        "rated_temperature_c": rated_temperature,
    }


def handle_task(payload: dict[str, Any]) -> dict[str, Any]:
    """评估设备健康度（仿真规则模型）。

    规则：
    - 振动超限惩罚 = max(0, 实测/额定 − 1) × 45；
    - 温度超限惩罚 = max(0, 实测/额定 − 1) × 50；
    - 健康度 = max(0, 100 − 两项惩罚)；
    - 健康度 ≥ 75 正常，≥ 45 预警，否则严重告警。
    """
    started = time.perf_counter()
    task = validate_task(payload)
    vibration_ratio = task["vibration_mm_s"] / task["rated_vibration_mm_s"]
    temperature_ratio = task["temperature_c"] / task["rated_temperature_c"]
    vibration_penalty = max(0.0, vibration_ratio - 1.0) * 45.0
    temperature_penalty = max(0.0, temperature_ratio - 1.0) * 50.0
    health_score = round(max(0.0, 100.0 - vibration_penalty - temperature_penalty), 2)
    if health_score >= 75.0:
        alert_level = "normal"
    elif health_score >= 45.0:
        alert_level = "warning"
    else:
        alert_level = "critical"
    anomalies: list[str] = []
    if vibration_ratio > 1.0:
        anomalies.append(f"振动超出额定值 {(vibration_ratio - 1.0) * 100.0:.1f}%")
    if temperature_ratio > 1.0:
        anomalies.append(f"温度超出额定值 {(temperature_ratio - 1.0) * 100.0:.1f}%")
    processing_ms = (time.perf_counter() - started) * 1000.0
    return {
        "device_id": task["device_id"],
        "health_score": health_score,
        "alert_level": alert_level,
        "vibration_ratio": round(vibration_ratio, 4),
        "temperature_ratio": round(temperature_ratio, 4),
        "anomalies": anomalies,
        "processing_time_ms": round(processing_ms, 3),
    }
'''

MAINTENANCE_TESTS = r'''"""自动生成的 pytest 测试：设备预测性维护评估服务。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SERVICE_PATH = Path(__file__).with_name("service.py")
_spec = importlib.util.spec_from_file_location("edgecoder_service", _SERVICE_PATH)
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)


def payload(**overrides):
    data = {
        "device_id": "PRESS-01",
        "vibration_mm_s": 2.0,
        "temperature_c": 50.0,
        "rated_vibration_mm_s": 4.5,
        "rated_temperature_c": 75.0,
    }
    data.update(overrides)
    return data


def test_normal_device_is_healthy():
    result = service.handle_task(payload())
    assert result["health_score"] == 100.0
    assert result["alert_level"] == "normal"
    assert result["anomalies"] == []


def test_moderate_vibration_triggers_warning():
    result = service.handle_task(payload(vibration_mm_s=8.0))
    assert result["alert_level"] == "warning"
    assert result["health_score"] < 100.0
    assert len(result["anomalies"]) == 1


def test_severe_vibration_triggers_critical():
    result = service.handle_task(payload(vibration_mm_s=20.0))
    assert result["alert_level"] == "critical"
    assert result["health_score"] == 0.0


def test_missing_required_field_raises():
    data = payload()
    del data["temperature_c"]
    with pytest.raises(service.ValidationError):
        service.handle_task(data)


def test_out_of_range_value_raises():
    with pytest.raises(service.ValidationError):
        service.handle_task(payload(vibration_mm_s=-1.0))


def test_non_dict_payload_raises():
    with pytest.raises(service.ValidationError):
        service.handle_task(42)
'''

QUALITY_CODE = r'''"""EdgeCoder 可信模板：质检批量数据统计服务（仿真演示）。

说明：
1. 本文件由 EdgeCoder 的本地可信模板生成，未调用任何大模型 API。
2. 统计口径：均值、总体标准差、合格率与过程能力指数 Cpk；
   不引入任何第三方数值库，结果可复算。
3. 服务入口为纯函数 handle_task(payload: dict) -> dict。
"""

from __future__ import annotations

import statistics
import time
from typing import Any


class ValidationError(ValueError):
    """表示调用方传入的任务参数不合法。"""


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValidationError(f"缺少必填字段: {key}")
    return payload[key]


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"字段 {key} 必须为数值")
    return float(value)


def validate_task(payload: Any) -> dict[str, Any]:
    """校验输入参数并返回规范化后的字典。"""
    if not isinstance(payload, dict):
        raise ValidationError("任务参数必须是 JSON 对象（dict）")
    batch_id = _require(payload, "batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValidationError("字段 batch_id 必须为非空字符串")
    samples = _require(payload, "samples")
    if not isinstance(samples, list):
        raise ValidationError("字段 samples 必须为数值列表")
    if not 2 <= len(samples) <= 5000:
        raise ValidationError("字段 samples 长度必须在 [2, 5000] 范围内")
    cleaned = [_number(value, f"samples[{index}]") for index, value in enumerate(samples)]
    lower = _number(_require(payload, "lower_limit"), "lower_limit")
    upper = _number(_require(payload, "upper_limit"), "upper_limit")
    if lower >= upper:
        raise ValidationError("lower_limit 必须小于 upper_limit")
    return {
        "batch_id": batch_id.strip(),
        "samples": cleaned,
        "lower_limit": lower,
        "upper_limit": upper,
    }


def handle_task(payload: dict[str, Any]) -> dict[str, Any]:
    """统计一批质量检测数据并返回统计结果。"""
    started = time.perf_counter()
    task = validate_task(payload)
    samples = task["samples"]
    lower = task["lower_limit"]
    upper = task["upper_limit"]
    mean = statistics.fmean(samples)
    std = statistics.pstdev(samples)  # 总体标准差
    pass_count = sum(1 for value in samples if lower <= value <= upper)
    cpk = None
    if std > 0:
        cpk = min(upper - mean, mean - lower) / (3.0 * std)
    processing_ms = (time.perf_counter() - started) * 1000.0
    return {
        "batch_id": task["batch_id"],
        "sample_count": len(samples),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "pass_rate": round(pass_count / len(samples), 4),
        "cpk": None if cpk is None else round(cpk, 4),
        "out_of_spec": len(samples) - pass_count,
        "processing_time_ms": round(processing_ms, 3),
    }
'''

QUALITY_TESTS = r'''"""自动生成的 pytest 测试：质检批量数据统计服务。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SERVICE_PATH = Path(__file__).with_name("service.py")
_spec = importlib.util.spec_from_file_location("edgecoder_service", _SERVICE_PATH)
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)


def payload(**overrides):
    data = {
        "batch_id": "BATCH-2026-001",
        "samples": [9.9, 10.0, 10.1, 10.05, 9.95],
        "lower_limit": 9.5,
        "upper_limit": 10.5,
    }
    data.update(overrides)
    return data


def test_statistics_are_computed():
    result = service.handle_task(payload())
    assert result["sample_count"] == 5
    assert abs(result["mean"] - 10.0) < 1e-9
    assert result["std"] > 0.0
    assert result["pass_rate"] == 1.0
    assert result["out_of_spec"] == 0
    assert result["cpk"] > 1.0


def test_out_of_spec_samples_are_counted():
    result = service.handle_task(payload(samples=[9.0, 10.0, 11.0]))
    assert result["out_of_spec"] == 2
    assert abs(result["pass_rate"] - (1.0 / 3.0)) < 1e-4


def test_identical_samples_have_zero_std_and_null_cpk():
    result = service.handle_task(payload(samples=[10.0, 10.0, 10.0]))
    assert result["std"] == 0.0
    assert result["cpk"] is None


def test_too_few_samples_raise():
    with pytest.raises(service.ValidationError):
        service.handle_task(payload(samples=[10.0]))


def test_non_numeric_sample_raises():
    with pytest.raises(service.ValidationError):
        service.handle_task(payload(samples=[10.0, "bad"]))


def test_invalid_spec_limits_raise():
    with pytest.raises(service.ValidationError):
        service.handle_task(payload(lower_limit=11.0, upper_limit=10.0))


def test_non_dict_payload_raises():
    with pytest.raises(service.ValidationError):
        service.handle_task("bad payload")
'''


TEMPLATES: dict[str, dict] = {
    "defect_detection": {
        "label": "工业产品缺陷检测",
        "title": "工业产品缺陷检测服务",
        "description": (
            "接收工业相机采集的检测任务参数，校验订单、产品与图像尺寸等输入，"
            "返回可复现的缺陷检测结果（仿真置信度、缺陷列表）。"
        ),
        "code": DEFECT_CODE,
        "tests": DEFECT_TESTS,
        "inputs": [
            {"name": "order_id", "type": "str", "required": True, "constraint": "非空字符串", "description": "生产订单编号"},
            {"name": "product_id", "type": "str", "required": True, "constraint": "非空字符串", "description": "产品编号"},
            {"name": "image_width", "type": "int", "required": True, "constraint": "64 ~ 8192", "description": "图像宽度（像素）"},
            {"name": "image_height", "type": "int", "required": True, "constraint": "64 ~ 8192", "description": "图像高度（像素）"},
            {"name": "confidence_threshold", "type": "float", "required": False, "constraint": "0.0 ~ 1.0，默认 0.5", "description": "判定缺陷的最低置信度"},
            {"name": "simulated_defect_rate", "type": "float", "required": False, "constraint": "0.0 ~ 1.0，默认 0.08", "description": "仿真缺陷出现概率（非真实模型精度）"},
            {"name": "seed", "type": "int", "required": False, "constraint": "整数，可省略", "description": "随机种子，用于复现实验结果"},
        ],
        "outputs": [
            {"name": "order_id", "type": "str", "required": True, "constraint": "原样返回", "description": "生产订单编号"},
            {"name": "product_id", "type": "str", "required": True, "constraint": "原样返回", "description": "产品编号"},
            {"name": "defect_found", "type": "bool", "required": True, "constraint": "—", "description": "是否检出缺陷"},
            {"name": "confidence", "type": "float", "required": True, "constraint": "0.55 ~ 0.99", "description": "仿真检测置信度"},
            {"name": "defects", "type": "list[dict]", "required": True, "constraint": "可为空列表", "description": "缺陷类型、位置与面积占比"},
            {"name": "processing_time_ms", "type": "float", "required": True, "constraint": "≥ 0", "description": "服务内部处理耗时"},
        ],
        "assumptions": [
            "检测结果由带种子的伪随机仿真生成，不调用真实视觉模型，不能用于生产判定。",
            "相同 seed 与相同输入会得到相同输出，便于测试与复现。",
            "图像尺寸仅参与参数校验与坐标生成，不进行真实解码。",
        ],
    },
    "predictive_maintenance": {
        "label": "设备预测性维护",
        "title": "设备预测性维护评估服务",
        "description": (
            "接收设备振动与温度读数，与额定值比较，输出 0~100 的健康度评分、"
            "告警等级和异常说明，用于演示边缘侧设备状态评估接口。"
        ),
        "code": MAINTENANCE_CODE,
        "tests": MAINTENANCE_TESTS,
        "inputs": [
            {"name": "device_id", "type": "str", "required": True, "constraint": "非空字符串", "description": "设备编号"},
            {"name": "vibration_mm_s", "type": "float", "required": True, "constraint": "0 ~ 100", "description": "实测振动速度（mm/s）"},
            {"name": "temperature_c", "type": "float", "required": True, "constraint": "-40 ~ 300", "description": "实测温度（℃）"},
            {"name": "rated_vibration_mm_s", "type": "float", "required": False, "constraint": "0.1 ~ 50，默认 4.5", "description": "振动额定值"},
            {"name": "rated_temperature_c", "type": "float", "required": False, "constraint": "0 ~ 300，默认 75", "description": "温度额定值"},
        ],
        "outputs": [
            {"name": "device_id", "type": "str", "required": True, "constraint": "原样返回", "description": "设备编号"},
            {"name": "health_score", "type": "float", "required": True, "constraint": "0 ~ 100", "description": "健康度评分，越高越好"},
            {"name": "alert_level", "type": "str", "required": True, "constraint": "normal / warning / critical", "description": "告警等级"},
            {"name": "vibration_ratio", "type": "float", "required": True, "constraint": "≥ 0", "description": "实测振动 / 额定振动"},
            {"name": "temperature_ratio", "type": "float", "required": True, "constraint": "≥ 0", "description": "实测温度 / 额定温度"},
            {"name": "anomalies", "type": "list[str]", "required": True, "constraint": "可为空", "description": "异常说明列表"},
            {"name": "processing_time_ms", "type": "float", "required": True, "constraint": "≥ 0", "description": "服务内部处理耗时"},
        ],
        "assumptions": [
            "健康度采用可解释的线性超限惩罚规则，不代表真实故障诊断结论。",
            "额定值未提供时使用默认值（振动 4.5 mm/s、温度 75 ℃）。",
            "阈值：健康度 ≥ 75 正常，45 ~ 75 预警，< 45 严重告警。",
        ],
    },
    "quality_statistics": {
        "label": "质检数据统计",
        "title": "质检批量数据统计服务",
        "description": (
            "接收一批尺寸测量值及规格上下限，计算均值、总体标准差、合格率"
            "与过程能力指数 Cpk，适合在边缘节点就近完成批量质量统计。"
        ),
        "code": QUALITY_CODE,
        "tests": QUALITY_TESTS,
        "inputs": [
            {"name": "batch_id", "type": "str", "required": True, "constraint": "非空字符串", "description": "批次编号"},
            {"name": "samples", "type": "list[float]", "required": True, "constraint": "长度 2 ~ 5000", "description": "测量值列表"},
            {"name": "lower_limit", "type": "float", "required": True, "constraint": "小于 upper_limit", "description": "规格下限 LSL"},
            {"name": "upper_limit", "type": "float", "required": True, "constraint": "大于 lower_limit", "description": "规格上限 USL"},
        ],
        "outputs": [
            {"name": "batch_id", "type": "str", "required": True, "constraint": "原样返回", "description": "批次编号"},
            {"name": "sample_count", "type": "int", "required": True, "constraint": "≥ 2", "description": "样本数量"},
            {"name": "mean", "type": "float", "required": True, "constraint": "—", "description": "样本均值"},
            {"name": "std", "type": "float", "required": True, "constraint": "≥ 0", "description": "总体标准差"},
            {"name": "pass_rate", "type": "float", "required": True, "constraint": "0 ~ 1", "description": "规格内样本占比"},
            {"name": "cpk", "type": "float | null", "required": True, "constraint": "标准差为 0 时为 null", "description": "过程能力指数 Cpk"},
            {"name": "out_of_spec", "type": "int", "required": True, "constraint": "≥ 0", "description": "超规格样本数"},
            {"name": "processing_time_ms", "type": "float", "required": True, "constraint": "≥ 0", "description": "服务内部处理耗时"},
        ],
        "assumptions": [
            "标准差使用总体标准差（pstdev），Cpk = min(USL − 均值, 均值 − LSL) / (3σ)。",
            "样本标准差为 0 时 Cpk 无定义，返回 null 而不是除零。",
            "统计仅做描述性计算，不假设数据严格服从正态分布。",
        ],
    },
}
