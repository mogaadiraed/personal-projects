"""OpenRouter: free models (ids ending in ``:free``) from many publishers through one API key
(IA & API → OpenRouter), on the OpenAI-compatible adapter.

* API: ``https://openrouter.ai/api/v1`` (Chat Completions), ``Authorization: Bearer sk-or-v1-…``.
  No attribution headers are sent (they would name this project to OpenRouter).
* Free models: 20 requests per minute; 50 requests per day, or 1,000 per day once 10 credits have
  been bought (openrouter.ai/docs/api-reference/limits, 2026-11-20). One question takes 2 to 4
  requests (tool rounds).
* Optional fallback: OpenRouter's ``models`` list, with free models only, so a rate-limited or
  unavailable model hands over to the next one without any cost.
* Privacy: OpenRouter does not store prompts by default; each free model's own provider may log
  or train on them. Only aggregated data is sent (Expert rules), and the page offers
  ``provider.data_collection = "deny"``.
* The top 5 were picked on 2026-11-20 from the public model list: free, tools, reasoning and
  ``tool_choice: "none"`` supported. The list of free models can be refreshed from the page.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from carbonsync.assistant import usage, vault
from carbonsync.assistant.context import get_project_root
from carbonsync.assistant.providers.base import ProviderError, Turn
from carbonsync.assistant.providers.openai_compat import OpenAICompatProvider
from carbonsync.obs import get_logger

log = get_logger(__name__)

PROVIDER = "openrouter"
API = "https://openrouter.ai/api/v1"
FREE_LIMITS = {"per_minute": 20, "per_day": 50, "per_day_with_credits": 1000, "credits_threshold": 10}
MAX_FALLBACKS = 2

TOP5: list[dict[str, Any]] = [
    {"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "label": "Nemotron 3 Ultra", "publisher": "NVIDIA",
     "host": "Nvidia", "context": "1M", "tags": ["tools", "thinking"],
     "role": ("recommandé · raisonnement", "recommended · reasoning"),
     "note": (("Le plus grand modèle gratuit avec outils : 550 milliards de paramètres (55 actifs), conçu pour le "
              "raisonnement et l'orchestration d'outils."),
              ("The largest free model with tools: 550 billion parameters (55 active), built for reasoning and tool "
              "orchestration."))},
    {"model": "google/gemma-4-31b-it:free", "label": "Gemma 4 31B", "publisher": "Google DeepMind",
     "host": "Google AI Studio", "context": "256K", "tags": ["tools", "thinking", "vision"],
     "role": ("multilingue · Google", "multilingual · Google"),
     "note": ("Modèle dense de Google, appel d'outils natif et réflexion réglable. Servi par Google AI Studio.",
              "Google's dense model, native tool calling and adjustable reasoning. Served by Google AI Studio.")},
    {"model": "nvidia/nemotron-3.5-super-vl:free", "label": "Nemotron 3.5 Super VL", "publisher": "NVIDIA",
     "host": "Nvidia", "context": "1M", "tags": ["tools", "thinking", "vision"],
     "role": ("équilibré · 1M de contexte", "balanced · 1M context"),
     "note": ("120 milliards de paramètres (12 actifs) : plus rapide que Nemotron 3 Ultra, lit aussi les images.",
              "120 billion parameters (12 active): faster than Nemotron 3 Ultra, also reads images.")},
    {"model": "inclusionai/ling-3.2-flash:free", "label": "Ling 3.2 Flash", "publisher": "inclusionAI",
     "host": "Ant Digital", "context": "1M", "tags": ["tools", "thinking", "vision"],
     "role": ("réflexion hybride", "hybrid reasoning"),
     "note": ("Réflexion activable selon la question, contexte d'un million de jetons. Le plus récent des cinq.",
              "Reasoning that can be switched on per question, one-million-token context. The newest of the five.")},
    {"model": "google/gemma-4-26b-a4b-it:free", "label": "Gemma 4 26B A4B", "publisher": "Google DeepMind",
     "host": "Google AI Studio", "context": "256K", "tags": ["tools", "thinking", "vision"],
     "role": ("rapide", "fast"),
     "note": ("Mélange d'experts : 3,8 milliards de paramètres actifs, donc rapide, pour une qualité proche du 31B.",
              "Mixture of experts: 3.8 billion active parameters, so fast, at quality close to the 31B.")},
]
TOP5_MODELS = [m["model"] for m in TOP5]
DEFAULT_MODEL = TOP5_MODELS[0]

# free models with tools and tool_choice, from the public list on 2026-11-20 (refreshable on the page)
SNAPSHOT: list[dict[str, Any]] = [
    {"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "name": "NVIDIA: Nemotron 3 Ultra", "context": 1_000_000},
    {"model": "google/gemma-4-31b-it:free", "name": "Google: Gemma 4 31B", "context": 262_144},
    {"model": "nvidia/nemotron-3.5-super-vl:free", "name": "NVIDIA: Nemotron 3.5 Super VL", "context": 1_000_000},
    {"model": "inclusionai/ling-3.2-flash:free", "name": "inclusionAI: Ling 3.2 Flash", "context": 1_048_576},
    {"model": "google/gemma-4-26b-a4b-it:free", "name": "Google: Gemma 4 26B A4B", "context": 262_144},
    {"model": "baidu/ernie-5.5-flash-preview:free", "name": "Baidu: ERNIE-5.5-Flash-Preview", "context": 1_024_000},
    {"model": "nvidia/nemotron-3.5-lightning:free", "name": "NVIDIA: Nemotron 3.5 Lightning", "context": 1_000_000},
    {"model": "particle-ai/particle-1:free", "name": "Particle AI: Particle-1", "context": 1_048_576},
    {"model": "dots-studio/dots-3-note-preview:free", "name": "Dots Studio: Dots3-Note Preview", "context": 512_000},
    {"model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "NVIDIA: Nemotron 3 Nano Omni",
     "context": 256_000},
    {"model": "liquid/lfm-3-1.7b:free", "name": "LiquidAI: LFM3-1.7B", "context": 262_144},
]
_LABELS = {m["model"]: m["label"] for m in TOP5}


def model_label(model: str) -> str:
    if model in _LABELS:
        return _LABELS[model]
    for m in SNAPSHOT:
        if m["model"] == model:
            return m["name"].split(": ", 1)[-1]
    return model.split("/", 1)[-1].removesuffix(":free")


def is_free(model: str) -> bool:
    return model.endswith(":free")


# ---------------------------------------------------------------------------
# The free model list (public endpoint, no key)
# ---------------------------------------------------------------------------
def _list_path(root: Path | None = None) -> Path:
    d = (root or get_project_root()) / "data" / "assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d / "openrouter_models.json"


def free_models(root: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """(models, as-of): the last refreshed list, or the built-in one of 2026-11-20."""
    path = _list_path(root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("models"), list) and data["models"]:
                return data["models"], str(data.get("fetched", ""))
        except (ValueError, OSError):
            log.warning("OpenRouter: unreadable model list, built-in list used")
    return SNAPSHOT, "2026-11-20"


def parse_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Free models that take tools and ``tool_choice`` (the assistant needs both), the router excluded."""
    out = []
    for m in payload.get("data", []):
        mid = str(m.get("id", ""))
        params = m.get("supported_parameters") or []
        if not mid.endswith(":free") or mid.startswith("openrouter/") or "tools" not in params or "tool_choice" not in params:
            continue
        out.append({"model": mid, "name": str(m.get("name") or mid).replace(" (free)", ""),
                    "context": int(m.get("context_length") or 0), "reasoning": "reasoning" in params})
    return sorted(out, key=lambda x: (x["model"] not in TOP5_MODELS, TOP5_MODELS.index(x["model"])
                                      if x["model"] in TOP5_MODELS else 0, x["name"]))


def refresh_free_models(root: Path | None = None, transport: Any = None) -> dict[str, Any]:
    """Reads the public model list (no key, free) and keeps the free models with tools."""
    import httpx

    try:
        with httpx.Client(timeout=20.0, transport=transport) as http:
            r = http.get(f"{API}/models")
        r.raise_for_status()
        models = parse_models(r.json())
    except Exception as exc:  # noqa: BLE001 - reported on the page
        log.warning("OpenRouter: model list refresh failed (%s)", type(exc).__name__)
        return {"ok": False, "fr": "Impossible de lire la liste des modèles d'OpenRouter : vérifiez la connexion.",
                "en": "Could not read OpenRouter's model list: check the connection."}
    if not models:
        return {"ok": False, "fr": "La liste d'OpenRouter ne contient aucun modèle gratuit avec outils.",
                "en": "OpenRouter's list has no free model with tools."}
    fetched = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = _list_path(root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched": fetched, "models": models}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    missing = [m for m in TOP5_MODELS if m not in {x["model"] for x in models}]
    log.info("OpenRouter: %d free models with tools (missing from the top 5: %s)", len(models), missing or "none")
    return {"ok": True, "count": len(models), "missing": missing,
            "fr": f"Liste mise à jour : {len(models)} modèles gratuits avec outils."
                  + (f" Plus disponibles : {', '.join(model_label(m) for m in missing)}." if missing else ""),
            "en": f"List updated: {len(models)} free models with tools."
                  + (f" No longer available: {', '.join(model_label(m) for m in missing)}." if missing else "")}


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------
class OpenRouterProvider(OpenAICompatProvider):
    key = PROVIDER
    label = "OpenRouter"
    base_url = API

    def model_label(self) -> str:
        return model_label(self.model)

    def _options(self) -> dict[str, Any]:
        return (self.settings or {}).get(PROVIDER, {})

    def _extra_body(self, think: bool, final: bool) -> dict[str, Any]:
        opts = self._options()
        extra: dict[str, Any] = {}
        if think:
            extra["reasoning"] = {"enabled": True}
        if opts.get("fallback", True) and is_free(self.model):
            backups = [m for m in TOP5_MODELS if m != self.model][:MAX_FALLBACKS]
            extra["models"] = [self.model, *backups]  # free models only, so a fallback never costs anything
        if opts.get("deny_collection"):
            extra["provider"] = {"data_collection": "deny"}
        return extra

    def _status_error(self, code: int, detail: str) -> ProviderError:
        low = detail.lower()
        if code == 404 and any(w in low for w in ("data policy", "privacy", "guardrail", "no endpoints")):
            return ProviderError(
                "Aucun fournisseur ne sert ce modèle avec vos réglages de confidentialité OpenRouter : autorisez les "
                "modèles gratuits dans openrouter.ai/settings/privacy, ou désactivez « Refuser la collecte » dans "
                "IA & API → OpenRouter.",
                "No provider serves this model with your OpenRouter privacy settings: allow free models in "
                "openrouter.ai/settings/privacy, or turn off \"Deny data collection\" in AI & API → OpenRouter.")
        if code == 429:
            return ProviderError(
                "Limite des modèles gratuits atteinte (20 requêtes par minute ; 50 par jour, ou 1 000 par jour après "
                "un achat de 10 crédits). Réessayez plus tard ou choisissez un autre modèle.",
                "Free model limit reached (20 requests per minute; 50 per day, or 1,000 per day after buying 10 "
                "credits). Try again later or pick another model.")
        if code == 402:
            return ProviderError("Solde OpenRouter négatif : même les modèles gratuits sont bloqués tant qu'il n'est "
                                 "pas remis à zéro (openrouter.ai/settings/credits).",
                                 "OpenRouter balance is negative: even free models are blocked until it is back to "
                                 "zero (openrouter.ai/settings/credits).")
        return super()._status_error(code, detail)


# ---------------------------------------------------------------------------
# Account checks
# ---------------------------------------------------------------------------
def _key_info_text(data: dict[str, Any]) -> tuple[str, str]:
    fr, en = [], []
    daily = data.get("free_model_daily_requests") or {}
    if isinstance(daily.get("limit"), int | float):
        used = int(daily.get("used") or 0)
        fr.append(f"modèles gratuits aujourd'hui : {used} / {int(daily['limit'])} requêtes")
        en.append(f"free models today: {used} / {int(daily['limit'])} requests")
    if isinstance(data.get("limit_remaining"), int | float):
        fr.append(f"crédit restant sur la clé : {data['limit_remaining']:.2f} $")
        en.append(f"credit left on the key: ${data['limit_remaining']:.2f}")
    if data.get("is_free_tier") is True:
        fr.append("compte gratuit (aucun crédit acheté)")
        en.append("free account (no credit bought)")
    return "; ".join(fr), "; ".join(en)


def check_connection(api_key: str | None = None, transport: Any = None) -> dict[str, Any]:
    """Checks the key with ``GET /api/v1/key`` (no tokens used) and reads today's free-model quota."""
    import httpx

    key = api_key or vault.get_key(PROVIDER)[0]
    if not key:
        return {"ok": False, "fr": "Aucune clé OpenRouter : ajoutez-la ci-dessus.",
                "en": "No OpenRouter key: add it above.", "info": None}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=15.0, transport=transport,
                          headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}) as http:
            r = http.get(f"{API}/key")
    except Exception as exc:  # noqa: BLE001 - reported on the page
        log.warning("OpenRouter: connection test failed (%s)", type(exc).__name__)
        return {"ok": False, "fr": "Impossible de joindre openrouter.ai : vérifiez la connexion Internet.",
                "en": "Cannot reach openrouter.ai: check the Internet connection.", "info": None}
    ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code != 200:
        log.warning("OpenRouter: connection test returned %d", r.status_code)
        err = OpenRouterProvider("—")._status_error(r.status_code, "")
        return {"ok": False, "fr": err.fr, "en": err.en, "info": None}
    try:
        data = (r.json() or {}).get("data") or {}
    except ValueError:
        data = {}
    tfr, ten = _key_info_text(data)
    log.info("OpenRouter: key OK (%d ms)", ms)
    return {"ok": True, "info": data,
            "fr": f"Connexion réussie à OpenRouter ({ms} ms)." + (f" {tfr[0].upper()}{tfr[1:]}." if tfr else ""),
            "en": f"Connected to OpenRouter ({ms} ms)." + (f" {ten[0].upper()}{ten[1:]}." if ten else "")}


def quick_test(model: str, client: Any = None, root: Any = None) -> dict[str, Any]:
    """One tiny question to ``model`` (free models: no cost, one request of the daily quota)."""
    settings = usage.load_settings(root)
    settings[PROVIDER] = {**settings.get(PROVIDER, {}), "fallback": False}  # test this model, not a fallback
    p = OpenRouterProvider(model, settings=settings, root=root, client=client, timeout=120.0)
    t0 = time.perf_counter()
    try:
        turn: Turn | None = None
        for item in p.stream("Answer with one word.", [{"role": "user", "content": "Réponds seulement : OK"}], []):
            if isinstance(item, Turn):
                turn = item
    except ProviderError as exc:
        return {"ok": False, "fr": exc.fr, "en": exc.en}
    secs = time.perf_counter() - t0
    if turn is None:
        return {"ok": False, "fr": "Pas de réponse.", "en": "No answer."}
    text = (turn.text or "").strip()[:40] or "…"
    cost_fr = "gratuit" if not turn.cost_usd else f"coût {turn.cost_usd:.5f} $"
    cost_en = "free" if not turn.cost_usd else f"cost ${turn.cost_usd:.5f}"
    return {"ok": True, "seconds": round(secs, 2), "cost_usd": turn.cost_usd,
            "fr": f"{model_label(model)} répond en {secs:.1f} s (« {text} »), {cost_fr}.",
            "en": f"{model_label(model)} answers in {secs:.1f} s (\"{text}\"), {cost_en}."}
