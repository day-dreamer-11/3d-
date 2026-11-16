"""把已有场景数据迁移到“OIP 工程内容寻址 + 轻量版本快照”格式。

用法：
    python tools/compact_scene_storage.py --root data/projects demo_seed/projects
    python tools/compact_scene_storage.py --root data/projects --dry-run

效果：
- OIP 工程按内容哈希存到 scenes/<sid>/oip/<hash>.json，相同工程只存一份；
- versions/*.json 只保留设备/连接/设置 + OIP 哈希引用（从 ~20MB 降到 KB 级）；
- scenes/<sid>/summary.json 供列表页快速读取；
- scene.json 仍保留完整 OIP 工程（编辑器需要），并写入 content_hash。
可重复执行；已迁移的数据不会重复膨胀。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.scene import store  # noqa: E402


def _size(paths: list[Path]) -> int:
    return sum(p.stat().st_size for p in paths if p.is_file())


def migrate_scene(project_root: Path, project_id: str, scene_dir: Path, *, dry_run: bool) -> tuple[int, int]:
    scene_file = scene_dir / store.SCENE_FILE
    if not scene_file.exists():
        return 0, 0
    before = _size(list(scene_dir.rglob("*.json")))
    scene_id = scene_dir.name
    doc = store.load_scene(project_id, scene_id, base_dir=project_root)
    oip_hash = store._oip_hash(doc.oip_project)
    digest = store.content_hash(doc, oip_hash=oip_hash)

    scene_payload: dict[str, Any] = doc.to_dict()
    scene_payload["content_hash"] = digest
    if oip_hash:
        scene_payload["oip_project"] = None
        scene_payload["oip_blob"] = oip_hash
    version_files = sorted((scene_dir / store.VERSIONS_DIR).glob("*.json"))
    version_payloads: list[tuple[Path, dict[str, Any], dict[str, Any] | None, str | None]] = []
    for path in version_files:
        vraw = store._read_json(path)
        vdoc = store.load_scene(project_id, scene_id, base_dir=project_root, version=int(path.stem))
        vdigest = vraw.get("content_hash") or store.content_hash(vdoc)
        voip_hash = store._oip_hash(vdoc.oip_project)
        payload = vdoc.to_dict()
        payload["content_hash"] = vdigest
        if voip_hash:
            payload["oip_project"] = None
            payload["oip_blob"] = voip_hash
        version_payloads.append((path, payload, vdoc.oip_project, voip_hash))

    if dry_run:
        estimated = len(json.dumps(scene_payload, ensure_ascii=False).encode("utf-8"))
        estimated += sum(len(json.dumps(p, ensure_ascii=False).encode("utf-8")) for _, p, _, _ in version_payloads)
        blobs: dict[str, int] = {}
        if doc.oip_project is not None and oip_hash:
            blobs[oip_hash] = len(json.dumps(doc.oip_project, ensure_ascii=False).encode("utf-8"))
        for _, _, voip, vhash in version_payloads:
            if voip is not None and vhash and vhash not in blobs:
                blobs[vhash] = len(json.dumps(voip, ensure_ascii=False).encode("utf-8"))
        estimated += sum(blobs.values())
        return before, estimated

    store._write_oip_blob(doc.oip_project, project_id, doc.scene_id, project_root, oip_hash=oip_hash)
    store._atomic_write_json(scene_file, scene_payload)
    store._write_summary(doc, project_root, digest)
    for path, payload, voip_project, voip_hash in version_payloads:
        store._write_oip_blob(voip_project, project_id, doc.scene_id, project_root, oip_hash=voip_hash)
        store._atomic_write_json(path, payload)
    after = _size(list(scene_dir.rglob("*.json")))
    return before, after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", nargs="+", default=["data/projects"], help="项目根目录（可多个）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    args = parser.parse_args()

    total_before = total_after = 0
    for root_arg in args.root:
        root = Path(root_arg)
        if not root.exists():
            print(f"跳过（不存在）：{root}")
            continue
        for project_dir in sorted(root.iterdir()):
            if not (project_dir / "project.json").exists():
                continue
            scenes_dir = project_dir / "scenes"
            if not scenes_dir.exists():
                continue
            for scene_dir in sorted(scenes_dir.iterdir()):
                if not (scene_dir / store.SCENE_FILE).exists():
                    continue
                before, after = migrate_scene(root, project_dir.name, scene_dir, dry_run=args.dry_run)
                total_before += before
                total_after += after
                print(f"{project_dir.name}/{scene_dir.name}: {before / 1048576:.1f} MB -> {after / 1048576:.1f} MB")
    saved = total_before - total_after
    print(f"合计：{total_before / 1048576:.1f} MB -> {total_after / 1048576:.1f} MB（节省 {saved / 1048576:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
