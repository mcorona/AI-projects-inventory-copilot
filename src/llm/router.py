"""Router de modelos en cascada: nivel barato/rapido primero, escala al capaz si falla.

Dos puntos de escalamiento:
- `chat()`: falla del proveedor (red, cuota, modelo caido), respuesta vacia o truncada.
- `run(fn, accept)`: la tool decide si el resultado es aceptable (p. ej. el SQL fue
  rechazado por el guard, fallo en la DB o no devolvio filas) y escala si no.
Los tokens y la latencia se acumulan en todos los intentos: es el costo real.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, TypeVar

from src.llm import ChatResult, LLMProvider
from src.llm.text import is_truncated_think, strip_think

T = TypeVar("T")
DEFAULT_TIERS = "omniroute:kr/minimax-m2.1,lmstudio:qwen/qwen3.6-35b-a3b"


@dataclass
class Attempt:
    tier: int
    provider: str
    model: str
    ok: bool
    reason: str = ""


def parse_tiers(spec: str) -> list[tuple[str, str | None]]:
    """'omniroute:kr/minimax-m2.1,lmstudio' -> [('omniroute', 'kr/minimax-m2.1'), ('lmstudio', None)].
    Solo el primer ':' separa proveedor y modelo (los modelos pueden llevar ':free')."""
    tiers = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        provider, _, model = part.partition(":")
        tiers.append((provider.strip().lower(), model.strip() or None))
    if not tiers:
        raise ValueError("ROUTER_TIERS vacio")
    return tiers


class CascadeRouter:
    name = "router"

    def __init__(self, tiers: list[LLMProvider]):
        if not tiers:
            raise ValueError("el router necesita al menos un nivel")
        self.tiers = tiers

    @classmethod
    def from_env(cls) -> "CascadeRouter":
        from src.llm import get_provider
        spec = os.getenv("ROUTER_TIERS", DEFAULT_TIERS)
        return cls([get_provider(p, m) for p, m in parse_tiers(spec)])

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024, tools=None) -> ChatResult:
        attempts: list[Attempt] = []
        in_tok = out_tok = 0
        latency = 0.0
        last_exc: Exception | None = None
        for i, tier in enumerate(self.tiers):
            model = getattr(tier, "chat_model", "")
            try:
                r = tier.chat(messages, system=system, temperature=temperature,
                              max_tokens=max_tokens, tools=tools)
            except Exception as e:  # proveedor caido: escala
                last_exc = e
                attempts.append(Attempt(i, tier.name, model, False, f"error: {e}"))
                continue
            in_tok, out_tok, latency = in_tok + r.input_tokens, out_tok + r.output_tokens, latency + r.latency_ms
            reason = _chat_rejection(r)
            attempts.append(Attempt(i, r.provider, r.model, reason == "", reason))
            if reason == "" or i == len(self.tiers) - 1:
                r.input_tokens, r.output_tokens, r.latency_ms = in_tok, out_tok, latency
                r.raw = {**r.raw, "attempts": attempts}
                return r
        raise RuntimeError(f"todos los niveles del router fallaron: {last_exc}") from last_exc

    def run(self, fn: Callable[[LLMProvider], T], accept: Callable[[T], bool]) -> tuple[T, list[T]]:
        """Ejecuta `fn` con cada nivel hasta que `accept` lo apruebe; devuelve (final, intentos)."""
        results: list[T] = []
        for tier in self.tiers:
            res = fn(tier)
            results.append(res)
            if accept(res):
                break
        return results[-1], results

    def embed(self, texts):
        raise NotImplementedError("router: usa get_embedder() para embeddings")


def _chat_rejection(r: ChatResult) -> str:
    if r.tool_calls:
        return ""
    if is_truncated_think(r.text):
        return "truncated"
    if not strip_think(r.text):
        return "empty"
    return ""
