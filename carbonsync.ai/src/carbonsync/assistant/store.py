"""Saved conversations: one JSON file per chat in ``data/assistant/chats/`` (local only, git-ignored)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from carbonsync.assistant.context import get_project_root
from carbonsync.assistant.engine import Chat
from carbonsync.obs import get_logger

log = get_logger(__name__)


def chats_dir(root: Path | None = None) -> Path:
    d = (root or get_project_root()) / "data" / "assistant" / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(chat: Chat, root: Path | None = None) -> Path:
    path = chats_dir(root) / f"{chat.id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(chat.to_dict(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load(chat_id: str, root: Path | None = None) -> Chat | None:
    path = chats_dir(root) / f"{Path(chat_id).name}.json"
    if not path.exists():
        return None
    try:
        return Chat.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, KeyError):
        log.warning("Unreadable conversation file: %s", path.name)
        return None


def recent(limit: int = 15, root: Path | None = None) -> list[dict[str, Any]]:
    """Newest first: id, title, number of questions, last update."""
    out = []
    for p in sorted(chats_dir(root).glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"id": d.get("id", p.stem), "title": d.get("title") or "—", "questions": len(d.get("turns", [])),
                    "updated": p.stat().st_mtime})
    return out


def delete(chat_id: str, root: Path | None = None) -> bool:
    path = chats_dir(root) / f"{Path(chat_id).name}.json"
    if path.exists():
        path.unlink()
        return True
    return False
