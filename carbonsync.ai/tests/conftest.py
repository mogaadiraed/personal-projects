"""Shared test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _login_off(monkeypatch):
    """Pages render as a local administrator (the login has its own tests in test_auth.py)."""
    monkeypatch.setenv("CARBONSYNC_AUTH", "off")


@pytest.fixture(autouse=True)
def _isolated_evaluation(tmp_path_factory, monkeypatch):
    """Tests never read this PC's evaluation results (they choose the default local model)."""
    from carbonsync.assistant import evaluation

    root = tmp_path_factory.mktemp("eval_root")
    monkeypatch.setattr(evaluation, "get_project_root", lambda: root)
    return root
