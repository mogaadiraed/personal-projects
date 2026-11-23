"""Assistant Phase 2: tool loop with a scripted fake model, built-in assistant, fallback,
history trimming, Ollama message format, saved conversations, and the Assistant page."""
from __future__ import annotations

import json

import pytest

from carbonsync.assistant import engine, prompts, store
from carbonsync.assistant.context import load_context
from carbonsync.assistant.providers import (
    Delta,
    Provider,
    ProviderError,
    ToolCall,
    Turn,
)
from carbonsync.assistant.providers import ollama_provider as olp
from carbonsync.assistant.providers.builtin import BuiltinProvider, route


class FakeModel(Provider):
    """Plays back scripted turns; records what each call received."""

    key, label = "fake", "Fake"

    def __init__(self, script: list) -> None:
        super().__init__("fake-1")
        self.script = list(script)
        self.calls: list[dict] = []

    def stream(self, system, messages, tools, *, think=False, final=False):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        step = self.script.pop(0) if self.script else Turn(text="")
        if isinstance(step, Exception):
            raise step
        if isinstance(step, Turn):
            if step.text:
                yield Delta("text", step.text)
            yield Turn(text=step.text, tool_calls=step.tool_calls, input_tokens=100, output_tokens=20)


def _run(chat, question, provider, **kw):
    events = list(engine.ask(chat, question, provider, **kw))
    return events, events[-1].turn


def test_loop_runs_tools_then_answers():
    fake = FakeModel([Turn(tool_calls=[ToolCall("c1", "get_budget", {})]), Turn(text="Le budget est dépassé.")])
    chat = engine.new_chat("fr")
    events, turn = _run(chat, "Où en est le budget ?", fake, lang="fr")
    kinds = [e.kind for e in events]
    assert kinds[0] == "model" and "tool_start" in kinds and "tool_end" in kinds and kinds[-1] == "done"
    assert turn["answer"] == "Le budget est dépassé." and turn["error"] is None
    assert [t["name"] for t in turn["tools"]] == ["get_budget"] and turn["tools"][0]["ok"]
    assert turn["rounds"] == 2 and turn["input_tokens"] == 200
    assert [m["role"] for m in chat.messages] == ["user", "assistant", "tool", "assistant"]
    tool_msg = chat.messages[2]
    assert tool_msg["tool_call_id"] == "c1" and json.loads(tool_msg["content"])["ok"] is True
    # the second call saw the tool result; the system prompt carries the data context
    assert fake.calls[1]["messages"][-1]["role"] == "tool"
    assert "CURRENT CONTEXT" in fake.calls[0]["system"] and "French" in fake.calls[0]["system"]
    assert len(fake.calls[0]["tools"]) == 16


def test_last_round_has_no_tools_so_the_model_must_answer():
    loop = [Turn(tool_calls=[ToolCall(f"c{i}", "get_methodology", {})]) for i in range(3)]
    fake = FakeModel([*loop, Turn(text="Voici la réponse.")])
    _, turn = _run(engine.new_chat(), "facteurs ?", fake, lang="fr", max_rounds=4)
    assert fake.calls[-1]["tools"] == [] and all(c["tools"] for c in fake.calls[:-1])
    assert turn["answer"] == "Voici la réponse." and len(turn["tools"]) == 3


def test_bad_tool_call_is_reported_to_the_model():
    fake = FakeModel([Turn(tool_calls=[ToolCall("c1", "drop_table", {}), ToolCall("c2", "get_budget", {"year": "x"})]),
                      Turn(text="ok")])
    chat = engine.new_chat("en")
    _, turn = _run(chat, "hi", fake, lang="en")
    assert [t["ok"] for t in turn["tools"]] == [False, False]
    errors = [json.loads(m["content"]) for m in chat.messages if m["role"] == "tool"]
    assert all(e["ok"] is False and e["error"] for e in errors)


def test_model_failure_falls_back_to_builtin():
    fake = FakeModel([ProviderError("Ollama ne répond pas", "Ollama is not responding")])
    _, turn = _run(engine.new_chat(), "Où en est le budget 2026 ?", fake, lang="fr")
    assert turn["provider"] == "builtin" and turn["notice"] == "Ollama ne répond pas"
    assert [t["name"] for t in turn["tools"]] == ["get_budget"] and "Budget" in turn["answer"]
    events, turn = _run(engine.new_chat(), "budget ?", FakeModel([ProviderError("down")]), lang="fr", fallback=False)
    assert turn["error"] == "down" and any(e.kind == "error" for e in events)


def test_fallback_after_tools_uses_their_results():
    fake = FakeModel([Turn(tool_calls=[ToolCall("c1", "get_forecast", {})]), ProviderError("crash", "crash")])
    _, turn = _run(engine.new_chat(), "prévision ?", fake, lang="en")
    assert [t["name"] for t in turn["tools"]] == ["get_forecast"]  # not called twice
    assert turn["provider"] == "builtin" and "forecast" in turn["answer"].lower()


def test_empty_answer_is_an_error():
    _, turn = _run(engine.new_chat(), "?", FakeModel([Turn(text="")]), lang="en")
    assert turn["error"] and not turn["answer"]


def test_history_window_trims_old_tool_results():
    msgs = [{"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "analyse", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "a", "name": "analyse", "content": "x" * 5000},
            {"role": "assistant", "content": "answer 1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "b", "name": "get_budget", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "b", "name": "get_budget", "content": "y" * 3000}]
    win = engine.history_window(msgs, budget=50_000)
    assert len(win) == len(msgs) and win[2]["content"] == engine._STUB and win[6]["content"] == "y" * 3000
    assert msgs[2]["content"] == "x" * 5000  # original untouched
    tight = engine.history_window(msgs, budget=3100)
    assert tight[0]["content"] == "q2"  # oldest question dropped, current kept whole


def test_tool_content_is_capped():
    big = {"ok": True, "result": {"rows": list(range(20000)), "total": 5}}
    text = engine.tool_content(big, limit=2000)
    data = json.loads(text)
    assert data["truncated"] and data["result"] == {"total": 5} and len(text) < 2000


def test_chat_roundtrip_and_store(tmp_path):
    chat = engine.new_chat("fr")
    _run(chat, "Quels sont les facteurs d'émission ?", BuiltinProvider("fr"), lang="fr")
    path = store.save(chat, root=tmp_path)
    assert path.parent == tmp_path / "data" / "assistant" / "chats"
    loaded = store.load(chat.id, root=tmp_path)
    assert loaded.to_dict() == chat.to_dict()
    assert store.recent(root=tmp_path)[0]["questions"] == 1
    assert store.delete(chat.id, root=tmp_path) and store.load(chat.id, root=tmp_path) is None
    assert store.load("../../etc/passwd", root=tmp_path) is None


# ---------------------------------------------------------------------------
# Built-in assistant
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("question", "tool"), [
    ("Fais l'analyse des 6 derniers mois", "analyse"), ("Comment réduire le scope 2 ?", "analyse"),
    ("Pourquoi les émissions ont-elles augmenté ?", "analyse"), ("Où en est le budget 2026 ?", "get_budget"),
    ("Que prévoit le modèle pour les 6 prochains mois ?", "get_forecast"),
    ("Quels fichiers sont en quarantaine ?", "get_data_quality"),
    ("Quel modèle de prévision est le plus fiable ?", "get_model_performance"),
    ("Quels sont les facteurs d'émission ?", "get_methodology"),
    ("Comment ajouter 3 mois de données ?", "explain_dashboard"), ("Why is a month waiting?", "explain_dashboard"),
    ("How do I change the annual cap?", "explain_dashboard"), ("how much did we emit last year", "get_overview"),
    ("show me the latest pipeline runs", "get_pipeline_activity"), ("bonjour", ""),
    ("How much did we emit in 2025 compared with 2024?", "query_emissions"),
    ("Combien avons-nous émis en 2025 ?", "query_emissions"), ("budget 2025", "get_budget"),
])
def test_builtin_routing(question, tool):
    assert route(question)[0] == tool


def test_builtin_months_and_year():
    assert route("analyse des 6 derniers mois")[1] == {"months": 6}
    assert route("analyse the last 12 months")[1] == {"months": 12}
    assert route("budget 2024")[1] == {"year": 2024}
    assert route("emissions 2025 vs 2023")[1] == {"start": "2023-01-01", "end": "2025-12-31", "granularity": "year"}
    assert route("total 2025")[1]["start"] == "2024-01-01"  # one year: compared with the year before


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_builtin_answers_every_intent(lang):
    questions = ["analyse 3 mois", "budget", "budget 2025", "prévision", "quarantaine", "modèle fiable", "facteurs",
                 "historique du pipeline", "résumé", "comment déposer un fichier", "bonjour", "xyzzy",
                 "émissions 2025 vs 2024", "total 2026"]
    for q in questions:
        _, turn = _run(engine.new_chat(lang), q, BuiltinProvider(lang), lang=lang)
        assert turn["answer"] and not turn["error"], q
        assert "nan" not in turn["answer"].lower().split() and "None" not in turn["answer"], q


# ---------------------------------------------------------------------------
# Ollama adapter (no server needed)
# ---------------------------------------------------------------------------
def test_ollama_message_format():
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "name": "get_budget", "arguments": {"year": 2026}}]},
            {"role": "tool", "tool_call_id": "x", "name": "get_budget", "content": "{}"},
            {"role": "assistant", "content": "done"}]
    out = olp._to_ollama(msgs)
    assert out[1] == {"role": "assistant", "content": "",
                      "tool_calls": [{"function": {"name": "get_budget", "arguments": {"year": 2026}}}]}
    assert out[2] == {"role": "tool", "content": "{}", "tool_name": "get_budget"}
    assert out[3] == {"role": "assistant", "content": "done"}


def test_ollama_status_when_unreachable(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9")  # nothing listens there
    s = olp.status(timeout=0.5)
    assert s["running"] is False and s["models"] == [] and s["error"]
    assert olp.pick_default(["llama3:8b", "qwen3.5:4b"]) == "qwen3.5:4b"
    assert olp.pick_default([]) is None and olp.pick_default(["qwen3:1.7b"]) is None


def test_ollama_unreachable_raises_provider_error(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9")
    with pytest.raises(ProviderError) as err:
        list(olp.OllamaProvider("qwen3.5:9b", timeout=1).stream("s", [{"role": "user", "content": "q"}], []))
    assert "Ollama" in err.value.text("en")


def test_system_prompt_languages():
    ctx = load_context()
    fr, en = prompts.system_prompt(ctx, "fr"), prompts.system_prompt(ctx, "en", mode="expert", page="PageXYZ")
    assert "Always answer in French" in fr and "expert brief" not in fr
    assert "Always answer in English" in en and en.startswith("# CarbonSync expert brief")
    assert str(ctx.last.date()) in fr and fr.index("RULES") < fr.index("CURRENT CONTEXT")
    # expert: the brief is its own (cacheable) part, without the maintainer comments; the page is left out
    # so the system prompt stays the same for the whole conversation
    assert len(en.parts) == 2 and en.parts[0] == prompts.load_brief()["text"] and "<!--" not in en
    assert "Emission factors" in en.parts[1] and "Data mode" in en.parts[1] and "PageXYZ" not in en
    assert fr == "\n".join(fr.parts) and len(fr.parts) == 2


# ---------------------------------------------------------------------------
# The Streamlit page (built-in engine, so no model is needed)
# ---------------------------------------------------------------------------
def _page():
    from app.pages import assistant

    assistant.render()


def test_assistant_page_answers(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    at = AppTest.from_function(_page, default_timeout=60)
    at.session_state["as_engine"] = "builtin"
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    assert len(at.button) >= 6  # suggestion buttons on an empty chat
    at.chat_input[0].set_value("Où en est le budget 2026 ?").run()
    assert not at.exception
    chat = at.session_state["as_chat"]
    assert len(chat.turns) == 1 and chat.turns[0]["tools"][0]["name"] == "get_budget"
    assert any("Budget 2026" in m.value for m in at.markdown)
    assert list((tmp_path / "data" / "assistant" / "chats").glob("*.json"))
    at.run()  # history re-renders from the saved turn
    assert not at.exception and any("Budget 2026" in m.value for m in at.markdown)


# ---------------------------------------------------------------------------
# Prefetch (the obvious tool runs before the first model call) and the figure check
# ---------------------------------------------------------------------------
class PrefetchModel(FakeModel):
    prefetch = True


def test_prefetch_gives_the_model_the_data_first():
    fake = PrefetchModel([Turn(text="Le budget 2026 est dépassé.")])
    chat = engine.new_chat("fr")
    events, turn = _run(chat, "Où en est le budget carbone de 2026 ?", fake, lang="fr")
    kinds = [e.kind for e in events]
    assert kinds.index("tool_end") < kinds.index("model")          # data first, then the model
    assert [(t["name"], t["args"], t["round"]) for t in turn["tools"]] == [("get_budget", {"year": 2026}, 0)]
    assert turn["rounds"] == 1                                    # one model call instead of two
    sent = fake.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool"]
    assert sent[1]["tool_calls"][0]["id"].startswith("pre_") and json.loads(sent[2]["content"])["ok"]


def test_no_prefetch_for_vague_questions_or_cloud_models():
    from carbonsync.assistant.providers.builtin import route_confident

    assert route_confident("bonjour") is None
    assert route_confident("Que penses-tu de tout ça ?") is None
    # a named lever is not vague any more: the built-in router knows the simulator (session 09)
    assert route_confident("Et si on passait à 15 % de solaire ?") == ("run_scenario", {"solar_pct": 15.0})
    assert route_confident("Comment ajouter 3 mois de données ?")[0] == "explain_dashboard"
    assert route_confident("faut-il baisser l'électricité ?") == ("analyse", {"months": 3})
    fake = FakeModel([Turn(text="ok")])                            # prefetch off by default
    _, turn = _run(engine.new_chat(), "Où en est le budget ?", fake, lang="fr")
    assert turn["tools"] == []


def test_grounding_parses_french_and_english_numbers():
    from carbonsync.assistant import grounding

    vals = {tok: v for tok, v, _, _ in grounding.numbers("1 088 kWh, 222,3 t, 1,055,716.9 kWh, −9,3 %, 0.64, Q1, CO2")}
    assert vals == {"1 088": [1088.0], "222,3": [222.3], "1,055,716.9": [1055716.9], "−9,3": [9.3], "0.64": [0.64]}
    pool = grounding.source_values({"cut": -1088.4, "gap": 222.34, "pct": -9.31, "share": 0.64, "until": "2026-03-31"})
    answer = ("1. Couper 1 088 kWh/jour (−9,3 %), écart 222,3 t, part 64 %, au 31 mars 2026, sur 3 mois.\n"
              "2. Soit 120 kWh/jour et +14,5 %.")
    assert grounding.unverified(answer, pool) == ["120", "+14,5"]


def test_answer_figures_are_checked_against_tool_results():
    fake = FakeModel([Turn(tool_calls=[ToolCall("c1", "get_budget", {})]), Turn(text="x")])
    chat = engine.new_chat("fr")
    _, first = _run(chat, "budget ?", fake, lang="fr")
    cap = first["tools"][0]["result"]["cap_t"]
    fake.script = [Turn(text=f"Le plafond est de {cap:.0f} t ; il faut couper 987,65 kWh/jour.")]
    _, turn = _run(chat, "et donc ?", fake, lang="fr")
    assert turn["unverified"] == ["987,65"]                         # earlier tool results still count
    _, built = _run(engine.new_chat(), "Où en est le budget ?", BuiltinProvider("fr"), lang="fr")
    assert built["unverified"] == []                                # templated answers are not checked
