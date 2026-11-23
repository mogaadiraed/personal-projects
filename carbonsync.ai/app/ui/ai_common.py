"""Pieces shared by the AI & API pages: the API-key card (password field in a self-clearing form,
masked status, delete with confirmation), flash messages, money and the usage table.

The key is typed in a password field inside a form that clears itself, stored with ``keyring``
and only ever shown masked. Nothing here writes it anywhere else.
"""
from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import streamlit as st

from app.core.i18n import lang, tr
from app.ui import components as ui
from app.ui.format import num
from carbonsync.assistant import usage, vault
from carbonsync.assistant.providers import registry


def money(v: float, digits: int = 2) -> str:
    return f"{num(v, digits)} $" if lang() == "fr" else f"${num(v, digits)}"


def price_text(inp: float, out: float) -> str:
    return tr(f"{num(inp, 2)} $ / {num(out, 2)} $ par M jetons", f"${num(inp, 2)} / ${num(out, 2)} per M tokens")


def flash() -> None:
    """Shows (once) the message a button left in ``ai_flash`` before its rerun."""
    msg = st.session_state.pop("ai_flash", None)
    if not msg:
        return
    kind, text = msg
    if kind == "ok":
        st.success(text, icon=":material/check_circle:")
    else:
        st.error(text, icon=":material/error:")


def key_chips(status: dict) -> None:
    if status["source"] == "vault":
        ui.chip_row([(tr(f"Clé enregistrée · {status['masked']}", f"Key stored · {status['masked']}"), "accent"),
                     (tr("Gestionnaire d'identification Windows", "Windows Credential Manager"), "")])
    elif status["source"] == "env":
        ui.chip_row([(tr(f"Clé lue dans {status['env']} · {status['masked']}",
                         f"Key read from {status['env']} · {status['masked']}"), "accent")])
    else:
        ui.chip_row([(tr("Aucune clé", "No key"), "warn")])


def key_form(provider: str, status: dict, help_fr: str, help_en: str) -> None:
    """Password field + save button. The form clears the field on submit, so the key never stays in
    the page state."""
    spec = registry.PROVIDERS[provider]
    with st.form(f"{provider}_key_form", clear_on_submit=True, border=False):
        key = st.text_input(
            tr("Nouvelle clé API" if status["has_key"] else "Clé API", "New API key" if status["has_key"] else "API key"),
            type="password", placeholder=spec.get("placeholder", "…"), autocomplete="off",
            help=tr(help_fr + " Elle est enregistrée dans le Gestionnaire d'identification Windows, jamais dans "
                    "config.yaml, les journaux, les exports ou les conversations.",
                    help_en + " It is stored in the Windows Credential Manager, never in config.yaml, logs, exports "
                    "or conversations."))
        if st.form_submit_button(tr("Enregistrer la clé", "Save the key"), type="primary", icon=":material/key:"):
            try:
                masked = vault.set_key(provider, key)
                st.session_state["ai_flash"] = ("ok", tr(f"Clé enregistrée ({masked}). Testez la connexion.",
                                                         f"Key stored ({masked}). Test the connection."))
            except vault.VaultError as exc:
                st.session_state["ai_flash"] = ("err", exc.text(lang()))
            st.rerun()


def key_actions(provider: str, status: dict, test: Callable[[], dict]) -> None:
    """Test connection + delete (with confirmation). ``test`` returns {'ok', 'fr', 'en'}."""
    c1, c2 = st.columns(2)
    with c1:
        if st.button(tr("Tester la connexion", "Test the connection"), icon=":material/network_check:",
                     width="stretch", disabled=not status["has_key"], key=f"{provider}_test"):
            with st.spinner(tr("Test en cours…", "Testing…")):
                res = test()
            st.session_state["ai_flash"] = ("ok" if res["ok"] else "err", res["fr"] if lang() == "fr" else res["en"])
            st.rerun()
    with c2, st.popover(tr("Supprimer la clé", "Delete the key"), icon=":material/delete:", width="stretch",
                        disabled=status["source"] != "vault"):
        st.caption(tr("La clé sera retirée du Gestionnaire d'identification Windows. Ce fournisseur ne sera plus "
                      "disponible dans l'assistant tant qu'une nouvelle clé n'est pas enregistrée.",
                      "The key will be removed from the Windows Credential Manager. This provider stays unavailable "
                      "in the assistant until a new key is stored."))
        if st.button(tr("Confirmer la suppression", "Confirm deletion"), type="primary", key=f"{provider}_delete"):
            try:
                vault.delete_key(provider)
                st.session_state["ai_flash"] = ("ok", tr("Clé supprimée.", "Key deleted."))
            except vault.VaultError as exc:
                st.session_state["ai_flash"] = ("err", exc.text(lang()))
            st.rerun()
    if status["source"] == "env":
        st.caption(tr(f"La clé vient de la variable d'environnement {status['env']} : supprimez-la dans Windows "
                      "pour la retirer.", f"The key comes from the {status['env']} environment variable: remove it "
                      "in Windows to take it away."))


def usage_table(provider: str | None = None, days: int = 30, empty_fr: str = "", empty_en: str = "") -> None:
    """Calls, tokens and estimated cost per day and model (all cloud providers, or one)."""
    rows = [r for r in usage.history(days) if provider is None or r["provider"] == provider]
    if not rows:
        st.caption(tr(empty_fr or "Aucun appel pour l'instant.", empty_en or "No call yet."))
        return
    df = pd.DataFrame(rows)
    total, calls = float(df["cost_usd"].sum()), int(df["requests"].sum())
    st.caption(tr(f"{days} derniers jours : {money(total)} pour {calls} appel(s) au modèle, soit "
                  f"{money(total / calls, 4)} par appel en moyenne.",
                  f"Last {days} days: {money(total)} for {calls} model call(s), {money(total / calls, 4)} per call "
                  "on average."))
    view = {tr("Jour", "Day"): df["date"]}
    if provider is None:
        view[tr("Fournisseur", "Provider")] = df["provider"].map(registry.label)
    view |= {
        tr("Modèle", "Model"): [registry.model_label(p, m) for p, m in zip(df["provider"], df["model"], strict=True)],
        tr("Appels", "Calls"): df["requests"],
        tr("Jetons lus", "Tokens read"): df["input"] + df["cache_read"] + df["cache_write"],
        tr("dont cache", "from cache"): df["cache_read"], tr("Jetons écrits", "Tokens written"): df["output"],
        tr("Coût (USD)", "Cost (USD)"): df["cost_usd"].round(4),
    }
    out = pd.DataFrame(view)
    st.dataframe(out, hide_index=True, width="stretch", height=min(36 * (len(out) + 1) + 4, 300))


def cap_line(settings: dict) -> None:
    """Today's spend against the shared daily cap (read-only; the cap is set on the overview page)."""
    allowed, spent, cap = usage.check_cap(settings)
    fr, en = registry.WHERE["overview"]
    if cap > 0:
        st.progress(min(spent / cap, 1.0), text=tr(
            f"Tous fournisseurs cloud, aujourd'hui : {money(spent)} sur {money(cap)} (plafond dans {fr})",
            f"All cloud providers, today: {money(spent)} of {money(cap)} (cap in {en})"))
    else:
        st.caption(tr(f"Aujourd'hui : {money(spent)}, sans plafond ({fr}).", f"Today: {money(spent)}, no cap ({en})."))
    if not allowed:
        st.warning(tr("Plafond du jour atteint : les modèles cloud sont en pause jusqu'à demain (l'assistant intégré "
                      "répond à leur place).", "Today's cap is reached: cloud models are paused until tomorrow (the "
                      "built-in assistant answers instead)."), icon=":material/pause_circle:")
