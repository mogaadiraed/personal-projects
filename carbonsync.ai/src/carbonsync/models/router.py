"""Forecast router: which model the pipeline and "Auto" use, and the rule in words.

Kept free of model imports (Prophet, Chronos) so pages, reports, alerts and the assistant can read the rule
without loading the models. ``router`` in config.yaml: ``model``, ``fallback``, ``min_days``.
"""
from __future__ import annotations

from typing import Any

MODELS = ("chronos", "ens_pc", "prophet")
MODEL_LABELS = {"chronos": "Chronos-2", "ens_pc": "Ens-PC", "prophet": "Prophet"}
ROUTER_DEFAULTS = {"model": "chronos", "fallback": "prophet", "min_days": 60}


def router_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Cleaned ``router`` section: model, fallback and the minimum history for the model (days).

    Older configs only had ``min_days_for_ensemble`` (Prophet below it, Ens-PC above): they keep that rule
    until a ``model`` is set, so an old config.yaml still behaves as it did.
    """
    raw = dict((cfg or {}).get("router", {}) or {})
    out = dict(ROUTER_DEFAULTS)
    if "model" not in raw and "min_days_for_ensemble" in raw:
        out.update(model="ens_pc", fallback="prophet", min_days=int(raw["min_days_for_ensemble"]))
    if raw.get("model") in MODELS:
        out["model"] = raw["model"]
    if raw.get("fallback") in MODELS:
        out["fallback"] = raw["fallback"]
    if raw.get("min_days") is not None:
        out["min_days"] = max(0, int(raw["min_days"]))
    return out


def choose_model(history_days: int, cfg: dict[str, Any] | None = None) -> str:
    """The router: the configured model (Chronos-2 by default), or the fallback under ``min_days`` of history."""
    r = router_settings(cfg)
    return r["model"] if history_days >= r["min_days"] else r["fallback"]


def router_rule(cfg: dict[str, Any] | None, history_days: int | None = None) -> dict[str, Any]:
    """The rule in words (FR / EN) and the current choice, for pages, reports and the assistant."""
    r = router_settings(cfg)
    m, fb, n = MODEL_LABELS[r["model"]], MODEL_LABELS[r["fallback"]], r["min_days"]
    out = {**r, "label": m, "fallback_label": fb,
           "fr": f"{m} dès {n} jours d'historique, sinon {fb} ; {fb} aussi si {m} ne peut pas tourner",
           "en": f"{m} from {n} days of history, otherwise {fb}; {fb} also if {m} cannot run"}
    if r["model"] == r["fallback"]:
        out["fr"], out["en"] = f"{m} pour tout historique", f"{m} for any history"
    if history_days is not None:
        out["history_days"] = int(history_days)
        out["choice"] = choose_model(int(history_days), cfg)
    return out
