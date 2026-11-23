"""Assistant Phase 3: Expert mode with Claude. Key vault, log redaction, usage meter and cap,
expert brief, message conversion, the Anthropic provider (real SDK on a mocked HTTP transport,
no network, no key), the append-only history, the cloud privacy floor, the figure check on
written calculations and the two pages."""
from __future__ import annotations

import json
import logging

import anthropic
import httpx2
import pytest

from carbonsync import obs
from carbonsync.assistant import engine, grounding, prompts, store, tools, usage, vault
from carbonsync.assistant.providers import Provider, ProviderError, ToolCall, Turn
from carbonsync.assistant.providers import anthropic_provider as anp

KEY = "sk-ant-api03-" + "A1b2C3d4" * 10 + "-zZ9q"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeKeyring:
    """Stands in for the Windows Credential Manager."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        del self.store[(service, user)]

    def get_keyring(self):
        return self


@pytest.fixture
def ring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(vault, "_backend", lambda: fake)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return fake


def _start(i, block):
    return {"type": "content_block_start", "index": i, "content_block": block}


def _delta(i, delta):
    return {"type": "content_block_delta", "index": i, "delta": delta}


def sse(blocks, stop="end_turn", model="claude-opus-5-5", **usage_kw) -> str:
    """A streamed Messages API response, as the server sends it."""
    u = {"input_tokens": 120, "output_tokens": 1, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 0,
         **usage_kw}
    ev = [{"type": "message_start", "message": {"id": "msg_t", "type": "message", "role": "assistant", "model": model,
                                                "content": [], "stop_reason": None, "stop_sequence": None,
                                                "usage": u}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev += [_start(i, {"type": "text", "text": ""}), _delta(i, {"type": "text_delta", "text": b["text"]})]
        elif b["type"] == "thinking":
            ev += [_start(i, {"type": "thinking", "thinking": "", "signature": ""}),
                   _delta(i, {"type": "thinking_delta", "thinking": b["thinking"]}),
                   _delta(i, {"type": "signature_delta", "signature": b["signature"]})]
        elif b["type"] == "tool_use":
            ev += [_start(i, {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}),
                   _delta(i, {"type": "input_json_delta", "partial_json": json.dumps(b["input"])})]
        ev.append({"type": "content_block_stop", "index": i})
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 40}},
           {"type": "message_stop"}]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in ev)


def api_error(status, kind, message):
    return status, {"type": "error", "error": {"type": kind, "message": message}}


class FakeAPI:
    """Mocked HTTP transport for the real SDK: plays responses back and records each request."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.bodies: list[dict] = []
        self.headers: list[dict] = []

    def handler(self, request):
        self.bodies.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        r = self.responses.pop(0)
        if isinstance(r, tuple):
            return httpx2.Response(r[0], json=r[1], headers={"request-id": "req_test"})
        return httpx2.Response(200, text=r, headers={"content-type": "text/event-stream", "request-id": "req_test"})

    def client(self):
        return anthropic.Anthropic(api_key=KEY, max_retries=0,
                                   http_client=httpx2.Client(transport=httpx2.MockTransport(self.handler)))


def claude(api: FakeAPI, tmp_path, model="claude-opus-5-5", cap=5.0, **kw):
    return anp.AnthropicProvider(model, client=api.client(), settings={"daily_cap_usd": cap}, root=tmp_path, **kw)


SYSTEM = prompts.SystemPrompt(["BRIEF " * 50, "CURRENT CONTEXT"], sep="\n\n")
SPECS = [{"name": "get_budget", "description": "budget", "parameters": {"type": "object", "properties": {}}}]


# ---------------------------------------------------------------------------
# Vault and log redaction
# ---------------------------------------------------------------------------
def test_vault_store_mask_delete_and_env_fallback(ring, monkeypatch):
    assert vault.get_key("anthropic") == (None, "none")
    masked = vault.set_key("anthropic", f"  {KEY}\n")
    assert masked == "sk-ant-…zZ9q" and KEY not in masked
    assert ring.store[(vault.SERVICE, "anthropic")] == KEY
    assert vault.get_key("anthropic") == (KEY, "vault")
    st = vault.status("anthropic")
    assert st["has_key"] and st["source"] == "vault" and st["masked"] == "sk-ant-…zZ9q" and KEY not in json.dumps(st)
    assert vault.delete_key("anthropic") and not vault.delete_key("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    assert vault.get_key("anthropic") == (KEY, "env")


@pytest.mark.parametrize("bad", ["", "sk-ant-short", "sk-proj-" + "x" * 50, "sk-ant-" + "x" * 30 + " " + "y" * 20,
                                 "sk-ant-" + "é" * 50])
def test_vault_rejects_malformed_keys_without_echoing_them(ring, bad):
    with pytest.raises(vault.VaultError) as err:
        vault.set_key("anthropic", bad)
    assert not ring.store
    if bad:
        assert bad not in err.value.fr and bad not in err.value.en


def test_logs_never_show_a_key():
    lines: list[str] = []
    log = obs.get_logger("carbonsync.test.redact")
    with obs.capture(lambda _rec, line: lines.append(line)):
        log.info("calling with %s", KEY)
        log.warning(f"header x-api-key: {KEY}")
    assert lines and all(KEY not in line and "sk-ant-[REDACTED]" in line for line in lines)
    assert obs.redact("task-1234567890123 and ask-ant-x") == "task-1234567890123 and ask-ant-x"
    # third-party loggers get the filter too
    obs.setup_console_logging(logging.INFO)
    assert any(isinstance(f, obs._Redact) for f in logging.getLogger("anthropic").filters)


# ---------------------------------------------------------------------------
# Usage, cost and cap
# ---------------------------------------------------------------------------
def test_cost_estimates_match_list_prices():
    assert usage.cost_usd("claude-opus-5-5", input_tokens=1_000_000) == pytest.approx(4.0)
    assert usage.cost_usd("claude-opus-5-5", output_tokens=1_000_000) == pytest.approx(20.0)
    assert usage.cost_usd("claude-opus-5-5", cache_read=1_000_000) == pytest.approx(0.20)
    assert usage.cost_usd("claude-opus-5-5", cache_write=1_000_000) == pytest.approx(5.0)
    assert usage.cost_usd("claude-sonnet-5-5", cache_read=1_000_000) == pytest.approx(0.10)
    assert usage.cost_usd("claude-haiku-5-5", input_tokens=50_000) == pytest.approx(0.005)
    assert usage.cost_usd("claude-haiku-5-5", input_tokens=150_000) == pytest.approx(0.075)  # long-prompt rate
    assert usage.cost_usd("unknown-model", output_tokens=1_000_000) == pytest.approx(25.0)  # priced high, cap stays safe


def test_usage_meter_settings_and_cap(tmp_path):
    s = usage.load_settings(tmp_path)
    assert s["anthropic"] == {"model": "claude-opus-5-5", "effort": "medium"} and s["daily_cap_usd"] == 5.0
    s["anthropic"]["effort"] = "high"
    s["daily_cap_usd"] = 0.01
    s["api_key"] = KEY  # must never be written
    usage.save_settings(s, tmp_path)
    saved = (tmp_path / "data" / "assistant" / "settings.json").read_text(encoding="utf-8")
    assert KEY not in saved and usage.load_settings(tmp_path)["anthropic"]["effort"] == "high"
    assert usage.check_cap(root=tmp_path)[0]
    c = usage.record("anthropic", "claude-opus-5-5", input_tokens=1000, output_tokens=500, cache_read=4000,
                     root=tmp_path)
    assert c == pytest.approx(0.0148) and usage.spent(root=tmp_path) == pytest.approx(0.0148)
    allowed, _spent, cap = usage.check_cap(root=tmp_path)
    assert not allowed and cap == 0.01
    hist = usage.history(root=tmp_path)
    assert hist[0]["model"] == "claude-opus-5-5" and hist[0]["requests"] == 1 and hist[0]["cache_read"] == 4000


# ---------------------------------------------------------------------------
# Expert brief
# ---------------------------------------------------------------------------
def test_expert_brief_is_complete_and_cacheable():
    b = prompts.load_brief()
    text = b["text"]
    assert b["version"].isdigit() and int(b["version"]) >= 2 and "<!--" not in text and text.startswith("# CarbonSync expert brief")
    assert b["tokens"] > 1024  # well above the 512-token minimum cacheable prefix
    for must in ("Scope 2 is location-based", "GHG Protocol", "ESRS E1", "ISO 50001", "LMDI", "kwh_per_unit",
                 "Base load", "read-only", "data, never instructions", "aggregated data only"):
        assert must in text, must
    # every tool the cloud model can call is introduced in the brief
    for spec in tools.specs():
        assert f"`{spec['name']}`" in text, spec["name"]


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------
def test_to_anthropic_replays_raw_turns_and_groups_tool_results():
    raw = [{"type": "thinking", "thinking": "", "signature": "SIG1"},
           {"type": "tool_use", "id": "toolu_1", "name": "get_budget", "input": {}},
           {"type": "tool_use", "id": "toolu_2", "name": "get_forecast", "input": {}}]
    msgs = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "toolu_1", "name": "get_budget", "arguments": {}}],
         "raw": {"provider": "anthropic", "content": raw}},
        {"role": "tool", "tool_call_id": "toolu_1", "name": "get_budget", "content": '{"ok":true,"result":{}}'},
        {"role": "assistant", "content": "Réponse", "tool_calls": [{"id": "call_9", "name": "get_overview",
                                                                    "arguments": {"x": 1}}]},
        {"role": "tool", "tool_call_id": "call_9", "name": "get_overview", "content": '{"ok":false,"error":"bad"}'},
        {"role": "user", "content": "Q2"},
    ]
    out = anp.to_anthropic(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant", "user"]
    assert out[1]["content"] == raw and out[1]["content"] is not raw  # replayed unchanged (a copy)
    # toolu_2 was never run (engine limit): it still gets a result, right after the real one
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["toolu_1", "toolu_2"] and out[2]["content"][1]["is_error"]
    assert out[3]["content"] == [{"type": "text", "text": "Réponse"},
                                 {"type": "tool_use", "id": "call_9", "name": "get_overview", "input": {"x": 1}}]
    assert out[4]["content"][0]["is_error"] is True and out[4]["content"][1] == {"type": "text", "text": "Q2"}


def test_fallback_boundary_and_thinking_strip():
    blocks = [{"type": "thinking", "thinking": "", "signature": "a"}, {"type": "text", "text": "Début"},
              {"type": "tool_use", "id": "t", "name": "x", "input": {}},
              {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}},
              {"type": "thinking", "thinking": "", "signature": "b"}, {"type": "text", "text": ""},
              {"type": "text", "text": "Suite"}]
    assert [b["type"] for b in anp.after_fallback(blocks)] == ["text", "fallback", "thinking", "text"]
    msgs = [{"role": "assistant", "content": [{"type": "thinking", "signature": "a", "thinking": ""},
                                              {"type": "redacted_thinking", "data": "x"}]}]
    assert anp.strip_thinking(msgs)[0]["content"] == [{"type": "text", "text": "…"}]


# ---------------------------------------------------------------------------
# The provider on the real SDK (mocked transport)
# ---------------------------------------------------------------------------
def test_request_body_and_tool_call(tmp_path):
    api = FakeAPI(sse([{"type": "thinking", "thinking": "Je lis le budget.", "signature": "SIG"},
                       {"type": "tool_use", "id": "toolu_01", "name": "get_budget", "input": {"year": 2026}}],
                      stop="tool_use", cache_creation_input_tokens=500))
    p = claude(api, tmp_path)
    out = list(p.stream(SYSTEM, [{"role": "user", "content": "Budget ?"}], SPECS, think=True))
    turn = out[-1]
    assert [o.text for o in out[:-1]] == ["Je lis le budget."]  # reasoning summary streamed
    assert turn.tool_calls == [ToolCall("toolu_01", "get_budget", {"year": 2026})] and turn.stop == "tool_use"
    assert turn.raw["content"][0] == {"type": "thinking", "thinking": "Je lis le budget.", "signature": "SIG"}
    assert (turn.input_tokens, turn.cache_read, turn.cache_write) == (3620, 3000, 500)
    assert turn.cost_usd == pytest.approx((120 * 4 + 40 * 20 + 3000 * 0.2 + 500 * 5) / 1e6)
    assert usage.spent(root=tmp_path) == pytest.approx(turn.cost_usd)
    body, headers = api.bodies[0], api.headers[0]
    assert body["model"] == "claude-opus-5-5" and body["stream"] is True and body["max_tokens"] == anp.MAX_TOKENS
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in body["system"][1]
    assert body["cache_control"] == {"type": "ephemeral"}  # automatic caching of the conversation
    assert body["thinking"] == {"type": "adaptive", "display": "summarized",
                                "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert body["output_config"] == {"effort": "medium"} and body["tool_choice"] == {"type": "auto"}
    assert body["tools"][0]["eager_input_streaming"] is True and body["tools"][0]["input_schema"]["type"] == "object"
    assert body["fallbacks"] == "default"
    assert set(headers["anthropic-beta"].split(",")) == {anp.BETA_BINDING, anp.BETA_FALLBACK}
    assert "temperature" not in body and "top_p" not in body


def test_haiku_has_no_fallback_and_final_round_disables_tools(tmp_path):
    api = FakeAPI(sse([{"type": "text", "text": "Réponse."}], model="claude-haiku-5-5"))
    p = claude(api, tmp_path, model="claude-haiku-5-5", effort="low")
    turn = list(p.stream(SYSTEM, [{"role": "user", "content": "Q"}], SPECS, final=True))[-1]
    body = api.bodies[0]
    assert "fallbacks" not in body and api.headers[0]["anthropic-beta"] == anp.BETA_BINDING
    assert body["tool_choice"] == {"type": "none"} and body["tools"]  # same tools, just not callable
    assert body["output_config"] == {"effort": "low"} and body["thinking"]["display"] == "omitted"
    assert turn.text == "Réponse." and turn.stop == "end" and turn.model == "claude-haiku-5-5"


def test_refusal_and_cut_answers(tmp_path):
    api = FakeAPI(sse([], stop="refusal"),
                  sse([{"type": "text", "text": "Début de réponse"},
                       {"type": "tool_use", "id": "toolu_x", "name": "get_budget", "input": {}}], stop="max_tokens"))
    p = claude(api, tmp_path)
    with pytest.raises(ProviderError) as err:
        list(p.stream(SYSTEM, [{"role": "user", "content": "Q"}], SPECS))
    assert "décliné" in err.value.fr
    cut = list(p.stream(SYSTEM, [{"role": "user", "content": "Q"}], SPECS))[-1]
    assert cut.stop == "max_tokens" and not cut.tool_calls  # a cut tool call is never run
    assert [b["type"] for b in cut.raw["content"]] == ["text"]


def test_api_errors_become_actionable_messages(tmp_path):
    cases = [(api_error(401, "authentication_error", "invalid x-api-key"), "Clé API Anthropic refusée"),
             (api_error(400, "invalid_request_error", "Your credit balance is too low"), "Crédit insuffisant"),
             (api_error(404, "not_found_error", "model: nope"), "introuvable"),
             (api_error(529, "overloaded_error", "Overloaded"), "surchargé")]
    for resp, expected in cases:
        p = claude(FakeAPI(resp), tmp_path)
        with pytest.raises(ProviderError) as err:
            list(p.stream(SYSTEM, [{"role": "user", "content": "Q"}], SPECS))
        assert expected in err.value.fr and KEY not in err.value.fr + err.value.en


def test_rejected_reasoning_is_retried_once_without_thinking(tmp_path):
    api = FakeAPI(api_error(400, "invalid_request_error", "messages.1.content.0: Invalid `signature` in `thinking` "
                                                          "block. The block is bound to a different conversation."),
                  sse([{"type": "text", "text": "OK"}]))
    msgs = [{"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "R1", "raw": {"provider": "anthropic", "content": [
                {"type": "thinking", "thinking": "", "signature": "OLD"}, {"type": "text", "text": "R1"}]}},
            {"role": "user", "content": "Q2"}]
    turn = list(claude(api, tmp_path).stream(SYSTEM, msgs, SPECS))[-1]
    assert turn.text == "OK" and len(api.bodies) == 2
    assert api.bodies[0]["messages"][1]["content"][0]["type"] == "thinking"
    assert api.bodies[1]["messages"][1]["content"] == [{"type": "text", "text": "R1"}]


def test_daily_cap_blocks_before_any_request(tmp_path):
    usage.record("anthropic", "claude-opus-5-5", input_tokens=0, output_tokens=300_000, root=tmp_path)  # $6
    api = FakeAPI()
    with pytest.raises(ProviderError) as err:
        list(claude(api, tmp_path, cap=5.0).stream(SYSTEM, [{"role": "user", "content": "Q"}], SPECS))
    assert "Plafond" in err.value.fr and not api.bodies


def test_no_key_says_where_to_add_it(ring, tmp_path):
    p = anp.AnthropicProvider(settings={"daily_cap_usd": 5}, root=tmp_path)
    with pytest.raises(ProviderError) as err:
        list(p.stream(SYSTEM, [{"role": "user", "content": "Q"}], SPECS))
    assert "IA & API → Claude" in err.value.fr and "AI & API → Claude" in err.value.en


def test_connection_check_uses_the_free_models_api():
    def handler(request):
        assert request.method == "GET" and request.url.path == "/v1/models/claude-opus-5-5"
        return httpx2.Response(200, json={"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
                                          "created_at": "2026-10-01T00:00:00Z"})
    client = anthropic.Anthropic(api_key=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    res = anp.check_connection("claude-opus-5-5", client=client)
    assert res["ok"] and "Claude Opus 5.5" in res["fr"]
    bad = anthropic.Anthropic(api_key=KEY, max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(
        lambda r: httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                        "message": "invalid x-api-key"}}))))
    assert not anp.check_connection(client=bad)["ok"]


# ---------------------------------------------------------------------------
# Engine + Claude: Expert brief, append-only history, privacy
# ---------------------------------------------------------------------------
def test_engine_keeps_claude_history_append_only(tmp_path):
    api = FakeAPI(
        sse([{"type": "thinking", "thinking": "", "signature": "SIG1"},
             {"type": "tool_use", "id": "toolu_1", "name": "get_budget", "input": {}}], stop="tool_use"),
        sse([{"type": "thinking", "thinking": "", "signature": "SIG2"},
             {"type": "text", "text": "Le budget 2026 est suivi."}]),
        sse([{"type": "text", "text": "Deuxième réponse."}]),
    )
    p = claude(api, tmp_path)
    chat = engine.new_chat("fr")
    t1 = list(engine.ask(chat, "Où en est le budget ?", p, lang="fr"))[-1].turn
    t2 = list(engine.ask(chat, "Et ensuite ?", p, lang="fr", page="Overview"))[-1].turn
    b0, b1, b2 = api.bodies
    # the expert brief is the cached first system block; system and tools never change
    assert b0["system"][0]["text"] == prompts.load_brief()["text"]
    assert b0["system"] == b1["system"] == b2["system"] and b0["tools"] == b1["tools"] == b2["tools"]
    assert len(b0["tools"]) == len(tools.specs())
    # each request starts with the previous one, unchanged (thinking blocks and tool results included)
    assert b1["messages"][:len(b0["messages"])] == b0["messages"]
    assert b2["messages"][:len(b1["messages"])] == b1["messages"]
    assert b1["messages"][1]["content"][0]["signature"] == "SIG1"
    assert b2["messages"][3]["content"][0]["signature"] == "SIG2"
    tool_result = json.loads(b1["messages"][2]["content"][0]["content"])
    assert tool_result["ok"] and "cap_t" in tool_result["result"]
    # turn records: Expert mode, cloud privacy, cost, brief version
    assert t1["mode"] == "expert" and t1["privacy"] == "aggregated" and t1["brief"] == prompts.load_brief()["version"]
    assert t1["label"] == "Expert · Claude Opus 5.5" and t1["cost_usd"] > 0 and t1["cache_read"] == 6000
    assert t1["answer"] == "Le budget 2026 est suivi." and t2["answer"] == "Deuxième réponse."
    assert chat.messages[2]["privacy"] == "aggregated" and chat.messages[3]["raw"]["content"][0]["signature"] == "SIG2"


class CloudFake(Provider):
    key, label, privacy, append_only = "cloudfake", "CloudFake", "aggregated", True

    def __init__(self):
        super().__init__("m")
        self.calls = []

    def stream(self, system, messages, tools, *, think=False, final=False):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "final": final})
        yield Turn(text="ok")


def test_cloud_providers_never_see_local_results():
    chat = engine.new_chat("fr")
    chat.messages = [{"role": "user", "content": "Q local"},
                     {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "query_emissions",
                                                                          "arguments": {"granularity": "day"}}]},
                     {"role": "tool", "tool_call_id": "c1", "name": "query_emissions",
                      "content": '{"ok":true,"result":{"table":"daily rows"}}', "privacy": "full"},
                     {"role": "tool", "tool_call_id": "c2", "name": "x", "content": '{"ok":true,"result":{}}'},
                     {"role": "assistant", "content": "Réponse locale"}]
    fake = CloudFake()
    list(engine.ask(chat, "Question experte", fake, lang="fr"))
    sent = fake.calls[0]["messages"]
    assert "daily rows" not in json.dumps(sent) and sent[2]["content"] == engine._LOCAL_ONLY
    assert sent[3]["content"] == engine._LOCAL_ONLY  # no privacy mark (older chats) = local
    assert sent[1]["tool_calls"][0]["name"] == "query_emissions"  # the history itself stays in place


def test_append_only_window_drops_whole_old_questions_only():
    msgs = []
    for i in range(5):
        msgs += [{"role": "user", "content": f"Q{i} " + "x" * 400},
                 {"role": "tool", "tool_call_id": f"c{i}", "name": "t", "content": "r" * 400, "privacy": "aggregated"},
                 {"role": "assistant", "content": f"A{i}"}]
    fake = CloudFake()
    out = engine.model_messages(msgs, fake, budget=2_000)
    assert out[0]["role"] == "user" and out[0]["content"].startswith("Q3") and len(out) == 6
    assert all(m["content"] == "r" * 400 for m in out if m["role"] == "tool")  # never stubbed
    assert engine.model_messages(msgs, fake, budget=10_000) == msgs


def test_engine_falls_back_to_builtin_without_key(ring, tmp_path):
    p = anp.AnthropicProvider(settings={"daily_cap_usd": 5}, root=tmp_path)
    turn = list(engine.ask(engine.new_chat("fr"), "Où en est le budget carbone de 2026 ?", p, lang="fr"))[-1].turn
    assert turn["provider"] == "builtin" and "IA & API → Claude" in turn["notice"] and turn["answer"]


# ---------------------------------------------------------------------------
# Cloud privacy floor in the tools
# ---------------------------------------------------------------------------
def test_cloud_tools_only_send_whole_months():
    agg = tools.ToolContext(privacy="aggregated")
    q = tools.execute("query_emissions", {"start": "2026-03-10", "end": "2026-03-12", "granularity": "day"}, agg)
    r = q["result"]
    assert (r["start"], r["end"], r["granularity"], r["days"]) == ("2026-03-01", "2026-03-31", "month", 31)
    assert any("whole months" in n for n in r["notes"])
    c = tools.execute("compare_periods", {"period_start": "2026-03-10", "period_end": "2026-03-10"}, agg)["result"]
    assert c["period"]["days"] == 31 and c["reference"]["start"] == "2025-03-01"
    assert tools.execute("get_overview", {}, agg)["result"]["peaks"]["highest_day"] is None
    local = tools.execute("query_emissions", {"start": "2026-03-10", "end": "2026-03-10", "granularity": "day"})
    assert local["result"]["days"] == 1 and local["result"]["granularity"] == "day"


# ---------------------------------------------------------------------------
# Figure check: calculations written out in the answer
# ---------------------------------------------------------------------------
def test_written_calculations_are_checked():
    pool = grounding.source_values({"a": 3155.2, "b": 3096.4, "c": 222.3})
    assert grounding.unverified("Écart : 3 155 t − 3 096 t = 59 t, soit 59 / 3 096 = 1,9 %.", pool) == []
    assert grounding.unverified("EN: 3,155 - 3,096 = 59 t; 222.3 x 2 = 444.6 t; 222,3 × 1000 = 222 300 kg", pool) == []
    assert grounding.unverified("Faux : 3 155 − 3 096 = 70 t.", pool) == ["70"]
    assert grounding.unverified("Inventé : 4 000 − 3 096 = 904 t.", pool) == ["4 000", "904"]
    assert grounding.unverified("Seul : 59 t de plus.", pool) == ["59"]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def _settings_page():
    from app.pages import ai_claude

    ai_claude.render()


def _overview_page():
    from app.pages import ai_overview

    ai_overview.render()


def _assistant_page():
    from app.pages import assistant

    assistant.render()


def test_settings_page_stores_the_key_and_never_shows_it(ring, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_settings_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    text = _page_text(at)
    assert "Aucune clé" in text and "Repli automatique" in text
    at.text_input[0].input(KEY)
    at.button[0].click().run()  # "Enregistrer la clé" (form submit)
    assert not at.exception
    assert ring.store[(vault.SERVICE, "anthropic")] == KEY
    text = _page_text(at)
    assert "sk-ant-…zZ9q" in text and KEY not in text
    assert at.text_input[0].value == ""  # the form cleared the field
    assert KEY not in json.dumps(at._session_state.filtered_state, default=str)  # nothing kept after submit


def test_overview_page_lists_providers_cap_privacy_and_brief(ring, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    ring.set_password(vault.SERVICE, "anthropic", KEY)
    at = AppTest.from_function(_overview_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    text = _page_text(at)
    for label in ("Ollama Cloud", "Claude (Anthropic)", "OpenAI", "Google Gemini", "Mistral AI"):
        assert label in text
    assert "Bientôt disponible" in text and "sk-ant-…zZ9q" in text and KEY not in text
    assert "Données envoyées au cloud" in text and "expert_brief.md" in text
    at.number_input(key="ai_cap").set_value(2.0).run()
    assert usage.load_settings(tmp_path)["daily_cap_usd"] == 2.0


def _page_text(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "success", "error", "warning", "info", "text_input"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [str(e.proto) for e in at.get("html")]
    return "\n".join(parts)


def test_assistant_page_expert_mode_end_to_end(ring, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    ring.set_password(vault.SERVICE, "anthropic", KEY)
    api = FakeAPI(sse([{"type": "text", "text": "Analyse experte prête."}]))
    monkeypatch.setattr(anp.AnthropicProvider, "_client", lambda self: api.client())
    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_assistant_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.session_state["as_engine"] = "expert:anthropic"
    at.run()
    assert not at.exception
    assert any("Mode Expert" in c.value for c in at.caption)
    at.chat_input[0].set_value("Quel est le point clé ?").run()
    assert not at.exception
    chat = at.session_state["as_chat"]
    assert chat.turns[0]["provider"] == "anthropic" and chat.turns[0]["privacy"] == "aggregated"
    assert api.bodies and api.bodies[0]["system"][0]["text"].startswith("# CarbonSync expert brief")
    # switching to a local engine starts a new conversation (local and cloud never share data)
    at.selectbox(key="as_engine_select").set_value("builtin").run()
    assert not at.exception and not at.session_state["as_chat"].turns
