"""Observability: one logger tree (``carbonsync.*``) for the pipeline, the models and the app.

* ``setup_console_logging()`` prints every step to the terminal that runs
  ``streamlit run streamlit_app.py`` (idempotent – safe to call on every rerun).
* ``step(log, "…")`` logs the start and the duration of a block.
* ``capture(callback)`` forwards records to a callback (used to stream the
  same lines live into the dashboard).
"""
from __future__ import annotations

import logging
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

ROOT_LOGGER = "carbonsync"
_FMT = "%(asctime)s │ %(levelname)-5s │ %(name_short)-20s │ %(message)s"


_ASCII = str.maketrans({"→": "->", "←": "<-", "✓": "OK", "✗": "FAILED", "━": "=", "│": "|", "≥": ">=",
                        "≤": "<=", "…": "...", "·": "-", "—": "-", "–": "-", "₂": "2"})


class _ConsoleHandler(logging.StreamHandler):
    """Stream handler that degrades to ASCII when the console code page (e.g. cp1252) can't print a symbol."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            enc = getattr(self.stream, "encoding", None) or "utf-8"
            try:
                msg.encode(enc)
            except UnicodeEncodeError:
                msg = msg.translate(_ASCII).encode(enc, errors="replace").decode(enc)
            self.stream.write(msg + self.terminator)
            self.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)


# API keys never reach a log line: known prefixes (Anthropic "sk-ant-…", OpenAI "sk-…", OpenRouter
# "sk-or-…", Groq "gsk_…", Google "AIza…") are blanked by pattern, and every key the vault hands out
# is also blanked by value (keys without a prefix, such as Ollama's).
_SECRET = re.compile(r"(?<![A-Za-z0-9_])(sk-(?:ant-|or-|proj-)?|gsk_|AIza)[A-Za-z0-9_-]{12,}")
_KNOWN: set[str] = set()


def add_secret(value: str | None) -> None:
    """Registers a key so that it is blanked wherever it would appear in a log line."""
    if value and len(value) >= 12:
        _KNOWN.add(value)


def redact(text: str) -> str:
    """Blanks anything that looks like an API key, and every key the vault has handed out."""
    text = _SECRET.sub(lambda m: m.group(1) + "[REDACTED]", text)
    for value in _KNOWN:
        if value in text:
            text = text.replace(value, "[REDACTED]")
    return text


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class _ShortName(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        record.name_short = name[len(ROOT_LOGGER) + 1:] if name.startswith(ROOT_LOGGER + ".") else name
        return True


def get_logger(name: str) -> logging.Logger:
    """Logger under the ``carbonsync`` tree (``carbonsync.models.prophet`` → shown as ``models.prophet``)."""
    if not name.startswith(ROOT_LOGGER):
        name = f"{ROOT_LOGGER}.{name}"
    return logging.getLogger(name)


def setup_console_logging(level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger(ROOT_LOGGER)
    if not any(getattr(h, "_carbonsync_console", False) for h in root.handlers):
        handler = _ConsoleHandler(sys.stdout)
        handler._carbonsync_console = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(_FMT, datefmt="%H:%M:%S"))
        handler.addFilter(_ShortName())
        handler.addFilter(_Redact())
        root.addHandler(handler)
        root.propagate = False
    root.setLevel(level)
    for lib in ("anthropic", "httpx", "httpcore"):  # their own records, if a debug level is ever enabled
        lg = logging.getLogger(lib)
        if not any(isinstance(f, _Redact) for f in lg.filters):
            lg.addFilter(_Redact())
    # Silence chatty third-party loggers in the terminal
    for noisy in ("cmdstanpy", "prophet", "prophet.plot", "transformers", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("prophet").setLevel(logging.ERROR)  # its seasonality caveats are shown in the UI instead
    quiet_cmdstanpy()
    return root


def quiet_cmdstanpy() -> None:
    """cmdstanpy installs its own INFO handler unless one exists; give it a silent one first."""
    lg = logging.getLogger("cmdstanpy")
    if not any(isinstance(h, logging.NullHandler) for h in lg.handlers):
        lg.addHandler(logging.NullHandler())
    lg.setLevel(logging.WARNING)


@contextmanager
def step(log: logging.Logger, message: str) -> Iterator[dict]:
    """Log ``message…`` then ``message ✓ (1.23 s)``; yields a dict receiving ``seconds``."""
    info: dict = {}
    log.info("%s…", message)
    t0 = time.perf_counter()
    try:
        yield info
    except Exception:
        log.exception("%s ✗ failed after %.2f s", message, time.perf_counter() - t0)
        raise
    info["seconds"] = time.perf_counter() - t0
    log.info("%s ✓ (%.2f s)", message, info["seconds"])


class _CallbackHandler(logging.Handler):
    def __init__(self, callback: Callable[[logging.LogRecord, str], None]):
        super().__init__(logging.INFO)
        self.addFilter(_Redact())
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(record, self.format(record))
        except Exception:  # noqa: BLE001 - a UI failure must never break the pipeline
            self.handleError(record)


@contextmanager
def capture(callback: Callable[[logging.LogRecord, str], None]) -> Iterator[None]:
    """Forward every ``carbonsync.*`` INFO+ record to ``callback`` while the block runs."""
    root = logging.getLogger(ROOT_LOGGER)
    handler = _CallbackHandler(callback)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        yield
    finally:
        root.removeHandler(handler)
