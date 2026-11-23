"""AI & API → OpenRouter: the OpenRouter API key, the top 5 free models (NVIDIA Nemotron, Google
Gemma, inclusionAI Ling), any other free model with tools, the free usage limits, fallback and
data-collection options, a quick test and the usage table.
"""
from __future__ import annotations

import streamlit as st

from app.core.i18n import lang, tr
from app.ui import ai_common as ai
from app.ui import components as ui
from app.ui.format import num
from carbonsync.assistant import usage, vault
from carbonsync.assistant.providers import openrouter as orr
from carbonsync.obs import get_logger

log = get_logger("carbonsync.app.ai_openrouter")

TAGS = {"tools": ("outils", "tools"), "thinking": ("réflexion", "reasoning"), "vision": ("vision", "vision")}


def _choose(settings: dict, model: str) -> None:
    settings[orr.PROVIDER]["model"] = model
    usage.save_settings(settings)
    log.info("OpenRouter: model → %s", model)
    st.toast(tr(f"{orr.model_label(model)} choisi pour l'assistant", f"{orr.model_label(model)} chosen for the assistant"),
             icon=":material/check:")


def _key_card(status: dict) -> None:
    with ui.card("OpenRouter", tr("Clé API et test de connexion", "API key and connection test"), stretch=True):
        ai.key_chips(status)
        ai.key_form(orr.PROVIDER, status, "Créez une clé sur openrouter.ai → Settings → Keys (openrouter.ai/settings/keys).",
                    "Create a key on openrouter.ai → Settings → Keys (openrouter.ai/settings/keys).")
        ai.key_actions(orr.PROVIDER, status, orr.check_connection)
        st.caption(tr("Le test lit les informations de la clé (aucune requête de modèle) et le quota gratuit du jour. "
                      "OpenRouter recommande de fixer une limite de crédit sur chaque clé.",
                      "The test reads the key's details (no model request) and today's free quota. OpenRouter "
                      "recommends setting a credit limit on every key."))


def _limits_card(settings: dict) -> None:
    lim = orr.FREE_LIMITS
    with ui.card(tr("Limites gratuites et dépenses", "Free limits and spending"), stretch=True,
                 subtitle=tr("Les modèles gratuits ne coûtent rien, mais leur nombre de requêtes est limité.",
                    "Free models cost nothing, but their number of requests is limited.")):
        st.markdown(tr(
            f"- **{lim['per_minute']} requêtes par minute** sur les modèles gratuits ;\n"
            f"- **{lim['per_day']} requêtes par jour**, ou **{num(lim['per_day_with_credits'], 0)}** une fois "
            f"{lim['credits_threshold']} crédits achetés (une seule fois) ;\n"
            "- une question prend 2 à 4 requêtes (lecture des données, puis réponse) : comptez une quinzaine de "
            "questions par jour sans crédit.",
            f"- **{lim['per_minute']} requests per minute** on free models;\n"
            f"- **{lim['per_day']} requests per day**, or **{num(lim['per_day_with_credits'], 0)}** once "
            f"{lim['credits_threshold']} credits have been bought (once);\n"
            "- a question takes 2 to 4 requests (reading the data, then the answer): count on about fifteen "
            "questions a day without credit."))
        ai.cap_line(settings)
        ai.usage_table(orr.PROVIDER, empty_fr="Aucun appel à OpenRouter pour l'instant.",
                       empty_en="No OpenRouter call yet.")


def _options_card(settings: dict) -> None:
    opts = settings[orr.PROVIDER]
    with ui.card(tr("Repli et confidentialité", "Fallback and privacy"), stretch=True,
                 subtitle=tr("Options envoyées avec chaque requête.", "Options sent with every request.")):
        fallback = st.toggle(
            tr("Repli automatique sur les autres modèles gratuits", "Automatic fallback to the other free models"),
            value=bool(opts.get("fallback", True)), key="or_fallback",
            help=tr("Si le modèle choisi est saturé ou indisponible, OpenRouter passe au suivant de la liste (gratuits "
                    "uniquement, donc sans coût). Le panneau Sources indique le modèle qui a répondu.",
                    "If the chosen model is busy or unavailable, OpenRouter moves to the next one in the list (free "
                    "ones only, so no cost). The Sources panel shows which model answered."))
        deny = st.toggle(
            tr("Refuser les fournisseurs qui collectent les données", "Deny providers that collect data"),
            value=bool(opts.get("deny_collection", False)), key="or_deny",
            help=tr("N'utilise que des fournisseurs qui ne conservent pas les requêtes. Plusieurs modèles gratuits "
                    "deviennent alors indisponibles.",
                    "Only uses providers that do not keep requests. Several free models then become unavailable."))
        if (fallback, deny) != (bool(opts.get("fallback", True)), bool(opts.get("deny_collection", False))):
            opts.update(fallback=bool(fallback), deny_collection=bool(deny))
            usage.save_settings(settings)
            st.toast(tr("Réglage enregistré", "Setting saved"), icon=":material/check:")
        st.caption(tr("OpenRouter ne conserve pas les requêtes par défaut ; le fournisseur d'un modèle gratuit peut "
                      "les journaliser ou s'en servir pour l'entraînement. Seules des données agrégées (mensuelles) "
                      "partent. Réglages du compte : openrouter.ai/settings/privacy.",
                      "OpenRouter does not keep requests by default; the provider of a free model may log them or "
                      "train on them. Only aggregated (monthly) data is sent. Account settings: "
                      "openrouter.ai/settings/privacy."))


def _model_card(m: dict, chosen: bool, has_key: bool, listed: bool, settings: dict) -> None:
    with ui.card(m["label"], f"{m['publisher']} · {tr('contexte', 'context')} {m['context']}", stretch=True):
        chips = [(tr(*m["role"]), "accent" if chosen else ""), (tr("gratuit", "free"), "")]
        chips += [(tr(*TAGS[t]), "") for t in m["tags"]]
        if not listed:
            chips.append((tr("absent de la dernière liste", "missing from the latest list"), "warn"))
        ui.chip_row(chips)
        st.caption(tr(*m["note"]))
        st.caption(tr(f"Servi par {m['host']} · `{m['model']}`", f"Served by {m['host']} · `{m['model']}`"))
        if chosen:
            st.button(tr("Modèle choisi", "Chosen model"), icon=":material/check_circle:", disabled=True,
                      width="stretch", key=f"or_pick_{m['model']}")
        elif st.button(tr("Choisir", "Choose"), icon=":material/radio_button_unchecked:", width="stretch",
                       key=f"or_pick_{m['model']}", disabled=not has_key,
                       help=None if has_key else tr("Enregistrez d'abord une clé OpenRouter.",
                                                    "Store an OpenRouter key first.")):
            _choose(settings, m["model"])
            st.rerun()


def _top5(settings: dict, has_key: bool, listed: set[str]) -> None:
    current = settings[orr.PROVIDER]["model"]
    st.html('<div class="cs-section">' + tr("Les 5 meilleurs modèles gratuits pour l'assistant",
                                             "The 5 best free models for the assistant") + "</div>"
            '<div class="cs-section-sub">' + tr(
                "Tous gratuits, avec appel d'outils et réflexion ; choisis le 20/11/2026 dans la liste publique "
                "d'OpenRouter. Les cinq apparaissent dans le choix du moteur de l'assistant.",
                "All free, with tool calling and reasoning; picked on 2026-11-20 from OpenRouter's public list. All "
                "five appear in the assistant's engine picker.") + "</div>")
    cols = st.columns(len(orr.TOP5), gap="small")
    for col, m in zip(cols, orr.TOP5, strict=True):
        with col:
            _model_card(m, m["model"] == current, has_key, m["model"] in listed, settings)


def _other_card(settings: dict, has_key: bool, models: list[dict], as_of: str) -> None:
    current = settings[orr.PROVIDER]["model"]
    with ui.card(tr("Autres modèles gratuits et essai", "Other free models and test"), stretch=True,
                 subtitle=tr(f"Modèles gratuits avec outils dans la liste d'OpenRouter (au {as_of}).",
                    f"Free models with tools in OpenRouter's list (as of {as_of}).")):
        others = [m["model"] for m in models if m["model"] not in orr.TOP5_MODELS]
        ctx = {m["model"]: m.get("context", 0) for m in models}

        def label(mid: str) -> str:
            c = ctx.get(mid) or 0
            size = f"{c // 1_000_000}M" if c >= 1_000_000 else f"{c // 1000}K"
            return f"{orr.model_label(mid)} · {tr('contexte', 'context')} {size}"

        c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
        with c1:
            pick = st.selectbox(tr("Autre modèle gratuit", "Other free model"), others, index=None, format_func=label,
                                placeholder=tr("Choisir un autre modèle…", "Pick another model…"), key="or_other",
                                disabled=not has_key or not others)
        with c2:
            if st.button(tr("Utiliser ce modèle", "Use this model"), width="stretch", key="or_other_use",
                         disabled=not has_key or not pick):
                _choose(settings, pick)
                st.rerun()
        st.markdown(tr(f"Modèle de l'assistant : **{orr.model_label(current)}** (`{current}`)",
                       f"Assistant model: **{orr.model_label(current)}** (`{current}`)"))
        b1, b2 = st.columns(2)
        with b1:
            if st.button(tr("Essai rapide du modèle", "Quick model test"), icon=":material/bolt:", key="or_quick",
                         disabled=not has_key, width="stretch",
                         help=tr("Envoie une question d'un mot au modèle choisi (une requête du quota gratuit).",
                                 "Sends a one-word question to the chosen model (one request of the free quota).")):
                with st.spinner(tr(f"Essai de {orr.model_label(current)}…", f"Testing {orr.model_label(current)}…")):
                    res = orr.quick_test(current)
                st.session_state["ai_flash"] = ("ok" if res["ok"] else "err", res["fr"] if lang() == "fr" else res["en"])
                st.rerun()
        with b2:
            if st.button(tr("Actualiser la liste", "Refresh the list"), icon=":material/refresh:", key="or_refresh",
                         width="stretch",
                         help=tr("Relit la liste publique des modèles d'OpenRouter (sans clé, gratuit).",
                                 "Reads OpenRouter's public model list again (no key, free).")):
                with st.spinner(tr("Lecture de la liste…", "Reading the list…")):
                    res = orr.refresh_free_models()
                st.session_state["ai_flash"] = ("ok" if res["ok"] else "err", res["fr"] if lang() == "fr" else res["en"])
                st.rerun()


def render() -> None:
    ui.page_header(
        "IA & API",
        "OpenRouter",
        tr("Des modèles gratuits de plusieurs éditeurs (NVIDIA, Google, inclusionAI…) avec une seule clé API, sous les "
           "règles Expert : données agrégées uniquement, plafond du jour, coût par réponse.",
           "Free models from several publishers (NVIDIA, Google, inclusionAI…) with a single API key, under the "
           "Expert rules: aggregated data only, daily cap, cost per answer."),
    )
    ai.flash()
    settings = usage.load_settings()
    status = vault.status(orr.PROVIDER)
    models, as_of = orr.free_models()
    listed = {m["model"] for m in models}
    left, right = st.columns(2, gap="medium")
    with left:
        _key_card(status)
    with right:
        _limits_card(settings)
    _top5(settings, status["has_key"], listed)
    left, right = st.columns(2, gap="medium")
    with left:
        _other_card(settings, status["has_key"], models, as_of)
    with right:
        _options_card(settings)
