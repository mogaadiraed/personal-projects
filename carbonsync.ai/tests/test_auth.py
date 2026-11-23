"""Users, passwords, sessions, lockout and permissions (carbonsync.auth)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from carbonsync import auth

CFG = {"auth": {"enabled": True, "session_hours": 12, "max_failed_logins": 3, "lockout_minutes": 15}}


@pytest.fixture
def store(tmp_path):
    return auth.AuthStore(tmp_path, CFG)


def _file(tmp_path) -> dict:
    return json.loads((tmp_path / "data" / "auth" / "users.json").read_text(encoding="utf-8"))


def test_password_hash_round_trip():
    rec = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", rec) and not auth.verify_password("wrong horse", rec)
    assert "correct horse" not in json.dumps(rec) and rec["iterations"] >= 600_000
    assert auth.hash_password("correct horse")["hash"] != rec["hash"]          # random salt


def test_first_user_must_be_an_administrator(store):
    assert not store.has_users()
    with pytest.raises(auth.AuthError):
        store.create_user("vera", "viewer-pass", "viewer")
    store.create_user("Admin", "admin-pass", "admin", "Site Admin")
    assert store.has_users() and store.get("admin")["role"] == "admin"        # names are lower-cased


@pytest.mark.parametrize(("username", "password"), [("a", "long-enough"), ("bad name", "long-enough"),
                                                    ("good.name", "short")])
def test_bad_user_names_and_short_passwords_are_refused(store, username, password):
    store.create_user("admin", "admin-pass", "admin")
    with pytest.raises(auth.AuthError):
        store.create_user(username, password, "viewer")


def test_duplicate_and_unknown_role_are_refused(store):
    store.create_user("admin", "admin-pass", "admin")
    with pytest.raises(auth.AuthError):
        store.create_user("ADMIN", "other-pass", "viewer")
    with pytest.raises(auth.AuthError):
        store.create_user("eve", "other-pass", "root")


def test_authenticate_and_lockout(store):
    store.create_user("admin", "admin-pass", "admin")
    store.create_user("editor1", "editor-pass", "editor")
    assert store.authenticate("nobody", "x") == (None, "unknown")
    user, why = store.authenticate("EDITOR1", "editor-pass")
    assert why == "ok" and user["role"] == "editor" and user["last_login"]
    for _ in range(2):
        assert store.authenticate("editor1", "nope")[1] == "bad_password"
    assert store.authenticate("editor1", "nope")[1] == "locked"                  # the 3rd failure locks
    assert store.authenticate("editor1", "editor-pass") == (None, "locked")      # right password, still locked
    assert store.get("editor1")["locked_until"]
    store.unlock("editor1")
    assert store.authenticate("editor1", "editor-pass")[1] == "ok"


def test_sessions_follow_role_changes_and_password_resets(store, tmp_path):
    store.create_user("admin", "admin-pass", "admin")
    store.create_user("editor1", "editor-pass", "editor")
    token = store.create_session("editor1")
    assert store.session_user(token)["role"] == "editor"
    assert token not in json.dumps(_file(tmp_path))                            # only its sha256 is kept
    store.set_role("editor1", "viewer")
    assert store.session_user(token)["role"] == "viewer"                       # applies at once
    store.set_password("editor1", "new-editor-pass")
    assert store.session_user(token) is None                                    # reset closes the sessions
    token = store.create_session("editor1")
    store.end_session(token)
    assert store.session_user(token) is None
    for bad in (None, "", 123, "x" * 500, "unknown-token"):
        assert store.session_user(bad) is None


def test_expired_sessions_are_refused(store, tmp_path):
    store.create_user("admin", "admin-pass", "admin")
    token = store.create_session("admin")
    data = _file(tmp_path)
    for s in data["sessions"].values():
        s["expires"] = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    (tmp_path / "data" / "auth" / "users.json").write_text(json.dumps(data), encoding="utf-8")
    assert store.session_user(token) is None


def test_change_password_needs_the_current_one(store):
    store.create_user("admin", "admin-pass", "admin")
    with pytest.raises(auth.AuthError):
        store.change_password("admin", "wrong-pass", "brand-new-pass")
    store.change_password("admin", "admin-pass", "brand-new-pass")
    assert store.authenticate("admin", "brand-new-pass")[1] == "ok"


def test_the_last_administrator_is_protected(store):
    store.create_user("admin", "admin-pass", "admin")
    with pytest.raises(auth.AuthError):
        store.set_role("admin", "editor")
    with pytest.raises(auth.AuthError):
        store.delete_user("admin")
    store.create_user("boss", "boss-pass-1", "admin")
    store.set_role("admin", "editor")                                           # another admin exists now
    store.delete_user("admin")
    assert [u["username"] for u in store.list_users()] == ["boss"]


def test_permissions_by_role():
    assert not auth.can("viewer", "upload") and auth.can("editor", "upload") and auth.can("admin", "upload")
    assert auth.can("editor", "settings_targets") and not auth.can("editor", "users")
    assert not auth.can("editor", "reset_data") and auth.can("admin", "reset_data")
    assert all(auth.can("admin", a) for a in auth.PERMISSIONS)
    assert not auth.can(None, "upload") and not auth.can("hacker", "upload")
    assert auth.can("viewer", "unknown_action")                                 # read-only by default
    assert auth.at_least("editor", "viewer") and not auth.at_least("viewer", "editor")


def test_login_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("CARBONSYNC_AUTH", raising=False)
    assert auth.settings(CFG)["enabled"] and auth.settings({})["enabled"]
    assert not auth.settings({"auth": {"enabled": False}})["enabled"]
    monkeypatch.setenv("CARBONSYNC_AUTH", "off")
    assert not auth.settings(CFG)["enabled"]


def test_cli_list_and_role(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(auth, "get_project_root", lambda: tmp_path)
    assert auth.main(["list"]) == 0 and "No user yet" in capsys.readouterr().out
    monkeypatch.setattr(auth, "_ask_password", lambda: "admin-pass")
    assert auth.main(["add", "admin", "--role", "admin"]) == 0
    assert auth.main(["add", "editor1", "--role", "editor"]) == 0
    assert auth.main(["role", "editor1", "viewer"]) == 0
    assert auth.main(["role", "admin", "viewer"]) == 1                          # last admin
    out = capsys.readouterr()
    assert "viewer" in out.out and "Error" in out.err


def test_presence_online_offline_and_last_seen(store, tmp_path):
    store.create_user("admin", "admin-pass", "admin", "Amira")
    store.create_user("viewer1", "viewer-pass", "viewer", "Sami")
    token = store.create_session("admin")
    team = {u["username"]: u for u in store.presence()}
    assert team["admin"]["online"] and not team["viewer1"]["online"] and team["viewer1"]["last_seen"] is None
    data = _file(tmp_path)                                     # the admin's page was closed 10 minutes ago
    for sess in data["sessions"].values():
        sess["seen"] = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    (tmp_path / "data" / "auth" / "users.json").write_text(json.dumps(data), encoding="utf-8")
    assert not {u["username"]: u for u in store.presence()}["admin"]["online"]
    store.touch(token)                                         # the page beats again
    assert {u["username"]: u for u in store.presence()}["admin"]["online"]
    store.end_session(token)                                   # logged out
    assert not {u["username"]: u for u in store.presence()}["admin"]["online"]


def test_changing_the_password_closes_the_other_sessions(store):
    store.create_user("admin", "admin-pass", "admin")
    here, elsewhere = store.create_session("admin"), store.create_session("admin")
    store.change_password("admin", "admin-pass", "brand-new-pass", keep_token=here)
    assert store.session_user(here) is not None and store.session_user(elsewhere) is None
