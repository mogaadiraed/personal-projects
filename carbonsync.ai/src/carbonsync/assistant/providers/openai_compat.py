"""Any OpenAI-compatible chat API (OpenRouter first; OpenAI, Gemini, Mistral and custom URLs next),
through the official ``openai`` SDK with another ``base_url``.

Streaming Chat Completions with tools: text, reasoning and tool-call fragments arrive in chunks
(tool calls by ``index``); everything is collected into one ``Turn``. Rules every subclass gets:

* The tools are sent on every request, the final round included, with ``tool_choice: "none"``
  there (some APIs validate the tool history against the tools of each request). A model that
  does not take ``"none"`` is retried once without ``tool_choice``; tool calls on the final round
  are dropped.
* Every assistant tool call is followed by its result (missing ones get an error result).
* The reasoning of the current question's tool steps goes back as ``reasoning`` (plain text);
  reasoning from earlier questions is not resent.
* A cut answer (``finish_reason: length``) never runs its tool calls; an error chunk mid-stream
  raises.
* Expert rules: ``privacy = "aggregated"`` (expert brief, monthly data), daily cap checked before
  each request, cost recorded after.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
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
from carbonsync.obs import get_logger, redact

log = get_logger(__name__)

_NOT_RUN = json.dumps({"ok": False, "error": "tool not run (answer was cut or the conversation was trimmed)"})


def to_openai(system: str, messages: list[dict[str, Any]], *, reasoning_from: str | None = None) -> list[dict[str, Any]]:
    """Provider-neutral messages → Chat Completions messages.

    ``reasoning_from``: provider key whose saved reasoning goes back with the tool steps of the
    current question (None: never)."""
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    out: list[dict[str, Any]] = [{"role": "system", "content": str(system)}]
    pending: list[str] = []          # tool_call ids still waiting for their result
    known: set[str] = set()

    def close_pending() -> None:
        for cid in pending:
            out.append({"role": "tool", "tool_call_id": cid, "content": _NOT_RUN})
        pending.clear()

    for i, m in enumerate(messages):
        role = m["role"]
        if role == "tool":
            cid = m.get("tool_call_id", "")
            if cid not in known:
                continue  # its call was trimmed away: an orphan result is rejected by the API
            if cid in pending:
                pending.remove(cid)
            out.append({"role": "tool", "tool_call_id": cid, "content": m.get("content") or ""})
            continue
        close_pending()
        if role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            calls = m.get("tool_calls") or []
            if calls:
                msg["content"] = m.get("content") or None
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"],
                                                   "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False)}}
                                     for c in calls]
                pending.extend(c["id"] for c in calls)
                known.update(c["id"] for c in calls)
            raw = m.get("raw") or {}
            if reasoning_from and i > last_user and raw.get("provider") == reasoning_from and raw.get("reasoning"):
                msg["reasoning"] = raw["reasoning"]
            if msg["content"] is None and not calls:
                msg["content"] = ""
            out.append(msg)
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    close_pending()
    return out


class OpenAICompatProvider(Provider):
    """Base for OpenAI-compatible APIs. Subclasses set ``key``, ``label``, ``base_url`` and may
    override ``_extra_body``, ``_status_error`` and ``_cost``."""

    key = "openai_compat"
    label = "OpenAI-compatible"
    base_url = ""
    privacy = "aggregated"
    supports_thinking = True
    keep_tools = True
    replay_reasoning = True
    max_tokens = 16_000

    def __init__(self, model: str, *, settings: dict[str, Any] | None = None, root: Any = None,
                 timeout: float = 300.0, client: Any = None) -> None:
        super().__init__(model)
        self.history_chars = 200_000
        self.settings, self.root, self.timeout = settings, root, timeout
        self._client_obj = client

    # -- hooks ----------------------------------------------------------------------------------
    def model_label(self) -> str:
        return self.model

    @property
    def display_name(self) -> str:
        return f"{self.label} · {self.model_label()}"

    def _where(self) -> tuple[str, str]:
        return WHERE.get(self.key, WHERE["overview"])

    def _client(self) -> Any:
        if self._client_obj is not None:
            return self._client_obj
        key, _ = vault.get_key(self.key)
        if not key:
            fr, en = self._where()
            raise ProviderError(f"Aucune clé {self.label} : ajoutez-la dans {fr}.", f"No {self.label} key: add it in {en}.")
        try:
            import openai
        except ImportError as exc:
            raise ProviderError("Le paquet Python « openai » manque : pip install openai",
                                "The Python package 'openai' is missing: pip install openai") from exc
        self._client_obj = openai.OpenAI(api_key=key, base_url=self.base_url, max_retries=1, timeout=self.timeout)
        return self._client_obj

    def _extra_body(self, think: bool, final: bool) -> dict[str, Any]:
        return {}

    def _cost(self, prompt: int, cached: int, completion: int, reported: float | None) -> float:
        return float(reported or 0.0)

    def _status_error(self, code: int, detail: str) -> ProviderError:
        fr, en = self._where()
        if code == 401:
            return ProviderError(f"Clé {self.label} refusée (invalide ou révoquée) : vérifiez-la dans {fr}.",
                                 f"{self.label} key rejected (invalid or revoked): check it in {en}.")
        if code == 402:
            return ProviderError(f"Crédit {self.label} insuffisant : ajoutez des crédits sur le site du fournisseur.",
                                 f"Not enough {self.label} credit: add credits on the provider's site.")
        if code == 403:
            return ProviderError(f"Demande refusée par {self.label} (accès ou modération) : {detail}",
                                 f"Request refused by {self.label} (access or moderation): {detail}")
        if code == 404:
            return ProviderError(f"Modèle « {self.model} » introuvable chez {self.label} : choisissez-en un autre dans "
                                 f"{fr}. ({detail})",
                                 f"Model '{self.model}' not found at {self.label}: pick another one in {en}. ({detail})")
        if code == 429:
            return ProviderError(f"Limite d'utilisation {self.label} atteinte : réessayez dans un moment.",
                                 f"{self.label} usage limit reached: try again in a moment.")
        if code in (408, 504, 524):
            return ProviderError("Le modèle n'a pas répondu à temps : réessayez, ou choisissez un modèle plus rapide.",
                                 "The model did not answer in time: try again, or pick a faster model.")
        if code >= 500:
            return ProviderError(f"{self.label} ne joint pas « {self.model} » pour l'instant (erreur {code}) : réessayez "
                                 "ou changez de modèle.",
                                 f"{self.label} cannot reach '{self.model}' right now (error {code}): try again or switch "
                                 "model.")
        return ProviderError(f"{self.label} : {detail}", f"{self.label}: {detail}")

    # -------------------------------------------------------------------------------------------
    def _map(self, exc: Exception) -> ProviderError:
        import openai

        if isinstance(exc, openai.APITimeoutError):
            return self._status_error(408, "timeout")
        if isinstance(exc, openai.APIConnectionError):
            host = self.base_url.split("//")[-1].split("/")[0]
            return ProviderError(f"Impossible de joindre {host} : vérifiez la connexion Internet.",
                                 f"Cannot reach {host}: check the Internet connection.")
        code = int(getattr(exc, "status_code", 0) or 0)
        body = getattr(exc, "body", None)
        detail = ""
        if isinstance(body, dict):
            err = body.get("error", body)
            detail = str(err.get("message") if isinstance(err, dict) else err)
            if not code and isinstance(err, dict):
                code = int(err.get("code") or 0) if str(err.get("code") or "").isdigit() else 0
        detail = redact(detail or str(getattr(exc, "message", exc)))[:200]
        return self._status_error(code, detail)

    def _before_call(self) -> None:
        allowed, spent, cap = usage.check_cap(self.settings, self.root)
        if not allowed:
            fr, en = WHERE["overview"]
            raise ProviderError(
                f"Plafond de dépense du jour atteint ({spent:.2f} $ sur {cap:.2f} $). Il repart à zéro demain ; "
                f"modifiable dans {fr}.",
                f"Today's spending cap reached (${spent:.2f} of ${cap:.2f}). It resets tomorrow; change it in {en}.")

    def request(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                think: bool = False, final: bool = False) -> dict[str, Any]:
        """The request body (also used by the tests)."""
        body: dict[str, Any] = {
            "model": self.model, "stream": True, "max_tokens": self.max_tokens,
            "messages": to_openai(system, messages, reasoning_from=self.key if self.replay_reasoning else None),
        }
        if tools:
            body["tools"] = [{"type": "function", "function": s} for s in tools]
            body["tool_choice"] = "none" if final else "auto"
        return body

    def stream(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
               think: bool = False, final: bool = False) -> Iterator[Delta | Turn]:
        import openai

        self._before_call()
        client = self._client()
        body = self.request(system, messages, tools, think=think, final=final)
        extra = self._extra_body(think, final)
        t0 = time.perf_counter()
        for attempt in (1, 2):
            turn, acc, state = Turn(), {}, {"finish": "", "served": "", "usage": None}
            emitted = False
            try:
                for item in self._chunks(client, body, extra, turn, acc, state):
                    emitted = True
                    yield item
                break
            except openai.APIError as exc:
                err = self._map(exc)
                retry = (attempt == 1 and not emitted and final and "tool_choice" in body
                         and "tool_choice" in (err.en + str(getattr(exc, "message", ""))).lower())
                if retry:  # this model does not take tool_choice "none": ask again without it
                    log.info("%s %s: tool_choice 'none' refused, retrying without it", self.label, self.model)
                    body = {k: v for k, v in body.items() if k != "tool_choice"}
                    continue
                log.warning("%s %s: %s", self.label, self.model, err.en)
                raise err from exc
        turn.seconds = time.perf_counter() - t0
        self._finish(turn, acc, state, final)
        yield turn

    def _chunks(self, client: Any, body: dict[str, Any], extra: dict[str, Any], turn: Turn, acc: dict[int, dict],
                state: dict[str, Any]) -> Iterator[Delta]:
        stream = client.chat.completions.create(**body, extra_body=extra or None)
        for chunk in stream:
            err = (chunk.model_extra or {}).get("error") if hasattr(chunk, "model_extra") else None
            if err:  # mid-stream error: HTTP stayed 200
                code = int(err.get("code") or 0) if isinstance(err, dict) and str(err.get("code") or "").isdigit() else 0
                raise self._status_error(code or 502, redact(str(err.get("message") if isinstance(err, dict) else err))[:200])
            if chunk.model:
                state["served"] = chunk.model
            if chunk.usage:
                state["usage"] = chunk.usage
            for choice in chunk.choices or []:
                d = choice.delta
                if d is not None:
                    reasoning = getattr(d, "reasoning", None) or getattr(d, "reasoning_content", None)
                    if isinstance(reasoning, str) and reasoning:
                        turn.thinking += reasoning
                        yield Delta("thinking", reasoning)
                    if d.content:
                        turn.text += d.content
                        yield Delta("text", d.content)
                    for tc in d.tool_calls or []:
                        slot = acc.setdefault(tc.index if tc.index is not None else len(acc),
                                              {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function is not None:
                            if tc.function.name:
                                slot["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["args"] += tc.function.arguments
                if choice.finish_reason:
                    state["finish"] = choice.finish_reason
        if state["finish"] == "error":
            raise self._status_error(502, "stream ended with an error")

    def _finish(self, turn: Turn, acc: dict[int, dict], state: dict[str, Any], final: bool) -> None:
        for _, slot in sorted(acc.items()):
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except ValueError:
                args = {"_unreadable_input": slot["args"][:500]}
            if not isinstance(args, dict):
                args = {"_unreadable_input": str(args)[:500]}
            turn.tool_calls.append(ToolCall(slot["id"] or f"call_{uuid.uuid4().hex[:10]}", slot["name"], args))
        finish = state["finish"]
        if finish == "length":
            turn.stop = "max_tokens"
            turn.tool_calls = []  # a cut tool call is never run
        elif finish == "content_filter":
            turn.stop = "refusal"
        if final:
            turn.tool_calls = []  # the final round answers with what it has
        if turn.tool_calls:
            turn.stop = "tool_use"
        u = state["usage"]
        prompt = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
        completion = int(getattr(u, "completion_tokens", 0) or 0) if u else 0
        details = getattr(u, "prompt_tokens_details", None) if u else None
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        reported = getattr(u, "cost", None) if u else None
        turn.input_tokens, turn.output_tokens, turn.cache_read = prompt, completion, cached
        turn.cost_usd = round(self._cost(prompt, cached, completion, reported), 6)
        served = state["served"]
        if served and served.split(":")[0] != self.model.split(":")[0]:
            turn.model = served  # a fallback model answered
        if self.replay_reasoning and turn.tool_calls and turn.thinking:
            turn.raw = {"provider": self.key, "model": served or self.model, "reasoning": turn.thinking}
        usage.record(self.key, turn.model or self.model, input_tokens=prompt - cached, output_tokens=completion,
                     cache_read=cached, cost=turn.cost_usd, root=self.root)
        rate = completion / turn.seconds if turn.seconds else 0
        log.info("Model %s (%s): %d in / %d out tokens, %.1fs (%.0f tok/s), ≈ $%.4f%s%s", self.model, self.key, prompt,
                 completion, turn.seconds, rate, turn.cost_usd, f", served by {turn.model}" if turn.model else "",
                 f", tools: {', '.join(c.name for c in turn.tool_calls)}" if turn.tool_calls else "")
