"""新页面与旧页面共用的轻量 UI helper。"""

from __future__ import annotations

from typing import Any, Optional

import streamlit as st

from integration.project_store import ProjectError, load_project


def hero(title: str, subtitle: str, tags: list[str]) -> None:
    tag_html = "".join(f'<span class="ec-tag">{tag}</span>' for tag in tags)
    st.markdown(
        f'<div class="ec-hero"><h1>{title}</h1><p>{subtitle}</p>{tag_html}</div>',
        unsafe_allow_html=True,
    )


def current_project(required: bool = True) -> Optional[dict[str, Any]]:
    project_id = st.session_state.get("project_id")
    if not project_id:
        if required:
            st.info("当前没有选中的项目。请在左侧导航中新建或选择项目。")
        return None
    try:
        return load_project(project_id)
    except ProjectError as exc:
        st.error(f"项目加载失败：{exc}")
        return None


def goto_page(page: str) -> None:
    # platform_page 是侧栏 radio 的 key，不能在 widget 实例化后直接改写；
    # 先写入待跳转标记，下一次运行在 render_sidebar 创建 radio 之前应用。
    st.session_state["_pending_page"] = page
    st.rerun()


def package_version(name: str) -> str:
    try:
        import importlib.metadata as md

        return md.version(name)
    except Exception:  # noqa: BLE001
        return "未安装"


__all__ = ["current_project", "goto_page", "hero", "package_version"]
