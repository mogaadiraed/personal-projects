"""AI providers, step 1: Ollama Cloud (DeepSeek, Kimi… with the user's key) and the provider pages.

Vault for keys without a prefix, cost, the cloud adapter on a fake Ollama client (stream, tools,
reasoning replay, cost, cap, errors), the free connection test on a mocked transport, the engine
running a cloud model under the Expert rules, and the Ollama / Assistant pages. No network, no key.
"""
from __future__ import annotations

import json
import logging

import httpx
import ollama
import pytest
from ollama import ChatResponse, Message

from carbonsync import obs
from carbonsync.assistant import engine, store, usage, vault
from carbonsync.assistant.providers import ProviderError, Turn, registry
from carbonsync.assistant.providers import ollama_cloud as oc
from carbonsync.assistant.providers import ollama_provider as olp

OKEY = "3f9c2a7b61d04e8f9a1b2c3d4e5f6a7b.Zx81Qw7Lm2Np4Rt6Vy9Ab3Cd5Ef7Gh1Jk"


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
    for env in ("OLLAMA_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return fake


def chunk(text="", thinking="", calls=None, done=False, inp=0, out=0, reason="stop"):
    tool_calls = [Message.ToolCall(function=Message.ToolCall.Function(name=n, arguments=a)) for n, a in (calls or [])]
    return ChatResponse(model="m", done=done, done_reason=reason if done else None,
                        prompt_eval_count=inp if done else None, eval_count=out if done else None,
                        message=Message(role="assistant", content=text, thinking=thinking or None,
                                        tool_calls=tool_calls or None))


class FakeOllama:
    """Stands in for ``ollama.Client``: records each request, plays scripted streams."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    def chat(self, **kw):
        self.calls.append(kw)
        script = self.scripts.pop(0) if self.scripts else [chunk("OK", done=True, inp=50, out=2)]
        if isinstance(script, Exception):
            raise script
        return iter(script)


def cloud(fake, tmp_path, cap=5.0, model="deepseek-v4-pro"):
    return oc.OllamaCloudProvider(model, settings={"daily_cap_usd": cap}, root=tmp_path, client=fake)


# ---------------------------------------------------------------------------
# Registry and vault
# ---------------------------------------------------------------------------
def test_registry_lists_every_provider_with_a_key_spec():
    assert {"ollama", "ollama_cloud", "anthropic", "openai", "gemini", "mistral", "custom"} <= set(registry.PROVIDERS)
    assert set(vault.PROVIDERS) == {k for k, v in registry.PROVIDERS.items() if v.get("env")}
    assert registry.PROVIDERS["ollama_cloud"]["status"] == "ready" and registry.PROVIDERS["openai"]["status"] == "planned"
    assert registry.model_label("ollama_cloud", "kimi-k3.1-pro") == "Kimi K3.1 Pro"
    assert registry.model_label("anthropic", "claude-opus-5-5") == "Claude Opus 5.5"


def test_vault_takes_an_ollama_key_without_prefix(ring, monkeypatch):
    masked = vault.set_key("ollama_cloud", f" {OKEY} ")
    assert masked == "…" + OKEY[-4:] and OKEY[:-4] not in masked
    assert ring.store[(vault.SERVICE, "ollama_cloud")] == OKEY
    assert vault.mask("sk-ant-api03-" + "x" * 60 + "zZ9q") == "sk-ant-…zZ9q"  # Claude masking unchanged
    with pytest.raises(vault.VaultError):
        vault.set_key("ollama_cloud", "short")
    vault.delete_key("ollama_cloud")
    monkeypatch.setenv("OLLAMA_API_KEY", OKEY)
    assert vault.get_key("ollama_cloud") == (OKEY, "env")


def test_logs_blank_keys_without_a_prefix(ring, caplog):
    vault.set_key("ollama_cloud", OKEY)
    log = obs.get_logger("carbonsync.test.redact2")
    with caplog.at_level(logging.INFO, logger="carbonsync"):
        log.warning(f"Authorization: Bearer {OKEY}")
    assert OKEY not in obs.redact(f"Bearer {OKEY}")
    assert "gsk_[REDACTED]" in obs.redact("key gsk_" + "a" * 30) and "AIza[REDACTED]" in obs.redact("AIza" + "b" * 35)


# ---------------------------------------------------------------------------
# Catalogue and cost
# ---------------------------------------------------------------------------
def test_top5_has_deepseek_and_kimi_with_prices():
    assert len(oc.TOP5) == 5 and oc.DEFAULT_MODEL == oc.TOP5_MODELS[0] == "deepseek-v4-pro"
    assert {"deepseek-v4-pro", "kimi-k3.1-pro"} <= set(oc.TOP5_MODELS)
    for m in oc.TOP5:
        assert m["model"] in oc.PRICES and "tools" in m["tags"] and m["note"][0] and m["note"][1]
    assert not set(oc.OTHER_MODELS) & set(oc.TOP5_MODELS)


def test_cost_uses_ollama_prices_and_prices_unknown_models_high():
    assert oc.cost("deepseek-v4-pro", 10_000, 5_000, 2_000) == pytest.approx(0.02134)
    assert oc.price("brand-new-model") == oc.PRICES["kimi-k3"]


# ---------------------------------------------------------------------------
# The cloud adapter
# ---------------------------------------------------------------------------
def test_cloud_stream_tools_reasoning_replay_and_cost(tmp_path):
    fake = FakeOllama(
        [chunk(thinking="Je lis le budget."), chunk(calls=[("get_budget", {"year": 2026})], done=True, inp=4_000, out=60)],
        [chunk("Le budget est dépassé."), chunk(done=True, inp=4_500, out=40)])
    p = cloud(fake, tmp_path)
    assert p.privacy == "aggregated" and p.display_name == "Ollama Cloud · DeepSeek V4 Pro"
    items = list(p.stream("SYS", [{"role": "user", "content": "Budget ?"}], [{"name": "get_budget"}], think=True))
    turn = items[-1]
    assert isinstance(turn, Turn) and turn.stop == "tool_use" and turn.tool_calls[0].name == "get_budget"
    assert turn.raw == {"provider": "ollama_cloud", "thinking": "Je lis le budget."}
    assert turn.cost_usd == pytest.approx(oc.cost("deepseek-v4-pro", 4_000, 0, 60))
    first = fake.calls[0]
    assert first["model"] == "deepseek-v4-pro" and first["think"] is True and "keep_alive" not in first
    assert "num_ctx" not in first["options"] and first["messages"][0] == {"role": "system", "content": "SYS"}
    # the next round sends the reasoning back with the tool call of the same question
    history = [{"role": "user", "content": "Budget ?"},
               {"role": "assistant", "content": "", "raw": turn.raw,
                "tool_calls": [{"id": "c1", "name": "get_budget", "arguments": {"year": 2026}}]},
               {"role": "tool", "tool_call_id": "c1", "name": "get_budget", "content": "{}"}]
    list(p.stream("SYS", history, [{"name": "get_budget"}]))
    sent = fake.calls[1]["messages"]
    assert sent[2]["thinking"] == "Je lis le budget." and sent[3] == {"role": "tool", "content": "{}",
                                                                       "tool_name": "get_budget"}
    rows = usage.history(1, root=tmp_path)
    assert rows[0]["provider"] == "ollama_cloud" and rows[0]["requests"] == 2


def test_reasoning_of_earlier_questions_is_not_resent():
    msgs = [{"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "", "raw": {"thinking": "old"}, "tool_calls": [{"name": "t", "arguments": {}}]},
            {"role": "tool", "name": "t", "content": "{}"}, {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"}]
    assert all("thinking" not in m for m in olp._to_ollama(msgs, thinking=True))


def test_cut_tool_call_is_never_run(tmp_path):
    fake = FakeOllama([chunk(calls=[("get_budget", {"year": 2026})], done=True, inp=10, out=5, reason="length")])
    turn = list(cloud(fake, tmp_path).stream("S", [{"role": "user", "content": "Q"}], [{"name": "get_budget"}]))[-1]
    assert turn.stop == "max_tokens" and turn.tool_calls == []


def test_client_targets_ollama_com_with_the_bearer_key(ring):
    vault.set_key("ollama_cloud", OKEY)
    client = oc.OllamaCloudProvider("glm-5.3")._client()
    assert str(client._client.base_url).rstrip("/") == "https://ollama.com"
    assert client._client.headers["authorization"] == f"Bearer {OKEY}"


def test_no_key_says_where_to_add_it(ring, tmp_path):
    p = oc.OllamaCloudProvider(settings={"daily_cap_usd": 5}, root=tmp_path)
    with pytest.raises(ProviderError) as err:
        list(p.stream("S", [{"role": "user", "content": "Q"}], []))
    assert "IA & API → Ollama" in err.value.fr and "AI & API → Ollama" in err.value.en


def test_cap_blocks_before_any_request(tmp_path):
    usage.record("anthropic", "claude-opus-5-5", input_tokens=0, output_tokens=0, cost=1.0, root=tmp_path)
    fake = FakeOllama()
    with pytest.raises(ProviderError) as err:
        list(cloud(fake, tmp_path, cap=0.5).stream("S", [{"role": "user", "content": "Q"}], []))
    assert "Plafond" in err.value.fr and "Vue d'ensemble" in err.value.fr and not fake.calls


@pytest.mark.parametrize("code,body,word", [
    (401, '{"error":"unauthorized"}', "refusée"),
    (402, '{"error":"insufficient balance"}', "Crédit"),
    (403, '{"error":"this model requires a subscription"}', "abonnement"),
    (404, '{"error":"model \'nope\' not found"}', "inconnu"),
    (429, '{"error":"rate limit exceeded"}', "Limite"),
    (502, '{"error":"bad gateway"}', "réessayez"),
])
def test_api_errors_become_clear_messages(tmp_path, code, body, word):
    fake = FakeOllama(ollama.ResponseError(body, code))
    with pytest.raises(ProviderError) as err:
        list(cloud(fake, tmp_path, model="nope").stream("S", [{"role": "user", "content": "Q"}], []))
    assert word in err.value.fr and OKEY not in err.value.fr


def test_engine_runs_a_cloud_model_under_expert_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    fake = FakeOllama([chunk(calls=[("query_emissions", {"start": "2026-03-10", "end": "2026-03-10",
                                                          "granularity": "day"})], done=True, inp=900, out=30)],
                      [chunk("Mars 2026 analysé."), chunk(done=True, inp=1200, out=20)])
    p = cloud(fake, tmp_path)
    chat = engine.new_chat("fr")
    turn = list(engine.ask(chat, "Combien le 10 mars ?", p, lang="fr"))[-1].turn
    assert turn["provider"] == "ollama_cloud" and turn["privacy"] == "aggregated" and turn["mode"] == "expert"
    assert turn["cost_usd"] > 0 and turn["brief"]
    assert fake.calls[0]["messages"][0]["content"].startswith("# CarbonSync expert brief")
    result = turn["tools"][0]["result"]
    assert result["granularity"] == "month" and result["days"] == 31  # whole-month floor in the cloud


# ---------------------------------------------------------------------------
# Account checks (free)
# ---------------------------------------------------------------------------
def test_connection_test_reads_the_balance():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/balance" and request.headers["authorization"] == f"Bearer {OKEY}"
        return httpx.Response(200, json={"included": {"balance_usd": 72.5, "allowance_usd": 100},
                                         "purchased": {"balance_usd": 25}})

    res = oc.check_connection(OKEY, transport=httpx.MockTransport(handler))
    assert res["ok"] and "72.50 $ sur 100.00 $" in res["fr"] and "$25.00" in res["en"]
    bad = oc.check_connection(OKEY, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "x"})))
    assert not bad["ok"] and "refusée" in bad["fr"] and OKEY not in json.dumps(bad)


def test_quick_test_measures_and_counts_the_cost(tmp_path):
    res = oc.quick_test("glm-5.3", client=FakeOllama(), root=tmp_path)
    assert res["ok"] and "GLM 5.3" in res["fr"] and res["cost_usd"] > 0
    assert usage.history(1, root=tmp_path)[0]["model"] == "glm-5.3"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def _ollama_page():
    from app.pages import ai_ollama

    ai_ollama.render()


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
    """No local Ollama during page tests (deterministic, whatever runs on this PC)."""
    down = {"installed": False, "running": False, "models": [], "loaded": [], "error": "test"}
    monkeypatch.setattr(olp, "status", lambda timeout=2.0: dict(down))
    monkeypatch.setattr(olp, "details", lambda timeout=2.0: {**down, "version": "", "installed_models": [],
                                                              "loaded_models": []})
    from app.pages import ai_ollama, assistant

    ai_ollama._local_details.clear()
    assistant._ollama_status.clear()


def test_ollama_page_stores_the_key_and_picks_a_model(ring, tmp_path, monkeypatch, local_down):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_ollama_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    text = _text(at)
    assert "Aucune clé" in text and all(m["label"] in text for m in oc.TOP5)
    assert at.button(key="oc_pick_kimi-k3.1-pro").disabled  # no key yet
    at.text_input[0].input(OKEY)
    at.button[0].click().run()  # "Enregistrer la clé" (form submit)
    assert not at.exception and ring.store[(vault.SERVICE, "ollama_cloud")] == OKEY
    text = _text(at)
    assert "…" + OKEY[-4:] in text and OKEY not in text and at.text_input[0].value == ""
    assert OKEY not in json.dumps(at._session_state.filtered_state, default=str)
    at.button(key="oc_pick_kimi-k3.1-pro").click().run()
    assert not at.exception and usage.load_settings(tmp_path)["ollama_cloud"]["model"] == "kimi-k3.1-pro"
    assert at.button(key="oc_pick_kimi-k3.1-pro").disabled  # now the chosen one


def test_assistant_lists_cloud_models_and_answers_with_one(ring, tmp_path, monkeypatch, local_down):
    from streamlit.testing.v1 import AppTest

    ring.set_password(vault.SERVICE, "ollama_cloud", OKEY)
    fake = FakeOllama([chunk("Réponse DeepSeek."), chunk(done=True, inp=800, out=10)])
    monkeypatch.setattr(oc.OllamaCloudProvider, "_client", lambda self: fake)
    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_assistant_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    options = at.selectbox(key="as_engine_select").options
    assert sum("Ollama Cloud" in o for o in options) == 5 and not any("Anthropic" in o for o in options)
    at.selectbox(key="as_engine_select").set_value("expert:ollama_cloud:deepseek-v4-pro").run()
    assert not at.exception and any("Mode Expert" in c.value and "Ollama" in c.value for c in at.caption)
    at.chat_input[0].set_value("Quel est le point clé ?").run()
    assert not at.exception
    turn = at.session_state["as_chat"].turns[0]
    assert turn["provider"] == "ollama_cloud" and turn["privacy"] == "aggregated" and "DeepSeek" in turn["answer"]
