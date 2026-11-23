"""AI providers, step 2: OpenRouter's free models on the OpenAI-compatible adapter.

The real ``openai`` SDK runs on a mocked HTTP transport (true SSE parsing, no network, no key).
Covered: vault, message conversion, streaming text / reasoning / tool calls, request body (tools on
every call, tool_choice, free-only fallback, no attribution headers), final round, errors, the cap,
the engine under the Expert rules, the free model list, the key check and the two pages.
"""
from __future__ import annotations

import json

import httpx
import httpx2
import openai
import pytest

from carbonsync import obs
from carbonsync.assistant import engine, store, usage, vault
from carbonsync.assistant.providers import ProviderError, Turn
from carbonsync.assistant.providers import ollama_provider as olp
from carbonsync.assistant.providers import openrouter as orr
from carbonsync.assistant.providers.openai_compat import to_openai

KEY = "sk-or-v1-" + "0123456789abcdef" * 4
MODEL = orr.DEFAULT_MODEL


class FakeKeyring:
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
    for env in ("OPENROUTER_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return fake


# ---------------------------------------------------------------------------
# A fake OpenRouter: streamed chat completions on a mocked transport
# ---------------------------------------------------------------------------
def ck(delta=None, finish=None, model=MODEL, usage_=None, **extra):
    c = {"id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": model,
         "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}
    if usage_ is not None:
        c["usage"] = usage_
    return c


def sse(*chunks) -> str:
    return ": OPENROUTER PROCESSING\n\n" + "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def text_reply(text="Réponse.", model=MODEL, cost=0.0):
    return sse(ck({"role": "assistant", "content": text}, model=model),
               ck({"content": ""}, "stop", model=model,
                  usage_={"prompt_tokens": 900, "completion_tokens": 12, "total_tokens": 912, "cost": cost}))


def tool_reply(name, args, reasoning="Je lis les données."):
    raw = json.dumps(args)
    return sse(ck({"role": "assistant", "content": None, "reasoning": reasoning}),
               ck({"tool_calls": [{"index": 0, "id": "call_a1", "type": "function",
                                   "function": {"name": name, "arguments": raw[:6]}}]}),
               ck({"tool_calls": [{"index": 0, "function": {"arguments": raw[6:]}}]}),
               ck({}, "tool_calls", usage_={"prompt_tokens": 800, "completion_tokens": 30, "total_tokens": 830,
                                            "cost": 0, "prompt_tokens_details": {"cached_tokens": 0}}))


class FakeRouter:
    """Plays scripted responses (SSE text, or (status, json body)) and records each request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies: list[dict] = []
        self.headers: list[dict] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        reply = self.replies.pop(0) if self.replies else text_reply()
        if isinstance(reply, tuple):
            return httpx2.Response(reply[0], json=reply[1])
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=reply)

    def client(self) -> openai.OpenAI:
        return openai.OpenAI(api_key=KEY, base_url=orr.API, max_retries=0,
                             http_client=httpx2.Client(transport=httpx2.MockTransport(self.handler)))


def router(fake, tmp_path, cap=5.0, model=MODEL, **opts):
    settings = {"daily_cap_usd": cap, "openrouter": {"fallback": True, "deny_collection": False, **opts}}
    return orr.OpenRouterProvider(model, settings=settings, root=tmp_path, client=fake.client())


SPECS = [{"name": "get_budget", "description": "Budget", "parameters": {"type": "object", "properties": {}}}]


# ---------------------------------------------------------------------------
# Vault and catalogue
# ---------------------------------------------------------------------------
def test_vault_stores_an_openrouter_key(ring):
    assert vault.set_key("openrouter", KEY) == "sk-or-…" + KEY[-4:]
    assert ring.store[(vault.SERVICE, "openrouter")] == KEY
    with pytest.raises(vault.VaultError) as err:
        vault.set_key("openrouter", "sk-ant-" + "x" * 60)
    assert "sk-or-" in err.value.fr and "x" * 20 not in err.value.fr
    assert "sk-or-[REDACTED]" in obs.redact(f"Bearer {KEY}")


def test_top5_are_free_tool_models_and_the_list_filters():
    assert len(orr.TOP5) == 5 and all(orr.is_free(m) for m in orr.TOP5_MODELS)
    assert all("tools" in m["tags"] and m["note"][0] and m["host"] for m in orr.TOP5)
    snap = {m["model"] for m in orr.SNAPSHOT}
    assert set(orr.TOP5_MODELS) <= snap and "openrouter/free" not in snap
    payload = {"data": [
        {"id": "a/good:free", "name": "A: Good (free)", "context_length": 1000, "supported_parameters": ["tools", "tool_choice"]},
        {"id": "a/notools:free", "name": "x", "supported_parameters": ["temperature"]},
        {"id": "a/nochoice:free", "name": "x", "supported_parameters": ["tools"]},
        {"id": "a/paid", "name": "x", "supported_parameters": ["tools", "tool_choice"]},
        {"id": "openrouter/free", "name": "router", "supported_parameters": ["tools", "tool_choice"]},
    ]}
    assert [m["model"] for m in orr.parse_models(payload)] == ["a/good:free"]
    assert orr.parse_models(payload)[0]["name"] == "A: Good"


def test_refresh_saves_the_list_and_reports_missing_top_models(tmp_path):
    payload = {"data": [{"id": MODEL, "name": "NVIDIA: Nemotron 3 Ultra (free)", "context_length": 1_000_000,
                         "supported_parameters": ["tools", "tool_choice", "reasoning"]}]}
    res = orr.refresh_free_models(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert res["ok"] and res["count"] == 1 and len(res["missing"]) == 4
    models, as_of = orr.free_models(tmp_path)
    assert [m["model"] for m in models] == [MODEL] and as_of.startswith("20")
    bad = orr.refresh_free_models(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert not bad["ok"] and orr.free_models(tmp_path)[0][0]["model"] == MODEL  # the saved list is kept


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------
def test_to_openai_pairs_tool_results_and_replays_current_reasoning_only():
    msgs = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "", "raw": {"provider": "openrouter", "reasoning": "old"},
         "tool_calls": [{"id": "c0", "name": "t", "arguments": {"a": 1}}]},
        {"role": "tool", "tool_call_id": "c0", "name": "t", "content": "{}"},
        {"role": "assistant", "content": "A1"},
        {"role": "tool", "tool_call_id": "orphan", "name": "t", "content": "{}"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "", "raw": {"provider": "openrouter", "reasoning": "now"},
         "tool_calls": [{"id": "c1", "name": "t", "arguments": {}}, {"id": "c2", "name": "u", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "{\"ok\": true}"},
    ]
    out = to_openai("SYS", msgs, reasoning_from="openrouter")
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[2]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}' and out[2]["content"] is None
    assert "reasoning" not in out[2]  # earlier question
    assert not any(m.get("tool_call_id") == "orphan" for m in out)
    assert out[-3]["reasoning"] == "now"
    assert out[-2] == {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"}
    assert out[-1]["tool_call_id"] == "c2" and "not run" in out[-1]["content"]  # a missing result is filled in


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def test_stream_collects_reasoning_and_split_tool_calls(tmp_path):
    fake = FakeRouter(tool_reply("get_budget", {"year": 2026}))
    p = router(fake, tmp_path)
    items = list(p.stream("SYS", [{"role": "user", "content": "Budget ?"}], SPECS, think=True))
    turn = items[-1]
    assert isinstance(turn, Turn) and turn.stop == "tool_use" and turn.thinking == "Je lis les données."
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [("call_a1", "get_budget", {"year": 2026})]
    assert turn.raw == {"provider": "openrouter", "model": MODEL, "reasoning": "Je lis les données."}
    assert turn.cost_usd == 0 and turn.input_tokens == 800 and not turn.model
    body, headers = fake.bodies[0], fake.headers[0]
    assert body["model"] == MODEL and body["stream"] is True and body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "get_budget" and body["reasoning"] == {"enabled": True}
    assert body["models"][0] == MODEL and len(body["models"]) == 3 and all(m.endswith(":free") for m in body["models"])
    assert "provider" not in body
    assert headers["authorization"] == f"Bearer {KEY}"
    assert not any(h in headers for h in ("http-referer", "x-title", "x-openrouter-title"))  # no attribution
    assert usage.history(1, root=tmp_path)[0]["provider"] == "openrouter"


def test_options_no_fallback_and_deny_collection(tmp_path):
    fake = FakeRouter()
    list(router(fake, tmp_path, fallback=False, deny_collection=True).stream("S", [{"role": "user", "content": "Q"}], []))
    body = fake.bodies[0]
    assert "models" not in body and body["provider"] == {"data_collection": "deny"}
    assert "reasoning" not in body and "tools" not in body


def test_final_round_keeps_tools_and_drops_tool_calls(tmp_path):
    fake = FakeRouter(tool_reply("get_budget", {}))
    turn = list(router(fake, tmp_path).stream("S", [{"role": "user", "content": "Q"}], SPECS, final=True))[-1]
    assert fake.bodies[0]["tool_choice"] == "none" and fake.bodies[0]["tools"]
    assert turn.tool_calls == [] and turn.stop == "end"


def test_model_without_tool_choice_none_is_retried_without_it(tmp_path):
    fake = FakeRouter((400, {"error": {"code": 400, "message": "tool_choice 'none' is not supported"}}), text_reply("OK"))
    turn = list(router(fake, tmp_path).stream("S", [{"role": "user", "content": "Q"}], SPECS, final=True))[-1]
    assert turn.text == "OK" and "tool_choice" not in fake.bodies[1] and fake.bodies[1]["tools"]


def test_cut_answer_never_runs_its_tool_call(tmp_path):
    cut = sse(ck({"tool_calls": [{"index": 0, "id": "c", "type": "function",
                                  "function": {"name": "get_budget", "arguments": "{\"ye"}}]}), ck({}, "length"))
    turn = list(router(FakeRouter(cut), tmp_path).stream("S", [{"role": "user", "content": "Q"}], SPECS))[-1]
    assert turn.stop == "max_tokens" and turn.tool_calls == []


def test_fallback_model_is_reported(tmp_path):
    other = orr.TOP5_MODELS[1]
    turn = list(router(FakeRouter(text_reply(model=other)), tmp_path).stream("S", [{"role": "user", "content": "Q"}], []))[-1]
    assert turn.model == other
    same = list(router(FakeRouter(text_reply(model=MODEL.removesuffix(":free"))), tmp_path)
                .stream("S", [{"role": "user", "content": "Q"}], []))[-1]
    assert same.model == ""  # the ":free" suffix alone is not a fallback


@pytest.mark.parametrize("status,message,word", [
    (401, "No auth credentials found", "refusée"),
    (402, "Insufficient credits", "négatif"),
    (404, "No endpoints found matching your data policy", "confidentialité"),
    (429, "Rate limit exceeded: free-models-per-day", "50 par jour"),
    (502, "Provider returned error", "réessayez"),
])
def test_api_errors_become_clear_messages(tmp_path, status, message, word):
    fake = FakeRouter((status, {"error": {"code": status, "message": message}}))
    with pytest.raises(ProviderError) as err:
        list(router(fake, tmp_path).stream("S", [{"role": "user", "content": "Q"}], SPECS))
    assert word in err.value.fr and KEY not in err.value.fr + err.value.en


def test_error_chunk_mid_stream_raises(tmp_path):
    broken = sse(ck({"content": "Début"}), ck({}, "error", error={"code": 429, "message": "Rate limit exceeded"}))
    with pytest.raises(ProviderError) as err:
        list(router(FakeRouter(broken), tmp_path).stream("S", [{"role": "user", "content": "Q"}], []))
    assert "Limite" in err.value.fr


def test_cap_blocks_and_missing_key_says_where(ring, tmp_path):
    usage.record("anthropic", "claude-opus-5-5", input_tokens=0, output_tokens=0, cost=2.0, root=tmp_path)
    fake = FakeRouter()
    with pytest.raises(ProviderError) as err:
        list(router(fake, tmp_path, cap=1.0).stream("S", [{"role": "user", "content": "Q"}], []))
    assert "Plafond" in err.value.fr and not fake.bodies
    with pytest.raises(ProviderError) as err:
        list(orr.OpenRouterProvider(MODEL, settings={"daily_cap_usd": 0}, root=tmp_path)
             .stream("S", [{"role": "user", "content": "Q"}], []))
    assert "IA & API → OpenRouter" in err.value.fr and "AI & API → OpenRouter" in err.value.en


def test_engine_runs_a_free_model_under_expert_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    fake = FakeRouter(tool_reply("query_emissions", {"start": "2026-03-10", "end": "2026-03-10", "granularity": "day"}),
                      text_reply("Mars 2026 : analyse faite."))
    p = router(fake, tmp_path)
    turn = list(engine.ask(engine.new_chat("fr"), "Combien le 10 mars ?", p, lang="fr"))[-1].turn
    assert turn["provider"] == "openrouter" and turn["privacy"] == "aggregated" and turn["mode"] == "expert"
    assert turn["answer"].startswith("Mars 2026") and turn["brief"]
    assert fake.bodies[0]["messages"][0]["content"].startswith("# CarbonSync expert brief")
    assert turn["tools"][0]["result"]["granularity"] == "month"  # whole-month floor
    second = fake.bodies[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_a1" and fake.bodies[1]["tools"]
    assert second[-2]["reasoning"] == "Je lis les données."


# ---------------------------------------------------------------------------
# Account checks
# ---------------------------------------------------------------------------
def test_connection_test_reads_the_free_quota():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/key" and request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={"data": {"label": "k", "is_free_tier": True, "limit_remaining": None,
                                                  "free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38}}})

    res = orr.check_connection(KEY, transport=httpx.MockTransport(handler))
    assert res["ok"] and "12 / 50 requêtes" in res["fr"] and "compte gratuit" in res["fr"]
    bad = orr.check_connection(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    assert not bad["ok"] and "refusée" in bad["fr"] and KEY not in json.dumps(bad)


def test_quick_test_is_free(tmp_path):
    fake = FakeRouter(text_reply("OK"))
    res = orr.quick_test(MODEL, client=fake.client(), root=tmp_path)
    assert res["ok"] and "gratuit" in res["fr"] and "models" not in fake.bodies[0]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def _openrouter_page():
    from app.pages import ai_openrouter

    ai_openrouter.render()


def _assistant_page():
    from app.pages import assistant

    assistant.render()


def _text(at) -> str:
    parts = []
    for kind in ("markdown", "caption", "success", "error", "warning", "info", "text_input"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [str(e.proto) for e in at.get("html")]
    return "\n".join(parts)


@pytest.fixture
def local_down(monkeypatch):
    down = {"installed": False, "running": False, "models": [], "loaded": [], "error": "test"}
    monkeypatch.setattr(olp, "status", lambda timeout=2.0: dict(down))
    from app.pages import assistant

    assistant._ollama_status.clear()


def test_openrouter_page_key_models_and_options(ring, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(orr, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_openrouter_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    text = _text(at)
    assert "Aucune clé" in text and all(m["label"] in text for m in orr.TOP5) and "50 requêtes par jour" in text
    assert at.button(key=f"or_pick_{orr.TOP5_MODELS[1]}").disabled
    at.text_input[0].input(KEY)
    at.button[0].click().run()
    assert not at.exception and ring.store[(vault.SERVICE, "openrouter")] == KEY
    text = _text(at)
    assert "sk-or-…" + KEY[-4:] in text and KEY not in text and at.text_input[0].value == ""
    assert KEY not in json.dumps(at._session_state.filtered_state, default=str)
    at.button(key=f"or_pick_{orr.TOP5_MODELS[1]}").click().run()
    assert usage.load_settings(tmp_path)["openrouter"]["model"] == orr.TOP5_MODELS[1]
    at.toggle(key="or_deny").set_value(True).run()
    assert not at.exception and usage.load_settings(tmp_path)["openrouter"]["deny_collection"] is True


def test_assistant_lists_free_models_and_answers_with_one(ring, tmp_path, monkeypatch, local_down):
    from streamlit.testing.v1 import AppTest

    ring.set_password(vault.SERVICE, "openrouter", KEY)
    fake = FakeRouter(text_reply("Réponse Nemotron."))
    monkeypatch.setattr(orr.OpenRouterProvider, "_client", lambda self: fake.client())
    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_assistant_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    options = at.selectbox(key="as_engine_select").options
    assert sum("OpenRouter (gratuit)" in o for o in options) == 5
    at.selectbox(key="as_engine_select").set_value(f"expert:openrouter:{MODEL}").run()
    assert not at.exception and any("via OpenRouter" in c.value for c in at.caption)
    at.chat_input[0].set_value("Quel est le point clé ?").run()
    assert not at.exception
    turn = at.session_state["as_chat"].turns[0]
    assert turn["provider"] == "openrouter" and turn["privacy"] == "aggregated" and "Nemotron" in turn["answer"]
