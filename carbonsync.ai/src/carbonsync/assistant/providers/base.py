"""What every model provider implements, in a provider-neutral message format.

Messages kept by the engine (plain JSON, saved with the conversation):

    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<json>"}

An assistant message can also carry ``raw``: the provider's own content blocks (for Claude: the
thinking blocks with their signatures), replayed unchanged on the next call to the same provider.

``Provider.stream`` yields ``Delta`` chunks (text or thinking) while the model writes, then
exactly one ``Turn`` with the full text, the tool calls it asked for and the token usage.
``final=True`` marks the last round of a question: the model must answer without tools.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Delta:
    kind: str  # "text" | "thinking"
    text: str


@dataclass
class Turn:
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"  # "end" | "tool_use" | "max_tokens" | "refusal"
    input_tokens: int = 0       # all input tokens, cached ones included
    output_tokens: int = 0
    seconds: float = 0.0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0       # cloud providers only (estimate)
    model: str = ""             # model that actually answered (a server-side fallback can change it)
    raw: dict[str, Any] | None = None   # provider-specific content to replay (see module docstring)


class ProviderError(RuntimeError):
    """A failure the user can act on (model not running, not downloaded, bad key…), in FR and EN."""

    def __init__(self, fr: str, en: str | None = None) -> None:
        super().__init__(en or fr)
        self.fr, self.en = fr, en or fr

    def text(self, lang: str) -> str:
        return self.fr if lang == "fr" else self.en


class Provider:
    key = "base"            # "builtin" | "ollama" | "anthropic" …
    label = "Provider"
    privacy = "full"        # "full": data stays on this PC; "aggregated": cloud, monthly aggregates only
    supports_thinking = False
    uses_model = True       # False for the built-in assistant (no free conversation)
    history_chars = 60_000  # conversation text sent per call (system prompt and tool specs not included)
    prefetch = False        # run the obvious tool before the first call (helps small local models)
    append_only = False     # history must be replayed unchanged (no trimming of old tool results, same tools
                            # on every call): cloud models that keep their reasoning across calls
    keep_tools = False      # send the tools on the final round too (with final=True → no tool call allowed):
                            # APIs that validate tool history against the tools of each request (OpenRouter)

    def __init__(self, model: str = "") -> None:
        self.model = model

    @property
    def display_name(self) -> str:
        return f"{self.label} · {self.model}" if self.model else self.label

    def stream(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
               think: bool = False, final: bool = False) -> Iterator[Delta | Turn]:
        raise NotImplementedError
