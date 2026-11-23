"""Stop / Start buttons for the local models: free the GPU (unload every model, the server stays
open), load the default model again, start the Ollama app when it is closed, no automatic load in
the Assistant while stopped, and a local question that starts the model again. No real Ollama.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import httpx
import ollama
import pytest

from carbonsync.assistant import store, usage
from carbonsync.assistant.providers import ollama_provider as olp
from tests.test_assistant_ollama_cloud import FakeOllama, chunk


class FakeServer:
    """``ollama.Client`` and ``httpx.post`` for a local Ollama with models in memory."""

    def __init__(self, loaded=None, unload_status=200, gate: threading.Event | None = None):
        self.loaded = dict(loaded or {})   # model -> bytes on the GPU
        self.unload_status = unload_status
        self.unloaded: list[str] = []
        self.generated: list[dict] = []
        self.gate = gate

    def client(self, host=None, timeout=None, **kw):
        server = self

        class _Client:
            def ps(self):
                return SimpleNamespace(models=[SimpleNamespace(model=m, size=v, size_vram=v)
                                               for m, v in server.loaded.items()])

            def generate(self, **kw):
                if server.gate is not None:
                    server.gate.wait(5)
                server.generated.append(kw)
                server.loaded[kw["model"]] = 5_600_000_000

        return _Client()

    def post(self, url, json=None, timeout=None):
        assert url.endswith("/api/generate") and json["keep_alive"] == 0
        if self.unload_status == 200:
            self.unloaded.append(json["model"])
            self.loaded.pop(json["model"], None)
        return httpx.Response(self.unload_status, json={"done_reason": "unload"})


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer({"qwen3.5:9b": 5_597_225_285})
    monkeypatch.setattr(ollama, "Client", fake.client)
    monkeypatch.setattr(httpx, "post", fake.post)
    return fake


def test_free_gpu_unloads_every_model_and_reports_the_memory(server):
    server.loaded["qwen3.5:4b"] = 3_000_000_000
    res = olp.free_gpu()
    assert res["ok"] and sorted(res["unloaded"]) == ["qwen3.5:4b", "qwen3.5:9b"] and res["freed_gb"] == 8.6
    assert not server.loaded and "8,6 Go" in res["fr"] and "8.6 GB" in res["en"]
    again = olp.free_gpu()
    assert again["ok"] and again["unloaded"] == [] and "déjà libre" in again["fr"]


def test_free_gpu_when_ollama_is_closed_or_refuses(monkeypatch, server):
    server.unload_status = 500
    res = olp.free_gpu()
    assert not res["ok"] and "qwen3.5:9b" in res["fr"]

    def closed(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(ollama, "Client", closed)
    res = olp.free_gpu()
    assert res["ok"] and "ne tourne pas" in res["fr"]


def test_a_warm_up_running_during_stop_unloads_its_model(monkeypatch):
    gate = threading.Event()
    fake = FakeServer(gate=gate)
    monkeypatch.setattr(ollama, "Client", fake.client)
    monkeypatch.setattr(httpx, "post", fake.post)
    assert olp.warm_up("qwen3.5:9b", 16384)
    olp.free_gpu()          # pressed while the model is still loading
    gate.set()
    for t in threading.enumerate():
        if t.name == "warm-qwen3.5:9b":
            t.join(5)
    assert fake.generated[0]["keep_alive"] == "20m" and fake.unloaded == ["qwen3.5:9b"] and not fake.loaded


def test_start_loads_the_default_model_with_the_chat_context(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(ollama, "Client", fake.client)
    states = iter([{"running": False, "models": []}, {"running": True, "models": ["qwen3.5:4b", "qwen3.5:9b"]}])
    monkeypatch.setattr(olp, "status", lambda timeout=2.0: next(states))
    monkeypatch.setattr(olp, "start_server", lambda wait=30.0: True)
    res = olp.start(None, 8192, preferred="qwen3.5:4b")
    assert res["ok"] and res["started"] and res["model"] == "qwen3.5:4b" and res["vram_gb"] == 5.6
    assert fake.generated == [{"model": "qwen3.5:4b", "prompt": "", "options": {"num_ctx": 8192}, "keep_alive": "20m"}]
    assert "Ollama a été démarré" in res["fr"]

    monkeypatch.setattr(olp, "status", lambda timeout=2.0: {"running": False, "models": []})
    monkeypatch.setattr(olp, "start_server", lambda wait=30.0: False)
    res = olp.start("qwen3.5:9b")
    assert not res["ok"] and "Ollama ne répond pas" in res["fr"]


def test_the_stopped_flag_is_saved_and_validated(tmp_path):
    assert usage.load_settings(tmp_path)["ollama"]["stopped"] is False
    s = usage.load_settings(tmp_path)
    s["ollama"]["stopped"] = True
    usage.save_settings(s, tmp_path)
    assert usage.load_settings(tmp_path)["ollama"]["stopped"] is True
    s["ollama"]["stopped"] = "yes"
    usage.save_settings(s, tmp_path)
    assert usage.load_settings(tmp_path)["ollama"]["stopped"] is False


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
RUNNING = {"installed": True, "running": True, "models": ["qwen3.5:9b"], "loaded": ["qwen3.5:9b"], "error": "",
           "version": "0.40.16",
           "installed_models": [{"model": "qwen3.5:9b", "size_gb": 6.6, "parameters": "9.0B", "quantization": "Q4_K_M",
                                 "family": "qwen35", "modified": None, "remote": False}],
           "loaded_models": [{"model": "qwen3.5:9b", "size_gb": 6.7, "vram_gb": 5.6, "gpu_pct": 84, "context": 16384,
                              "until": None}]}


def _ollama_page():
    from app.pages import ai_ollama

    ai_ollama.render()


def _assistant_page():
    from app.pages import assistant

    assistant.render()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(store, "get_project_root", lambda: tmp_path)
    for env in ("OLLAMA_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    from carbonsync.assistant import vault

    monkeypatch.setattr(vault, "_backend", lambda: SimpleNamespace(get_password=lambda s, u: None))
    return tmp_path


def test_ollama_page_stop_and_start(project, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from app.pages import ai_ollama

    state = {"d": dict(RUNNING)}
    monkeypatch.setattr(olp, "details", lambda timeout=2.0: state["d"])
    calls = []

    def free_gpu():
        calls.append("stop")
        state["d"] = {**RUNNING, "loaded": [], "loaded_models": []}
        return {"ok": True, "unloaded": ["qwen3.5:9b"], "freed_gb": 5.6, "fr": "GPU libéré : qwen3.5:9b retiré.",
                "en": "GPU freed: qwen3.5:9b removed."}

    def start(model, num_ctx=16384, preferred=None, timeout=300.0):
        calls.append(("start", model, num_ctx))
        state["d"] = dict(RUNNING)
        return {"ok": True, "fr": f"{model} chargé sur le GPU.", "en": f"{model} loaded on the GPU."}

    monkeypatch.setattr(olp, "free_gpu", free_gpu)
    monkeypatch.setattr(olp, "start", start)
    ai_ollama._local_details.clear()
    at = AppTest.from_function(_ollama_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception
    assert any("Sur le GPU" in m.value and "5,6 Go" in m.value for m in at.markdown)
    assert at.button(key="ol_start").disabled  # already loaded
    at.button(key="ol_stop").click().run()
    assert not at.exception and calls == ["stop"] and usage.load_settings(project)["ollama"]["stopped"] is True
    assert any("GPU libéré" in s.value for s in at.success) and at.button(key="ol_stop").disabled
    assert any("modèles locaux arrêtés" in m.value for m in at.markdown)
    at.button(key="ol_start").click().run()
    assert not at.exception and calls[-1] == ("start", "qwen3.5:9b", 16384)
    assert usage.load_settings(project)["ollama"]["stopped"] is False


def test_ollama_page_starts_ollama_when_it_is_closed(project, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from app.pages import ai_ollama

    monkeypatch.setattr(olp, "details", lambda timeout=2.0: {"installed": True, "running": False, "models": [],
                                                             "loaded": [], "error": "x", "version": "",
                                                             "installed_models": [], "loaded_models": []})
    seen = []
    monkeypatch.setattr(olp, "start", lambda model, num_ctx=16384, preferred=None, timeout=300.0:
                        seen.append(model) or {"ok": True, "fr": "Ollama démarré.", "en": "Ollama started."})
    ai_ollama._local_details.clear()
    at = AppTest.from_function(_ollama_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    at.button(key="ol_start_server").click().run()
    assert not at.exception and seen == [None]


def test_assistant_does_not_reload_a_stopped_model_until_asked(project, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from app.pages import assistant

    s = usage.load_settings(project)
    s["ollama"]["stopped"] = True
    usage.save_settings(s, project)
    monkeypatch.setattr(olp, "status", lambda timeout=2.0: {**RUNNING, "loaded": []})
    warmed = []
    monkeypatch.setattr(olp, "warm_up", lambda model, num_ctx=16384: warmed.append(model) or True)
    fake = FakeOllama([chunk("Réponse locale.", done=True, inp=40, out=3)])
    monkeypatch.setattr(olp.OllamaProvider, "_client", lambda self: fake)
    assistant._ollama_status.clear()
    at = AppTest.from_function(_assistant_page, default_timeout=60)
    at.session_state["lang"] = "fr"
    at.run()
    assert not at.exception and warmed == []
    assert any("Modèles locaux arrêtés" in c.value for c in at.caption) and at.button(key="as_start_local")
    at.chat_input[0].set_value("Quel est le total ?").run()
    assert not at.exception and fake.calls and usage.load_settings(project)["ollama"]["stopped"] is False
