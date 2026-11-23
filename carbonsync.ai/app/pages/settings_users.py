"""Configuration → Users (administrators): accounts, roles, password resets, unlocks.

The same operations exist on the command line: ``python -m carbonsync.auth --help``.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core import auth
from app.core.i18n import tr
from app.ui import components as ui
from carbonsync import auth as core


def users_tab(cfg: dict) -> None:
    s = core.settings(cfg)
    store = core.AuthStore(cfg=cfg)
    if not s["enabled"]:
        st.info(tr("Connexion désactivée (auth.enabled: false dans config.yaml, ou variable CARBONSYNC_AUTH=off) : "
                   "tout le monde a le rôle administrateur.",
                   "Login is off (auth.enabled: false in config.yaml, or CARBONSYNC_AUTH=off): everyone is an "
                   "administrator."), icon=":material/lock_open:")
    try:
        users = store.list_users()
    except core.AuthError as err:
        st.error(str(err))
        return
    ui.kpis([
        ui.Kpi(tr("Comptes", "Accounts"), str(len(users))),
        *[ui.Kpi(auth.role_label(r), str(sum(1 for u in users if u["role"] == r))) for r in core.ROLES],
    ])
    st.caption(tr(f"Session : {s['session_hours']} h · blocage {s['lockout_minutes']} min après "
                  f"{s['max_failed_logins']} échecs · mots de passe PBKDF2-SHA256 (600 000 itérations), jamais stockés en clair.",
                  f"Session: {s['session_hours']} h · lock {s['lockout_minutes']} min after {s['max_failed_logins']} "
                  "failures · PBKDF2-SHA256 passwords (600,000 iterations), never stored in clear."))
    if users:
        st.dataframe(pd.DataFrame([{
            tr("Identifiant", "User name"): u["username"], tr("Nom", "Name"): u["name"],
            tr("Rôle", "Role"): auth.role_label(u["role"]),
            tr("Dernière connexion", "Last login"): (u["last_login"] or "—").replace("T", " "),
            tr("Bloqué jusqu'à", "Locked until"): (u["locked_until"] or "—").replace("T", " "),
        } for u in users]), hide_index=True, width="stretch")

    left, right = st.columns(2, gap="medium")
    with left, ui.card(tr("Nouveau compte", "New account"), stretch=True), st.form("usr_add", border=False,
                                                                                  clear_on_submit=True):
        c1, c2 = st.columns(2)
        username = c1.text_input(tr("Identifiant", "User name"), key="usr_new_name")
        name = c2.text_input(tr("Nom affiché", "Display name"), key="usr_new_display")
        role = st.segmented_control(tr("Rôle", "Role"), list(core.ROLES), default="viewer", format_func=auth.role_label,
                                    key="usr_new_role") or "viewer"
        pw = st.text_input(tr("Mot de passe provisoire (8 caractères min.)", "Temporary password (8 characters min.)"),
                           type="password", key="usr_new_pw", autocomplete="new-password")
        if st.form_submit_button(tr("Créer le compte", "Create account"), type="primary", icon=":material/person_add:"):
            try:
                store.create_user(username, pw, role, name)
                st.toast(tr("Compte créé.", "Account created."), icon=":material/check:")
                st.rerun()
            except core.AuthError as err:
                st.error(str(err))
    with right, ui.card(tr("Gérer un compte", "Manage an account"), stretch=True):
        if not users:
            st.caption(tr("Aucun compte.", "No account."))
            return
        me = auth.current()["username"]
        pick = st.selectbox(tr("Compte", "Account"), [u["username"] for u in users], key="usr_pick")
        u = next(x for x in users if x["username"] == pick)
        new_role = st.segmented_control(tr("Rôle", "Role"), list(core.ROLES), default=u["role"],
                                        format_func=auth.role_label, key=f"usr_role_{pick}") or u["role"]
        c1, c2 = st.columns(2)
        if c1.button(tr("Changer le rôle", "Change role"), disabled=new_role == u["role"], key="usr_role_save",
                     width="stretch"):
            _do(lambda: store.set_role(pick, new_role), tr("Rôle changé.", "Role changed."))
        if c2.button(tr("Débloquer", "Unlock"), disabled=not u["locked_until"] and not u["failed"], key="usr_unlock",
                     width="stretch"):
            _do(lambda: store.unlock(pick), tr("Compte débloqué.", "Account unlocked."))
        pw = st.text_input(tr("Nouveau mot de passe", "New password"), type="password", key=f"usr_pw_{pick}",
                           autocomplete="new-password")
        if st.button(tr("Réinitialiser le mot de passe", "Reset password"), disabled=len(pw) < core.MIN_PASSWORD,
                     key="usr_pw_save", icon=":material/key:"):
            _do(lambda: store.set_password(pick, pw), tr("Mot de passe réinitialisé ; ses sessions sont fermées.",
                                                         "Password reset; their sessions are closed."))
        confirm = st.checkbox(tr(f"Je confirme la suppression de « {pick} »", f"I confirm deleting '{pick}'"),
                              key=f"usr_del_ok_{pick}", disabled=pick == me)
        if st.button(tr("Supprimer le compte", "Delete account"), icon=":material/delete:", key="usr_del",
                     disabled=not confirm or pick == me,
                     help=tr("Vous ne pouvez pas supprimer votre propre compte.", "You cannot delete your own account.")
                     if pick == me else None):
            _do(lambda: store.delete_user(pick), tr("Compte supprimé.", "Account deleted."))
    st.caption(tr("En ligne de commande : `python -m carbonsync.auth list | add | passwd | role | unlock | delete`.",
                  "Command line: `python -m carbonsync.auth list | add | passwd | role | unlock | delete`."))


def _do(action, message: str) -> None:
    try:
        action()
    except core.AuthError as err:
        st.error(str(err))
        return
    st.toast(message, icon=":material/check:")
    st.rerun()


def password_tab(cfg: dict) -> None:
    """Configuration → Password: every signed-in user changes their own password here."""
    s = core.settings(cfg)
    u = auth.current()
    left, right = st.columns([1.15, 1], gap="medium")
    sub = tr(f"Compte {u['username']} · {auth.role_label(u['role'])}",
             f"Account {u['username']} · {auth.role_label(u['role'])}")
    with left:
        card = ui.card(tr("Changer mon mot de passe", "Change my password"), sub)
    with card, st.form("auth_pw_change", border=False, clear_on_submit=True):
        old = st.text_input(tr("Mot de passe actuel", "Current password"), type="password", key="auth_old",
                            autocomplete="current-password")
        new1 = st.text_input(tr("Nouveau mot de passe", "New password"), type="password", key="auth_new1",
                             autocomplete="new-password")
        new2 = st.text_input(tr("Confirmer le nouveau", "Confirm the new one"), type="password", key="auth_new2",
                             autocomplete="new-password")
        if st.form_submit_button(tr("Changer le mot de passe", "Change the password"), type="primary",
                                 icon=":material/key:"):
            if new1 != new2:
                st.error(tr("Les deux nouveaux mots de passe diffèrent.", "The two new passwords differ."))
            elif new1 == old:
                st.error(tr("Le nouveau mot de passe doit être différent de l'actuel.",
                            "The new password must differ from the current one."))
            else:
                try:
                    core.AuthStore(cfg=cfg).change_password(u["username"], old, new1,
                                                            keep_token=st.session_state.get("_auth_token"))
                    st.success(tr("Mot de passe changé. Vos autres sessions ouvertes sont fermées.",
                                  "Password changed. Your other open sessions are closed."), icon=":material/check:")
                except core.AuthError as err:
                    st.error(str(err))
    with right, ui.card(tr("Règles", "Rules")):
        st.markdown(tr(
            f"- **{core.MIN_PASSWORD} caractères** au moins, sans espace au début ni à la fin.\n"
            f"- Après **{s['max_failed_logins']} essais manqués**, le compte est bloqué **{s['lockout_minutes']} min**.\n"
            f"- Une session dure **{s['session_hours']} h** ; changer le mot de passe ferme les autres sessions.\n"
            "- Mot de passe oublié : un administrateur le réinitialise (Configuration → Utilisateurs).\n"
            "- Le mot de passe n'est jamais stocké : seule son empreinte PBKDF2-SHA256 l'est.",
            f"- At least **{core.MIN_PASSWORD} characters**, no space at the start or end.\n"
            f"- After **{s['max_failed_logins']} failed tries**, the account is locked for **{s['lockout_minutes']} min**.\n"
            f"- A session lasts **{s['session_hours']} h**; changing the password closes your other sessions.\n"
            "- Forgotten password: an administrator resets it (Configuration → Users).\n"
            "- The password is never stored: only its PBKDF2-SHA256 hash is."))
        last = u.get("last_login")
        if last:
            st.caption(tr(f"Dernière connexion : {str(last).replace('T', ' ')}",
                          f"Last sign-in: {str(last).replace('T', ' ')}"))
