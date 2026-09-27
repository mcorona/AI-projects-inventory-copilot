"""Chunking de Markdown por secciones.

Cada seccion `##` es la unidad natural de una politica, asi que se usa como chunk; si una
seccion excede `max_chars` se parte por parrafos con traslape. Cada chunk lleva un
encabezado de contexto ("Documento > Seccion") para que el embedding no pierda de que
documento viene un fragmento corto como "Plazo: 30 dias".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_CHARS = 1600      # ~400 tokens
OVERLAP_CHARS = 200

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    source: str
    index: int
    title: str
    section: str
    content: str
    metadata: dict = field(default_factory=dict)


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > max_chars:
            parts.append(current)
            current = current[-overlap:].lstrip() + "\n\n" + p if overlap else p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        parts.append(current)
    return parts


def chunk_markdown(text: str, source: str, max_chars: int = MAX_CHARS,
                   overlap: int = OVERLAP_CHARS) -> list[Chunk]:
    m = _H1_RE.search(text)
    title = m.group(1).strip() if m else source
    heads = list(_H2_RE.finditer(text))
    sections: list[tuple[str, str]] = []
    # el preambulo (entre el titulo y la primera ##) solo se indexa si tiene contenido propio
    pre_start = m.end() if m else 0
    preamble = text[pre_start: heads[0].start() if heads else len(text)]
    # las lineas de cita (> ...) son notas de control del documento, no contenido
    preamble = "\n".join(ln for ln in preamble.splitlines() if not ln.lstrip().startswith(">")).strip()
    if preamble:
        sections.append(("Introduccion", preamble))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.end():end].strip()
        if body:
            sections.append((h.group(1).strip(), body))

    chunks: list[Chunk] = []
    for section, body in sections:
        for part in _split_long(body, max_chars, overlap):
            chunks.append(Chunk(source=source, index=len(chunks), title=title, section=section,
                                content=f"Documento: {title} > {section}\n\n{part}"))
    return chunks
