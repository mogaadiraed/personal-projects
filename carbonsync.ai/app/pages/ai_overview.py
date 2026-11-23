"""AI & API → Overview: every provider at a glance (local Ollama, Ollama Cloud, OpenRouter, Claude,
and OpenAI, Gemini, Mistral and custom shown as "coming soon"), the daily spending cap shared by all cloud providers, the usage table, what is sent
to the cloud, and the expert brief every cloud model reads first.
"""
from __future__ import annotations

import streamlit as st

from app.core.i18n import tr
from app.ui import ai_common as ai
from app.ui import components as ui
from app.ui.format import num
from carbonsync.assistant import prompts, usage, vault
from carbonsync.assistant.providers import ollama_provider as olp
from carbonsync.assistant.providers import registry
from carbonsync.obs import get_logger

log = get_logger("carbonsync.app.ai_overview")


@st.cache_data(ttl=15, show_spinner=False)
def _ollama_status() -> dict:
    return olp.status(timeout=1.5)


def _today_by_provider() -> dict[str, float]:
    out: dict[str, float] = {}
    for r in usage.history(1):
        out[r["provider"]] = out.get(r["provider"], 0.0) + float(r["cost_usd"])
    return out


def _tile(provider: str, spec: dict, settings: dict, spent: dict[str, float]) -> None:
    fr_where, en_where = registry.WHERE.get(provider, ("", ""))
    with ui.card(spec["label"], stretch=True):
        if spec["status"] != "ready":
            ui.chip_row([(tr("Bientôt disponible", "Coming soon"), "")])
            st.caption(tr(*spec["about"]))
            return
        if provider == "ollama":
            s = _ollama_status()
            if s.get("running"):
                chips = [(tr(f"En marche · {len(s['models'])} modèle(s)", f"Running · {len(s['models'])} model(s)"),
                          "accent"), (tr("Local · gratuit", "Local · free"), ""),
                         (tr("GPU occupé", "GPU in use"), "warn") if s.get("loaded") else (tr("GPU libre", "GPU free"), "")]
            else:
                chips = [(tr("Arrêté", "Not running"), "warn"), (tr("Local · gratuit", "Local · free"), "")]
            model = olp.pick_default(s.get("models", []), settings["ollama"].get("model")) or "—"
        else:
            st_ = vault.status(provider)
            chips = [(tr(f"Clé {st_['masked']}", f"Key {st_['masked']}"), "accent") if st_["has_key"]
                     else (tr("Aucune clé", "No key"), "warn"),
                     (tr("Cloud · agrégé", "Cloud · aggregated"), "warn")]
            model = registry.model_label(provider, settings.get(provider, {}).get("model", "")) or "—"
        ui.chip_row(chips)
        st.caption(tr(*spec["about"]))
        line = tr(f"Modèle : **{model}**", f"Model: **{model}**")
        if spec["kind"] == "cloud":
            line += tr(f" · aujourd'hui {ai.money(spent.get(provider, 0.0))}",
                       f" · today {ai.money(spent.get(provider, 0.0))}")
        st.markdown(line + "  \n" + tr(f"Réglages : {fr_where}", f"Settings: {en_where}"))


def _providers(settings: dict) -> None:
    spent = _today_by_provider()
    items = list(registry.PROVIDERS.items())
    for row in range(0, len(items), 4):   # 8 providers: two full rows
        cols = st.columns(4, gap="small")
        for col, (provider, spec) in zip(cols, items[row:row + 4], strict=False):
            with col:
                _tile(provider, spec, settings, spent)


def _spend_card(settings: dict) -> None:
    allowed, spent, cap = usage.check_cap(settings)
    with ui.card(tr("Dépenses et plafond", "Spending and cap"), stretch=True,
                 subtitle=tr("Un seul plafond par jour pour tous les fournisseurs cloud. Estimation au tarif public ; la "
                    "facture fait foi chez chaque fournisseur.",
                    "One daily cap for all cloud providers. Estimate at list prices; each provider's console has "
                    "the invoice.")):
        new_cap = st.number_input(tr("Plafond par jour (USD, 0 = sans plafond)", "Daily cap (USD, 0 = no cap)"),
                                  0.0, 500.0, float(cap), 1.0, format="%.2f", key="ai_cap")
        if abs(new_cap - cap) > 1e-9:
            settings["daily_cap_usd"] = float(new_cap)
            usage.save_settings(settings)
            st.toast(tr("Plafond enregistré", "Cap saved"), icon=":material/check:")
            cap = new_cap
        if cap > 0:
            st.progress(min(spent / cap, 1.0), text=tr(f"Aujourd'hui : {ai.money(spent)} sur {ai.money(cap)}",
                                                       f"Today: {ai.money(spent)} of {ai.money(cap)}"))
            if not allowed:
                st.warning(tr("Plafond atteint : les modèles cloud sont en pause jusqu'à demain (l'assistant intégré "
                              "répond à leur place).", "Cap reached: cloud models are paused until tomorrow (the "
                              "built-in assistant answers instead)."), icon=":material/pause_circle:")
        ai.usage_table(None, empty_fr="Aucun appel à un modèle cloud pour l'instant.",
                       empty_en="No cloud model call yet.")


def _privacy_card() -> None:
    with ui.card(tr("Données envoyées au cloud", "Data sent to the cloud"), stretch=True,
                 subtitle=tr("Seulement avec un modèle cloud ; les modèles locaux n'envoient rien.",
                    "Only with a cloud model; local models send nothing.")):
        st.markdown(tr(
            "**Envoyé au fournisseur choisi** (Ollama Cloud, OpenRouter ou Anthropic), pour chaque question :\n"
            "- la question et la conversation en cours ;\n"
            "- la fiche de l'expert et le contexte : dates des données, plafond, facteurs d'émission, prévision active ;\n"
            "- les résultats des outils que le modèle appelle : totaux **par mois ou plus larges** (les périodes sont "
            "élargies au mois entier), prévision mensuelle, budget, scénarios, nombre et noms des fichiers en "
            "quarantaine (sans chemin).\n\n"
            "**Jamais envoyé** : les lignes journalières, les fichiers CSV, les résultats lus avec un modèle local (une "
            "nouvelle conversation démarre quand vous passez du local au cloud), votre identité, vos clés.",
            "**Sent to the chosen provider** (Ollama Cloud, OpenRouter or Anthropic), for each question:\n"
            "- the question and the current conversation;\n"
            "- the expert brief and the context: data dates, cap, emission factors, active forecast;\n"
            "- the results of the tools the model calls: **monthly or coarser** totals (periods are widened to whole "
            "months), monthly forecast, budget, scenarios, number and names of quarantined files (no path).\n\n"
            "**Never sent**: daily rows, CSV files, results read with a local model (a new conversation starts when "
            "you switch from local to cloud), your identity, your keys."))
        st.caption(tr("Ollama indique ne pas journaliser les requêtes cloud ni s'en servir pour entraîner des modèles "
                      "(serveurs aux États-Unis et en Europe). OpenRouter ne conserve pas les requêtes par défaut, "
                      "mais le fournisseur d'un modèle gratuit peut les journaliser. Anthropic n'utilise pas les "
                      "données de l'API pour l'entraînement par défaut. Chaque réponse détaille ses sources et son coût.",
                      "Ollama states that it does not log cloud requests or train on them (servers in the US and "
                      "Europe). OpenRouter does not keep requests by default, but the provider of a free model may "
                      "log them. Anthropic does not use API data for training by default. Every answer lists its "
                      "sources and its cost."))


def _brief_card() -> None:
    try:
        brief = prompts.load_brief()
    except OSError:
        st.warning(tr("Fiche de l'expert introuvable : docs/assistant/expert_brief.md",
                      "Expert brief not found: docs/assistant/expert_brief.md"), icon=":material/warning:")
        return
    with ui.card(tr("Fiche d'intégration de l'expert", "Expert brief"),
                 tr("Lue par chaque modèle cloud au début de chaque conversation.",
                    "Read by every cloud model at the start of every conversation.")):
        st.markdown(tr(
            f"`docs/assistant/expert_brief.md` · version **{brief['version']}** · {num(brief['words'], 0)} mots "
            f"(≈ {num(brief['tokens'], 0)} jetons) · modifiée le {brief['modified']:%d/%m/%Y %H:%M}",
            f"`docs/assistant/expert_brief.md` · version **{brief['version']}** · {num(brief['words'], 0)} words "
            f"(≈ {num(brief['tokens'], 0)} tokens) · modified {brief['modified']:%Y-%m-%d %H:%M}"))
        st.caption(tr("Elle donne au modèle son rôle (analyste expert en décarbonation industrielle), le site, les "
                      "données, les indicateurs, les outils et les règles de réponse. Tout fournisseur ou modèle cloud "
                      "la reçoit automatiquement : DeepSeek, Kimi, Nemotron, Gemma, Claude…",
                      "It gives the model its role (expert industrial decarbonisation analyst), the site, the data, "
                      "the metrics, the tools and the answering rules. Every cloud provider or model gets it "
                      "automatically: DeepSeek, Kimi, Nemotron, Gemma, Claude…"))
        with st.expander(tr("Lire la fiche", "Read the brief"), icon=":material/menu_book:"):
            st.markdown(brief["text"])


def render() -> None:
    ui.page_header(
        "IA & API",
        tr("Fournisseurs d'IA", "AI providers"),
        tr("Les moteurs de l'assistant : modèles locaux (gratuits, rien ne quitte le PC) et modèles cloud avec vos "
           "propres clés API. Les clés restent dans le Gestionnaire d'identification Windows ; seules des données "
           "agrégées partent dans le cloud.",
           "The assistant's engines: local models (free, nothing leaves the PC) and cloud models with your own API "
           "keys. Keys stay in the Windows Credential Manager; only aggregated data goes to the cloud."),
    )
    ai.flash()
    settings = usage.load_settings()
    _providers(settings)
    left, right = st.columns(2, gap="medium")
    with left:
        _spend_card(settings)
    with right:
        _privacy_card()
    _brief_card()
