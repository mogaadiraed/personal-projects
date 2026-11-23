"""Floating assistant: a round AI button at the bottom right of every page. It opens a chat panel
over the page, so a question can be asked without leaving the page.

* Same conversation and engine as the Assistant tab (``as_chat`` / ``as_engine``): a question asked
  here continues there, and **Ouvrir dans l'onglet Assistant** jumps to it.
* Page-aware: the panel knows the page it was opened on. The suggestions match that page,
  "Expliquer cette page" asks the dashboard guide about it, and the page goes to the model with
  the question.
* Runs as a fragment: opening, closing and chatting only rerun the panel, not the page under it.
* Not shown on the Assistant tab itself.
"""
from __future__ import annotations

import streamlit as st

from app.core.i18n import lang, tr
from app.pages import assistant as asp
from app.ui import appearance as ap
from app.ui import chat as chat_ui
from carbonsync.assistant import engine

SHOWN_TURNS = 4  # older questions stay in the Assistant tab

# url_path → page-specific suggestions (fr, en). Each one is answered by the right tool even by the
# built-in assistant (checked in tests/test_assistant_phase4.py)
SUGGESTIONS: dict[str, list[tuple[str, str]]] = {
    "overview": [("Résume la situation des émissions sur les 12 derniers mois.",
                  "What is the situation over the last 12 months?"),
                 ("Pourquoi les émissions ont-elles changé récemment ?", "Why have emissions changed recently?")],
    "forecast": [("Que prévoit le modèle pour les 6 prochains mois ?", "What does the forecast say for the next 6 months?"),
                 ("Quelle est la fourchette d'incertitude de la prévision ?",
                  "What is the uncertainty range of the forecast?")],
    "model-lab": [("Quel modèle de prévision est le plus précis ?", "Which model is the most accurate in the backtest?")],
    "budget": [("Où en est le budget carbone de cette année ?", "Where does this year's carbon budget stand?"),
               ("Combien faut-il réduire pour rester sous le plafond ?", "How much must we cut to stay under the cap?")],
    "scenarios": [("Quel serait l'effet de 15 % d'électricité solaire ?", "What would 15% solar electricity do?"),
                  ("Quel mélange de leviers ramène l'année sous le plafond ?",
                   "Which mix of levers brings the year under the cap?")],
    "intake": [("Comment ajouter 3 mois de données ?", "How do I add 3 months of data?")],
    "data": [("Quels fichiers sont en quarantaine et comment les corriger ?",
              "Which files are quarantined and how do I fix them?")],
    "methodology": [("Quels facteurs d'émission sont utilisés et d'où viennent-ils ?",
                     "Which emission factors are used and where do they come from?")],
    "reports": [("Que contient le rapport PDF ?", "What is in the PDF report?")],
    "configuration": [("À quoi servent les réglages de configuration ?", "What are the configuration settings for?")],
    "alerts": [("Quelles alertes sont ouvertes et que faire ?", "Which alerts are open and what should we do?")],
    "breakdown": [("Quelle zone de l'usine émet le plus ?", "Which plant zone emits the most?"),
                  ("Quels postes pèsent le plus dans la zone P3 ?", "Which items weigh most in zone P3?")],
    "inventory": [("Compare le Scope 2 localisation et marché.", "Compare location-based and market-based Scope 2."),
                  ("Quelles catégories du Scope 3 sont estimées ou exclues ?",
                   "Which Scope 3 categories are estimated or excluded?")],
}
_AI = [("Quelle différence entre le mode local et le mode Expert ?", "What is the difference between local and Expert mode?")]
_DEFAULT = [("Fais l'analyse des 3 derniers mois et propose un plan d'action.",
             "Analyse the last 3 months and propose an action plan.")]


def page_suggestions(page_path: str, page_title: str) -> list[str]:
    explain = tr(f"Explique la page « {page_title} » : que montre-t-elle et comment l'utiliser ?",
                 f'Explain the "{page_title}" page: what does it show and how do I use it?')
    path = page_path or "overview"   # the default page is served at "/" (empty url_path)
    items = SUGGESTIONS.get(path) or (_AI if path.startswith("ai-") else [])
    return [explain, *[tr(fr, en) for fr, en in items], *[tr(fr, en) for fr, en in _DEFAULT]][:4]


def _css(side: str = "right") -> str:
    """Button and panel styles in the colours of the chosen palette (glass: frosted panel)."""
    look = ap.current()
    pal = ap.palette(look)
    accent = pal["dark"]["primary"]                       # white icon on it ≥ 3:1
    light = ap.mix("#ffffff", accent, 0.35)
    deep = ap.mix("#000000", accent, 0.25)
    glass = look["appearance"]["style"] == "glass"
    blur = "backdrop-filter: blur(18px) saturate(140%); -webkit-backdrop-filter: blur(18px) saturate(140%);" if glass else ""
    other = "left" if side == "right" else "right"
    return f"""<style>
/* Layers: above the page, under Streamlit's header (999990), dropdowns (1000060) and toasts */
.st-key-cs_fab, .st-key-cs_fab_open {{
  position: fixed; {side}: 28px; {other}: auto; bottom: 28px; z-index: 999985; width: 62px !important;
}}
.st-key-cs_fab button, .st-key-cs_fab_open button {{
  position: relative; width: 60px; height: 60px; min-height: 60px; padding: 0; border-radius: 50%;
  border: 0; color: #fff; background: radial-gradient(circle at 30% 25%, {light}, {accent} 50%, {deep});
  box-shadow: 0 10px 28px {ap.rgba(accent, 0.45)}, 0 2px 6px rgba(0,0,0,.30); transition: transform .15s ease;
}}
.st-key-cs_fab button:hover, .st-key-cs_fab button:focus,
.st-key-cs_fab_open button:hover, .st-key-cs_fab_open button:focus {{
  transform: scale(1.06); color: #fff; border: 0;
}}
.st-key-cs_fab [data-testid="stIconMaterial"], .st-key-cs_fab_open [data-testid="stIconMaterial"] {{
  font-size: 30px; color: #fff;
}}
.st-key-cs_fab button::after {{
  content: ""; position: absolute; inset: -5px; border-radius: 50%; border: 2px solid {ap.rgba(accent, 0.55)};
  animation: csPulse 2.6s ease-out infinite; pointer-events: none;
}}
.st-key-cs_fab_open button::after {{ display: none; }}
@keyframes csPulse {{ 0% {{ transform: scale(.92); opacity: .9; }} 100% {{ transform: scale(1.28); opacity: 0; }} }}
@media (prefers-reduced-motion: reduce) {{ .st-key-cs_fab button::after {{ animation: none; display: none; }} }}
.st-key-cs_dock {{
  position: fixed; {side}: 28px; {other}: auto; bottom: 102px; width: min(460px, calc(100vw - 32px));
  max-height: calc(100vh - 128px); overflow-y: auto; z-index: 999980; gap: .45rem;
  background: var(--cs-panel); {blur} border: 1px solid var(--cs-line); border-radius: 18px; padding: 10px 14px 12px;
  box-shadow: 0 24px 60px rgba(0,0,0,.40);
}}
.st-key-cs_dock_head {{ background: var(--cs-panel-strip); border-radius: 12px; padding: 6px 8px 6px 12px; }}
.cs-dock-title {{ font-weight: 650; font-size: .98rem; line-height: 1.2; }}
.cs-dock-sub {{ font-size: .74rem; opacity: .65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.st-key-cs_dock [data-testid="stChatMessage"] {{ padding: .35rem .2rem; }}
@media (max-width: 640px) {{
  .st-key-cs_fab, .st-key-cs_fab_open {{ {side}: 16px; bottom: 16px; }}
  .st-key-cs_dock {{ left: 16px; right: 16px; width: auto; bottom: 88px; max-height: calc(100vh - 110px); }}
}}
</style>"""


def _toggle() -> None:
    st.session_state["dock_open"] = not st.session_state.get("dock_open", False)


def _pick(text: str) -> None:
    st.session_state["dock_pending"] = text


def _new_chat() -> None:
    st.session_state["as_chat"] = engine.new_chat(lang())


def render(page_path: str, page_title: str, assistant_page=None, side: str = "right") -> None:
    """Call once per run, after the page: the button, and the panel when it is open."""
    if page_path == "assistant":
        return
    st.html(_css(side))
    _dock(page_path, page_title, assistant_page)


@st.fragment
def _dock(page_path: str, page_title: str, assistant_page=None) -> None:
    is_open = bool(st.session_state.get("dock_open"))
    with st.container(key="cs_fab_open" if is_open else "cs_fab"):
        st.button(" ", key="cs_fab_btn", on_click=_toggle, icon=":material/close:" if is_open else ":material/auto_awesome:",
                  help=tr("Fermer l'assistant", "Close the assistant") if is_open
                  else tr("Assistant IA : poser une question sur cette page", "AI assistant: ask about this page"))
    if is_open:
        with st.container(key="cs_dock"):
            _panel(page_path, page_title, assistant_page)


def _panel(page_path: str, page_title: str, assistant_page=None) -> None:
    chat = asp._chat()
    status, _ready, options, current = asp.engine_state()
    with st.container(key="cs_dock_head"):
        c1, c2, c3 = st.columns([5, 1, 1], vertical_alignment="center", gap="small")
        with c1:
            st.html('<div class="cs-dock-title">' + tr("Assistant CarbonSync", "CarbonSync assistant") + "</div>"
                    '<div class="cs-dock-sub">' + tr(f"Page : {page_title}", f"Page: {page_title}") + " · "
                    + options.get(current, "") + "</div>")
        with c2:
            st.button("", icon=":material/add_comment:", key="dock_new", on_click=_new_chat, disabled=not chat.turns,
                      help=tr("Nouvelle conversation", "New conversation"))
        with c3:
            if assistant_page is not None and st.button("", icon=":material/open_in_full:", key="dock_full",
                                                        help=tr("Ouvrir dans l'onglet Assistant",
                                                                "Open in the Assistant tab")):
                st.session_state["dock_open"] = False
                st.switch_page(assistant_page)
    with st.expander(tr("Moteur et export", "Engine and export"), icon=":material/tune:"):
        asp.engine_select(options, current, "dock_engine")
        asp.export_menu(chat, "dock_export", width="stretch")
    if toast := st.session_state.pop("as_toast", None):
        st.toast(toast, icon=":material/swap_horiz:")

    provider = asp._provider(current)
    if current.startswith("ollama:"):
        asp.warm_or_stopped(provider, status)
    elif current.startswith("expert:"):
        st.caption(":material/cloud: " + tr("Mode Expert : seules des données agrégées (mensuelles) partent dans le cloud.",
                                            "Expert mode: only aggregated (monthly) data goes to the cloud."))

    msgs = st.container(height=400, key="cs_dock_msgs", autoscroll=True)
    # the input sits under the messages, but is read first: a question just sent hides the suggestions
    typed = st.chat_input(tr("Votre question…", "Your question…"), key="dock_input")
    question = (typed or st.session_state.pop("dock_pending", None) or "").strip()
    with msgs:
        shown = chat.turns[-SHOWN_TURNS:]
        if len(chat.turns) > SHOWN_TURNS:
            st.caption(tr(f"{len(chat.turns) - SHOWN_TURNS} question(s) plus ancienne(s) dans l'onglet Assistant.",
                          f"{len(chat.turns) - SHOWN_TURNS} older question(s) in the Assistant tab."))
        start = len(chat.turns) - len(shown)
        for i, turn in enumerate(shown, start):
            chat_ui.render_turn(turn, key=f"d{chat.id}_{i}")
        if not chat.turns and not question:
            st.caption(tr("Posez une question sur cette page ou sur vos émissions. Les chiffres viennent des données.",
                          "Ask about this page or your emissions. Figures come from the data."))
            for k, text in enumerate(page_suggestions(page_path, page_title)):
                st.button(text, key=f"dock_sugg_{k}", on_click=_pick, args=(text,), width="stretch",
                          icon=":material/help:" if k == 0 else ":material/chat_bubble:")
        if question:
            asp.ask_and_stream(chat, question, current, provider, think=bool(st.session_state.get("as_think")),
                               page=page_title)
