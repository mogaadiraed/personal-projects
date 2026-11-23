"""Local models through Ollama (runs on the GPU; nothing leaves this PC).

Streaming and tool calling together: text and thinking arrive in chunks, tool calls arrive
whole; everything is collected into one ``Turn``. Ollama gives tool calls no id, so one is
made up here to pair each call with its result.

``free_gpu()`` and ``start()`` back the Stop / Start buttons: unload every local model (the GPU is
freed, the server keeps running) or load the default one again.

``OllamaProvider`` is also the base of the Ollama Cloud adapter (``ollama_cloud``): same
protocol, another host and a key. The hooks (``_client``, ``_options``, ``_before_call``,
``_after_call``, ``_response_error``) are what the cloud version changes.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from carbonsync.assistant.providers.base import (
    Delta,
    Provider,
    ProviderError,
    ToolCall,
    Turn,
)
from carbonsync.obs import get_logger

log = get_logger(__name__)

DEFAULT_HOST = "http://127.0.0.1:11434"
# (tag, label, size, role): the plan's recommendations, best first
RECOMMENDED = [
    ("qwen3.5:9b", "Qwen 3.5 9B", "≈6.6 GB", ("recommandé", "recommended")),
    ("qwen3.5:4b", "Qwen 3.5 4B", "≈3.3 GB", ("rapide", "fast")),
    ("ministral-3:8b", "Ministral 3 8B", "≈6.0 GB", ("français", "French")),
]
DEFAULT_NUM_CTX = 16384
NUM_CTX_CHOICES = [8192, 16384, 32768]
_NOT_RUNNING = ("Ollama ne répond pas : lancez l'application Ollama (ou « ollama serve »).",
                "Ollama is not responding: start the Ollama app (or 'ollama serve').")


def host() -> str:
    h = os.environ.get("OLLAMA_HOST", "").strip()
    if not h:
        return DEFAULT_HOST
    return h if h.startswith("http") else f"http://{h}"


def executable() -> Path | None:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return local if local.exists() else None


def status(timeout: float = 2.0) -> dict[str, Any]:
    """{'installed', 'running', 'models': [tags], 'loaded': [tags in memory], 'error'} — fast, safe on every page load."""
    out: dict[str, Any] = {"installed": executable() is not None, "running": False, "models": [], "loaded": [],
                           "error": ""}
    try:
        import ollama
    except ImportError:
        out["error"] = "python package 'ollama' missing: pip install ollama"
        return out
    try:
        client = ollama.Client(host=host(), timeout=timeout)
        resp = client.list()
        out["running"] = True
        out["models"] = sorted(m.model for m in resp.models if m.model)
        out["loaded"] = sorted(m.model for m in client.ps().models if m.model)
    except Exception as exc:  # noqa: BLE001 - not running / unreachable
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def details(timeout: float = 2.0) -> dict[str, Any]:
    """For the Ollama page: server version, installed models (size, parameters, quantisation) and the
    models in memory (GPU memory and share). Read-only; never downloads or deletes anything."""
    out: dict[str, Any] = {**status(timeout), "version": "", "installed_models": [], "loaded_models": []}
    if not out["running"]:
        return out
    try:
        import httpx
        import ollama

        out["version"] = httpx.get(f"{host()}/api/version", timeout=timeout).json().get("version", "")
        client = ollama.Client(host=host(), timeout=timeout)
        for m in client.list().models:
            d = m.details
            out["installed_models"].append({
                "model": m.model, "size_gb": round((m.size or 0) / 1e9, 1),
                "parameters": getattr(d, "parameter_size", "") or "", "quantization": getattr(d, "quantization_level", "") or "",
                "family": getattr(d, "family", "") or "", "modified": m.modified_at,
                "remote": bool(getattr(m, "remote_host", None)),
            })
        for m in client.ps().models:
            size, vram = int(m.size or 0), int(m.size_vram or 0)
            out["loaded_models"].append({"model": m.model, "size_gb": round(size / 1e9, 1),
                                         "vram_gb": round(vram / 1e9, 1),
                                         "gpu_pct": round(100 * vram / size) if size else 0,
                                         "context": int(getattr(m, "context_length", 0) or 0), "until": m.expires_at})
    except Exception as exc:  # noqa: BLE001 - informative page only
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


_WARMING: set[str] = set()
_WARM_LOCK = threading.Lock()
_EPOCH = [0]          # bumped by free_gpu(): a warm-up that started before unloads its model when it ends
KEEP_ALIVE = "20m"    # a local model leaves the GPU by itself 20 minutes after its last use


def warm_up(model: str, num_ctx: int = DEFAULT_NUM_CTX) -> bool:
    """Load the model into memory in the background, so the first answer does not wait for it.

    Uses the same ``num_ctx`` as the chat calls: a different value would make Ollama load it again.
    Returns False if a warm-up of this model is already running.
    """
    with _WARM_LOCK:
        if model in _WARMING:
            return False
        _WARMING.add(model)
        epoch = _EPOCH[0]

    def run() -> None:
        t0 = time.perf_counter()
        try:
            import ollama

            ollama.Client(host=host(), timeout=300).generate(model=model, prompt="", options={"num_ctx": num_ctx},
                                                             keep_alive=KEEP_ALIVE)
            if _EPOCH[0] != epoch:  # the GPU was freed while the model was loading
                unload(model)
                log.info("Model %s unloaded again: the GPU was freed during its warm-up", model)
            else:
                log.info("Model %s loaded in memory in %.1fs (context %d)", model, time.perf_counter() - t0, num_ctx)
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            log.warning("Model %s warm-up failed: %s", model, exc)
        finally:
            with _WARM_LOCK:
                _WARMING.discard(model)

    log.info("Loading model %s in memory (background)…", model)
    threading.Thread(target=run, name=f"warm-{model}", daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Stop / start buttons: free the GPU, load a model again
# ---------------------------------------------------------------------------
def _gb(value: float, lang: str) -> str:
    text = f"{value:.1f}"
    return (text.replace(".", ",") + " Go") if lang == "fr" else (text + " GB")


def unload(model: str, timeout: float = 30.0) -> bool:
    """Removes ``model`` from memory now (``keep_alive: 0``), which frees its GPU memory."""
    import httpx

    try:
        r = httpx.post(f"{host()}/api/generate", json={"model": model, "keep_alive": 0}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - reported by the caller
        log.warning("Model %s unload failed: %s", model, type(exc).__name__)
        return False
    if r.status_code != 200:
        log.warning("Model %s unload returned HTTP %d", model, r.status_code)
        return False
    return True


def free_gpu(timeout: float = 30.0) -> dict[str, Any]:
    """The "Stop" button: unloads every model Ollama holds in memory. The Ollama server keeps running
    but holds no GPU memory; ``start()`` or the next local question loads a model again."""
    with _WARM_LOCK:
        _EPOCH[0] += 1
    try:
        import ollama

        loaded = [(m.model, int(m.size_vram or 0)) for m in ollama.Client(host=host(), timeout=5).ps().models if m.model]
    except Exception:  # noqa: BLE001 - not running: nothing on the GPU
        return {"ok": True, "unloaded": [], "freed_gb": 0.0,
                "fr": "Ollama ne tourne pas : aucun modèle local n'occupe le GPU.",
                "en": "Ollama is not running: no local model is using the GPU."}
    if not loaded:
        return {"ok": True, "unloaded": [], "freed_gb": 0.0,
                "fr": "Aucun modèle local en mémoire : le GPU est déjà libre.",
                "en": "No local model in memory: the GPU is already free."}
    done = [m for m, _ in loaded if unload(m, timeout)]
    failed = [m for m, _ in loaded if m not in done]
    freed = sum(v for m, v in loaded if m in done) / 1e9
    log.info("GPU freed: %s unloaded (%.1f GB)%s", ", ".join(done) or "nothing", freed,
             f", failed: {', '.join(failed)}" if failed else "")
    if failed:
        return {"ok": False, "unloaded": done, "freed_gb": round(freed, 1),
                "fr": f"Impossible de décharger {', '.join(failed)} : réessayez, ou quittez Ollama (icône près de "
                      "l'horloge).",
                "en": f"Could not unload {', '.join(failed)}: try again, or quit Ollama (icon near the clock)."}
    return {"ok": True, "unloaded": done, "freed_gb": round(freed, 1),
            "fr": f"GPU libéré : {', '.join(done)} retiré de la mémoire ({_gb(freed, 'fr')}).",
            "en": f"GPU freed: {', '.join(done)} removed from memory ({_gb(freed, 'en')})."}


def start_server(wait: float = 30.0) -> bool:
    """Starts the Ollama app (or ``ollama serve``) when it is installed but not running; waits until it answers."""
    import subprocess

    exe = executable()
    if exe is None:
        return False
    app = exe.with_name("ollama app.exe")
    cmd = [str(app)] if app.exists() else [str(exe), "serve"]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        log.warning("Could not start Ollama (%s): %s", cmd[0], exc)
        return False
    log.info("Starting Ollama: %s", Path(cmd[0]).name)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if status(timeout=1.0)["running"]:
            return True
        time.sleep(0.5)
    return False


def start(model: str | None, num_ctx: int = DEFAULT_NUM_CTX, preferred: str | None = None,
          timeout: float = 300.0) -> dict[str, Any]:
    """The "Start" button: loads ``model`` (or the default local model) into GPU memory now, starting the
    Ollama app first if it is not running. The model stays loaded 20 minutes after its last use."""
    started = False
    s = status(timeout=2.0)
    if not s["running"]:
        if not start_server():
            return {"ok": False, "started": False, "fr": _NOT_RUNNING[0], "en": _NOT_RUNNING[1]}
        started = True
        s = status(timeout=3.0)
    model = model or pick_default(s.get("models", []), preferred)
    if not model:
        return {"ok": False, "started": started,
                "fr": "Aucun modèle local installé : « ollama pull qwen3.5:9b » dans un terminal.",
                "en": "No local model installed: 'ollama pull qwen3.5:9b' in a terminal."}
    with _WARM_LOCK:
        _EPOCH[0] += 1  # a warm-up still running must not be undone by an older Stop
    t0 = time.perf_counter()
    try:
        import ollama

        ollama.Client(host=host(), timeout=timeout).generate(model=model, prompt="", options={"num_ctx": num_ctx},
                                                             keep_alive=KEEP_ALIVE)
    except Exception as exc:  # noqa: BLE001 - reported on the page
        log.warning("Model %s start failed: %s", model, exc)
        return {"ok": False, "started": started, "model": model,
                "fr": f"Impossible de charger {model} : {type(exc).__name__}.",
                "en": f"Could not load {model}: {type(exc).__name__}."}
    secs = time.perf_counter() - t0
    vram = 0.0
    try:
        import ollama

        vram = sum(int(m.size_vram or 0) for m in ollama.Client(host=host(), timeout=5).ps().models
                   if m.model == model) / 1e9
    except Exception as exc:  # noqa: BLE001 - informative only
        log.debug("GPU memory of %s not read: %s", model, exc)
    log.info("Model %s started on request in %.1fs (context %d, %.1f GB on the GPU)%s", model, secs, num_ctx, vram,
             ", Ollama started" if started else "")
    app_fr, app_en = (" Ollama a été démarré." if started else ""), (" Ollama was started." if started else "")
    secs_fr = f"{secs:.1f}".replace(".", ",")
    return {"ok": True, "started": started, "model": model, "seconds": round(secs, 1), "vram_gb": round(vram, 1),
            "fr": f"{model} chargé sur le GPU en {secs_fr} s ({_gb(vram, 'fr')}).{app_fr} Il se retire seul 20 min "
                  "après la dernière question.",
            "en": f"{model} loaded on the GPU in {secs:.1f} s ({_gb(vram, 'en')}).{app_en} It leaves by itself 20 min "
                  "after the last question."}


def pick_default(models: list[str], preferred: str | None = None) -> str | None:
    """The model chosen on the Ollama page when it is installed, else the first recommended model that
    is installed. Other models stay selectable, but are not chosen by default: small or unknown models
    call tools poorly, and the built-in assistant is exact."""
    if preferred and preferred in models:
        return preferred
    for tag, *_ in RECOMMENDED:
        if tag in models:
            return tag
    return None


def _to_ollama(messages: list[dict[str, Any]], thinking: bool = False) -> list[dict[str, Any]]:
    """Provider-neutral messages → Ollama chat messages. With ``thinking``, the reasoning of the
    assistant steps of the current question goes back with their tool calls (reasoning models need
    it to continue a tool loop); reasoning from earlier questions is not resent."""
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    out = []
    for i, m in enumerate(messages):
        role = m["role"]
        if role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                     for c in m["tool_calls"]]
            raw = m.get("raw") or {}
            if thinking and i > last_user and raw.get("thinking"):
                msg["thinking"] = raw["thinking"]
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "content": m["content"], "tool_name": m.get("name", "")})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


class OllamaProvider(Provider):
    key = "ollama"
    label = "Ollama"
    privacy = "full"
    supports_thinking = True
    prefetch = True
    replay_thinking = False
    keep_alive: str | None = KEEP_ALIVE

    def __init__(self, model: str, num_ctx: int = DEFAULT_NUM_CTX, temperature: float = 0.2,
                 timeout: float = 300.0) -> None:
        super().__init__(model)
        self.num_ctx = num_ctx
        # ≈2.5 characters per token for French + JSON; keep ≈4k tokens for the system prompt, tools and answer
        self.history_chars = max(8_000, int((num_ctx - 4_000) * 2.5))
        self.temperature = temperature
        self.timeout = timeout

    # -- hooks (the cloud adapter overrides these) ---------------------------------------------
    def _client(self) -> Any:
        import ollama

        return ollama.Client(host=host(), timeout=self.timeout)

    def _options(self) -> dict[str, Any]:
        return {"num_ctx": self.num_ctx, "temperature": self.temperature}

    def _before_call(self) -> None:
        """Runs before any request (the cloud adapter checks the spending cap here)."""

    def _after_call(self, turn: Turn, cached: int) -> None:
        """Runs once the answer is complete (the cloud adapter records the cost here)."""

    def _response_error(self, exc: Any) -> ProviderError:
        if exc.status_code == 404:
            return ProviderError(f"Modèle « {self.model} » absent : ollama pull {self.model}",
                                 f"Model '{self.model}' is not downloaded: ollama pull {self.model}")
        return ProviderError(f"Ollama : {exc.error}", f"Ollama: {exc.error}")

    def _not_reachable(self) -> ProviderError:
        return ProviderError(*_NOT_RUNNING)

    # -------------------------------------------------------------------------------------------
    def stream(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
               think: bool = False, final: bool = False) -> Iterator[Delta | Turn]:
        try:
            import ollama
        except ImportError as exc:
            raise ProviderError("Le paquet Python « ollama » manque : pip install ollama",
                                "The Python package 'ollama' is missing: pip install ollama") from exc
        self._before_call()
        client = self._client()
        payload = [{"role": "system", "content": str(system)}, *_to_ollama(messages, self.replay_thinking)]
        specs = [{"type": "function", "function": s} for s in tools]
        extra: dict[str, Any] = {"keep_alive": self.keep_alive} if self.keep_alive else {}
        turn = Turn()
        cached = 0
        t0 = time.perf_counter()
        try:
            chunks = client.chat(model=self.model, messages=payload, tools=specs or None, stream=True, think=think,
                                 options=self._options(), **extra)
            for chunk in chunks:
                msg = chunk.message
                if msg.thinking:
                    turn.thinking += msg.thinking
                    yield Delta("thinking", msg.thinking)
                if msg.content:
                    turn.text += msg.content
                    yield Delta("text", msg.content)
                for call in msg.tool_calls or []:
                    args = dict(call.function.arguments or {})
                    turn.tool_calls.append(ToolCall(f"call_{uuid.uuid4().hex[:10]}", call.function.name, args))
                if chunk.done:
                    turn.input_tokens = int(chunk.prompt_eval_count or 0)
                    turn.output_tokens = int(chunk.eval_count or 0)
                    cached = int(getattr(chunk, "prompt_eval_cached_count", 0) or 0)
                    if chunk.done_reason == "length":
                        turn.stop = "max_tokens"
        except ollama.ResponseError as exc:
            raise self._response_error(exc) from exc
        except ConnectionError as exc:
            raise self._not_reachable() from exc
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "connect" in name or "connect" in str(exc).lower():
                raise self._not_reachable() from exc
            if "timeout" in name:
                raise ProviderError("Le modèle n'a pas répondu à temps : réessayez, ou choisissez un modèle plus rapide.",
                                    "The model did not answer in time: try again, or pick a faster model.") from exc
            raise
        turn.seconds = time.perf_counter() - t0
        if turn.tool_calls and turn.stop == "end":
            turn.stop = "tool_use"
        if turn.stop == "max_tokens" and turn.tool_calls:
            turn.tool_calls = []  # a cut tool call is never run
        if self.replay_thinking and turn.tool_calls and turn.thinking:
            turn.raw = {"provider": self.key, "thinking": turn.thinking}
        self._after_call(turn, cached)
        rate = turn.output_tokens / turn.seconds if turn.seconds else 0
        log.info("Model %s (%s): %d in / %d out tokens, %.1fs (%.0f tok/s)%s%s", self.model, self.key,
                 turn.input_tokens, turn.output_tokens, turn.seconds, rate,
                 f", ≈ ${turn.cost_usd:.4f}" if turn.cost_usd else "",
                 f", tools: {', '.join(c.name for c in turn.tool_calls)}" if turn.tool_calls else "")
        yield turn
