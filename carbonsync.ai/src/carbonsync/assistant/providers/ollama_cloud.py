"""Ollama Cloud: large open models (DeepSeek, Kimi, GLM, Mistral…) on ollama.com, with the user's
API key (IA & API → Ollama).

Same protocol as local Ollama (``/api/chat``, streaming, tools, ``think``), host
``https://ollama.com`` and an ``Authorization: Bearer`` header. It is a cloud service, so it
runs under the Expert rules: expert brief, aggregated data only, daily cap, cost per answer.

Prices: https://ollama.com/pricing, checked 2026-11-20, USD per million tokens (input, cached
input, output). DeepSeek is cheaper off-peak; the meter uses the peak price so the cap stays on
the safe side. Ollama's own usage page is the bill.
"""
from __future__ import annotations

import time
from typing import Any

from carbonsync.assistant import usage, vault
from carbonsync.assistant.providers.base import ProviderError, Turn
from carbonsync.assistant.providers.ollama_provider import OllamaProvider
from carbonsync.assistant.providers.registry import WHERE
from carbonsync.obs import get_logger, redact

log = get_logger(__name__)

PROVIDER = "ollama_cloud"
HOST = "https://ollama.com"
DEFAULT_MODEL = "deepseek-v4-pro"

PRICES: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-pro": (1.32, 0.044, 3.96),
    "deepseek-v4.1-flash": (0.30, 0.006, 1.20),
    "kimi-k3.1-pro": (2.00, 0.20, 10.00),
    "kimi-k3": (3.00, 0.30, 15.00),
    "kimi-k2.7-code": (0.95, 0.19, 4.00),
    "kimi-k2.6": (0.95, 0.16, 4.00),
    "glm-5.3": (1.40, 0.26, 4.40),
    "glm-5.3-flash": (0.15, 0.03, 0.50),
    "glm-5.2": (1.40, 0.26, 4.40),
    "mistral-large-4": (0.70, 0.07, 2.10),
    "minimax-m3": (0.60, 0.12, 2.40),
    "minimax-m3.1-flash": (0.15, 0.015, 0.60),
    "gpt-oss:120b": (0.15, 0.014, 0.60),
    "gpt-oss:20b": (0.07, 0.035, 0.30),
    "gemma4": (0.14, 0.05, 0.40),
    "mimo-v2.6-pro": (0.435, 0.0036, 0.87),
    "mimo-v2.6-flash": (0.14, 0.0028, 0.28),
    "nemotron-3-ultra": (0.10, 0.10, 3.00),
    "nemotron-3-super": (0.015, 0.015, 0.60),
    "muse-spark-open": (1.25, 0.15, 4.25),
    "inkling-2-small": (0.30, 0.03, 1.00),
    "beam": (0.125, 0.015, 0.40),
}

# The five picks for this assistant: tool calling + reasoning, ranked (ollama.com library, 2026-11-20)
TOP5: list[dict[str, Any]] = [
    {"model": "deepseek-v4-pro", "label": "DeepSeek V4 Pro", "publisher": "DeepSeek", "context": "1M",
     "tags": ["tools", "thinking"],
     "role": ("recommandé · raisonnement poussé", "recommended · deep reasoning"),
     "note": (("Modèle phare de DeepSeek, trois modes de réflexion. Le meilleur équilibre qualité / prix pour les "
              "analyses en plusieurs étapes."),
              ("DeepSeek's flagship, three reasoning modes. The best quality / price balance for multi-step "
              "analyses."))},
    {"model": "kimi-k3.1-pro", "label": "Kimi K3.1 Pro", "publisher": "Moonshot AI", "context": "1M",
     "tags": ["tools", "thinking", "vision"],
     "role": ("agentique · haut de gamme", "agentic · top of the range"),
     "note": (("Le haut de gamme de Kimi, conçu pour les tâches d'agent avec outils. Réponses chères (10 $ par "
              "million de jetons écrits)."),
              ("Kimi's top model, built for agent tasks with tools. Expensive answers ($10 per million tokens "
              "written)."))},
    {"model": "glm-5.3", "label": "GLM 5.3", "publisher": "Z.ai", "context": "1M", "tags": ["tools", "thinking"],
     "role": ("outils · réflexion longue", "tools · long reasoning"),
     "note": ("Grand modèle de Z.ai, solide sur les outils et le raisonnement. Texte seulement.",
              "Z.ai's large model, strong with tools and reasoning. Text only.")},
    {"model": "mistral-large-4", "label": "Mistral Large 4", "publisher": "Mistral AI", "context": "1M",
     "tags": ["tools", "thinking", "vision"],
     "role": ("éditeur français", "French publisher"),
     "note": ("Le modèle généraliste de Mistral (éditeur français). Le moins cher des grands modèles de cette liste.",
              "Mistral's general model (French publisher). The cheapest of the large models in this list.")},
    {"model": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash", "publisher": "DeepSeek", "context": "1M",
     "tags": ["tools", "thinking", "vision"],
     "role": ("rapide · économique", "fast · low cost"),
     "note": ("Environ 4 fois moins cher que V4 Pro et plus rapide : bien pour les questions courantes.",
              "About 4 times cheaper than V4 Pro and faster: good for everyday questions.")},
]
TOP5_MODELS = [m["model"] for m in TOP5]

_LABELS = {
    **{m["model"]: m["label"] for m in TOP5},
    "kimi-k3": "Kimi K3", "kimi-k2.7-code": "Kimi K2.7 Code", "kimi-k2.6": "Kimi K2.6", "glm-5.3-flash": "GLM 5.3 Flash",
    "glm-5.2": "GLM 5.2", "minimax-m3": "MiniMax M3", "minimax-m3.1-flash": "MiniMax M3.1 Flash",
    "gpt-oss:120b": "gpt-oss 120B", "gpt-oss:20b": "gpt-oss 20B", "gemma4": "Gemma 4", "mimo-v2.6-pro": "MiMo V2.6 Pro",
    "mimo-v2.6-flash": "MiMo V2.6 Flash", "nemotron-3-ultra": "Nemotron 3 Ultra", "nemotron-3-super": "Nemotron 3 Super",
    "muse-spark-open": "Muse Spark Open", "inkling-2-small": "Inkling 2 Small", "beam": "Beam",
}
OTHER_MODELS = [m for m in PRICES if m not in TOP5_MODELS]


def model_label(model: str) -> str:
    return _LABELS.get(model, model)


def price(model: str) -> tuple[float, float, float]:
    """(input, cached input, output) in USD per million tokens. An unknown model is priced as the
    most expensive one, so the daily cap stays safe."""
    return PRICES.get(model) or max(PRICES.values(), key=lambda p: p[2])


def cost(model: str, input_tokens: int, cached: int, output_tokens: int) -> float:
    """``input_tokens`` excludes the cached part."""
    inp, cin, out = price(model)
    return round((input_tokens * inp + cached * cin + output_tokens * out) / 1_000_000, 6)


def _where() -> tuple[str, str]:
    return WHERE[PROVIDER]


def _no_key() -> ProviderError:
    fr, en = _where()
    return ProviderError(f"Aucune clé Ollama Cloud : ajoutez-la dans {fr}.", f"No Ollama Cloud key: add it in {en}.")


def response_error(exc: Any, model: str) -> ProviderError:
    """``ollama.ResponseError`` → a bilingual message the user can act on (the key is never repeated)."""
    code = int(getattr(exc, "status_code", -1) or -1)
    detail = redact(str(getattr(exc, "error", exc)))[:160]
    low = detail.lower()
    fr_where, en_where = _where()
    if code == 401 or "unauthorized" in low or "invalid credentials" in low:
        return ProviderError(f"Clé Ollama Cloud refusée (invalide ou révoquée) : vérifiez-la dans {fr_where}.",
                             f"Ollama Cloud key rejected (invalid or revoked): check it in {en_where}.")
    if code == 402 or any(w in low for w in ("credit", "balance", "insufficient", "payment", "quota")):
        return ProviderError("Crédit Ollama Cloud épuisé : ajoutez des crédits ou un abonnement sur ollama.com "
                             "(Settings → Billing).",
                             "Ollama Cloud credit used up: add credits or a plan on ollama.com (Settings → Billing).")
    if code == 403 or "subscription" in low or "upgrade" in low:
        return ProviderError(f"Accès refusé par Ollama Cloud pour « {model} » : ce modèle demande peut-être un "
                             f"abonnement, ou le compte est suspendu. ({detail})",
                             f"Ollama Cloud refused access to '{model}': this model may need a plan, or the account "
                             f"is suspended. ({detail})")
    if code == 404 or "not found" in low or "doesn't exist" in low:
        return ProviderError(f"Modèle « {model} » inconnu d'Ollama Cloud : choisissez-en un autre dans {fr_where}.",
                             f"Model '{model}' is unknown to Ollama Cloud: pick another one in {en_where}.")
    if code == 429 or "rate limit" in low or "too many" in low:
        return ProviderError("Limite d'utilisation Ollama Cloud atteinte : réessayez dans quelques minutes.",
                             "Ollama Cloud usage limit reached: try again in a few minutes.")
    if code >= 500:
        return ProviderError(f"Ollama Cloud ne joint pas « {model} » pour l'instant (erreur {code}) : réessayez ou "
                             "changez de modèle.",
                             f"Ollama Cloud cannot reach '{model}' right now (error {code}): try again or switch "
                             "model.")
    return ProviderError(f"Ollama Cloud : {detail}", f"Ollama Cloud: {detail}")


class OllamaCloudProvider(OllamaProvider):
    """A cloud model on ollama.com: the local Ollama adapter with another host, the key, the cap and
    the cost meter."""

    key = PROVIDER
    label = "Ollama Cloud"
    privacy = "aggregated"
    supports_thinking = True
    prefetch = False
    replay_thinking = True   # reasoning goes back with the tool calls of the current question
    keep_alive = None

    def __init__(self, model: str = DEFAULT_MODEL, *, settings: dict[str, Any] | None = None, root: Any = None,
                 timeout: float = 600.0, client: Any = None) -> None:
        super().__init__(model, timeout=timeout)
        self.history_chars = 200_000  # 1M-token models; the history is still trimmed to keep each call cheap
        self.settings, self.root = settings, root
        self._client_obj = client

    @property
    def display_name(self) -> str:
        return f"Ollama Cloud · {model_label(self.model)}"

    def _client(self) -> Any:
        if self._client_obj is not None:
            return self._client_obj
        key, _ = vault.get_key(PROVIDER)
        if not key:
            raise _no_key()
        import ollama

        self._client_obj = ollama.Client(host=HOST, headers={"Authorization": f"Bearer {key}"}, timeout=self.timeout)
        return self._client_obj

    def _options(self) -> dict[str, Any]:
        return {"temperature": self.temperature}

    def _before_call(self) -> None:
        allowed, spent, cap = usage.check_cap(self.settings, self.root)
        if not allowed:
            fr, en = WHERE["overview"]
            raise ProviderError(
                f"Plafond de dépense du jour atteint ({spent:.2f} $ sur {cap:.2f} $). Il repart à zéro demain ; "
                f"modifiable dans {fr}.",
                f"Today's spending cap reached (${spent:.2f} of ${cap:.2f}). It resets tomorrow; change it in {en}.")

    def _response_error(self, exc: Any) -> ProviderError:
        return response_error(exc, self.model)

    def _not_reachable(self) -> ProviderError:
        return ProviderError("Impossible de joindre ollama.com : vérifiez la connexion Internet.",
                             "Cannot reach ollama.com: check the Internet connection.")

    def _after_call(self, turn: Turn, cached: int) -> None:
        cached = max(0, min(cached, turn.input_tokens))  # input_tokens keeps the total, as for Claude
        fresh = turn.input_tokens - cached
        turn.cache_read = cached
        turn.cost_usd = cost(self.model, fresh, cached, turn.output_tokens)
        usage.record(PROVIDER, self.model, input_tokens=fresh, output_tokens=turn.output_tokens, cache_read=cached,
                     cost=turn.cost_usd, root=self.root)


# ---------------------------------------------------------------------------
# Account checks (free: no tokens billed)
# ---------------------------------------------------------------------------
def _get(path: str, key: str, transport: Any = None) -> Any:
    import httpx

    with httpx.Client(base_url=HOST, timeout=15.0, transport=transport,
                      headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}) as http:
        return http.get(path)


def _balance_text(data: dict[str, Any]) -> tuple[str, str]:
    inc, bought = data.get("included") or {}, data.get("purchased") or {}
    parts_fr, parts_en = [], []
    if "balance_usd" in inc:
        allow = inc.get("allowance_usd")
        of_fr = f" sur {allow:.2f} $" if isinstance(allow, int | float) else ""
        of_en = f" of ${allow:.2f}" if isinstance(allow, int | float) else ""
        parts_fr.append(f"crédit inclus {inc['balance_usd']:.2f} ${of_fr}")
        parts_en.append(f"included credit ${inc['balance_usd']:.2f}{of_en}")
    for name, (fr, en) in {"session": ("session", "session"), "weekly": ("semaine", "week")}.items():
        pct = (inc.get(name) or {}).get("remaining_percent")
        if isinstance(pct, int | float):
            parts_fr.append(f"{fr} : {pct:.0f} % restants")
            parts_en.append(f"{en}: {pct:.0f}% left")
    if isinstance(bought.get("balance_usd"), int | float):
        parts_fr.append(f"crédit acheté {bought['balance_usd']:.2f} $")
        parts_en.append(f"purchased credit ${bought['balance_usd']:.2f}")
    return "; ".join(parts_fr), "; ".join(parts_en)


def check_connection(api_key: str | None = None, transport: Any = None) -> dict[str, Any]:
    """Checks the key with the account balance endpoint (``GET /api/balance``, free)."""
    key = api_key or vault.get_key(PROVIDER)[0]
    if not key:
        err = _no_key()
        return {"ok": False, "fr": err.fr, "en": err.en, "ms": 0, "balance": None}
    t0 = time.perf_counter()
    try:
        r = _get("/api/balance", key, transport)
    except Exception as exc:  # noqa: BLE001 - network problems are reported, not raised
        log.warning("Ollama Cloud: connection test failed (%s)", type(exc).__name__)
        return {"ok": False, "fr": "Impossible de joindre ollama.com : vérifiez la connexion Internet.",
                "en": "Cannot reach ollama.com: check the Internet connection.", "ms": 0, "balance": None}
    ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            data = {}
        bfr, ben = _balance_text(data if isinstance(data, dict) else {})
        log.info("Ollama Cloud: key OK (%d ms)", ms)
        return {"ok": True, "ms": ms, "balance": data,
                "fr": f"Connexion réussie à Ollama Cloud ({ms} ms)." + (f" Solde : {bfr}." if bfr else ""),
                "en": f"Connected to Ollama Cloud ({ms} ms)." + (f" Balance: {ben}." if ben else "")}
    log.warning("Ollama Cloud: connection test returned %d", r.status_code)

    class _E:
        status_code = r.status_code
        error = (r.text or "")[:200]

    err = response_error(_E(), "—")
    return {"ok": False, "fr": err.fr, "en": err.en, "ms": ms, "balance": None}


def quick_test(model: str, client: Any = None, root: Any = None) -> dict[str, Any]:
    """One tiny question to ``model`` (a fraction of a cent): checks the name works, measures the
    speed, and counts the cost in the usage meter."""
    p = OllamaCloudProvider(model, root=root, client=client, timeout=120.0)
    t0 = time.perf_counter()
    try:
        turn: Turn | None = None
        for item in p.stream("Answer with one word.", [{"role": "user", "content": "Réponds seulement : OK"}], [],
                             think=False):
            if isinstance(item, Turn):
                turn = item
    except ProviderError as exc:
        return {"ok": False, "fr": exc.fr, "en": exc.en}
    secs = time.perf_counter() - t0
    if turn is None:
        return {"ok": False, "fr": "Pas de réponse.", "en": "No answer."}
    rate = turn.output_tokens / turn.seconds if turn.seconds else 0.0
    text = (turn.text or "").strip()[:40]
    return {"ok": True, "seconds": round(secs, 2), "tokens_per_s": round(rate, 1), "cost_usd": turn.cost_usd,
            "fr": f"{model_label(model)} répond en {secs:.1f} s (« {text} »), coût {turn.cost_usd:.5f} $.",
            "en": f"{model_label(model)} answers in {secs:.1f} s (\"{text}\"), cost ${turn.cost_usd:.5f}."}
