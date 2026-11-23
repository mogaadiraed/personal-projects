"""Dashboard guide search: what each page and control does, in FR or EN.

The guide lives in ``docs/assistant/guide_<lang>.md``; each ``##`` section is one topic with
a ``<!-- keywords: ... -->`` line. Search is plain keyword scoring with accent folding
(no embeddings, no download), which is enough for a guide of this size.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

GUIDE_DIR = Path(__file__).resolve().parents[3] / "docs" / "assistant"

_STOPWORDS = """
    a au aux avec ce ces cette dans de des du elle en est et il je la le les leur mais me mon ne nous on
    ou par pas pour qu que qui sa se ses son sur ta te tes ton tu un une vos votre vous y comment quoi
    quel quelle quels quelles est-ce ca cela c d l j n s t m faire fait peux peut puis-je dois the an
    and are as at be by can do does for from how i in is it its me my of on or so that this to was what
    when where which who why will with you your
"""
_STOP = frozenset(_STOPWORDS.split())


@dataclass(frozen=True)
class Section:
    title: str
    keywords: tuple[str, ...]
    body: str


def fold(text: str) -> str:
    """Lower case, accents removed: 'Électricité' -> 'electricite'."""
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def _stem(word: str) -> str:
    for suffix in ("ements", "ement", "ations", "ation", "ing", "ies", "es", "s", "e"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", fold(text)) if w not in _STOP and len(w) > 1]


def _lang(lang: str) -> str:
    return "en" if str(lang).lower().startswith("en") else "fr"


def guide_path(lang: str) -> Path:
    return GUIDE_DIR / f"guide_{_lang(lang)}.md"


@lru_cache(maxsize=4)
def _parse(path: Path, _mtime: int) -> tuple[Section, ...]:
    text = path.read_text(encoding="utf-8")
    out = []
    for chunk in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, rest = chunk.partition("\n")
        m = re.search(r"<!--\s*keywords:(.*?)-->", rest, flags=re.DOTALL)
        keywords = tuple(k.strip() for k in m.group(1).split(",") if k.strip()) if m else ()
        body = re.sub(r"<!--.*?-->", "", rest, flags=re.DOTALL).strip()
        out.append(Section(title.strip(), keywords, body))
    return tuple(out)


def sections(lang: str = "fr") -> tuple[Section, ...]:
    path = guide_path(lang)
    if not path.exists():
        return ()
    return _parse(path, path.stat().st_mtime_ns)


def guide_text(lang: str = "fr") -> str:
    """The whole guide as plain text (fits in a system prompt: about 2-3k tokens)."""
    return "\n\n".join(f"## {s.title}\n{s.body}" for s in sections(lang))


def topics(lang: str = "fr") -> list[str]:
    return [s.title for s in sections(lang)]


def search(query: str, lang: str = "fr", k: int = 3) -> list[dict[str, object]]:
    """Best guide sections for a question, highest score first (empty list if nothing matches)."""
    q = tokens(query)
    if not q:
        return []
    q_set = set(q)
    q_folded = fold(query)
    scored = []
    for s in sections(lang):
        title_t = set(tokens(s.title))
        kw_t = set(tokens(" ".join(s.keywords)))
        body_t = tokens(s.body)
        body_set = set(body_t)
        score = 4.0 * len(q_set & title_t) + 2.5 * len(q_set & kw_t)
        score += sum(min(body_t.count(w), 3) for w in q_set & body_set) * 0.5
        # whole keyword phrases ("model lab", "jours imputés") are a strong signal
        score += sum(3.0 for kw in s.keywords if " " in kw and fold(kw) in q_folded)
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [{"title": s.title, "text": s.body, "score": round(sc, 1)} for sc, s in scored[:k]]
