"""从 Poly Haven 下载 CC0 工业模型并打包成单文件 .glb（内嵌贴图）。

用法：python tools/fetch_ph_glb.py
输出：data/decoration/<中文名>.glb
仅用于三维装饰演示；来源 Poly Haven，CC0 许可，无需署名（本文件保留记录）。
"""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "decoration"
ASSETS = {
    "tool_cart": "装饰 工具车",
    "industrial_storage_cart": "装饰 储物车",
    "WetFloorSign_01": "装饰 地滑牌",
    "ladder_sectioned_01": "装饰 人字梯",
    "portable_welding_cart": "装饰 焊接推车",
    "power_box_01": "装饰 配电箱",
}


def curl(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "-sS", "-m", "120", "-o", str(dest), url], check=True)


def fetch_json(url: str) -> dict:
    for attempt in range(3):
        result = subprocess.run(["curl", "-sS", "-m", "60", url], capture_output=True)
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except json.JSONDecodeError:
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"无法获取 JSON：{url}")


def pack_glb(gltf_path: Path) -> bytes:
    gltf = json.loads(gltf_path.read_text(encoding="utf-8"))
    base = gltf_path.parent
    blob = bytearray()

    def load_uri(uri: str) -> bytes:
        if uri.startswith("data:"):
            import base64

            return base64.b64decode(uri.split(",", 1)[1])
        return (base / urllib.parse.unquote(uri)).read_bytes()

    starts = []
    for buffer in gltf.get("buffers", []):
        while len(blob) % 4:
            blob.append(0)
        starts.append(len(blob))
        blob.extend(load_uri(buffer["uri"]) if buffer.get("uri") else b"")
        buffer.pop("uri", None)
        buffer.pop("name", None)
        buffer["byteLength"] = len(blob) - starts[-1]
    for view in gltf.get("bufferViews", []):
        view["buffer"] = 0
        view["byteOffset"] = starts[view.get("buffer", 0)] + int(view.get("byteOffset", 0))

    gltf["bufferViews"] = gltf.get("bufferViews", [])
    for image in gltf.get("images", []):
        if "uri" not in image:
            continue
        data = load_uri(image["uri"])
        while len(blob) % 4:
            blob.append(0)
        view = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        gltf["bufferViews"].append(view)
        image.pop("uri")
        image["bufferView"] = len(gltf["bufferViews"]) - 1
        image["mimeType"] = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
        blob.extend(data)

    while len(blob) % 4:
        blob.append(0)
    if not gltf.get("buffers"):
        gltf["buffers"] = [{"byteLength": len(blob)}]
    else:
        gltf["buffers"] = [{"byteLength": len(blob)}]

    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(payload) % 4:
        payload += b" "
    total = 12 + 8 + len(payload) + 8 + len(blob)
    glb = struct.pack("<III", 0x46546C67, 2, total)
    glb += struct.pack("<II", len(payload), 0x4E4F534A) + payload
    glb += struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    return glb


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for asset, display in ASSETS.items():
        out = OUT_DIR / f"{display}.glb"
        if out.exists():
            print(f"skip {asset}: {out.name} exists")
            continue
        meta = fetch_json(f"https://api.polyhaven.com/files/{asset}")
        entry = meta["gltf"]["1k"]
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            gltf_info = entry["gltf"]
            gltf_name = Path(urllib.parse.urlparse(gltf_info["url"]).path).name
            curl(gltf_info["url"], tmp / gltf_name)
            for rel, info in (gltf_info.get("include") or {}).items():
                curl(info["url"], tmp / rel)
            out = OUT_DIR / f"{display}.glb"
            out.write_bytes(pack_glb(tmp / gltf_name))
            print(f"{asset} -> {out.name} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
