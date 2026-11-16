"""从实机录屏项目制作独立的评审案例，不修改原项目和实验指标。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "PRJ-20261111-3FEF98"
TARGET_ID = "PRJ-VIDEO-DEMO-20261112"


def main() -> None:
    source = ROOT / "data" / "projects" / SOURCE_ID
    target = ROOT / "demo_seed" / "projects" / TARGET_ID
    if target.exists():
        raise SystemExit(f"目标已存在，拒绝覆盖：{target}")
    experiments = sorted((source / "experiments").glob("*.json"))
    replacements = {SOURCE_ID: TARGET_ID, **{p.stem: "VID-" + p.stem for p in experiments}}
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        dest = target / rel.with_name(replacements.get(path.stem, path.stem) + path.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {".json", ".md", ".html"}:
            text = path.read_text(encoding="utf-8")
            for old, new in replacements.items():
                text = text.replace(old, new)
            dest.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(path, dest)
    project_path = target / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project.update(
        name="视频演示案例 · 输送与质检产线",
        description="参赛实机录屏同款案例，已保存三维产线、生产仿真、优化前后对比及任务调度结果。可直接查看，也可修改后重新仿真。",
        demo_preset={"duration_sec": 600, "seed": 42, "source_project_id": SOURCE_ID},
    )
    project_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "default_project_id": TARGET_ID,
        "default_scene_id": "SCN-20261030-234230-6C86",
        "title": project["name"],
        "recording": "屏幕录制 2026-11-11 223731.mp4",
        "source_project_id": SOURCE_ID,
        "experiments": {old: new for old, new in replacements.items() if old != SOURCE_ID},
        "note": "独立副本仅重映射项目与实验 ID；保留原始参数、时间和结果，避免与已有 SQLite 实验冲突。",
    }
    (ROOT / "demo_seed" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {TARGET_ID}: {len(experiments)} archived experiments")


if __name__ == "__main__":
    main()
