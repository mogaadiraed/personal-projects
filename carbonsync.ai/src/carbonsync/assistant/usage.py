"""AI provider settings, token usage, cost and the daily spending cap (all local, git-ignored).

* ``data/assistant/settings.json``: per provider (local Ollama: default model and context size;
  Ollama Cloud: model; OpenRouter: model, fallback, data-collection filter; Claude: model and
  effort) and one daily cap shared by every cloud provider.
  No key here: keys live in the Windows Credential Manager, see ``vault``.
* ``data/assistant/usage.json``: tokens and cost per day, provider and model.

Claude prices are below; Ollama Cloud prices are in ``providers.ollama_cloud`` (that adapter
passes its cost in). All are list prices in USD per million tokens (checked 2026-11-20). The meter
is an estimate for the spending cap; each provider's console has the invoice figures.
"""
from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from carbonsync.assistant.context import get_project_root
from carbonsync.obs import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()

# model: (input, output, cache-read multiple of input). 5-minute cache writes cost 1.25 × input.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.05),
    "claude-sonnet-5-5": (2.00, 10.00, 0.05),
    "claude-haiku-5-5": (0.10, 0.50, 0.10),   # prompts of 100K tokens or fewer
    "claude-opus-5": (5.00, 25.00, 0.10),     # possible server-side fallback targets
    "claude-opus-4-8": (5.00, 25.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, 0.10),
}
_HAIKU_LONG = (0.50, 2.50, 0.10)              # Haiku 5.5 above 100K prompt tokens
CACHE_WRITE = 1.25

DEFAULTS: dict[str, Any] = {
    "ollama": {"model": None, "num_ctx": 16384, "stopped": False},  # stopped: GPU freed by hand, no auto-load
    "ollama_cloud": {"model": "deepseek-v4-pro"},
    "openrouter": {"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "fallback": True, "deny_collection": False},
    "anthropic": {"model": "claude-opus-5-5", "effort": "medium"},
    "daily_cap_usd": 5.0,
}
NUM_CTX = [8192, 16384, 32768]
EFFORTS = ["low", "medium", "high"]


def _dir(root: Path | None = None) -> Path:
    d = (root or get_project_root()) / "data" / "assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        log.warning("Unreadable file, defaults used: %s", path.name)
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def load_settings(root: Path | None = None) -> dict[str, Any]:
    saved = _read(_dir(root) / "settings.json")
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    if out["anthropic"].get("effort") not in EFFORTS:
        out["anthropic"]["effort"] = "medium"
    if out["ollama"].get("num_ctx") not in NUM_CTX:
        out["ollama"]["num_ctx"] = DEFAULTS["ollama"]["num_ctx"]
    out["ollama"]["stopped"] = out["ollama"].get("stopped") is True
    for p in ("ollama_cloud", "openrouter"):
        if not isinstance(out[p].get("model"), str) or not out[p]["model"].strip():
            out[p]["model"] = DEFAULTS[p]["model"]
    try:
        out["daily_cap_usd"] = max(0.0, float(out["daily_cap_usd"]))
    except (TypeError, ValueError):
        out["daily_cap_usd"] = DEFAULTS["daily_cap_usd"]
    return out


def save_settings(settings: dict[str, Any], root: Path | None = None) -> None:
    clean = {k: v for k, v in settings.items() if "key" not in k.lower()}  # never a key in this file
    with _LOCK:
        _write(_dir(root) / "settings.json", clean)
    log.info("Assistant settings saved: %s", json.dumps(clean, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------
def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
             cache_write: int = 0) -> float:
    """Estimated cost of one call. ``input_tokens`` excludes the cached parts (as the API reports it)."""
    price = PRICES.get(model)
    if price is None:  # unknown model: price as the most expensive one so the cap stays safe
        price = max(PRICES.values(), key=lambda p: p[1])
    if model == "claude-haiku-5-5" and input_tokens + cache_read + cache_write > 100_000:
        price = _HAIKU_LONG
    inp, out, read_mult = price
    total = (input_tokens * inp + output_tokens * out + cache_read * inp * read_mult
             + cache_write * inp * CACHE_WRITE) / 1_000_000
    return round(total, 6)


def record(provider: str, model: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0,
           cache_write: int = 0, cost: float | None = None, root: Path | None = None) -> float:
    """Adds one call to today's usage; returns its cost."""
    c = cost_usd(model, input_tokens, output_tokens, cache_read, cache_write) if cost is None else cost
    path = _dir(root) / "usage.json"
    with _LOCK:
        data = _read(path)
        day = data.setdefault("days", {}).setdefault(date.today().isoformat(), {})
        row = day.setdefault(f"{provider}:{model}", {"requests": 0, "input": 0, "output": 0, "cache_read": 0,
                                                      "cache_write": 0, "cost_usd": 0.0})
        row["requests"] += 1
        row["input"] += int(input_tokens)
        row["output"] += int(output_tokens)
        row["cache_read"] += int(cache_read)
        row["cache_write"] += int(cache_write)
        row["cost_usd"] = round(row["cost_usd"] + c, 6)
        _write(path, data)
    return c


def spent(day: date | None = None, root: Path | None = None) -> float:
    rows = _read(_dir(root) / "usage.json").get("days", {}).get((day or date.today()).isoformat(), {})
    return round(sum(float(r.get("cost_usd", 0)) for r in rows.values()), 6)


def history(days: int = 30, root: Path | None = None) -> list[dict[str, Any]]:
    """One row per day and model, newest first, for the last ``days`` days."""
    data = _read(_dir(root) / "usage.json").get("days", {})
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    out = []
    for d in sorted(data, reverse=True):
        if d < since:
            continue
        for name, r in data[d].items():
            provider, _, model = name.partition(":")
            out.append({"date": d, "provider": provider, "model": model, **r})
    return out


def check_cap(settings: dict[str, Any] | None = None, root: Path | None = None) -> tuple[bool, float, float]:
    """(allowed, spent today, cap). A cap of 0 means no limit."""
    cap = float((settings or load_settings(root)).get("daily_cap_usd", 0) or 0)
    today = spent(root=root)
    return (cap <= 0 or today < cap), today, cap
