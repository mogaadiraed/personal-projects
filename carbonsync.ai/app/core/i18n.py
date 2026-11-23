"""Language selection (FR / EN) shared by every page."""
from __future__ import annotations

import streamlit as st

from app.data.loader import load_config

SUPPORTED = ("fr", "en")


def lang() -> str:
    """Active UI language: session choice first, then config.yaml, then French."""
    if "lang" not in st.session_state:
        default = load_config().get("app", {}).get("language", "fr")
        st.session_state["lang"] = default if default in SUPPORTED else "fr"
    return st.session_state["lang"]


def tr(fr: str, en: str) -> str:
    """Return the string for the active language."""
    return fr if lang() == "fr" else en
