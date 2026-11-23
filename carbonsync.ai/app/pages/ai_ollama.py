"""AI & API → Ollama: Ollama Cloud with the user's API key (the top 5 cloud models: DeepSeek, Kimi,
GLM, Mistral…, any other cloud model, connection and model tests, usage), and the local Ollama
server (installed models, models in memory, default model, context size) with the Stop / Start
buttons that unload the local models to free the GPU, or load the default one again.

Nothing is downloaded or deleted from this page: local models are managed with the Ollama app.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.i18n import lang, tr
from app.ui import ai_common as ai
from app.ui import components as ui
from app.ui.format import num
from carbonsync.assistant import evaluation, usage, vault
from carbonsync.assistant.providers import ollama_cloud as oc
from carbonsync.assistant.providers import ollama_provider as olp
from carbonsync.obs import get_logger

log = get_logger("carbonsync.app.ai_ollama")

TAGS = {"tools": ("outils", "tools"), "thinking": ("réflexion", "reasoning"), "vision": ("vision", "vision")}
CTX_LABELS = {8192: ("8k · le plus rapide", "8k · fastest"), 16384: ("16k · recommandé", "16k · recommended"),
              32768: ("32k · longues conversations", "32k · long chats")}


@st.cache_data(ttl=15, show_spinner=False)
def _local_details() -> dict:
    return olp.details(timeout=1.5)


def _choose(settings: dict, model: str) -> None:
    settings["ollama_cloud"]["model"] = model
    usage.save_settings(settings)
    log.info("Ollama Cloud: model → %s", model)
    st.toast(tr(f"{oc.model_label(model)} choisi pour l'assistant", f"{oc.model_label(model)} chosen for the assistant"),
             icon=":material/check:")


# ---------------------------------------------------------------------------
# Ollama Cloud
# ---------------------------------------------------------------------------
def _cloud_key_card(status: dict) -> None:
    with ui.card("Ollama Cloud", tr("Clé API et test de connexion", "API key and connection test"), stretch=True):
        ai.key_chips(status)
        ai.key_form(oc.PROVIDER, status, "Créez une clé sur ollama.com → Settings → API keys (ollama.com/settings/keys).",
                    "Create a key on ollama.com → Settings → API keys (ollama.com/settings/keys).")
        ai.key_actions(oc.PROVIDER, status, oc.check_connection)
        st.caption(tr("Le test lit le solde du compte (gratuit, aucun jeton facturé). Ollama Cloud se paie à l'usage, "
                      "par jeton : le plan gratuit inclut une petite quantité d'usage chaque mois sur certains "
                      "modèles, puis il faut des crédits ou un abonnement (ollama.com/pricing).",
                      "The test reads the account balance (free, no tokens billed). Ollama Cloud is paid per token: "
                      "the free plan includes a small amount of usage each month on some models, then credits or a "
                      "plan are needed (ollama.com/pricing)."))


def _cloud_spend_card(settings: dict) -> None:
    with ui.card(tr("Dépenses Ollama Cloud", "Ollama Cloud spending"), stretch=True,
                 subtitle=tr("Estimation au tarif public ; la facture fait foi sur ollama.com (Settings → Usage).",
                    "Estimate at list prices; ollama.com has the invoice (Settings → Usage).")):
        ai.cap_line(settings)
        ai.usage_table(oc.PROVIDER, empty_fr="Aucun appel à Ollama Cloud pour l'instant.",
                       empty_en="No Ollama Cloud call yet.")
        st.caption(tr("DeepSeek coûte moitié moins en heures creuses chez Ollama ; l'estimation compte toujours le "
                      "tarif plein, pour que le plafond reste prudent.",
                      "DeepSeek costs half as much off-peak on Ollama; the estimate always counts the full price, so "
                      "the cap stays on the safe side."))


def _model_card(m: dict, chosen: bool, has_key: bool, settings: dict) -> None:
    inp, cached, out = oc.price(m["model"])
    with ui.card(m["label"], f"{m['publisher']} · {tr('contexte', 'context')} {m['context']}", stretch=True):
        ui.chip_row([(tr(*m["role"]), "accent" if chosen else "")]
                    + [(tr(*TAGS[t]), "") for t in m["tags"]])
        st.markdown(tr(f"**{num(inp, 2)} $** entrée · **{num(out, 2)} $** sortie  \npar million de jetons "
                       f"(cache {num(cached, 3)} $)",
                       f"**${num(inp, 2)}** input · **${num(out, 2)}** output  \nper million tokens "
                       f"(cache ${num(cached, 3)})"))
        st.caption(tr(*m["note"]))
        st.caption(f"`{m['model']}`")
        if chosen:
            st.button(tr("Modèle choisi", "Chosen model"), icon=":material/check_circle:", disabled=True,
                      width="stretch", key=f"oc_pick_{m['model']}")
        elif st.button(tr("Choisir", "Choose"), icon=":material/radio_button_unchecked:", width="stretch",
                       key=f"oc_pick_{m['model']}", disabled=not has_key,
                       help=None if has_key else tr("Enregistrez d'abord une clé Ollama Cloud.",
                                                    "Store an Ollama Cloud key first.")):
            _choose(settings, m["model"])
            st.rerun()


def _top5(settings: dict, has_key: bool) -> None:
    current = settings["ollama_cloud"]["model"]
    st.html('<div class="cs-section">' + tr("Les 5 meilleurs modèles cloud pour l'assistant",
                                             "The 5 best cloud models for the assistant") + "</div>"
            '<div class="cs-section-sub">' + tr(
                "Tous appellent les outils et raisonnent ; classés pour l'analyse des émissions (prix ollama.com, "
                "novembre 2026). Les cinq apparaissent dans le choix du moteur de l'assistant.",
                "All call tools and reason; ranked for emissions analysis (ollama.com prices, November 2026). All five "
                "appear in the assistant's engine picker.") + "</div>")
    cols = st.columns(len(oc.TOP5), gap="small")
    for col, m in zip(cols, oc.TOP5, strict=True):
        with col:
            _model_card(m, m["model"] == current, has_key, settings)


def _other_models(settings: dict, has_key: bool) -> None:
    current = settings["ollama_cloud"]["model"]
    with ui.card(tr("Autres modèles cloud et essai", "Other cloud models and test"),
                 tr("Tout modèle cloud d'Ollama est utilisable ; l'essai rapide vérifie le modèle choisi.",
                    "Any Ollama cloud model can be used; the quick test checks the chosen model.")):
        others = oc.OTHER_MODELS

        def label(m: str) -> str:
            inp, _, out = oc.price(m)
            return f"{oc.model_label(m)} · {ai.price_text(inp, out)}"

        c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
        with c1:
            pick = st.selectbox(tr("Autre modèle", "Other model"), others, index=None, format_func=label,
                                placeholder=tr("Choisir un autre modèle…", "Pick another model…"), key="oc_other",
                                disabled=not has_key)
        with c2:
            if st.button(tr("Utiliser ce modèle", "Use this model"), width="stretch", key="oc_other_use",
                         disabled=not has_key or not pick):
                _choose(settings, pick)
                st.rerun()
        st.markdown(tr(f"Modèle de l'assistant : **{oc.model_label(current)}** (`{current}`)",
                       f"Assistant model: **{oc.model_label(current)}** (`{current}`)"))
        if st.button(tr("Essai rapide du modèle", "Quick model test"), icon=":material/bolt:", key="oc_quick",
                     disabled=not has_key,
                     help=tr("Envoie une question d'un mot au modèle choisi : vérifie qu'il répond et mesure le temps. "
                             "Coût : une fraction de centime, compté dans les dépenses.",
                             "Sends a one-word question to the chosen model: checks it answers and measures the time. "
                             "Cost: a fraction of a cent, counted in the spending.")):
            with st.spinner(tr(f"Essai de {oc.model_label(current)}…", f"Testing {oc.model_label(current)}…")):
                res = oc.quick_test(current)
            st.session_state["ai_flash"] = ("ok" if res["ok"] else "err", res["fr"] if lang() == "fr" else res["en"])
            st.rerun()


# ---------------------------------------------------------------------------
# Local Ollama
# ---------------------------------------------------------------------------
def _set_stopped(settings: dict, stopped: bool) -> None:
    if settings["ollama"].get("stopped") is not stopped:
        settings["ollama"]["stopped"] = stopped
        usage.save_settings(settings)


def _start(settings: dict, model: str | None) -> None:
    with st.spinner(tr(f"Chargement de {model or 'Ollama'} sur le GPU…", f"Loading {model or 'Ollama'} on the GPU…")):
        res = olp.start(model, settings["ollama"]["num_ctx"], preferred=settings["ollama"].get("model"))
    if res["ok"]:
        _set_stopped(settings, False)
    _local_details.clear()
    st.session_state["ai_flash"] = ("ok" if res["ok"] else "err", res["fr"] if lang() == "fr" else res["en"])
    st.rerun()


def _gpu_controls(settings: dict, d: dict, model: str) -> None:
    """Stop: unload every local model (GPU freed). Start: load the default local model now."""
    loaded = d.get("loaded_models", [])
    names = [m["model"] for m in loaded]
    vram = sum(float(m.get("vram_gb") or 0) for m in loaded)
    stopped = bool(settings["ollama"].get("stopped"))
    if loaded:
        st.markdown(tr(f"**Sur le GPU** : {', '.join(names)} · {num(vram, 1)} Go de mémoire vidéo",
                       f"**On the GPU**: {', '.join(names)} · {num(vram, 1)} GB of video memory"))
    elif stopped:
        st.markdown(tr("**GPU libre** : modèles locaux arrêtés. L'assistant ne les recharge plus tout seul ; la "
                       "première question locale, ou **Démarrer**, recharge le modèle.",
                       "**GPU free**: local models stopped. The assistant no longer loads them by itself; the first "
                       "local question, or **Start**, loads the model again."))
    else:
        st.markdown(tr("**GPU libre** : aucun modèle local en mémoire.", "**GPU free**: no local model in memory."))
    c1, c2 = st.columns(2)
    with c1:
        if st.button(tr("Arrêter les modèles locaux", "Stop local models"), icon=":material/stop_circle:",
                     key="ol_stop", width="stretch", disabled=not loaded and stopped,
                     help=tr("Retire tous les modèles locaux de la mémoire pour libérer le GPU (jeux, rendu, autre "
                             "logiciel). Le serveur Ollama reste ouvert mais n'occupe plus le GPU.",
                             "Removes every local model from memory to free the GPU (games, rendering, other "
                             "software). The Ollama server stays open but no longer uses the GPU.")):
            with st.spinner(tr("Libération du GPU…", "Freeing the GPU…")):
                res = olp.free_gpu()
            _set_stopped(settings, True)
            _local_details.clear()
            st.session_state["ai_flash"] = ("ok" if res["ok"] else "err", res["fr"] if lang() == "fr" else res["en"])
            st.rerun()
    with c2:
        if st.button(tr(f"Démarrer {model}", f"Start {model}"), icon=":material/play_circle:", key="ol_start",
                     width="stretch", disabled=model in names,
                     help=tr("Charge le modèle local par défaut sur le GPU maintenant (environ 10 s), avec la taille "
                             "de contexte choisie ci-dessous. Il se retire seul 20 min après la dernière question.",
                             "Loads the default local model on the GPU now (about 10 s), with the context size chosen "
                             "below. It leaves by itself 20 min after the last question.")):
            _start(settings, model)


def _local_card(settings: dict) -> None:
    d = _local_details()
    with ui.card(tr("Ollama local (ce PC)", "Local Ollama (this PC)"),
                 tr("Modèles gratuits sur le GPU : rien ne quitte le PC. Ils se gèrent dans l'application Ollama ; "
                    "arrêtez-les ici pour libérer le GPU.",
                    "Free models on the GPU: nothing leaves the PC. They are managed in the Ollama app; stop them "
                    "here to free the GPU.")):
        if d.get("running"):
            ui.chip_row([(tr("Serveur en marche", "Server running"), "accent"),
                         (f"Ollama {d['version']}" if d.get("version") else "Ollama", ""),
                         (tr(f"{len(d['models'])} modèle(s) installé(s)", f"{len(d['models'])} model(s) installed"), ""),
                         (tr("GPU occupé", "GPU in use"), "warn") if d.get("loaded_models")
                         else (tr("GPU libre", "GPU free"), "")])
        elif d.get("installed"):
            ui.chip_row([(tr("Arrêté", "Not running"), "warn"), (tr("GPU libre", "GPU free"), "")])
            st.info(tr("Ollama est installé mais ne tourne pas : **Démarrer Ollama** l'ouvre et charge le modèle local "
                       "par défaut (ou lancez l'application **Ollama** depuis le menu Démarrer).",
                       "Ollama is installed but not running: **Start Ollama** opens it and loads the default local "
                       "model (or start the **Ollama** app from the Start menu)."),
                    icon=":material/memory:")
            if st.button(tr("Démarrer Ollama", "Start Ollama"), icon=":material/play_circle:", key="ol_start_server"):
                _start(settings, None)
            return
        else:
            ui.chip_row([(tr("Non installé", "Not installed"), "warn")])
            st.caption(tr("Ollama n'est pas installé : les modèles cloud et l'assistant intégré restent disponibles.",
                          "Ollama is not installed: cloud models and the built-in assistant remain available."))
            return

        local = [m for m in d["installed_models"] if not m["remote"] and "embed" not in m["model"]]
        loaded = {m["model"]: m for m in d["loaded_models"]}
        names = [m["model"] for m in local]
        preferred = settings["ollama"].get("model") or evaluation.recommended(installed=names)
        current = (olp.pick_default(names, preferred) or names[0]) if names else ""
        if current:
            _gpu_controls(settings, d, current)
        if local:
            st.dataframe(pd.DataFrame([{
                tr("Modèle", "Model"): m["model"], tr("Taille (Go)", "Size (GB)"): m["size_gb"],
                tr("Paramètres", "Parameters"): m["parameters"], tr("Quantification", "Quantisation"): m["quantization"],
                tr("En mémoire", "In memory"): (tr(f"oui · {loaded[m['model']]['gpu_pct']} % GPU",
                                                   f"yes · {loaded[m['model']]['gpu_pct']}% GPU")
                                                if m["model"] in loaded else "—"),
            } for m in local]), hide_index=True, width="stretch")
        else:
            st.caption(tr("Aucun modèle local installé.", "No local model installed."))
            return

        model = st.selectbox(tr("Modèle local par défaut", "Default local model"), names,
                             index=names.index(current), key="ol_default")
        # a horizontal slider: fits any column width (the 3 labelled buttons did not)
        ctx = st.select_slider(
            tr("Taille du contexte", "Context size"), usage.NUM_CTX, value=settings["ollama"]["num_ctx"],
            format_func=lambda n: f"{n // 1024}k", key="ol_ctx",
            help=tr("Mesuré sur ce PC (Qwen 3.5 9B) : 8k ≈ 35 jetons/s, 16k ≈ 27, 32k ≈ 19. Une analyse lit "
                    "environ 8k jetons.",
                    "Measured on this PC (Qwen 3.5 9B): 8k ≈ 35 tokens/s, 16k ≈ 27, 32k ≈ 19. An analysis "
                    "reads about 8k tokens."))
        st.caption(tr(*CTX_LABELS[ctx]))
        if (model, ctx) != (settings["ollama"].get("model") or current, settings["ollama"]["num_ctx"]):
            if model != (settings["ollama"].get("model") or current):
                settings["ollama"]["model"] = model   # a context change alone does not pin the model
            settings["ollama"]["num_ctx"] = int(ctx)
            usage.save_settings(settings)
            st.toast(tr("Réglage enregistré", "Setting saved"), icon=":material/check:")


def render() -> None:
    ui.page_header(
        "IA & API",
        "Ollama",
        tr("Ollama Cloud avec votre clé API : DeepSeek, Kimi, GLM, Mistral… sur les serveurs d'Ollama, avec les règles "
           "Expert (données agrégées uniquement, plafond du jour). Plus bas, le serveur Ollama local de ce PC.",
           "Ollama Cloud with your API key: DeepSeek, Kimi, GLM, Mistral… on Ollama's servers, under the Expert rules "
           "(aggregated data only, daily cap). Below, this PC's local Ollama server."),
    )
    ai.flash()
    settings = usage.load_settings()
    status = vault.status(oc.PROVIDER)
    left, right = st.columns(2, gap="medium")
    with left:
        _cloud_key_card(status)
    with right:
        _cloud_spend_card(settings)
    _top5(settings, status["has_key"])
    _other_models(settings, status["has_key"])
    _local_card(settings)   # full width: much taller than any card it could sit next to
