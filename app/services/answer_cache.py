"""Answer cache helpers for reusing prior expert replies."""

from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize_question_key(text: str) -> str:
    """Build a stable cache key: lowercase, ё→е, strip punctuation, collapse spaces."""
    value = unicodedata.normalize("NFKC", text or "").strip().lower().replace("ё", "е")
    value = _PUNCT_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()
