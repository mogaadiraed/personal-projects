"""Assistant – chat with the dashboard's data: local model (Ollama, GPU), Expert mode (cloud
models with the user's API keys: Ollama Cloud's DeepSeek, Kimi…, OpenRouter's free models, or
Claude) or the built-in assistant.

Every figure comes from read-only tools; the model only explains. Conversations are saved
locally in data/assistant/chats/ (git-ignored). Nothing leaves the PC in local mode; Expert mode
sends aggregated data only, and switching between local and Expert starts a new conversation.
Engine keys: ``ollama:<tag>``, ``expert:anthropic``, ``expert:ollama_cloud:<model>``,
``expert:openrouter:<model>``, ``builtin``.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from app.core.i18n import lang, tr
from app.data.loader import load_config, load_emissions_daily
from app.ui import chat as chat_ui
from app.ui import components as ui
from app.ui.format import date_label
from carbonsync.assistant import engine, evaluation, export, store, usage, vault
from carbonsync.assistant.providers import anthropic_provider as anp
from carbonsync.assistant.providers import ollama_cloud as oc
from carbonsync.assistant.providers import ollama_provider as olp
from carbonsync.assistant.providers import openrouter as orr
from carbonsync.assistant.providers import registry
from carbonsync.assistant.providers.builtin import BuiltinProvider
from carbonsync.obs import get_logger

log = get_logger("carbonsync.app.assistant")

BUILTIN = "builtin"
EXPERT = "expert:anthropic"
CLOUD = "expert:ollama_cloud:"
ROUTER = "expert:openrouter:"
SUGGESTIONS = [
    ("Fais l'analyse des 3 derniers mois et propose un plan d'action",
     "Analyse the last 3 months and propose an action plan", "query_stats"),
    ("Où en est le budget carbone de cette année ?", "Where does this year's carbon budget stand?", "track_changes"),
    ("Pourquoi les émissions ont-elles augmenté ?", "Why did emissions go up?", "trending_up"),
    ("Que prévoit le modèle pour les 6 prochains mois ?", "What does the forecast say for the next 6 months?", "insights"),
    ("Quels fichiers sont en quarantaine et comment les corriger ?", "Which files are quarantined and how do I fix them?",
     "fact_check"),
    ("Comment ajouter 3 mois de données ?", "How do I add 3 months of data?", "upload_file"),
]


@st.cache_data(ttl=15, show_spinner=False)
def _ollama_status() -> dict:
    return olp.status(timeout=1.5)


def _chat() -> engine.Chat:
    if "as_chat" not in st.session_state:
        st.session_state["as_chat"] = engine.new_chat(lang())
    return st.session_state["as_chat"]


def _cloud_ready() -> dict[str, bool]:
    """Which cloud providers have a key (vault or environment variable)."""
    return {p: vault.get_key(p)[0] is not None for p in ("ollama_cloud", "openrouter", "anthropic")}


def _engine_options(status: dict, ready: dict[str, bool]) -> dict[str, str]:
    """key -> label. Installed recommended local models first, then the cloud models that have a key
    (Ollama Cloud's and OpenRouter's top 5 with the model chosen on each page first, Claude), other
    local models, built-in."""
    opts: dict[str, str] = {}
    models = status.get("models", [])
    settings = usage.load_settings()
    for tag, label, _size, role in olp.RECOMMENDED:
        if tag in models:
            opts[f"ollama:{tag}"] = f"{label} · {tr(role[0], role[1])}"
    if ready.get("ollama_cloud"):
        chosen = settings["ollama_cloud"]["model"]
        for m in [chosen, *[t for t in oc.TOP5_MODELS if t != chosen]]:
            opts[CLOUD + m] = f"Expert · {oc.model_label(m)} · Ollama Cloud"
    if ready.get("openrouter"):
        chosen = settings["openrouter"]["model"]
        free = tr("gratuit", "free")
        for m in [chosen, *[t for t in orr.TOP5_MODELS if t != chosen]]:
            opts[ROUTER + m] = f"Expert · {orr.model_label(m)} · OpenRouter" + (f" ({free})" if orr.is_free(m) else "")
    if ready.get("anthropic"):
        opts[EXPERT] = f"Expert · {anp.model_label(settings['anthropic']['model'])} · Anthropic"
    for tag in models:
        if f"ollama:{tag}" not in opts and "embed" not in tag and not tag.endswith("cloud"):
            opts[f"ollama:{tag}"] = tag
    opts[BUILTIN] = tr("Assistant intégré (sans modèle)", "Built-in assistant (no model)")
    return opts


def _provider(key: str):
    s = usage.load_settings()
    if key.startswith("ollama:"):
        return olp.OllamaProvider(key.split(":", 1)[1], num_ctx=s["ollama"]["num_ctx"])
    if key == EXPERT:
        return anp.AnthropicProvider(s["anthropic"]["model"], s["anthropic"]["effort"], settings=s)
    if key.startswith(CLOUD):
        return oc.OllamaCloudProvider(key[len(CLOUD):], settings=s)
    if key.startswith(ROUTER):
        return orr.OpenRouterProvider(key[len(ROUTER):], settings=s)
    return BuiltinProvider(lang())


def _privacy(key: str) -> str:
    return "aggregated" if key.startswith("expert:") else "full"


def _money(v: float) -> str:
    return f"{v:.2f} $".replace(".", ",") if lang() == "fr" else f"${v:.2f}"


def _expert_caption(provider) -> None:
    s = provider.settings or usage.load_settings()
    allowed, spent, cap = usage.check_cap(s)
    budget = (tr(f"aujourd'hui {_money(spent)} / {_money(cap)}", f"today {_money(spent)} / {_money(cap)}") if cap > 0
              else tr(f"aujourd'hui {_money(spent)}", f"today {_money(spent)}"))
    company = registry.PROVIDERS[provider.key]["company"]
    where_fr, where_en = registry.WHERE[provider.key]
    name = registry.model_label(provider.key, provider.model)
    if provider.key == "anthropic":
        effort = {"low": tr("faible", "low"), "medium": tr("moyen", "medium"),
                  "high": tr("élevé", "high")}[provider.effort]
        name_fr, name_en = f"{name} · effort {effort}", f"{name} · {effort} effort"
    else:
        via = registry.label(provider.key)
        name_fr, name_en = f"{name} via {via}", f"{name} via {via}"
    st.caption(":material/cloud: " + tr(
        f"Mode Expert · {name_fr} · {budget}. Seules des données agrégées (mensuelles) sont envoyées à {company}. "
        f"{where_fr}.",
        f"Expert mode · {name_en} · {budget}. Only aggregated (monthly) data is sent to {company}. {where_en}."))
    if not allowed:
        st.warning(tr(f"Plafond de dépense du jour atteint : l'assistant intégré répond à la place de {name}.",
                      f"Today's spending cap is reached: the built-in assistant answers instead of {name}."),
                   icon=":material/pause_circle:")


def _setup_help(status: dict) -> None:
    """Explains what is missing for the local model, with the exact commands."""
    if status.get("running") and status.get("models"):
        return
    if not status.get("installed"):
        msg = tr("Aucun modèle local : l'assistant intégré répond (questions guidées, chiffres exacts). Pour la "
                 "conversation libre, installez Ollama : `winget install Ollama.Ollama`, puis `ollama pull qwen3.5:9b`.",
                 "No local model: the built-in assistant answers (guided questions, exact figures). For free "
                 "conversation, install Ollama: `winget install Ollama.Ollama`, then `ollama pull qwen3.5:9b`.")
    elif not status.get("running"):
        msg = tr("Ollama est installé mais ne tourne pas : lancez l'application **Ollama** (menu Démarrer). "
                 "En attendant, l'assistant intégré répond.",
                 "Ollama is installed but not running: start the **Ollama** app (Start menu). "
                 "Meanwhile the built-in assistant answers.")
    else:
        msg = tr("Ollama tourne mais aucun modèle n'est téléchargé : `ollama pull qwen3.5:9b` (≈6,6 Go) ou "
                 "`ollama pull qwen3.5:4b` (≈3,3 Go, plus rapide).",
                 "Ollama is running but no model is downloaded: `ollama pull qwen3.5:9b` (≈6.6 GB) or "
                 "`ollama pull qwen3.5:4b` (≈3.3 GB, faster).")
    st.info(msg, icon=":material/memory:")


def _history_popover(chat: engine.Chat) -> None:
    with st.popover(tr("Historique", "History"), icon=":material/history:", width="stretch"):
        items = store.recent(12)
        if not items:
            st.caption(tr("Aucune conversation enregistrée.", "No saved conversation yet."))
        for it in items:
            when = datetime.fromtimestamp(it["updated"]).strftime("%d/%m %H:%M")
            label = f"{it['title'][:48]} · {it['questions']} · {when}"
            if st.button(label, key=f"hist_{it['id']}", width="stretch", disabled=it["id"] == chat.id,
                         type="tertiary"):
                loaded = store.load(it["id"])
                if loaded:
                    st.session_state["as_chat"] = loaded
                    log.info("Assistant: reopened conversation %s", it["id"])
                    st.rerun()
        if chat.turns:
            st.divider()
            if st.button(tr("Supprimer cette conversation", "Delete this conversation"), icon=":material/delete:",
                         key="hist_delete", width="stretch"):
                store.delete(chat.id)
                st.session_state["as_chat"] = engine.new_chat(lang())
                st.rerun()


def _analyse_popover() -> None:
    with st.popover(tr("Analyse", "Analysis"), icon=":material/query_stats:", width="stretch"):
        st.caption(tr("Diagnostic chiffré : points chauds, causes, projection de l'année, réductions nécessaires "
                      "et leviers, puis un plan d'action.",
                      "Quantified diagnosis: hotspots, causes, full-year projection, reductions needed and levers, "
                      "then an action plan."))
        months = st.segmented_control(tr("Période étudiée", "Period studied"), options=[1, 3, 6, 12], default=3,
                                      format_func=lambda m: tr(f"{m} mois", f"{m} mo"), key="as_an_months")
        if st.button(tr("Lancer l'analyse", "Run the analysis"), type="primary", icon=":material/play_arrow:",
                     width="stretch", key="as_an_run"):
            m = months or 3
            st.session_state["as_pending"] = tr(f"Fais l'analyse des {m} derniers mois et propose un plan d'action.",
                                                f"Analyse the last {m} months and propose an action plan.")
            st.rerun()


def _clear_stopped() -> None:
    s = usage.load_settings()
    if s["ollama"].get("stopped"):
        s["ollama"]["stopped"] = False
        usage.save_settings(s)


def _stopped_notice(provider) -> None:
    """Local models were stopped by hand (IA & API → Ollama) to free the GPU: no automatic load."""
    c1, c2 = st.columns([4, 1], vertical_alignment="center")
    with c1:
        st.caption(tr(f"Modèles locaux arrêtés (GPU libre) : la première question charge {provider.model} (environ "
                      "10 s), ou démarrez-le maintenant.",
                      f"Local models stopped (GPU free): the first question loads {provider.model} (about 10 s), or "
                      "start it now."))
    with c2:
        if st.button(tr("Démarrer", "Start"), icon=":material/play_circle:", key="as_start_local", width="stretch"):
            with st.spinner(tr(f"Chargement de {provider.model}…", f"Loading {provider.model}…")):
                res = olp.start(provider.model, provider.num_ctx)
            if res["ok"]:
                _clear_stopped()
                _ollama_status.clear()
            else:
                st.session_state["as_toast"] = res["fr"] if lang() == "fr" else res["en"]
            st.rerun()


# ---------------------------------------------------------------------------
# Shared with the floating assistant (app/ui/assistant_dock.py): same conversation, same engine
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def _eval_pick(models: tuple[str, ...]) -> str | None:
    return evaluation.recommended(installed=list(models))


def default_local(status: dict) -> str | None:
    """The local model chosen on the Ollama page, else the evaluation's pick, else the recommended order."""
    models = status.get("models", [])
    preferred = usage.load_settings()["ollama"].get("model") or _eval_pick(tuple(models))
    return olp.pick_default(models, preferred)


def engine_state() -> tuple[dict, dict[str, bool], dict[str, str], str]:
    """(Ollama status, cloud keys ready, engine options, current engine key)."""
    status = _ollama_status()
    ready = _cloud_ready()
    options = _engine_options(status, ready)
    current = st.session_state.get("as_engine")
    if current not in options:
        default = default_local(status)
        current = f"ollama:{default}" if default and f"ollama:{default}" in options else BUILTIN
        st.session_state["as_engine"] = current
    return status, ready, options, current


def switch_engine(choice: str) -> None:
    """New engine for the shared conversation. Local ↔ cloud starts a new conversation."""
    current = st.session_state.get("as_engine", BUILTIN)
    if choice == current:
        return
    st.session_state["as_engine"] = choice
    log.info("Assistant: engine → %s", choice)
    chat = _chat()
    if chat.turns and _privacy(choice) != _privacy(current):
        # local results (daily rows) must never reach the cloud, and the reverse is not useful
        st.session_state["as_chat"] = engine.new_chat(lang())
        st.session_state["as_toast"] = tr(
            "Nouvelle conversation : le mode Expert et le mode local ne partagent pas leurs données.",
            "New conversation: Expert and local modes do not share their data.")


def _on_engine(widget_key: str) -> None:
    switch_engine(st.session_state[widget_key])


def engine_select(options: dict[str, str], current: str, key: str, label: str | None = None, **kw) -> None:
    """Engine picker bound to the shared engine (both widgets follow a change made in the other one)."""
    keys = list(options)
    if st.session_state.get(key) != current:
        st.session_state[key] = current
    st.selectbox(label or tr("Moteur", "Engine"), keys, format_func=options.get, key=key, on_change=_on_engine,
                 args=(key,), **kw)


def warm_or_stopped(provider, status: dict) -> None:
    """Local model not in memory: load it in the background, unless the GPU was freed by hand."""
    if provider.model in status.get("loaded", []):
        return
    if usage.load_settings()["ollama"].get("stopped"):
        _stopped_notice(provider)
        return
    if olp.warm_up(provider.model, provider.num_ctx):
        _ollama_status.clear()
    st.caption(tr(f"Chargement de {provider.model} en mémoire (environ 10 s au premier lancement)…",
                  f"Loading {provider.model} into memory (about 10 s the first time)…"))


def ask_and_stream(chat: engine.Chat, question: str, current: str, provider, *, think: bool = False,
                   page: str | None = None) -> None:
    """Shows the question, streams the answer and saves the conversation."""
    if current.startswith("ollama:"):
        _clear_stopped()  # a local question loads the model: the GPU is in use again
    with st.chat_message("user", avatar=chat_ui.USER_AVATAR):
        st.markdown(question)
    events = engine.ask(chat, question, provider, lang=lang(), think=bool(think) and provider.supports_thinking,
                        mode="expert" if _privacy(current) == "aggregated" else "assistant", page=page)
    chat_ui.stream_turn(events, key=f"t{chat.id}_{len(chat.turns)}")
    try:
        store.save(chat)
    except OSError as exc:
        log.warning("Assistant: could not save the conversation (%s)", exc)


def export_menu(chat: engine.Chat, key: str, **kw) -> None:
    """Download the conversation, or only the last answer (the action plan), as Markdown or PDF."""
    with st.popover(tr("Exporter", "Export"), icon=":material/download:", disabled=not chat.turns, **kw):
        scope = st.radio(tr("Contenu", "Content"), ["all", "last"], key=f"{key}_scope", horizontal=True,
                         format_func=lambda s: tr("Toute la conversation", "Whole conversation") if s == "all"
                         else tr("Dernière réponse (plan d'action)", "Last answer (action plan)"))
        last, lg = scope == "last", lang()
        st.download_button("Markdown (.md)", data=lambda: export.to_markdown(chat, lg, last),
                           file_name=export.file_name(chat, last, "md"), mime="text/markdown", key=f"{key}_md",
                           icon=":material/description:", width="stretch", on_click="ignore")
        st.download_button("PDF", data=lambda: export.to_pdf(chat, lg, last),
                           file_name=export.file_name(chat, last, "pdf"), mime="application/pdf", key=f"{key}_pdf",
                           icon=":material/picture_as_pdf:", width="stretch", on_click="ignore")
        st.caption(tr("Avec les sources de chaque réponse (outils lus, date des données, moteur, chiffres à vérifier).",
                      "With each answer's sources (tools read, data date, engine, figures to check)."))


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    status, ready, options, current = engine_state()

    chips = ui.data_chips(date_label(df["date"].max()) if not df.empty else "—", demo)
    if _privacy(current) == "aggregated":
        chips.insert(1, (tr("Cloud · données agrégées uniquement", "Cloud · aggregated data only"), "warn"))
    else:
        chips.insert(1, (tr("Local · rien ne quitte ce PC", "Local · nothing leaves this PC"), "accent"))
    ui.page_header(
        "Assistant",
        tr("Assistant CarbonSync", "CarbonSync assistant"),
        tr("Posez vos questions sur les émissions, la prévision, le budget, les données ou le fonctionnement du tableau "
           "de bord. Les chiffres viennent des données ; l'IA explique.",
           "Ask about emissions, the forecast, the budget, the data or how the dashboard works. Figures come from "
           "the data; the AI explains."),
        chips,
    )

    chat = _chat()
    c1, c2, c3, c4, c5, c6 = st.columns([3.0, 1.2, 1.1, 1.1, 1.1, 1.1], vertical_alignment="bottom")
    with c1:
        engine_select(
            options, current, "as_engine_select",
            help=None if any(ready.values()) else tr(
                "Mode Expert (modèles cloud : DeepSeek, Kimi, modèles gratuits d'OpenRouter, Claude…) : ajoutez une clé "
                "API dans IA & API → Ollama, OpenRouter ou Claude.", "Expert mode (cloud models: DeepSeek, Kimi, "
                "OpenRouter's free models, Claude…): add an API key in AI & API → Ollama, OpenRouter or Claude."))
    provider = _provider(current)
    with c2:
        think = st.toggle(tr("Réflexion", "Reasoning"), value=st.session_state.get("as_think", False), key="as_think",
                          disabled=not provider.supports_thinking,
                          help=(tr("Claude réfléchit toujours ; activé, le résumé de sa réflexion s'affiche.",
                                   "Claude always reasons; when on, a summary of its reasoning is shown.")
                                if current == EXPERT else
                                tr("Le modèle réfléchit avant de répondre : plus juste, plus lent ; les jetons de "
                                   "réflexion sont facturés.", "The model thinks before answering: more accurate, "
                                   "slower; reasoning tokens are billed.")
                                if current.startswith((CLOUD, ROUTER)) else
                                tr("Le modèle réfléchit avant de répondre : plus juste, plus lent.",
                                   "The model thinks before answering: more accurate, slower.")))
    with c3:
        _analyse_popover()
    with c4:
        _history_popover(chat)
    with c5:
        export_menu(chat, "as_export", width="stretch")
    with c6:
        if st.button(tr("Nouvelle", "New chat"), icon=":material/add_comment:", width="stretch", key="as_new",
                     disabled=not chat.turns):
            st.session_state["as_chat"] = engine.new_chat(lang())
            st.rerun()

    if toast := st.session_state.pop("as_toast", None):
        st.toast(toast, icon=":material/swap_horiz:")
    if _privacy(current) == "aggregated":
        _expert_caption(provider)
    elif current == BUILTIN:
        _setup_help(status)
    else:
        warm_or_stopped(provider, status)

    for i, turn in enumerate(chat.turns):
        chat_ui.render_turn(turn, key=f"t{chat.id}_{i}")

    pending = st.session_state.pop("as_pending", None)
    typed = st.chat_input(tr("Votre question… (ex. : comment rester sous le plafond cette année ?)",
                             "Your question… (e.g. how do we stay under the cap this year?)"), key="as_input")
    question = (typed or pending or "").strip()
    if not chat.turns and not question:
        chat_ui.welcome(SUGGESTIONS, "as_pending")
    if not question:
        return

    ask_and_stream(chat, question, current, provider, think=bool(think))
