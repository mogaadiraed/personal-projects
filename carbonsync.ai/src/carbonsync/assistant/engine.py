"""The tool loop: question → model → tools → model … → answer, streamed as events.

``ask()`` yields ``Event`` objects the UI renders live (model writing, tool started/finished,
text chunks) and ends with a ``done`` event carrying the turn record: answer, tools used with
their results (for charts and the Sources panel), tokens, timings and any error.

If the model fails (Ollama not running, model missing, no API key, spending cap reached…), the
built-in assistant answers instead from the same tools, so the user always gets exact figures.

Cloud models (``privacy == "aggregated"``) always run in Expert mode with the expert brief, never
see tool results produced in local mode, and get their history unchanged (``append_only``).
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from carbonsync.assistant import grounding, prompts, tools
from carbonsync.assistant.providers.base import Delta, Provider, ProviderError, Turn
from carbonsync.assistant.providers.builtin import BuiltinProvider, route_confident
from carbonsync.obs import get_logger

log = get_logger(__name__)

MAX_ROUNDS = 6               # model calls per question (the last one has no tools, so it must answer)
MAX_CALLS_PER_ROUND = 6
MAX_TOOL_CHARS = 24_000      # one tool result sent to the model
HISTORY_CHARS = 60_000       # older turns are trimmed beyond this
_STUB = json.dumps({"ok": True, "note": "older result removed to save space; call the tool again if you need it"})
_LOCAL_ONLY = json.dumps({"ok": True, "note": "result read in local mode, not sent to the cloud; call the tool again "
                                              "if you need it"})


@dataclass
class Event:
    kind: str                      # model | thinking | text | tool_start | tool_end | notice | error | done
    text: str = ""
    record: dict[str, Any] | None = None
    turn: dict[str, Any] | None = None


@dataclass
class Chat:
    id: str
    created_at: str
    lang: str
    title: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)   # what the model sees (neutral format)
    turns: list[dict[str, Any]] = field(default_factory=list)      # what the UI shows

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Chat:
        return cls(**{k: d[k] for k in ("id", "created_at", "lang", "title", "messages", "turns") if k in d})


def new_chat(lang: str = "fr") -> Chat:
    now = datetime.now()
    return Chat(id=now.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4], created_at=now.isoformat(timespec="seconds"),
                lang=lang)


def tool_content(out: dict[str, Any], limit: int = MAX_TOOL_CHARS) -> str:
    """Tool result as the JSON text given to the model, capped in size."""
    body = {"ok": True, "result": out["result"]} if out["ok"] else {"ok": False, "error": out["error"]}
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= limit:
        return text
    small = {k: v for k, v in (out["result"] or {}).items() if not isinstance(v, list | dict) or len(json.dumps(v)) < 1500}
    return json.dumps({"ok": True, "truncated": True, "result": small,
                       "note": f"result too large ({len(text)} chars): ask for a coarser granularity, fewer metrics "
                               "or a shorter period"}, ensure_ascii=False, separators=(",", ":"))


def history_window(messages: list[dict[str, Any]], budget: int = HISTORY_CHARS) -> list[dict[str, Any]]:
    """Messages for the next model call: the current question in full; older tool results are
    replaced by a stub, and the oldest questions are dropped if the total is still too long."""
    if not messages:
        return []
    users = [i for i, m in enumerate(messages) if m["role"] == "user"]
    current = users[-1] if users else 0
    old = [dict(m, content=_STUB) if m["role"] == "tool" else m for m in messages[:current]]
    recent = messages[current:]

    def size(ms: list[dict[str, Any]]) -> int:
        return sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls", []))) for m in ms)

    starts = [i for i, m in enumerate(old) if m["role"] == "user"]
    while old and size(old) + size(recent) > budget:
        nxt = next((s for s in starts if s > 0), None)
        old = old[nxt:] if nxt else []
        starts = [i for i, m in enumerate(old) if m["role"] == "user"]
    return old + recent


def model_messages(messages: list[dict[str, Any]], provider: Provider, budget: int) -> list[dict[str, Any]]:
    """What ``provider`` receives. Cloud providers never get a tool result read in local mode
    (it may hold daily rows); append-only providers get the history unchanged, dropping whole
    oldest questions only beyond ``budget``."""
    if provider.privacy == "aggregated":
        messages = [dict(m, content=_LOCAL_ONLY) if m["role"] == "tool" and m.get("privacy") != "aggregated"
                    else m for m in messages]
    if not provider.append_only:
        return history_window(messages, budget)
    out = list(messages)
    while sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls", []))) for m in out) > budget:
        starts = [i for i, m in enumerate(out) if m["role"] == "user"]
        if len(starts) < 2:
            break
        out = out[starts[1]:]
    return out


def ask(chat: Chat, question: str, provider: Provider, *, lang: str = "fr", think: bool = False,
        mode: str = "assistant", page: str | None = None, fallback: bool = True, max_rounds: int = MAX_ROUNDS,
        history_chars: int | None = None) -> Iterator[Event]:
    tctx = tools.ToolContext(lang=lang, privacy=provider.privacy)
    ctx = tctx.data
    if provider.privacy == "aggregated":
        mode = "expert"  # every cloud model starts from the expert brief
    system = prompts.system_prompt(ctx, lang, mode, page)
    specs = tools.specs()
    t0 = time.perf_counter()
    turn: dict[str, Any] = {
        "question": question, "answer": "", "thinking": "", "tools": [], "provider": provider.key,
        "model": provider.model, "label": provider.display_name, "privacy": tctx.privacy, "mode": mode,
        "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0,
        "rounds": 0, "seconds": 0.0, "error": None, "notice": None,
        "brief": prompts.load_brief()["version"] if mode == "expert" else None,
        "stop": "end", "started_at": datetime.now().isoformat(timespec="seconds"),
        "data_until": ctx.last.date().isoformat(),
        "forecast_run": str(ctx.run["run_id"]) if ctx.run is not None else None,
    }
    content = question
    if page and mode == "expert":
        # Expert mode keeps one system prompt per conversation (cache, preserved reasoning): the page the
        # question was asked from goes with the question instead
        content = (f"(Page ouverte dans le tableau de bord : {page})\n{question}" if lang == "fr"
                   else f"(Dashboard page open: {page})\n{question}")
    turn["page"] = page
    chat.messages.append({"role": "user", "content": content})
    chat.title = chat.title or question.strip()[:80]
    log.info("Assistant %s · question (%s, %s): %s", chat.id, provider.display_name, lang, question.strip()[:160])

    history_chars = history_chars or provider.history_chars
    tool_limit = min(MAX_TOOL_CHARS, history_chars // 2)
    if provider.uses_model and provider.prefetch and (hint := route_confident(question)):
        # a small model does not always pick the right tool: when the question clearly matches one,
        # run it first so the model starts from the real figures (and one model call is saved)
        name, args = hint
        call = {"id": "pre_" + uuid.uuid4().hex[:8], "name": name, "arguments": args}
        chat.messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        log.info("Assistant %s · prefetch %s(%s)", chat.id, name, json.dumps(args, ensure_ascii=False)[:120])
        yield from _run_tool(call, 0, tctx, lang, turn, chat, tool_limit)
    active = provider
    rnd = 0
    while rnd < max_rounds:
        rnd += 1
        last = rnd == max_rounds
        yield Event("model", text=str(rnd))
        try:
            mturn = yield from _one_round(active, system, model_messages(chat.messages, active, history_chars),
                                          specs if active.append_only or active.keep_tools or not last else [],
                                          think, turn, last)
        except ProviderError as exc:
            if not (fallback and active.uses_model):
                turn["error"] = exc.text(lang)
                yield Event("error", text=turn["error"])
                break
            log.warning("Assistant %s · %s unavailable (%s) → built-in assistant", chat.id, active.display_name, exc)
            turn["notice"] = exc.text(lang)
            yield Event("notice", text=turn["notice"])
            active = BuiltinProvider(lang)
            turn["provider"], turn["model"], turn["label"] = active.key, "", active.display_name
            rnd -= 1
            continue
        except Exception as exc:
            log.exception("Assistant %s · model call failed", chat.id)
            turn["error"] = f"{type(exc).__name__}: {exc}"[:300]
            yield Event("error", text=turn["error"])
            break
        calls = mturn.tool_calls[:MAX_CALLS_PER_ROUND]
        msg: dict[str, Any] = {"role": "assistant", "content": mturn.text}
        if calls:
            msg["tool_calls"] = [c.to_dict() for c in calls]
        if mturn.raw:
            msg["raw"] = mturn.raw
        chat.messages.append(msg)
        if not calls:
            turn["stop"] = mturn.stop
            break
        for call in calls:
            yield from _run_tool(call.to_dict(), rnd, tctx, lang, turn, chat, tool_limit)

    turn["seconds"] = round(time.perf_counter() - t0, 2)
    turn["answer"] = turn["answer"].strip()
    if not turn["answer"] and not turn["error"]:
        turn["error"] = ("Le modèle n'a pas donné de réponse. Reformulez la question ou essayez un autre modèle."
                         if lang == "fr" else "The model gave no answer. Rephrase the question or try another model.")
        yield Event("error", text=turn["error"])
    turn["unverified"] = (grounding.check(turn["answer"], chat.turns, turn, system + json.dumps(specs))
                          if active.uses_model and turn["answer"] else [])
    chat.turns.append(turn)
    log.info("Assistant %s · answered in %.1fs: %d round(s), %d tool(s), %d in / %d out tokens%s%s%s", chat.id,
             turn["seconds"], turn["rounds"], len(turn["tools"]), turn["input_tokens"], turn["output_tokens"],
             f", {turn['cache_read']} read from cache, ≈ ${turn['cost_usd']:.4f}" if turn["cost_usd"] else "",
             f", figures not found in the data: {', '.join(turn['unverified'])}" if turn["unverified"] else "",
             f", error: {turn['error']}" if turn["error"] else "")
    yield Event("done", turn=turn)


def _run_tool(call: dict[str, Any], rnd: int, tctx: tools.ToolContext, lang: str, turn: dict[str, Any],
              chat: Chat, tool_limit: int) -> Iterator[Event]:
    rec: dict[str, Any] = {"id": call["id"], "name": call["name"], "args": call["arguments"],
                           "label": tools.status_label(call["name"], lang), "round": rnd}
    yield Event("tool_start", record=rec)
    out = tools.execute(call["name"], call["arguments"], tctx)
    rec.update(ok=out["ok"], seconds=out.get("seconds", 0.0), error=out.get("error"),
               args=out.get("args", call["arguments"]), result=out.get("result"))
    turn["tools"].append(rec)
    chat.messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                          "content": tool_content(out, tool_limit), "privacy": tctx.privacy})
    yield Event("tool_end", record=rec)


def _one_round(provider: Provider, system: str, messages: list[dict[str, Any]], specs: list[dict[str, Any]],
               think: bool, turn: dict[str, Any], final: bool = False) -> Iterator[Event]:
    """One model call; streams its deltas and returns the finished ``Turn`` (via ``yield from``)."""
    result: Turn | None = None
    first_text = True
    for item in provider.stream(system, messages, specs, think=think, final=final):
        if isinstance(item, Delta):
            if item.kind == "thinking":
                turn["thinking"] += item.text
                yield Event("thinking", text=item.text)
                continue
            if first_text and turn["answer"].strip():
                turn["answer"] += "\n\n"
                yield Event("text", text="\n\n")
            first_text = False
            turn["answer"] += item.text
            yield Event("text", text=item.text)
        else:
            result = item
    if result is None:
        raise ProviderError("Le modèle s'est arrêté sans réponse.", "The model stopped without an answer.")
    turn["rounds"] += 1
    turn["input_tokens"] += result.input_tokens
    turn["output_tokens"] += result.output_tokens
    turn["cache_read"] += result.cache_read
    turn["cache_write"] += result.cache_write
    turn["cost_usd"] = round(turn["cost_usd"] + result.cost_usd, 6)
    if result.model and result.model != turn["model"]:
        turn["served_by"] = result.model  # server-side fallback answered
    return result
