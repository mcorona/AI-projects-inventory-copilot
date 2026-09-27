"""Utilidades de texto para respuestas de modelos de razonamiento (Qwen emite <think>)."""
from __future__ import annotations

import re

_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK_RE = re.compile(r"<think>(?!.*</think>)", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    """Elimina bloques <think>...</think> (incluido uno sin cerrar por truncamiento)."""
    return _THINK_RE.sub("", text or "").strip()


def is_truncated_think(text: str) -> bool:
    """True si el modelo agoto max_tokens razonando (<think> abierto sin cerrar)."""
    return bool(_UNCLOSED_THINK_RE.search(text or ""))
