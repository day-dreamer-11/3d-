"""装饰模型（外部导入、名称以「装饰」开头）的映射行为。"""

from integration.scene.mapping import map_oip_project


def _part(part_id: str, name: str, package_id: str) -> dict:
    return {"id": part_id, "type": "part", "name": name, "packageId": package_id, "position": [0, 0, 0]}


def test_decoration_is_display_only_without_warning():
    result = map_oip_project({"parts": [_part("p1", "装饰·工具车", "user.part-7")]})
    assert len(result.devices) == 1
    device = result.devices[0]
    assert device.type == "decoration"
    assert device.simulation_type == "display_only"
    assert device.metadata.get("decoration") is True
    assert result.issues == []


def test_placed_part_without_name_uses_package_category():
    project = {
        "parts": [{"id": "pt_7", "type": "part", "packageId": "user.part-7", "position": [0, 0, 0]}],
        "packages": [{"id": "user.part-7", "name": "装饰·工具车", "category": "装饰"}],
    }
    result = map_oip_project(project)
    device = result.devices[0]
    assert device.type == "decoration"
    assert device.name == "装饰·工具车"
    assert result.issues == []


def test_unknown_non_decoration_still_warns():
    result = map_oip_project({"parts": [_part("p2", "Mystery Machine", "user.part-8")]})
    assert result.devices[0].type == "unknown"
    assert any(issue.code == "unsupported_device" for issue in result.issues)
