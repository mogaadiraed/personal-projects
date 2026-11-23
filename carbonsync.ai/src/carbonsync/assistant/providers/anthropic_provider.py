"""Claude through the Anthropic API: the Expert mode provider (user's own API key).

How each call is built (Claude Opus 5.5 / Sonnet 5.5 / Haiku 5.5 rules, checked 2026-11-20):

* Streaming beta Messages API, adaptive thinking (always on for these models) with an explicit
  ``effort``; ``display: "summarized"`` when the Reasoning switch is on, so its summary shows.
* System = [expert brief, current context]. Tools render before the system, so the stable
  prefix (tools + brief) gets an explicit cache breakpoint; top-level automatic caching covers
  the growing conversation. Cache reads cost 5-10 % of normal input.
* Preserved reasoning: Claude's own content blocks (thinking + signatures) are kept with each
  assistant message (``raw``) and replayed unchanged; the engine keeps the history append-only
  (same system and tools on every call, no trimming of old tool results). If the history ever
  changes anyway, ``prefix_mismatch_behavior: "drop_block"`` drops the stale reasoning instead
  of failing, and a rejected signature is retried once without thinking blocks.
* Server-side fallback (``fallbacks: "default"``) on Opus and Sonnet: if Claude declines a
  request for policy reasons, Anthropic retries it on another Claude model (Haiku has none).
* Tool inputs stream eagerly; they are validated by ``tools.execute`` before anything runs.
  ``refusal`` / ``max_tokens`` stop reasons are handled before any tool runs. All tool results
  of a round go back in one user message.
* Every call is metered (``usage.json``) and refused once the daily spending cap is reached.
"""
from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from carbonsync.assistant import usage, vault
from carbonsync.assistant.providers.base import (
    Delta,
    Provider,
    ProviderError,
    ToolCall,
    Turn,
)
from carbonsync.assistant.providers.registry import WHERE
from carbonsync.obs import get_logger

log = get_logger(__name__)

MODELS: dict[str, dict[str, Any]] = {
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "role": ("le plus capable", "most capable"), "fallback": True},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "role": ("équilibré", "balanced"), "fallback": True},
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5", "role": ("rapide, économique", "fast, low cost"),
                         "fallback": False},
}
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 20_000           # thinking + answer of one call
BETA_BINDING = "thinking-binding-controls-2026-08-01"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
_THINKING = ("thinking", "redacted_thinking")
_SETTINGS = WHERE["anthropic"]


def model_label(model: str) -> str:
    return MODELS.get(model, {}).get("label", model)


# ---------------------------------------------------------------------------
# Neutral messages → Anthropic messages
# ---------------------------------------------------------------------------
def _is_error(content: str) -> bool:
    try:
        return json.loads(content).get("ok") is False
    except (ValueError, AttributeError):
        return False


def to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Engine messages → Anthropic ``messages``. Claude's own turns are replayed from ``raw``
    (unchanged); turns from other engines become text + tool_use blocks; consecutive tool
    results are grouped in one user message; same-role neighbours are merged."""
    out: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m["role"]
        if role == "user":
            add("user", [{"type": "text", "text": m.get("content") or "…"}])
        elif role == "assistant":
            raw = m.get("raw") or {}
            if raw.get("provider") == "anthropic" and raw.get("content"):
                add("assistant", copy.deepcopy(raw["content"]))
                continue
            blocks: list[dict[str, Any]] = []
            if (m.get("content") or "").strip():
                blocks.append({"type": "text", "text": m["content"]})
            for c in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("arguments") or {}})
            add("assistant", blocks)
        elif role == "tool":
            res = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m.get("content") or "{}"}
            if _is_error(res["content"]):
                res["is_error"] = True
            add("user", [res])
    return _pair_tool_results(out)


def _pair_tool_results(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool_use needs its tool_result in the next user message (the engine runs at most
    6 calls per round): missing ones get an error result, in a stable way."""
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        ids = [b["id"] for b in m["content"] if b.get("type") == "tool_use"]
        if not ids:
            continue
        if i + 1 >= len(msgs) or msgs[i + 1]["role"] != "user":
            msgs.insert(i + 1, {"role": "user", "content": []})
        nxt = msgs[i + 1]["content"]
        have = {b.get("tool_use_id") for b in nxt if b.get("type") == "tool_result"}
        missing = [{"type": "tool_result", "tool_use_id": t, "is_error": True,
                    "content": json.dumps({"ok": False, "error": "not run: too many tool calls in one round"})}
                   for t in ids if t not in have]
        if missing:
            n_results = sum(1 for b in nxt if b.get("type") == "tool_result")
            nxt[n_results:n_results] = missing
    return msgs


def strip_thinking(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recovery only: the same messages without any thinking block."""
    out = []
    for m in msgs:
        if m["role"] == "assistant":
            blocks = [b for b in m["content"] if b.get("type") not in _THINKING] or [{"type": "text", "text": "…"}]
            m = {**m, "content": blocks}
        out.append(m)
    return out


def after_fallback(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After a mid-answer server-side fallback, the declined part keeps only its text: thinking
    and tool_use blocks before the last ``fallback`` block are left out (the fallback block stays
    where it is). Empty text blocks are dropped (the API rejects them on replay)."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    out = []
    for i, b in enumerate(blocks):
        if i < last and b.get("type") not in ("text", "fallback"):
            continue
        if b.get("type") == "text" and not b.get("text"):
            continue
        out.append(b)
    return out


def tool_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"],
             "eager_input_streaming": True} for s in specs]


def system_blocks(system: str) -> list[dict[str, Any]]:
    parts = [p for p in (getattr(system, "parts", None) or (system,)) if p and p.strip()]
    blocks: list[dict[str, Any]] = [{"type": "text", "text": p} for p in parts]
    if len(blocks) > 1:  # the brief: stable for every conversation
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def _api_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(getattr(exc, "message", "") or exc)


def provider_error(exc: Exception, model: str) -> ProviderError:
    """An SDK error → a message the user can act on (FR / EN). Never includes the key."""
    import anthropic

    status = getattr(exc, "status_code", None)
    log.warning("Anthropic API error %s (status %s, request %s): %s", type(exc).__name__, status,
                getattr(exc, "request_id", None), _api_message(exc)[:200])
    sfr, sen = _SETTINGS
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError(f"Clé API Anthropic refusée (invalide ou révoquée) : vérifiez-la dans {sfr}.",
                             f"Anthropic API key rejected (invalid or revoked): check it in {sen}.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError(f"Cette clé API n'a pas accès à {model_label(model)}.",
                             f"This API key has no access to {model_label(model)}.")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(f"Modèle {model} introuvable pour cette clé : choisissez-en un autre dans {sfr}.",
                             f"Model {model} not found for this key: choose another one in {sen}.")
    if isinstance(exc, anthropic.RateLimitError):
        response = getattr(exc, "response", None)
        wait = response.headers.get("retry-after") if response is not None else None
        return ProviderError("Limite de débit Anthropic atteinte" + (f" : réessayez dans {wait} s." if wait else "."),
                             "Anthropic rate limit reached" + (f": retry in {wait} s." if wait else "."))
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError("Pas de connexion à l'API Anthropic : vérifiez la connexion internet.",
                             "No connection to the Anthropic API: check the internet connection.")
    if isinstance(exc, anthropic.BadRequestError):
        msg = _api_message(exc)
        if "credit balance" in msg.lower():
            return ProviderError("Crédit insuffisant sur le compte Anthropic (console Claude → Billing).",
                                 "Not enough credit on the Anthropic account (Claude console → Billing).")
        return ProviderError(f"Requête refusée par l'API Anthropic : {msg[:200]}",
                             f"Request rejected by the Anthropic API: {msg[:200]}")
    if isinstance(exc, anthropic.APIStatusError) and (status or 0) >= 500:
        return ProviderError(f"Service Anthropic indisponible ou surchargé ({status}) : réessayez dans un moment.",
                             f"Anthropic service unavailable or overloaded ({status}): try again shortly.")
    return ProviderError(f"Erreur de l'API Anthropic : {_api_message(exc)[:200]}",
                         f"Anthropic API error: {_api_message(exc)[:200]}")


def _thinking_rejected(exc: Exception) -> bool:
    msg = _api_message(exc).lower()
    return "thinking" in msg and "signature" in msg


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class AnthropicProvider(Provider):
    key = "anthropic"
    label = "Claude"
    privacy = "aggregated"        # cloud: tools only return monthly or coarser aggregates
    supports_thinking = True      # the switch shows the reasoning summary (Claude always reasons)
    append_only = True
    history_chars = 600_000       # ≈ 170k tokens of conversation; the context window is 1M

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "medium", *, client: Any = None,
                 settings: dict[str, Any] | None = None, root: Path | None = None) -> None:
        super().__init__(model or DEFAULT_MODEL)
        self.effort = effort if effort in usage.EFFORTS else "medium"
        self._client_obj = client
        self.settings = settings
        self.root = root

    @property
    def display_name(self) -> str:
        return f"Expert · {model_label(self.model)}"

    @property
    def fallback(self) -> bool:
        return bool(MODELS.get(self.model, {}).get("fallback"))

    def _client(self) -> Any:
        if self._client_obj is not None:
            return self._client_obj
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("Le paquet Python « anthropic » manque : pip install anthropic",
                                "The Python package 'anthropic' is missing: pip install anthropic") from exc
        key, _source = vault.get_key("anthropic")
        if not key:
            raise ProviderError(f"Aucune clé API Anthropic : ajoutez-la dans {_SETTINGS[0]}.",
                                f"No Anthropic API key: add it in {_SETTINGS[1]}.")
        self._client_obj = anthropic.Anthropic(api_key=key, max_retries=2, timeout=600.0)
        return self._client_obj

    def params(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
               think: bool = False, final: bool = False) -> dict[str, Any]:
        """The request body (also used by the tests)."""
        betas = [BETA_BINDING]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system_blocks(system),
            "messages": to_anthropic(messages),
            "thinking": {"type": "adaptive", "display": "summarized" if think else "omitted",
                         "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            body["tools"] = tool_specs(tools)
            body["tool_choice"] = {"type": "none" if final else "auto"}
        if self.fallback:
            betas.append(BETA_FALLBACK)
            body["fallbacks"] = "default"
        body["betas"] = betas
        return body

    def stream(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
               think: bool = False, final: bool = False) -> Iterator[Delta | Turn]:
        import anthropic

        allowed, spent, cap = usage.check_cap(self.settings, self.root)
        if not allowed:
            raise ProviderError(
                f"Plafond de dépense du jour atteint ({spent:.2f} $ sur {cap:.2f} $). Il repart à zéro demain ; "
                f"modifiable dans {WHERE['overview'][0]}.",
                f"Today's spending cap reached (${spent:.2f} of ${cap:.2f}). It resets tomorrow; change it in "
                f"{WHERE['overview'][1]}.")
        client = self._client()
        body = self.params(system, messages, tools, think=think, final=final)
        t0 = time.perf_counter()
        msg = None
        stripped = False
        bad_json = 0
        while msg is None:
            emitted = False
            try:
                with client.beta.messages.stream(**body) as st:
                    for event in st:
                        if event.type == "text" and event.text:
                            emitted = True
                            yield Delta("text", event.text)
                        elif event.type == "thinking" and think and event.thinking:
                            yield Delta("thinking", event.thinking)
                    msg = st.get_final_message()
            except anthropic.BadRequestError as exc:
                if _thinking_rejected(exc) and not stripped and not emitted:
                    log.warning("Anthropic: stored reasoning rejected, retrying once without it")
                    body["messages"], stripped = strip_thinking(body["messages"]), True
                    continue
                raise provider_error(exc, self.model) from exc
            except anthropic.APIError as exc:
                raise provider_error(exc, self.model) from exc
            except ValueError as exc:  # a tool input the SDK could not parse at all
                bad_json += 1
                if emitted or bad_json > 2:
                    raise ProviderError("Réponse de Claude illisible (outil mal formé).",
                                        "Unreadable answer from Claude (malformed tool call).") from exc
                log.warning("Anthropic: unparseable tool input, asking again (%d)", bad_json)
        return_turn = self._finish(msg, think)
        return_turn.seconds = time.perf_counter() - t0
        log.info("Model %s: %d in (%d from cache, %d written) / %d out tokens, %.1fs, ≈ $%.4f, stop %s, request %s%s",
                 return_turn.model, return_turn.input_tokens, return_turn.cache_read, return_turn.cache_write,
                 return_turn.output_tokens, return_turn.seconds, return_turn.cost_usd, msg.stop_reason,
                 getattr(msg, "_request_id", None),
                 f", tools: {', '.join(c.name for c in return_turn.tool_calls)}" if return_turn.tool_calls else "")
        yield return_turn

    def _finish(self, msg: Any, think: bool) -> Turn:
        blocks = after_fallback([b.to_dict(exclude_none=True) for b in msg.content])
        u = msg.usage
        served = getattr(msg, "model", None) or self.model
        if served != self.model:
            log.info("Anthropic: answered by %s (server-side fallback from %s)", served, self.model)
        for item in getattr(msg, "input_transformations", None) or []:
            log.info("Anthropic: input transformation %s", item.to_dict() if hasattr(item, "to_dict") else item)
        fresh, read, written = int(u.input_tokens or 0), int(u.cache_read_input_tokens or 0), \
            int(u.cache_creation_input_tokens or 0)
        turn = Turn(model=served, input_tokens=fresh + read + written, output_tokens=int(u.output_tokens or 0),
                    cache_read=read, cache_write=written)
        turn.cost_usd = usage.record("anthropic", served, input_tokens=fresh, output_tokens=turn.output_tokens,
                                     cache_read=read, cache_write=written, root=self.root)
        turn.text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if think:
            turn.thinking = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
        stop = msg.stop_reason
        if stop == "refusal":
            details = getattr(msg, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            log.warning("Anthropic: request declined (category %s)", category)
            if not turn.text.strip():
                raise ProviderError("Claude a décliné cette demande (filtre de sécurité).",
                                    "Claude declined this request (safety filter).")
            turn.stop = "refusal"  # partial answer kept as plain text, nothing replayed
            return turn
        if stop in ("max_tokens", "model_context_window_exceeded"):
            if not turn.text.strip():
                raise ProviderError("La réponse de Claude a dépassé la longueur maximale : posez une question plus "
                                    "ciblée.", "Claude's answer went over the maximum length: ask a narrower "
                                    "question.")
            turn.stop = "max_tokens"  # a cut tool call is never run nor replayed
            turn.raw = {"provider": "anthropic", "model": served,
                        "content": [b for b in blocks if b.get("type") != "tool_use"]}
            return turn
        for b in blocks:
            if b.get("type") == "tool_use":
                args = b.get("input")
                turn.tool_calls.append(ToolCall(b["id"], b["name"], args if isinstance(args, dict)
                                                else {"_unreadable_input": str(args)[:200]}))
        turn.stop = "tool_use" if turn.tool_calls else "end"
        turn.raw = {"provider": "anthropic", "model": served, "content": blocks}
        return turn


def check_connection(model: str = DEFAULT_MODEL, api_key: str | None = None, client: Any = None) -> dict[str, Any]:
    """Checks the key and access to ``model`` with the free Models API (no tokens billed)."""
    try:
        import anthropic
    except ImportError:
        return {"ok": False, "fr": "Le paquet « anthropic » manque : pip install anthropic",
                "en": "The 'anthropic' package is missing: pip install anthropic"}
    if client is None:
        key = api_key or vault.get_key("anthropic")[0]
        if not key:
            return {"ok": False, "fr": "Aucune clé enregistrée.", "en": "No key stored."}
        client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=20.0)
    t0 = time.perf_counter()
    try:
        info = client.models.retrieve(model)
    except anthropic.APIError as exc:
        err = provider_error(exc, model)
        return {"ok": False, "fr": err.fr, "en": err.en}
    ms = round((time.perf_counter() - t0) * 1000)
    name = getattr(info, "display_name", None) or model_label(model)
    log.info("Anthropic: connection test ok (%s, %d ms)", model, ms)
    return {"ok": True, "model": name, "ms": ms,
            "fr": f"Connexion réussie : {name} est accessible avec cette clé ({ms} ms).",
            "en": f"Connected: {name} is available with this key ({ms} ms)."}
