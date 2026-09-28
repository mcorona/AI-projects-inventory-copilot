"""Faithfulness de respuestas del agente: chequeo determinista de cifras + juez LLM.

- numeric_grounding: cada numero de la respuesta debe aparecer (por valor) en el contexto
  (salidas de tools). Es estricto a proposito: una resta correcta (1036 - 84 = 952) cuenta como
  no respaldada. Senal barata, no veredicto.
- LLMJudge: extrae afirmaciones y clasifica cada una como respaldada / no respaldada /
  contradicha por el contexto; permite aritmetica derivada. Se valida contra
  evals/datasets/judge_calibration.jsonl antes de confiar en su numero.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

_SKU_RE = re.compile(r"\bsku[-_ ]?\d+\b", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"(?<=\d)[,   ](?=\d{3}(?!\d))", "", text)   # 2,326 -> 2326
    return _SKU_RE.sub(" ", text)                                        # los SKU no son cifras


def numbers_in(text: str) -> set[float]:
    return {float(n) for n in _NUM_RE.findall(_norm(text))}


@dataclass
class GroundingResult:
    numbers: list[float]
    unsupported: list[float]

    @property
    def grounded(self) -> bool:
        return not self.unsupported


def numeric_grounding(answer: str, context: str, question: str = "",
                      min_value: float = 10) -> GroundingResult:
    """Numeros >= min_value de la respuesta que no aparecen en el contexto ni en la pregunta
    (repetir una cifra que dio el usuario no es inventarla). Los de un digito suelen ser
    marcadores de lista o conteos triviales y generan ruido."""
    ctx = numbers_in(context) | numbers_in(question)
    nums = sorted(n for n in numbers_in(answer) if n >= min_value)
    return GroundingResult(nums, [n for n in nums if n not in ctx and round(n, 1) not in ctx])


JUDGE_PROMPT = """Eres un evaluador estricto de fidelidad (faithfulness). Recibes el CONTEXTO que
un asistente obtuvo de sus herramientas y la RESPUESTA que dio al usuario.

1. Extrae cada afirmacion factual de la RESPUESTA (cifras, fechas, nombres, reglas de politica).
2. Clasifica cada una:
   - "supported": esta en el CONTEXTO o se deriva con aritmetica simple de el.
   - "unsupported": no esta en el CONTEXTO (aunque pudiera ser cierta).
   - "contradicted": el CONTEXTO dice otra cosa.
3. Las frases sin contenido factual (saludos, ofrecer ayuda, decir que no se encontro algo) no
   son afirmaciones.

Responde SOLO con JSON:
{"claims": [{"claim": "...", "verdict": "supported|unsupported|contradicted"}], "faithful": true|false}
"faithful" es true solo si ninguna afirmacion es unsupported o contradicted."""


@dataclass
class JudgeResult:
    faithful: bool | None          # None si la respuesta del juez no se pudo interpretar
    claims: list[dict] = field(default_factory=list)
    raw: str = ""

    @property
    def support_rate(self) -> float | None:
        if not self.claims:
            return None
        return sum(c.get("verdict") == "supported" for c in self.claims) / len(self.claims)


class LLMJudge:
    def __init__(self, llm, max_tokens: int = 8192):
        self.llm, self.max_tokens = llm, max_tokens
        self.input_tokens = self.output_tokens = 0

    def judge(self, question: str, context: str, answer: str) -> JudgeResult:
        from src.llm.text import strip_think
        user = f"PREGUNTA:\n{question}\n\nCONTEXTO:\n<<<\n{context}\n>>>\n\nRESPUESTA:\n<<<\n{answer}\n>>>"
        r = self.llm.chat([{"role": "user", "content": user}], system=JUDGE_PROMPT,
                          temperature=0.0, max_tokens=self.max_tokens)
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        raw = strip_think(r.text)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group()) if m else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict) or "faithful" not in data:
            return JudgeResult(None, [], raw[:500])
        claims = [c for c in data.get("claims", []) if isinstance(c, dict)]
        # coherencia: el veredicto global se recalcula desde las afirmaciones si las hay
        faithful = (all(c.get("verdict") == "supported" for c in claims) if claims
                    else bool(data["faithful"]))
        return JudgeResult(faithful, claims, raw[:500])
