"""Phase 4: evaluation set (scoring, runs, recommended default), export (Markdown / PDF), the page
sent with Expert questions, the floating assistant on every page and the export menu.
No real Ollama, no network, no key.
"""
from __future__ import annotations

import json
import warnings
from types import SimpleNamespace

import pytest

from carbonsync.assistant import engine, evaluation, export, store, usage
from carbonsync.assistant.providers import ollama_provider as olp
from carbonsync.assistant.providers.builtin import BuiltinProvider

# ---------------------------------------------------------------------------
# Evaluation set
# ---------------------------------------------------------------------------
CASE = evaluation.Case("t_fr", "fr", "Quel est le total ?", ("query_emissions",),
                       (evaluation.Fact("query_emissions", {}, lambda r: r["total"]),
                        evaluation.Fact("get_overview", {}, lambda r: r["pct"], tol=0.5)))


def test_the_set_has_15_topics_in_both_languages():
    cs = evaluation.cases()
    assert len(cs) == 30 and len({c.id for c in cs}) == 30
    assert sum(c.lang == "fr" for c in cs) == 15 and all(c.tools for c in cs)
    assert all(c.facts or c.words for c in cs)


def test_expected_values_are_computed_by_the_tools(monkeypatch):
    calls = []

    def fake(name, args, tctx=None):
        calls.append(name)
        return {"ok": True, "result": {"total": 3155, "pct": 2.014}}

    monkeypatch.setattr(evaluation.tools, "execute", fake)
    cache: dict = {}
    assert evaluation.expected(CASE, cache) == [3155, 2.014]
    assert evaluation.expected(CASE, cache) == [3155, 2.014] and len(calls) == 2  # one call per tool and arguments


@pytest.mark.parametrize("answer,found", [
    ("Total : 3 155 t, en hausse de 2,0 %.", [True, True]),
    ("Total: 3,155 t, up 2%.", [True, True]),
    ("Total : 3 160 t (+1,6 %).", [True, True]),        # rounding within 2 % / 0.5 point
    ("Total : 2 900 t, +4 %.", [False, False]),
])
def test_figures_match_with_rounding_and_both_formats(answer, found):
    turn = {"answer": answer, "tools": [{"name": "query_emissions"}], "unverified": [], "seconds": 3.0}
    r = evaluation.score(CASE, turn, [3155, 2.014])
    assert r["found"] == found


def test_close_wrong_figures_do_not_count_and_equivalent_statements_do():
    q1 = evaluation.Case("q1", "fr", "Compare T1", ("compare_periods",),
                         (evaluation.Fact("x", {}, lambda r: r), evaluation.Fact("y", {}, lambda r: r)))
    wrong = {"answer": "T1 2026 : 811,1 t ; T1 2025 : 811,1 t.", "tools": [{"name": "compare_periods"}]}
    assert evaluation.score(q1, wrong, [826.2, 811.1])["found"] == [False, True]   # 1.8 % apart: not the same
    solar = evaluation.Case("s", "fr", "Solaire ?", ("run_scenario",), (evaluation.Fact("run_scenario", {}, lambda r: r),))
    as_total = {"answer": "Avec 15 % de solaire, l'année finirait à 2 976 t.", "tools": [{"name": "run_scenario"}]}
    assert evaluation.score(solar, as_total, [(246.6, 2976)])["found"] == [True]
    assert evaluation.score(solar, as_total, [[246.6, 3100]])["found"] == [False]


def test_score_weights_tools_figures_grounding_and_language():
    good = {"answer": "Le total des émissions est de 3 155 t pour les mois étudiés, +2,0 % sur la période.",
            "tools": [{"name": "query_emissions"}], "unverified": [], "seconds": 4.0}
    assert evaluation.score(CASE, good, [3155, 2.014])["score"] == 1.0
    wrong_tool = dict(good, tools=[{"name": "get_budget"}])
    assert evaluation.score(CASE, wrong_tool, [3155, 2.014])["score"] == 0.75
    invented = dict(good, unverified=["12", "40"])
    assert evaluation.score(CASE, invented, [3155, 2.014])["score"] == pytest.approx(0.9)
    english = dict(good, answer="The total emissions are 3,155 t for the months studied, +2.0% over the period.")
    assert evaluation.score(CASE, english, [3155, 2.014])["language"] == 0.0
    assert evaluation.score(CASE, {"answer": "", "error": "boom"}, [3155, 2.014])["score"] == 0.0


def test_recommend_prefers_a_faster_model_within_the_margin():
    s = {"big": {"model": "big", "score": 90.0, "seconds_mean": 20.0, "complete": True},
         "small": {"model": "small", "score": 88.0, "seconds_mean": 7.0, "complete": True},
         "tiny": {"model": "tiny", "score": 70.0, "seconds_mean": 3.0, "complete": True},
         "half": {"model": "half", "score": 99.0, "seconds_mean": 1.0, "complete": False}}
    assert evaluation.recommend(s) == "small"
    s["small"]["score"] = 85.0
    assert evaluation.recommend(s) == "big" and evaluation.recommend({}) is None


def test_a_faster_model_must_not_lose_on_figures():
    # this PC, 2026-11-20: qwen3.5:4b is 2.3 points behind but 5 points behind on figures → qwen3.5:9b
    s = {"qwen3.5:9b": {"model": "qwen3.5:9b", "score": 97.6, "figures_pct": 98.3, "seconds_mean": 12.2, "complete": True},
         "qwen3.5:4b": {"model": "qwen3.5:4b", "score": 95.3, "figures_pct": 93.3, "seconds_mean": 5.8, "complete": True}}
    assert evaluation.recommend(s) == "qwen3.5:9b"
    s["qwen3.5:4b"]["figures_pct"] = 96.7
    assert evaluation.recommend(s) == "qwen3.5:4b"


def test_a_run_is_saved_and_read_back(tmp_path):
    subset = [c for c in evaluation.cases() if c.id in ("total_2025_fr", "quarantine_en")]
    summary = evaluation.run_model("builtin-test", lambda m: BuiltinProvider("fr"), subset, root=tmp_path)
    assert summary["complete"] and summary["cases"] == 2 and summary["tools_pct"] == 100.0
    saved = json.loads(next((tmp_path / "data" / "assistant" / "eval").glob("2*.json")).read_text(encoding="utf-8"))
    assert saved["results"][0]["expected"] and saved["results"][0]["answer"]
    assert evaluation.latest(tmp_path)["builtin-test"]["score"] == summary["score"]
    assert evaluation.recommended(tmp_path, installed=["builtin-test"]) == "builtin-test"
    assert evaluation.recommended(tmp_path, installed=["other"]) is None


def test_background_run_reports_progress_and_calls_back(tmp_path):
    subset = [c for c in evaluation.cases() if c.id == "quarantine_fr"]
    done = []
    assert evaluation.start_background(["m1", "m2"], lambda m: BuiltinProvider("fr"), root=tmp_path,
                                       after=lambda: done.append(True), case_list=subset)
    evaluation._THREAD[0].join(60)
    p = evaluation.progress(tmp_path)
    assert not p["running"] and p["done"] == p["total"] == 2 and p["error"] is None and done == [True]
    assert set(evaluation.latest(tmp_path)) == {"m1", "m2"}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _chat():
    chat = engine.new_chat("fr")
    for q in ("Fais l'analyse des 3 derniers mois et propose un plan d'action.", "Où en est le budget carbone ?"):
        list(engine.ask(chat, q, BuiltinProvider("fr"), lang="fr"))
    chat.turns[-1]["answer"] += ("\n\n| Mois | Réel (t) |\n|---|---:|\n| Janv. | 280 |\n\n- Coût **1,32 $** "
                                 + chr(0x2705) + " " + chr(0x1F680) + "\n  - sous-point `code`")
    chat.turns[-1]["cost_usd"] = 0.0123
    return chat


def test_markdown_export_keeps_answers_and_sources():
    chat = _chat()
    md = export.to_markdown(chat, "fr")
    assert md.startswith("# Conversation avec l'assistant CarbonSync")
    assert "## 1. Fais l'analyse" in md and "## 2. Où en est le budget" in md
    assert "Outils lus : " in md and "Données jusqu'au" in md and "Coût estimé : 0,0123 $" in md
    last = export.to_markdown(chat, "en", last_only=True)
    assert "## 1. Où en est le budget" in last and "Fais l'analyse" not in last
    plan = engine.new_chat("fr")
    list(engine.ask(plan, "Fais l'analyse des 3 derniers mois et propose un plan d'action.", BuiltinProvider("fr"), lang="fr"))
    assert export.title(plan, "fr", True).startswith("Plan d'action")
    assert export.file_name(plan, True, "pdf").startswith("carbonsync_assistant_plan_")


def test_pdf_export_draws_without_missing_glyphs():
    chat = _chat()
    with warnings.catch_warnings():
        warnings.simplefilter("error")   # an emoji or a "$" would warn
        pdf = export.to_pdf(chat, "fr")
        plan = export.to_pdf(chat, "en", last_only=True)
        empty = export.to_pdf(engine.new_chat("fr"), "fr")
    assert pdf.startswith(b"%PDF") and len(pdf) > len(plan) > 0 and empty.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# The page goes with Expert questions
# ---------------------------------------------------------------------------
class _Echo(BuiltinProvider):
    privacy = "aggregated"

    def __init__(self):
        super().__init__("fr")
        self.key = "echo"


def test_expert_questions_carry_the_page_and_local_ones_keep_it_in_the_prompt():
    chat = engine.new_chat("fr")
    turn = list(engine.ask(chat, "Que montre cette page ?", _Echo(), lang="fr", page="Prévisions"))[-1].turn
    assert turn["page"] == "Prévisions" and turn["mode"] == "expert"
    assert chat.messages[0]["content"].startswith("(Page ouverte dans le tableau de bord : Prévisions)\n")
    local = engine.new_chat("en")
    list(engine.ask(local, "What does this page show?", BuiltinProvider("en"), lang="en", page="Forecast"))
    assert local.messages[0]["content"] == "What does this page show?"


# ---------------------------------------------------------------------------
# Floating assistant and export menu
# ---------------------------------------------------------------------------
@pytest.fixture
def app_env(tmp_path, monkeypatch):
    down = {"installed": False, "running": False, "models": [], "loaded": [], "error": "test"}
    monkeypatch.setattr(olp, "status", lambda timeout=2.0: dict(down))
    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    for env in ("OLLAMA_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    from carbonsync.assistant import vault

    monkeypatch.setattr(vault, "_backend", lambda: SimpleNamespace(get_password=lambda s, u: None))
    from app.pages import assistant

    assistant._ollama_status.clear()
    assistant._eval_pick.clear()
    return tmp_path


def _forecast_page_with_dock():
    import streamlit as st

    from app.ui import assistant_dock

    st.markdown("Page Prévisions")
    assistant_dock.render("forecast", "Prévisions", None)


def _assistant_tab_with_dock():
    from app.ui import assistant_dock

    assistant_dock.render("assistant", "Assistant", None)


def test_floating_button_opens_a_page_aware_chat(app_env):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_forecast_page_with_dock, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception and at.button(key="cs_fab_btn") and not at.chat_input
    at.button(key="cs_fab_btn").click().run()
    assert not at.exception and at.session_state["dock_open"] and at.chat_input
    first = at.button(key="dock_sugg_0")
    assert "Explique la page « Prévisions »" in first.label
    assert "Que prévoit le modèle" in at.button(key="dock_sugg_1").label   # the Forecast page's own suggestions
    first.click().run()
    assert not at.exception
    assert not [b for b in at.button if str(b.key).startswith("dock_sugg_")]   # the answer replaces them
    chat = at.session_state["as_chat"]
    assert len(chat.turns) == 1 and chat.turns[0]["page"] == "Prévisions"
    assert [t["name"] for t in chat.turns[0]["tools"]] == ["explain_dashboard"]
    at.chat_input(key="dock_input").set_value("Où en est le budget carbone de cette année ?").run()
    assert not at.exception and len(at.session_state["as_chat"].turns) == 2
    assert store.recent(5)[0]["questions"] == 2      # saved like the Assistant tab's conversations
    at.button(key="dock_new").click().run()
    assert not at.exception and not at.session_state["as_chat"].turns
    at.button(key="cs_fab_btn").click().run()
    assert not at.exception and not at.session_state["dock_open"] and not at.chat_input


def test_no_floating_button_on_the_assistant_tab(app_env):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_assistant_tab_with_dock, default_timeout=60)
    at.run()
    assert not at.exception and not [b for b in at.button if b.key == "cs_fab_btn"]


def test_page_suggestions_follow_the_page():
    from app.ui import assistant_dock

    assert len(assistant_dock.page_suggestions("budget", "Budget carbone")) == 4
    assert any("quarantaine" in s for s in assistant_dock.page_suggestions("data", "Qualité des données"))
    assert len(assistant_dock.page_suggestions("unknown", "X")) == 2
    assert assistant_dock.page_suggestions("", "Vue d'ensemble")[1].startswith("Résume")   # default page: "/"


EXPECTED_TOOL = {"overview": {"get_overview", "analyse"}, "forecast": {"get_forecast"},
                 "model-lab": {"get_model_performance"}, "budget": {"get_budget", "analyse"}, "scenarios": {"run_scenario", "analyse"},
                 "intake": {"explain_dashboard"}, "data": {"get_data_quality"}, "methodology": {"get_methodology"},
                 "reports": {"explain_dashboard"}, "configuration": {"explain_dashboard"},
                 "alerts": {"get_alerts"}, "breakdown": {"get_breakdown"}, "inventory": {"get_inventory"}}


@pytest.mark.parametrize("lg", ["fr", "en"])
def test_every_page_suggestion_gets_the_right_tool_from_the_built_in_assistant(lg):
    from app.ui import assistant_dock

    for path, items in assistant_dock.SUGGESTIONS.items():
        for fr, en in items:
            chat = engine.new_chat(lg)
            turn = list(engine.ask(chat, fr if lg == "fr" else en, BuiltinProvider(lg), lang=lg))[-1].turn
            assert {t["name"] for t in turn["tools"]} <= EXPECTED_TOOL[path], (path, fr if lg == "fr" else en)
    # "a mix of levers" gets the analysis (it ranks the levers); a named lever gets the simulator
    assert set(EXPECTED_TOOL) == set(assistant_dock.SUGGESTIONS)


def _assistant_page():
    from app.pages import assistant

    assistant.render()


def test_assistant_tab_has_the_export_menu_and_shares_the_engine(app_env):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_assistant_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    at.chat_input[0].set_value("Combien de fichiers sont en quarantaine ?").run()
    assert not at.exception and len(at.session_state["as_chat"].turns) == 1
    keys = {e.proto.id for e in at.get("download_button")} | {getattr(e, "key", None) for e in at.get("download_button")}
    assert at.get("download_button") and any("as_export" in str(k) for k in keys)
    assert at.selectbox(key="as_engine_select").value == "builtin"


def test_default_local_model_follows_the_evaluation(monkeypatch, tmp_path):
    from app.pages import assistant

    status = {"models": ["qwen3.5:4b", "qwen3.5:9b"]}
    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(evaluation, "recommended", lambda root=None, installed=None: "qwen3.5:4b")
    assistant._eval_pick.clear()
    assert assistant.default_local(status) == "qwen3.5:4b"
    s = usage.load_settings(tmp_path)
    s["ollama"]["model"] = "qwen3.5:9b"
    usage.save_settings(s, tmp_path)
    assert assistant.default_local(status) == "qwen3.5:9b"   # a choice made by hand wins
    assistant._eval_pick.clear()


# ---------------------------------------------------------------------------
# Built-in assistant: what-if questions (solar, efficiency, production)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question,args", [
    ("Quel serait l'effet de 15 % d'électricité solaire ?", {"solar_pct": 15.0}),
    ("What would 15% solar electricity do to this year's emissions?", {"solar_pct": 15.0}),
    ("Et avec 20 % de solaire et 5 % d'efficacité ?", {"solar_pct": 20.0, "efficiency_pct": 5.0}),
    ("solar 10% and efficiency 8%", {"solar_pct": 10.0, "efficiency_pct": 8.0}),
    ("Simule 30 % de solaire, 10 % d'efficacité et une production en baisse de 10 %",
     {"solar_pct": 30.0, "efficiency_pct": 10.0, "production_pct": -10.0}),
    ("What if production rises by 15%?", {"production_pct": 15.0}),
    ("Et si on installait des panneaux solaires ?", {"solar_pct": 10.0}),   # named without a number: 10 %
    ("Solaire à 60 % ?", {"solar_pct": 40}),                                # the simulator's limit
])
def test_built_in_assistant_runs_what_if_questions(question, args):
    from carbonsync.assistant.providers import builtin

    assert builtin.route(question) == ("run_scenario", args)


@pytest.mark.parametrize("question,tool", [
    ("Quel mélange de leviers ramène l'année sous le plafond ?", "analyse"),
    ("Explique la page « Scénarios » : que montre-t-elle et comment l'utiliser ?", "explain_dashboard"),
    ("Comment améliorer l'efficacité énergétique ?", "explain_dashboard"),
    ("Les émissions ont augmenté de 2 % : pourquoi ?", "analyse"),
])
def test_what_if_route_leaves_other_questions_alone(question, tool):
    from carbonsync.assistant.providers import builtin

    assert builtin.route(question)[0] == tool


def test_what_if_answer_is_grounded_in_both_languages():
    for lg, q in (("fr", "Et avec 20 % de solaire et 5 % d'efficacité ?"), ("en", "What if production rises by 15%?")):
        chat = engine.new_chat(lg)
        turn = list(engine.ask(chat, q, BuiltinProvider(lg), lang=lg))[-1].turn
        assert [t["name"] for t in turn["tools"]] == ["run_scenario"] and not turn["unverified"]
        assert ("Scénario" if lg == "fr" else "Scenario") in turn["answer"]
