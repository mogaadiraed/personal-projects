"""AI & API → Claude: the Anthropic API key (Windows Credential Manager), model, reasoning effort,
connection test and Claude's usage. The daily cap is shared by every cloud provider (overview page).
"""
from __future__ import annotations

import streamlit as st

from app.core.i18n import tr
from app.ui import ai_common as ai
from app.ui import components as ui
from carbonsync.assistant import usage, vault
from carbonsync.assistant.providers import anthropic_provider as anp
from carbonsync.obs import get_logger

log = get_logger("carbonsync.app.ai_claude")

PROVIDER = "anthropic"
EFFORT_LABELS = {"low": ("Faible · rapide", "Low · fast"), "medium": ("Moyen · recommandé", "Medium · recommended"),
                 "high": ("Élevé · analyses poussées", "High · deep analysis")}


def _model_label(m: str) -> str:
    spec = anp.MODELS[m]
    inp, out, _ = usage.PRICES[m]
    return f"{spec['label']} · {tr(*spec['role'])} · {ai.price_text(inp, out)}"


def _key_card(status: dict, settings: dict) -> None:
    with ui.card("Anthropic (Claude)", tr("Clé API, modèle et test de connexion", "API key, model and connection test"),
                 stretch=True):
        ai.key_chips(status)
        ai.key_form(PROVIDER, status, "Créez une clé dans la console Claude (platform.claude.com → API Keys).",
                    "Create a key in the Claude console (platform.claude.com → API Keys).")
        models = list(anp.MODELS)
        current = settings["anthropic"].get("model", anp.DEFAULT_MODEL)
        model = st.selectbox(tr("Modèle", "Model"), models, index=models.index(current) if current in models else 0,
                             format_func=_model_label, key="ai_model")
        effort = st.segmented_control(
            tr("Effort de réflexion", "Reasoning effort"), usage.EFFORTS, default=settings["anthropic"]["effort"],
            format_func=lambda e: tr(*EFFORT_LABELS[e]), key="ai_effort",
            help=tr("Claude réfléchit toujours avant de répondre ; l'effort règle la profondeur, donc le temps et le "
                    "coût. Moyen convient à presque tout.",
                    "Claude always reasons before answering; effort sets how deeply, so the time and the cost. "
                    "Medium suits almost everything.")) or settings["anthropic"]["effort"]
        if (model, effort) != (current, settings["anthropic"]["effort"]):
            settings["anthropic"].update(model=model, effort=effort)
            usage.save_settings(settings)
            st.toast(tr("Réglage enregistré", "Setting saved"), icon=":material/check:")
        ai.key_actions(PROVIDER, status, lambda: anp.check_connection(model))
        st.caption(tr("Repli automatique activé (Opus et Sonnet) : si Claude décline une demande pour raison de "
                      "politique, Anthropic la relance sur un autre modèle Claude. Haiku n'a pas de repli.",
                      "Automatic fallback on (Opus and Sonnet): if Claude declines a request for policy reasons, "
                      "Anthropic retries it on another Claude model. Haiku has no fallback."))


def _how_card() -> None:
    with ui.card(tr("Comment Claude travaille ici", "How Claude works here"),
                 tr("Ce que l'adaptateur Claude ajoute aux règles cloud communes.",
                    "What the Claude adapter adds to the shared cloud rules.")):
        st.markdown(tr(
            "- **Réflexion conservée** entre les étapes d'une analyse : l'historique est renvoyé tel quel, sans "
            "retouche.\n"
            "- **Cache** : la fiche de l'expert et les outils sont mis en cache ; une relecture coûte 5 % du prix "
            "d'entrée sur Opus et Sonnet.\n"
            "- **Effort** réglable (faible, moyen, élevé) : la profondeur de réflexion, donc le temps et le coût.\n"
            "- **Repli serveur** sur Opus et Sonnet : une demande déclinée à tort est relancée sur un autre modèle "
            "Claude.\n"
            "- Comme tous les modèles cloud : fiche de l'expert, données agrégées uniquement, plafond du jour, coût "
            "par réponse.",
            "- **Reasoning kept** across the steps of an analysis: the history is sent back unchanged.\n"
            "- **Caching**: the expert brief and the tools are cached; reading them again costs 5% of the input "
            "price on Opus and Sonnet.\n"
            "- Adjustable **effort** (low, medium, high): how deeply it reasons, so the time and the cost.\n"
            "- **Server-side fallback** on Opus and Sonnet: a request wrongly declined is retried on another Claude "
            "model.\n"
            "- Like every cloud model: expert brief, aggregated data only, daily cap, cost per answer."))


def render() -> None:
    ui.page_header(
        "IA & API",
        "Claude (Anthropic)",
        tr("Le mode Expert avec Claude, avec votre propre clé API. La clé reste dans le Gestionnaire d'identification "
           "Windows ; seules des données agrégées partent chez Anthropic.",
           "Expert mode with Claude, using your own API key. The key stays in the Windows Credential Manager; only "
           "aggregated data goes to Anthropic."),
    )
    ai.flash()
    settings = usage.load_settings()
    status = vault.status(PROVIDER)
    left, right = st.columns(2, gap="medium")
    with left:
        _key_card(status, settings)
    with right, ui.card(tr("Dépenses Claude", "Claude spending"),
                        tr("Estimation au tarif public ; la facture fait foi dans la console Claude.",
                           "Estimate at list prices; the Claude console has the invoice."), stretch=True):
        ai.cap_line(settings)
        ai.usage_table(PROVIDER, empty_fr="Aucun appel à Claude pour l'instant.", empty_en="No Claude call yet.")
    _how_card()
