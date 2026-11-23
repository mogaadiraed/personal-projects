"""Login gate, current user and permissions for the pages (users and sessions: ``carbonsync.auth``).

* ``gate(cfg)`` runs before the navigation: first start → create the administrator; otherwise the session
  cookie or the sign-in screen. Until someone is signed in the page stops there and the menu bar is hidden.
* ``can(action)`` / ``at_least(role)`` decide what a page shows or enables.
* ``account_menu()`` is the account button in the page header: the team (online or offline), the latest data
  files received and the last pipeline run, and Log out. The password is changed in Configuration → Password.
* ``heartbeat()`` marks the session as seen every minute while a page is open (presence).

The session cookie is set from a tiny same-origin script (Streamlit cannot set cookies itself). Without it
(cookies blocked), the login still works but a browser refresh asks for the password again.
"""
from __future__ import annotations

import json
from datetime import datetime
from html import escape
from typing import Any

import streamlit as st

from app.core.i18n import lang, tr
from carbonsync import auth as core
from carbonsync.obs import get_logger

log = get_logger("carbonsync.app")
COOKIE = "cs_session"
LOCAL_USER: dict[str, Any] = {"username": "local", "name": "Local", "role": "admin", "local": True}


def _store(cfg: dict | None = None) -> core.AuthStore:
    return core.AuthStore(cfg=cfg)


def current() -> dict[str, Any]:
    return st.session_state.get("auth_user") or LOCAL_USER


def role() -> str:
    return current()["role"]


def can(action: str) -> bool:
    return core.can(role(), action)


def at_least(minimum: str) -> bool:
    return core.at_least(role(), minimum)


def role_label(r: str) -> str:
    return tr(*core.ROLE_LABELS.get(r, (r, r)))


def need(action: str) -> str:
    """Tooltip for a control disabled by the role."""
    who = role_label(core.PERMISSIONS.get(action, "viewer"))
    return tr(f"Réservé au rôle {who} ou plus", f"Requires the {who} role or higher")


def _cookie(token: str | None, max_age: int) -> None:
    value = token or ""                     # our own urlsafe token: letters, digits, '-' and '_' only
    if any(not (ch.isalnum() or ch in "-_") for ch in value):
        return
    st.iframe(
        "<script>(function(){var c='" + COOKIE + "=" + value + "; path=/; max-age=" + str(int(max_age))
        + "; SameSite=Strict';try{window.parent.document.cookie=c;}catch(e){document.cookie=c;}})();</script>",
        height=1,
    )


def _sign_in(store: core.AuthStore, user: dict[str, Any]) -> None:
    token = store.create_session(user["username"])
    st.session_state["auth_user"] = user
    st.session_state["_auth_token"] = token
    st.session_state["_auth_cookie_set"] = token
    st.rerun()


def gate(cfg: dict) -> dict[str, Any]:
    """Returns the signed-in user, or shows the setup / sign-in screen and stops the script."""
    s = core.settings(cfg)
    if not s["enabled"]:
        st.session_state["auth_user"] = LOCAL_USER
        return LOCAL_USER
    if st.session_state.get("auth_user", {}).get("local"):
        st.session_state.pop("auth_user")       # the login was just switched on
    store = _store(cfg)
    if (token := st.session_state.pop("_auth_cookie_set", None)):
        _cookie(token, s["session_hours"] * 3600)
    if st.session_state.pop("_auth_cookie_clear", False):
        _cookie(None, 0)

    try:
        user = None
        if st.session_state.get("_auth_token"):
            user = store.session_user(st.session_state["_auth_token"])   # role change, reset or deletion apply at once
        if user is None:
            try:
                cookie = st.context.cookies.get(COOKIE)
            except Exception:  # noqa: BLE001 - no browser context (tests, bare mode)
                cookie = None
            user = store.session_user(cookie)
            if user is not None:
                st.session_state["_auth_token"] = cookie
        if user is not None:
            st.session_state["auth_user"] = user
            return user
        st.session_state.pop("auth_user", None)
        st.session_state.pop("_auth_token", None)
        if not store.has_users():
            _setup(store)
        else:
            _login(store, s)
    except core.AuthError as err:
        st.error(str(err))
    st.stop()
    raise RuntimeError("unreachable")       # st.stop() raises


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------
@st.fragment(run_every=60)
def _beat() -> None:
    if (token := st.session_state.get("_auth_token")):
        _store().touch(token)


def heartbeat() -> None:
    """Marks this session as seen now, then every minute while the page stays open (the team list)."""
    if current().get("local") or not st.session_state.get("_auth_token"):
        return
    _store().touch(st.session_state["_auth_token"])        # written at most every 50 s
    _beat()


# ---------------------------------------------------------------------------
# Sign-in screen
# ---------------------------------------------------------------------------
def _login_css() -> str:
    from app.ui import appearance as ap

    look = ap.current()
    accent = ap.palette(look)["dark"]["primary"]
    light, deep = ap.mix("#ffffff", accent, 0.30), ap.mix("#000000", accent, 0.45)
    return f"""<style>
/* Nobody signed in: no menu bar, no sidebar, no toolbar */
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"], [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="stMainBlockContainer"] {{ padding-top: 4vh !important; }}
.cs-login-brand {{
  position: relative; overflow: hidden; min-height: 500px; border-radius: 24px; padding: 34px 34px 30px;
  color: #fff; display: flex; flex-direction: column;
  background: radial-gradient(130% 100% at 0% 0%, {light} 0%, {accent} 42%, {deep} 100%);
  box-shadow: 0 28px 70px {ap.rgba(accent, 0.35)}, 0 4px 14px rgba(0,0,0,.18);
}}
.cs-login-brand::before, .cs-login-brand::after {{
  content: ""; position: absolute; border-radius: 50%; background: rgba(255,255,255,.08); pointer-events: none;
}}
.cs-login-brand::before {{ width: 340px; height: 340px; right: -120px; top: -110px; }}
.cs-login-brand::after {{ width: 220px; height: 220px; right: 40px; bottom: -120px; background: rgba(255,255,255,.06); }}
.cs-login-logo {{ display: flex; align-items: center; gap: 11px; font-weight: 700; font-size: 1.05rem; letter-spacing: .01em; }}
.cs-login-mark {{ width: 40px; height: 40px; border-radius: 12px; background: rgba(255,255,255,.18);
  display: grid; place-items: center; box-shadow: inset 0 1px 0 rgba(255,255,255,.25); }}
.cs-login-h {{ font-size: 2rem; font-weight: 750; line-height: 1.12; letter-spacing: -0.025em; margin: 42px 0 12px; max-width: 15ch; }}
.cs-login-p {{ font-size: .96rem; line-height: 1.55; opacity: .9; max-width: 36ch; margin: 0; }}
.cs-login-feats {{ margin-top: auto; padding-top: 28px; display: grid; gap: 11px; position: relative; z-index: 1; }}
.cs-login-feat {{ display: flex; gap: 11px; align-items: center; font-size: .9rem; }}
.cs-login-feat b {{ width: 26px; height: 26px; flex: none; border-radius: 8px; background: rgba(255,255,255,.18);
  display: grid; place-items: center; font-size: .8rem; }}
.st-key-cs_login_card {{
  min-height: 500px; border-radius: 24px; padding: 34px 32px 26px; gap: .55rem;
  background: var(--cs-panel); border: 1px solid var(--cs-line); box-shadow: var(--cs-edge), 0 24px 60px rgba(0,0,0,.16);
  backdrop-filter: blur(18px) saturate(140%); -webkit-backdrop-filter: blur(18px) saturate(140%);
}}
.cs-login-eyebrow {{ font-size: .74rem; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; color: var(--cs-accent); }}
.cs-login-title {{ font-size: 1.6rem; font-weight: 750; letter-spacing: -0.02em; margin: 4px 0 2px; line-height: 1.2; }}
.cs-login-sub {{ font-size: .92rem; opacity: .68; margin-bottom: 8px; line-height: 1.45; }}
.st-key-cs_login_card [data-testid="stTextInputRootElement"] {{ border-radius: 12px; min-height: 46px; }}
.st-key-cs_login_card [data-testid="stFormSubmitButton"] button {{ min-height: 48px; border-radius: 12px; font-weight: 650;
  font-size: 1rem; margin-top: 6px; }}
.st-key-cs_login_card [data-testid="stForm"] {{ padding: 0; border: 0; }}
.cs-login-help {{ font-size: .8rem; opacity: .62; line-height: 1.45; margin-top: 4px; }}
.cs-login-foot {{ text-align: center; font-size: .76rem; opacity: .5; margin-top: 18px; }}
@media (max-width: 900px) {{
  .cs-login-brand, .st-key-cs_login_card {{ min-height: 0; }}
  .cs-login-h {{ margin-top: 22px; font-size: 1.6rem; }}
}}
</style>"""


_LEAF = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 '
         '17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 '
         '12"/></svg>')


def _brand_html() -> str:
    feats = [
        ("1", tr("Émissions par scope, zone et poste, jour après jour", "Emissions by scope, zone and item, day by day")),
        ("2", tr("Prévision Chronos-2 et trajectoire −30 % en 2030", "Chronos-2 forecast and the path to −30 % by 2030")),
        ("3", tr("Alertes après chaque dépôt de données", "Alerts after every data upload")),
        ("4", tr("Tout tourne sur ce PC : les données restent locales", "Runs on this PC: the data stays local")),
    ]
    rows = "".join(f'<div class="cs-login-feat"><b>{n}</b><span>{escape(t)}</span></div>' for n, t in feats)
    return (f'<div class="cs-login-brand"><div class="cs-login-logo"><span class="cs-login-mark">{_LEAF}</span>'
            f'CarbonSync AI</div>'
            f'<div class="cs-login-h">{escape(tr("Le carbone du site, sous contrôle.", "Your site’s carbon, under control."))}</div>'
            f'<p class="cs-login-p">{escape(tr("Suivi des émissions, budget carbone, prévisions et alertes pour l’usine, en un seul tableau de bord.", "Emissions tracking, carbon budget, forecasts and alerts for the plant, in one dashboard."))}</p>'
            f'<div class="cs-login-feats">{rows}</div></div>')


def _frame(eyebrow: str, title: str, subtitle: str):
    """The sign-in layout: brand panel on the left, the form card on the right. Returns the card."""
    from app.ui import components as ui

    st.html(_login_css())
    _sp, lang_col = st.columns([6, 1], vertical_alignment="center")
    with lang_col:
        ui._language_switch()
    _l, mid, _r = st.columns([0.35, 3, 0.35])
    with mid:
        brand, form = st.columns([1.1, 1], gap="large")
        with brand:
            st.html(_brand_html())
        with form:
            card = st.container(key="cs_login_card")
            card.html(f'<div class="cs-login-eyebrow">{escape(eyebrow)}</div><div class="cs-login-title">'
                      f'{escape(title)}</div><div class="cs-login-sub">{escape(subtitle)}</div>')
        st.html(f'<div class="cs-login-foot">CarbonSync AI · v2.1 · '
                f'{escape(tr("GHG Protocol · CSRD / ESRS E1", "GHG Protocol · CSRD / ESRS E1"))}</div>')
    return card


def _login(store: core.AuthStore, s: dict[str, Any]) -> None:
    card = _frame(tr("Espace sécurisé", "Secure area"), tr("Bon retour", "Welcome back"),
                  tr("Connectez-vous avec le compte que l'administrateur vous a donné.",
                     "Sign in with the account your administrator gave you."))
    with card, st.form("auth_login", border=False):
        username = st.text_input(tr("Identifiant", "User name"), key="auth_user_in", autocomplete="username",
                                 placeholder=tr("ex. : a.benali", "e.g. a.benali"))
        password = st.text_input(tr("Mot de passe", "Password"), type="password", key="auth_pw_in",
                                 autocomplete="current-password", placeholder="••••••••")
        ok = st.form_submit_button(tr("Se connecter", "Sign in"), type="primary", icon=":material/login:", width="stretch")
        if ok:
            user, why = store.authenticate(username, password)
            if user:
                _sign_in(store, user)
            elif why == "locked":
                st.error(tr(f"Compte bloqué {s['lockout_minutes']} min après plusieurs échecs. Réessayer plus tard ou "
                            "demander à un administrateur de le débloquer.",
                            f"Account locked for {s['lockout_minutes']} min after several failures. Try again later or "
                            "ask an administrator to unlock it."), icon=":material/lock:")
            else:
                st.error(tr("Identifiant ou mot de passe incorrect.", "Wrong user name or password."),
                         icon=":material/error:")
    card.html('<div class="cs-login-help">' + escape(tr(
        f"Mot de passe oublié ? Un administrateur le réinitialise. Après {s['max_failed_logins']} essais manqués, "
        f"le compte est bloqué {s['lockout_minutes']} min.",
        f"Forgotten password? An administrator resets it. After {s['max_failed_logins']} failed tries the account "
        f"is locked for {s['lockout_minutes']} min.")) + "</div>")


def _setup(store: core.AuthStore) -> None:
    card = _frame(tr("Premier démarrage", "First start"), tr("Créer l'administrateur", "Create the administrator"),
                  tr("Ce compte gère ensuite l'équipe : lecteurs, éditeurs et administrateurs.",
                     "This account then manages the team: viewers, editors and administrators."))
    with card, st.form("auth_setup", border=False):
        username = st.text_input(tr("Identifiant", "User name"), placeholder="admin", key="auth_setup_user")
        name = st.text_input(tr("Nom affiché", "Display name"), key="auth_setup_name",
                             placeholder=tr("ex. : Amira Ben Ali", "e.g. Amira Ben Ali"))
        pw1 = st.text_input(tr("Mot de passe (8 caractères min.)", "Password (8 characters min.)"), type="password",
                            key="auth_setup_pw1", autocomplete="new-password")
        pw2 = st.text_input(tr("Confirmer", "Confirm"), type="password", key="auth_setup_pw2", autocomplete="new-password")
        if st.form_submit_button(tr("Créer l'administrateur", "Create the administrator"), type="primary",
                                 icon=":material/admin_panel_settings:", width="stretch"):
            if pw1 != pw2:
                st.error(tr("Les deux mots de passe diffèrent.", "The two passwords differ."))
                return
            try:
                user = store.create_user(username, pw1, "admin", name)
            except core.AuthError as err:
                st.error(str(err))
                return
            _sign_in(store, user)


def logout() -> None:
    _store().end_session(st.session_state.pop("_auth_token", None))
    log.info("Auth: '%s' logged out", current()["username"])
    for k in ("auth_user",):
        st.session_state.pop(k, None)
    st.session_state["_auth_cookie_clear"] = True
    st.rerun()


# ---------------------------------------------------------------------------
# Account button: the team and the latest data received
# ---------------------------------------------------------------------------
_ACCOUNT_CSS = """<style>
.cs-acc { width: min(380px, 84vw); display: flex; flex-direction: column; gap: 12px; }
.cs-acc-me { display: flex; align-items: center; gap: 11px; }
.cs-acc-av { width: 40px; height: 40px; border-radius: 50%; flex: none; display: grid; place-items: center;
  font-weight: 700; font-size: .9rem; color: #fff; background: var(--cs-accent); }
.cs-acc-name { font-weight: 700; font-size: .98rem; line-height: 1.2; }
.cs-acc-role { font-size: .76rem; opacity: .7; }
.cs-acc-h { display: flex; justify-content: space-between; align-items: baseline; font-size: .72rem; font-weight: 700;
  letter-spacing: .06em; text-transform: uppercase; opacity: .62; margin-bottom: 4px; }
.cs-acc-h span:last-child { text-transform: none; letter-spacing: 0; font-weight: 600; }
.cs-acc-list { display: flex; flex-direction: column; border: 1px solid var(--cs-line); border-radius: 12px; overflow: hidden; }
.cs-acc-row { display: flex; align-items: center; gap: 10px; padding: 7px 11px; font-size: .82rem; }
.cs-acc-row + .cs-acc-row { border-top: 1px solid var(--cs-line); }
.cs-acc-row .main { flex: 1; min-width: 0; }
.cs-acc-row .main b { font-weight: 650; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cs-acc-row .main small { opacity: .65; font-size: .74rem; display: block; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.cs-acc-row .side { font-size: .74rem; opacity: .75; white-space: nowrap; text-align: right; }
.cs-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: #9ca3af; }
.cs-dot.on { background: #16a34a; box-shadow: 0 0 0 3px rgba(22,163,74,.2); }
.cs-acc-ico { width: 28px; height: 28px; border-radius: 8px; flex: none; display: grid; place-items: center;
  background: var(--cs-accent-soft); color: var(--cs-accent); font-size: .72rem; font-weight: 700; }
.cs-acc-empty { font-size: .8rem; opacity: .65; padding: 8px 11px; }
.cs-acc-note { font-size: .74rem; opacity: .62; }
</style>"""


def _initials(name: str) -> str:
    parts = [p for p in (name or "?").replace(".", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def _ago(stamp: str | None) -> str:
    if not stamp:
        return tr("jamais connecté", "never signed in")
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return str(stamp)
    secs = (datetime.now() - when).total_seconds()
    if secs < 90:
        return tr("à l'instant", "just now")
    if secs < 3600:
        return tr(f"il y a {int(secs // 60)} min", f"{int(secs // 60)} min ago")
    if secs < 86400:
        return tr(f"il y a {int(secs // 3600)} h", f"{int(secs // 3600)} h ago")
    return when.strftime("%d/%m/%Y %H:%M")


def _last_pipeline_run() -> dict[str, Any] | None:
    path = core.get_project_root() / "data" / "pipeline_history.jsonl"
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 16384))
            lines = [ln for ln in fh.read().decode("utf-8", "replace").splitlines() if ln.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None
    except (OSError, json.JSONDecodeError):
        return None


def _team_html(me: str) -> str:
    try:
        team = _store().presence()
    except core.AuthError:
        return ""
    online = sum(u["online"] for u in team)
    rows = "".join(
        f'<div class="cs-acc-row"><span class="cs-dot{" on" if u["online"] else ""}"></span>'
        f'<div class="main"><b>{escape(u["name"])}{escape(tr(" (vous)", " (you)")) if u["username"] == me else ""}</b>'
        f'<small>{escape(role_label(u["role"]))} · {escape(u["username"])}</small></div>'
        f'<div class="side">{escape(tr("En ligne", "Online") if u["online"] else _ago(u.get("last_seen")))}</div></div>'
        for u in team)
    return (f'<div><div class="cs-acc-h"><span>{escape(tr("Équipe", "Team"))}</span>'
            f'<span>{escape(tr(f"{online} en ligne sur {len(team)}", f"{online} online of {len(team)}"))}</span></div>'
            f'<div class="cs-acc-list">{rows}</div></div>')


def _intake_html() -> str:
    from carbonsync.alerts import DEPT_NAMES
    from carbonsync.ingest import intake

    try:
        uploads = sorted(intake.list_uploads(), key=lambda e: str(e.get("uploaded_at", "")), reverse=True)[:5]
    except (OSError, ValueError):
        uploads = []
    L = 0 if lang() == "fr" else 1
    rows = "".join(
        f'<div class="cs-acc-row"><span class="cs-acc-ico">CSV</span><div class="main"><b>{escape(str(e.get("file", "")))}</b>'
        f'<small>{escape(DEPT_NAMES.get(e.get("department"), (e.get("department"), e.get("department")))[L] or "—")} · '
        f'{int(e.get("rows") or 0)} {escape(tr("lignes", "rows"))} · {escape(str(e.get("first_date", "")))} → '
        f'{escape(str(e.get("last_date", "")))}</small></div>'
        f'<div class="side">{escape(_ago(e.get("uploaded_at")))}'
        + (f'<br>{escape(tr("par", "by"))} {escape(str(e["uploaded_by"]))}' if e.get("uploaded_by") else "")
        + "</div></div>"
        for e in uploads) or f'<div class="cs-acc-empty">{escape(tr("Aucun fichier déposé depuis le tableau de bord.", "No file uploaded from the dashboard yet."))}</div>'
    run = _last_pipeline_run()
    note = ""
    if run:
        through = (run.get("steps", {}).get("features", {}) or {}).get("data_through", "—")
        note = (f'<div class="cs-acc-note">{escape(tr("Dernière exécution du pipeline", "Last pipeline run"))} : '
                f'{escape(_ago(run.get("started_at")))} · {escape(str(run.get("trigger", "")))} · '
                f'{escape(tr(f"données au {through}", f"data through {through}"))}</div>')
    return (f'<div><div class="cs-acc-h"><span>{escape(tr("Derniers fichiers reçus", "Latest data files"))}</span>'
            f'<span>{len(uploads)}</span></div><div class="cs-acc-list">{rows}</div>{note}</div>')


def account_menu() -> None:
    """Account button for the page header."""
    u = current()
    label = u["name"].split()[0][:12] if u.get("name") else u["username"]
    with st.popover(label, icon=":material/account_circle:", width="stretch", key="cs_account_pop"):
        me = (f'<div class="cs-acc-me"><span class="cs-acc-av">{escape(_initials(u["name"]))}</span><div>'
              f'<div class="cs-acc-name">{escape(u["name"])}</div><div class="cs-acc-role">{escape(role_label(u["role"]))}'
              + ("" if u.get("local") else f' · {escape(u["username"])}') + "</div></div></div>")
        if u.get("local"):
            team = (f'<div class="cs-acc-note">{escape(tr("Connexion désactivée : pas de comptes d’équipe "
                                                           "(auth.enabled dans config.yaml).",
                                                           "Login is off: no team accounts "
                                                           "(auth.enabled in config.yaml)."))}</div>')
        else:
            team = _team_html(u["username"])
        st.html(_ACCOUNT_CSS + f'<div class="cs-acc">{me}{team}{_intake_html()}</div>')
        if not u.get("local"):
            st.caption(tr("Mot de passe : Configuration → Mot de passe.", "Password: Configuration → Password."))
            if st.button(tr("Se déconnecter", "Log out"), icon=":material/logout:", key="auth_logout", width="stretch"):
                logout()
