"""Verificador de resultados de text-to-SQL para el router en cascada.

La cascada original solo escala ante fallos detectables (guard, DB, sin filas). Los errores
semanticos -SQL valido que devuelve la respuesta equivocada- pasaban sin escalar (ADR-005).
Este verificador le pide a un modelo barato que juzgue si el SQL y una muestra del resultado
responden la pregunta; si no, el router escala al nivel capaz.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

VERIFIER_PROMPT = """Revisas consultas SQL generadas por otro modelo sobre un inventario.
Decide si la CONSULTA y su RESULTADO responden exactamente la PREGUNTA:
- filtros, periodos y agrupaciones correctos;
- la cantidad de filas tiene sentido (p. ej. "¿que almacen...?" espera 1 fila, no 200);
- las columnas contienen lo que se pidio.
No exijas un formato particular ni columnas extra. Ante la duda razonable, acepta.

Responde SOLO con JSON: {"ok": true|false, "reason": "<breve>"}"""

MAX_SAMPLE_ROWS = 10


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class SQLVerifier:
    def __init__(self, llm, schema_hint: str = "", max_tokens: int = 2048):
        self.llm, self.schema_hint, self.max_tokens = llm, schema_hint, max_tokens

    def verify(self, question: str, sql: str, columns: list[str], rows: list[tuple]) -> Verdict:
        from src.llm.text import strip_think
        sample = [list(r) for r in rows[:MAX_SAMPLE_ROWS]]
        user = (f"PREGUNTA:\n{question}\n\nCONSULTA:\n{sql}\n\nRESULTADO ({len(rows)} filas; "
                f"muestra de {len(sample)}):\ncolumnas={columns}\n"
                f"{json.dumps(sample, ensure_ascii=False, default=str)}")
        system = VERIFIER_PROMPT + (f"\n\nEsquema:\n{self.schema_hint}" if self.schema_hint else "")
        r = self.llm.chat([{"role": "user", "content": user}], system=system,
                          temperature=0.0, max_tokens=self.max_tokens)
        raw = strip_think(r.text)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group()) if m else {}
        except json.JSONDecodeError:
            data = {}
        # ilegible: se acepta (fail-open) para no escalar por un fallo del verificador
        ok = bool(data.get("ok", True))
        return Verdict(ok, str(data.get("reason", "respuesta ilegible" if not data else ""))[:200],
                       r.input_tokens, r.output_tokens, r.latency_ms)
