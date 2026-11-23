"""Users, roles and sessions for the dashboard (standard library only).

* **Roles**: ``viewer`` reads the monitoring, plan, data-quality and evidence pages and uses the assistant
  (Data intake, Model lab, Configuration and the AI pages are left out of its menu); ``editor`` also uploads data, runs the
  pipeline and the models, saves scenarios, generates reports and edits targets, inventory and breakdown
  settings; ``admin`` also manages users, appearance, layout, system settings and the AI connections.
* **Passwords**: PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte random salt per user (OWASP 2023 level).
  Only the hash is stored, in ``data/auth/users.json`` (git-ignored with the rest of ``data/``).
* **Sessions**: a random 256-bit token is given to the browser (cookie); only its SHA-256 is stored, with an
  expiry (``auth.session_hours``, 12 h). Logging out deletes it.
* **Lockout**: ``auth.max_failed_logins`` wrong passwords in a row lock the account for
  ``auth.lockout_minutes`` (5 and 15 by default).
* **First run**: when no user exists, the dashboard asks for the first administrator.

Command line (for an administrator on the PC, e.g. a forgotten password)::

    python -m carbonsync.auth list
    python -m carbonsync.auth add alice --role editor --name "Alice B."
    python -m carbonsync.auth passwd alice
    python -m carbonsync.auth role alice admin
    python -m carbonsync.auth unlock alice
    python -m carbonsync.auth delete alice

``CARBONSYNC_AUTH=off`` (environment) turns the login off, for the automated tests only.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from carbonsync.obs import get_logger

log = get_logger(__name__)

ROLES = ("viewer", "editor", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
ROLE_LABELS = {"viewer": ("Lecteur", "Viewer"), "editor": ("Éditeur", "Editor"), "admin": ("Administrateur", "Admin")}
# Minimum role for each action (anything not listed: viewer)
PERMISSIONS: dict[str, str] = {
    "upload": "editor", "pipeline": "editor", "forecast_run": "editor", "model_lab": "editor",
    "scenarios_save": "editor", "reports_generate": "editor", "alerts_check": "editor",
    "settings_targets": "editor", "settings_inventory": "editor", "settings_reports": "editor",
    "settings_appearance": "admin", "settings_layout": "admin", "settings_system": "admin",
    "users": "admin", "ai_admin": "admin", "reset_data": "admin",
}
ITERATIONS = 600_000
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8
DEFAULTS = {"enabled": True, "session_hours": 12, "max_failed_logins": 5, "lockout_minutes": 15}
ONLINE_MINUTES = 3          # a session seen within this delay counts as online (the page sends a beat every minute)
TOUCH_SECONDS = 50          # do not rewrite the file more often than this for the same session
_LOCK = threading.Lock()


class AuthError(ValueError):
    """A request that cannot be done (shown to the user as is)."""


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("auth", {}) or {})
    out = dict(DEFAULTS)
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    for k in ("session_hours", "max_failed_logins", "lockout_minutes"):
        if raw.get(k) is not None:
            out[k] = max(1, int(raw[k]))
    if os.environ.get("CARBONSYNC_AUTH", "").strip().lower() in ("off", "0", "false", "no"):
        out["enabled"] = False
    return out


def can(role: str | None, action: str) -> bool:
    need = PERMISSIONS.get(action, "viewer")
    return RANK.get(role or "", -1) >= RANK[need]


def at_least(role: str | None, minimum: str) -> bool:
    return RANK.get(role or "", -1) >= RANK[minimum]


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {"algo": "pbkdf2_sha256", "iterations": iterations, "salt": salt.hex(), "hash": digest.hex()}


def verify_password(password: str, rec: dict[str, Any]) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rec["iterations"]))
        return hmac.compare_digest(digest.hex(), rec["hash"])
    except (KeyError, ValueError, TypeError):
        return False


_DUMMY = hash_password("not-a-password", salt=b"\x00" * 16, iterations=ITERATIONS)


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"the password needs at least {MIN_PASSWORD} characters")
    if password.strip() != password:
        raise AuthError("the password cannot start or end with a space")


def normalise_username(username: str) -> str:
    u = (username or "").strip().lower()
    if not USERNAME.match(u):
        raise AuthError("user name: 3 to 32 characters, lowercase letters, digits, dot, dash or underscore")
    return u


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class AuthStore:
    """``data/auth/users.json``: users and sessions. Every change is written atomically."""

    def __init__(self, root: Path | None = None, cfg: dict[str, Any] | None = None):
        self.root = root or get_project_root()
        self.path = self.root / "data" / "auth" / "users.json"
        self.s = settings(cfg)

    # -- low level -----------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}, "sessions": {}}
        except (OSError, json.JSONDecodeError) as err:
            raise AuthError(f"the user file cannot be read ({err}); restore data/auth/users.json") from err
        data.setdefault("users", {})
        data.setdefault("sessions", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        data["sessions"] = {k: v for k, v in data.get("sessions", {}).items()
                            if datetime.fromisoformat(v["expires"]) > now and v.get("user") in data["users"]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def public(username: str, u: dict[str, Any]) -> dict[str, Any]:
        return {"username": username, "name": u.get("name") or username, "role": u.get("role", "viewer"),
                "created_at": u.get("created_at"), "last_login": u.get("last_login"),
                "locked_until": u.get("locked_until"), "failed": int(u.get("failed", 0))}

    # -- users ----------------------------------------------------------------
    def has_users(self) -> bool:
        return bool(self._read()["users"])

    def list_users(self) -> list[dict[str, Any]]:
        users = self._read()["users"]
        return [self.public(k, v) for k, v in sorted(users.items())]

    def get(self, username: str) -> dict[str, Any] | None:
        u = self._read()["users"].get((username or "").strip().lower())
        return self.public(username.strip().lower(), u) if u else None

    def create_user(self, username: str, password: str, role: str = "viewer", name: str = "") -> dict[str, Any]:
        u = normalise_username(username)
        if role not in ROLES:
            raise AuthError(f"unknown role '{role}' (viewer, editor, admin)")
        check_password_rules(password)
        with _LOCK:
            data = self._read()
            if u in data["users"]:
                raise AuthError(f"the user '{u}' already exists")
            if not data["users"] and role != "admin":
                raise AuthError("the first user must be an administrator")
            data["users"][u] = {"name": (name or "").strip()[:60] or u, "role": role, "password": hash_password(password),
                                "created_at": datetime.now().isoformat(timespec="seconds"), "failed": 0,
                                "locked_until": None, "last_login": None}
            self._write(data)
        log.info("Auth: user '%s' created (%s)", u, role)
        return self.public(u, data["users"][u])

    def _admins(self, data: dict[str, Any]) -> list[str]:
        return [k for k, v in data["users"].items() if v.get("role") == "admin"]

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise AuthError(f"unknown role '{role}'")
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            if data["users"][u]["role"] == "admin" and role != "admin" and len(self._admins(data)) == 1:
                raise AuthError("the last administrator cannot lose the role")
            data["users"][u]["role"] = role
            self._write(data)
        log.info("Auth: role of '%s' set to %s", u, role)

    def set_name(self, username: str, name: str) -> None:
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            data["users"][u]["name"] = (name or "").strip()[:60] or u
            self._write(data)

    def set_password(self, username: str, password: str) -> None:
        """Administrator reset (no old password); ends the user's sessions and unlocks the account."""
        check_password_rules(password)
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            data["users"][u].update(password=hash_password(password), failed=0, locked_until=None)
            data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("user") != u}
            self._write(data)
        log.info("Auth: password of '%s' reset", u)

    def change_password(self, username: str, old: str, new: str, keep_token: str | None = None) -> None:
        """The user's own change; their other sessions end (``keep_token``: the session making the change)."""
        check_password_rules(new)
        keep = self._token_key(keep_token) if isinstance(keep_token, str) and keep_token else None
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            if not verify_password(old, data["users"][u]["password"]):
                raise AuthError("the current password is wrong")
            data["users"][u]["password"] = hash_password(new)
            data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("user") != u or k == keep}
            self._write(data)
        log.info("Auth: '%s' changed their password", u)

    def unlock(self, username: str) -> None:
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            data["users"][u].update(failed=0, locked_until=None)
            self._write(data)

    def delete_user(self, username: str) -> None:
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            if data["users"][u]["role"] == "admin" and len(self._admins(data)) == 1:
                raise AuthError("the last administrator cannot be deleted")
            del data["users"][u]
            self._write(data)
        log.info("Auth: user '%s' deleted", u)

    @staticmethod
    def _existing(data: dict[str, Any], username: str) -> str:
        u = (username or "").strip().lower()
        if u not in data["users"]:
            raise AuthError(f"no user '{u}'")
        return u

    # -- login and sessions -------------------------------------------------------
    def authenticate(self, username: str, password: str) -> tuple[dict[str, Any] | None, str]:
        """(user, "ok") or (None, "unknown" | "bad_password" | "locked"). Same work whether or not the user exists."""
        u = (username or "").strip().lower()
        with _LOCK:
            data = self._read()
            rec = data["users"].get(u)
            if rec is None:
                verify_password(password or "", _DUMMY)
                log.info("Auth: failed login (unknown user)")
                return None, "unknown"
            now = datetime.now()
            if rec.get("locked_until") and datetime.fromisoformat(rec["locked_until"]) > now:
                log.info("Auth: login refused for '%s' (locked)", u)
                return None, "locked"
            if not verify_password(password or "", rec["password"]):
                rec["failed"] = int(rec.get("failed", 0)) + 1
                if rec["failed"] >= self.s["max_failed_logins"]:
                    rec["locked_until"] = (now + timedelta(minutes=self.s["lockout_minutes"])).isoformat(timespec="seconds")
                    rec["failed"] = 0
                    log.warning("Auth: '%s' locked for %d min after repeated failures", u, self.s["lockout_minutes"])
                self._write(data)
                return None, "locked" if rec.get("locked_until") and datetime.fromisoformat(rec["locked_until"]) > now else "bad_password"
            rec.update(failed=0, locked_until=None, last_login=now.isoformat(timespec="seconds"))
            self._write(data)
        log.info("Auth: '%s' logged in (%s)", u, rec["role"])
        return self.public(u, rec), "ok"

    @staticmethod
    def _token_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with _LOCK:
            data = self._read()
            u = self._existing(data, username)
            data["sessions"][self._token_key(token)] = {
                "user": u, "created": datetime.now().isoformat(timespec="seconds"),
                "seen": datetime.now().isoformat(timespec="seconds"),
                "expires": (datetime.now() + timedelta(hours=self.s["session_hours"])).isoformat(timespec="seconds")}
            self._write(data)
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not isinstance(token, str) or not token or len(token) > 200:
            return None
        data = self._read()
        sess = data["sessions"].get(self._token_key(token))
        if not sess or datetime.fromisoformat(sess["expires"]) <= datetime.now():
            return None
        rec = data["users"].get(sess["user"])
        return self.public(sess["user"], rec) if rec else None

    def touch(self, token: str | None) -> None:
        """Marks the session as seen now (presence). Written at most every ``TOUCH_SECONDS``."""
        if not isinstance(token, str) or not token or len(token) > 200:
            return
        key, now = self._token_key(token), datetime.now()
        with _LOCK:
            data = self._read()
            sess = data["sessions"].get(key)
            if not sess or datetime.fromisoformat(sess["expires"]) <= now:
                return
            seen = sess.get("seen")
            if seen and (now - datetime.fromisoformat(seen)).total_seconds() < TOUCH_SECONDS:
                return
            sess["seen"] = now.isoformat(timespec="seconds")
            self._write(data)

    def presence(self, online_minutes: int = ONLINE_MINUTES) -> list[dict[str, Any]]:
        """Every user with ``online`` (a live session seen in the last minutes) and ``last_seen``."""
        data = self._read()
        now = datetime.now()
        seen: dict[str, datetime] = {}
        for sess in data["sessions"].values():
            if datetime.fromisoformat(sess["expires"]) <= now:
                continue
            stamp = datetime.fromisoformat(sess.get("seen") or sess.get("created"))
            if sess.get("user") not in seen or stamp > seen[sess["user"]]:
                seen[sess["user"]] = stamp
        out = []
        for username, rec in sorted(data["users"].items()):
            pub = self.public(username, rec)
            last = seen.get(username)
            pub["online"] = bool(last and (now - last).total_seconds() <= online_minutes * 60)
            login = rec.get("last_login")
            pub["last_seen"] = (last.isoformat(timespec="seconds") if last else login)
            out.append(pub)
        out.sort(key=lambda u: (not u["online"], u["name"].lower()))
        return out

    def end_session(self, token: str | None) -> None:
        if not isinstance(token, str) or not token:
            return
        with _LOCK:
            data = self._read()
            if data["sessions"].pop(self._token_key(token), None) is not None:
                self._write(data)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def _ask_password() -> str:
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat: "):
        raise AuthError("the two passwords differ")
    return first


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m carbonsync.auth", description="Manage CarbonSync dashboard users.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the users")
    a = sub.add_parser("add", help="create a user (asks for the password)")
    a.add_argument("username")
    a.add_argument("--role", choices=ROLES, default="viewer")
    a.add_argument("--name", default="")
    for name, hlp in (("passwd", "set a new password"), ("unlock", "unlock after failed logins"), ("delete", "delete a user")):
        sub.add_parser(name, help=hlp).add_argument("username")
    r = sub.add_parser("role", help="change a user's role")
    r.add_argument("username")
    r.add_argument("role", choices=ROLES)
    args = p.parse_args(argv)
    store = AuthStore()
    try:
        if args.cmd == "list":
            users = store.list_users()
            if not users:
                print("No user yet: the dashboard will ask for the first administrator.")
            for x in users:
                lock = f", locked until {x['locked_until']}" if x["locked_until"] else ""
                print(f"{x['username']:<20} {x['role']:<7} {x['name']} (last login {x['last_login'] or 'never'}{lock})")
        elif args.cmd == "add":
            store.create_user(args.username, _ask_password(), args.role, args.name)
            print(f"User '{args.username.lower()}' created ({args.role}).")
        elif args.cmd == "passwd":
            store.set_password(args.username, _ask_password())
            print("Password changed; the user's open sessions are closed.")
        elif args.cmd == "role":
            store.set_role(args.username, args.role)
            print(f"Role of '{args.username.lower()}' set to {args.role}.")
        elif args.cmd == "unlock":
            store.unlock(args.username)
            print("Unlocked.")
        elif args.cmd == "delete":
            store.delete_user(args.username)
            print("Deleted.")
    except AuthError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
