"""API keys of the cloud providers (Ollama Cloud, Claude, …), kept in the Windows Credential Manager.

* Stored with ``keyring`` (Windows Credential Manager on Windows: encrypted by Windows and tied
  to the Windows user account). Never in ``config.yaml``, git, logs, exports or saved
  conversations; the logs blank anything that looks like a key (``carbonsync.obs.redact``).
* The provider names are fixed (``providers.registry``), so nothing needs listing in the vault.
* An environment variable (``ANTHROPIC_API_KEY``, ``OLLAMA_API_KEY``…) is used when no key is stored.
* Keys are only ever shown masked: ``sk-ant-…a1b2``, or ``…a1b2`` for keys without a prefix.
* Every key read or stored is registered with ``obs.add_secret``, so the logs blank it by value
  too (Ollama keys have no prefix to recognise).
"""
from __future__ import annotations

import os
from typing import Any

from carbonsync.assistant.providers.registry import keyed
from carbonsync.obs import add_secret, get_logger

log = get_logger(__name__)

SERVICE = "CarbonSync AI"
PROVIDERS: dict[str, dict[str, Any]] = keyed()


class VaultError(RuntimeError):
    """A key could not be stored, read or deleted, in FR and EN."""

    def __init__(self, fr: str, en: str) -> None:
        super().__init__(en)
        self.fr, self.en = fr, en

    def text(self, lang: str) -> str:
        return self.fr if lang == "fr" else self.en


def _backend() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise VaultError("Le paquet Python « keyring » manque : pip install keyring",
                         "The Python package 'keyring' is missing: pip install keyring") from exc
    return keyring


def _spec(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise VaultError(f"Fournisseur inconnu : {provider}", f"Unknown provider: {provider}")
    return PROVIDERS[provider]


def mask(key: str | None) -> str:
    """``sk-ant-api03-…`` → ``sk-ant-…a1b2``; a key without a known prefix → ``…a1b2`` (never more than
    the prefix and the last 4 characters)."""
    if not key:
        return ""
    prefixes = sorted((p["prefix"] for p in PROVIDERS.values() if p.get("prefix")), key=len, reverse=True)
    prefix = next((p for p in prefixes if key.startswith(p)), "")
    return f"{prefix}…{key[-4:]}" if len(key) > len(prefix) + 8 else "…"


def check_format(provider: str, key: str) -> str:
    """The cleaned key, or a ``VaultError`` saying what is wrong (without repeating the key)."""
    spec = _spec(provider)
    key = (key or "").strip()
    if not key:
        raise VaultError("La clé est vide.", "The key is empty.")
    if any(c.isspace() for c in key) or not key.isascii():
        raise VaultError("La clé contient des espaces ou des caractères inattendus : recopiez-la depuis la console.",
                         "The key contains spaces or unexpected characters: copy it again from the console.")
    if spec.get("prefix") and not key.startswith(spec["prefix"]):
        raise VaultError(f"Une clé {spec['label']} commence par « {spec['prefix']} ».",
                         f"A {spec['label']} key starts with '{spec['prefix']}'.")
    if not spec.get("min_len", 40) <= len(key) <= 400:
        raise VaultError("La longueur de la clé ne correspond pas à une clé API : recopiez-la en entier.",
                         "The key length does not match an API key: copy the whole key.")
    return key


def set_key(provider: str, key: str) -> str:
    """Stores the key in the Credential Manager; returns its masked form."""
    key = check_format(provider, key)
    try:
        _backend().set_password(SERVICE, provider, key)
    except VaultError:
        raise
    except Exception as exc:
        log.warning("Vault: could not store the %s key (%s)", provider, type(exc).__name__)
        raise VaultError("Impossible d'enregistrer la clé dans le Gestionnaire d'identification Windows.",
                         "Could not store the key in the Windows Credential Manager.") from exc
    add_secret(key)
    log.info("Vault: %s key stored (%s)", provider, mask(key))
    return mask(key)


def stored_key(provider: str) -> str | None:
    _spec(provider)
    try:
        return _backend().get_password(SERVICE, provider) or None
    except VaultError:
        raise
    except Exception as exc:  # noqa: BLE001 - any backend failure means "no usable stored key"
        log.warning("Vault: could not read the %s key (%s)", provider, type(exc).__name__)
        return None


def get_key(provider: str) -> tuple[str | None, str]:
    """(key, source): source is ``"vault"``, ``"env"`` (environment variable) or ``"none"``."""
    spec = _spec(provider)
    try:
        key = stored_key(provider)
    except VaultError:
        key = None
    if key:
        add_secret(key)
        return key, "vault"
    env = os.environ.get(spec["env"], "").strip()
    if env:
        add_secret(env)
        return env, "env"
    return None, "none"


def delete_key(provider: str) -> bool:
    """Removes the stored key; False when there was none (an environment variable is left alone)."""
    _spec(provider)
    backend = _backend()
    try:
        if not backend.get_password(SERVICE, provider):
            return False
        backend.delete_password(SERVICE, provider)
    except Exception as exc:
        log.warning("Vault: could not delete the %s key (%s)", provider, type(exc).__name__)
        raise VaultError("Impossible de supprimer la clé du Gestionnaire d'identification Windows.",
                         "Could not delete the key from the Windows Credential Manager.") from exc
    log.info("Vault: %s key deleted", provider)
    return True


def status(provider: str) -> dict[str, Any]:
    """For the settings page: is there a key, where from, its masked form, and the backend name."""
    key, source = get_key(provider)
    try:
        backend = type(_backend().get_keyring()).__name__
    except Exception:  # noqa: BLE001 - informative only
        backend = "unavailable"
    return {"provider": provider, "has_key": bool(key), "source": source, "masked": mask(key),
            "env": _spec(provider)["env"], "backend": backend}
