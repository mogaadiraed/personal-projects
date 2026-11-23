"""Pages of the current navigation, so a page can link to another (filled by ``app.main`` on each run)."""
from __future__ import annotations

from typing import Any

import streamlit as st

PAGES: dict[str, Any] = {}


def link(path: str, label: str, icon: str | None = None, **kwargs: Any) -> None:
    """``st.page_link`` to a page of the menu; plain text when the page is hidden or unknown (tests)."""
    page = PAGES.get(path)
    if page is not None:
        st.page_link(page, label=label, icon=icon, **kwargs)
    else:
        st.caption(label)
