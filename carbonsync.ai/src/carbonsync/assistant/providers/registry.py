"""Every AI provider the assistant knows, in one place: pages, key vault, usage meter and the
engine picker all read this table.

``status`` says how far a provider is built: ``ready`` (page and chat work) or ``planned``
(listed on the overview page, page still to come). Adding an OpenAI-compatible provider later
means adding a row here, not new plumbing.
"""
from __future__ import annotations

from typing import Any

PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "label": "Ollama (local)", "kind": "local", "status": "ready", "page": "ai-ollama",
        "about": ("Modèles sur ce PC (GPU) : gratuits, rien ne quitte le PC.",
                  "Models on this PC (GPU): free, nothing leaves the PC."),
    },
    "ollama_cloud": {
        "label": "Ollama Cloud", "kind": "cloud", "status": "ready", "page": "ai-ollama",
        "env": "OLLAMA_API_KEY", "prefix": "", "min_len": 20, "placeholder": "…",
        "console": "https://ollama.com/settings/keys", "pricing": "https://ollama.com/pricing",
        "company": "Ollama",
        "about": ("Grands modèles ouverts (DeepSeek, Kimi, GLM, Mistral…) sur les serveurs d'Ollama, avec votre clé.",
                  "Large open models (DeepSeek, Kimi, GLM, Mistral…) on Ollama's servers, with your key."),
    },
    "anthropic": {
        "label": "Claude (Anthropic)", "kind": "cloud", "status": "ready", "page": "ai-claude",
        "env": "ANTHROPIC_API_KEY", "prefix": "sk-ant-", "min_len": 40, "placeholder": "sk-ant-…",
        "console": "https://platform.claude.com", "company": "Anthropic",
        "about": ("Claude Opus, Sonnet et Haiku 5.5 : raisonnement conservé entre les étapes, cache, repli serveur.",
                  "Claude Opus, Sonnet and Haiku 5.5: reasoning kept across steps, caching, server-side fallback."),
    },
    "openrouter": {
        "label": "OpenRouter", "kind": "cloud", "status": "ready", "page": "ai-openrouter",
        "env": "OPENROUTER_API_KEY", "prefix": "sk-or-", "min_len": 40, "placeholder": "sk-or-v1-…",
        "console": "https://openrouter.ai/settings/keys", "company": "OpenRouter",
        "about": (("Modèles gratuits de nombreux éditeurs (NVIDIA, Google…) avec une seule clé, dans les limites "
                  "d'usage gratuites."),
                  "Free models from many publishers (NVIDIA, Google…) with one key, within the free usage limits."),
    },
    "openai": {
        "label": "OpenAI", "kind": "cloud", "status": "planned", "env": "OPENAI_API_KEY", "prefix": "sk-",
        "min_len": 20, "console": "https://platform.openai.com/api-keys", "company": "OpenAI",
        "about": ("Modèles GPT avec votre clé OpenAI.", "GPT models with your OpenAI key."),
    },
    "gemini": {
        "label": "Google Gemini", "kind": "cloud", "status": "planned", "env": "GEMINI_API_KEY", "prefix": "",
        "min_len": 20, "console": "https://aistudio.google.com/apikey", "company": "Google",
        "about": ("Gemini, avec une offre gratuite limitée.", "Gemini, with a limited free tier."),
    },
    "mistral": {
        "label": "Mistral AI", "kind": "cloud", "status": "planned", "env": "MISTRAL_API_KEY", "prefix": "",
        "min_len": 20, "console": "https://console.mistral.ai/api-keys", "company": "Mistral AI",
        "about": ("Modèles Mistral (éditeur français), avec un mode gratuit limité.",
                  "Mistral models (French publisher), with a limited free mode."),
    },
    "custom": {
        "label": "Autre (compatible OpenAI)", "kind": "cloud", "status": "planned", "env": "CARBONSYNC_CUSTOM_API_KEY",
        "prefix": "", "min_len": 8, "company": "",
        "about": ("Tout service compatible OpenAI : Groq, LM Studio, Azure…",
                  "Any OpenAI-compatible service: Groq, LM Studio, Azure…"),
    },
}


# where things are in the dashboard, for messages (FR, EN)
WHERE = {
    "overview": ("IA & API → Vue d'ensemble", "AI & API → Overview"),
    "ollama": ("IA & API → Ollama", "AI & API → Ollama"),
    "ollama_cloud": ("IA & API → Ollama", "AI & API → Ollama"),
    "anthropic": ("IA & API → Claude", "AI & API → Claude"),
    "openrouter": ("IA & API → OpenRouter", "AI & API → OpenRouter"),
}


def keyed() -> dict[str, dict[str, Any]]:
    """Providers that take an API key (the vault only accepts these)."""
    return {k: v for k, v in PROVIDERS.items() if v.get("env")}


def label(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("label", provider)


def model_label(provider: str, model: str) -> str:
    """A readable model name for tables and captions."""
    if provider == "anthropic":
        from carbonsync.assistant.providers import anthropic_provider as anp

        return anp.model_label(model)
    if provider == "ollama_cloud":
        from carbonsync.assistant.providers import ollama_cloud

        return ollama_cloud.model_label(model)
    if provider == "openrouter":
        from carbonsync.assistant.providers import openrouter

        return openrouter.model_label(model)
    return model
