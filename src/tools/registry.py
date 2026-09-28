"""Registro de tools compartido por el agente y el MCP server (una sola implementacion).

Cada tool declara su contrato en formato neutro (nombre, descripcion, JSON Schema) y una
funcion que recibe los argumentos y devuelve un dict serializable. Las dependencias (LLM,
ejecutores de DB, embedder) se inyectan para poder probar sin servicios.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

MAX_ROWS_TO_MODEL = 50       # filas que ve el LLM; el conteo real se reporta aparte
MAX_TOOL_OUTPUT_CHARS = 8000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict], dict]
    # human-in-the-loop: el agente se pausa y muestra `preview(args)` antes de ejecutar `fn`
    requires_confirmation: bool = False
    preview: Callable[[dict], dict] | None = field(default=None, repr=False)

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _default(o: Any):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def to_json(result: dict, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    text = json.dumps(result, ensure_ascii=False, default=_default)
    if len(text) > max_chars:
        text = text[:max_chars] + ' ... [salida truncada]'
    return text


def build_tools(llm=None, sql_executor=None, param_executor=None, embedder=None,
                po_writer=None, audit=None, include_actions: bool = True) -> dict[str, Tool]:
    """Construye las tools. Los argumentos None usan la configuracion del entorno.

    `include_actions=False` deja solo las tools de lectura (lo usa el MCP server).
    """
    from src.audit import NullAuditSink
    from src.rag.store import search
    from src.tools import purchase_orders as po
    from src.tools.sku_status import get_sku_status
    from src.tools.sql_tool import run_sql_tool

    def query_inventory(args: dict) -> dict:
        r = run_sql_tool(args["question"], llm=llm, executor=sql_executor)
        out = {"sql": r.sql, "columns": r.columns, "rows": r.rows[:MAX_ROWS_TO_MODEL],
               "row_count": len(r.rows), "error": r.error}
        if len(r.rows) > MAX_ROWS_TO_MODEL:
            out["note"] = f"se muestran {MAX_ROWS_TO_MODEL} de {len(r.rows)} filas"
        if r.escalations:
            out["escalations"] = r.escalations
        return out

    def sku_status(args: dict) -> dict:
        return get_sku_status(args["sku"], executor=param_executor)

    def search_documents(args: dict) -> dict:
        hits = search(args["query"], k=int(args.get("k", 4)), embedder=embedder, executor=param_executor)
        return {"results": [{"source": h["source"], "section": h["section"],
                             "score": h["score"], "content": h["content"]} for h in hits]}

    audit = audit or NullAuditSink()

    def preview_po(args: dict) -> dict:
        return po.preview_purchase_order(args["sku"], args["qty"], args.get("reason", ""),
                                         executor=param_executor)

    def propose_po(args: dict) -> dict:
        # solo el loop del agente inyecta _confirmed_by tras la confirmacion humana
        if not args.get("_confirmed_by"):
            return {"error": "propose_purchase_order requiere confirmacion humana previa"}
        p = preview_po(args)   # se revalida: el estado pudo cambiar mientras se confirmaba
        if not p["ok"]:
            return {"error": "; ".join(p["errors"])}
        row = po.create_purchase_order(p["sku"], p["qty"], p["reason"], args["_requested_by"],
                                       args["_confirmed_by"], writer=po_writer)
        audit.log(args["_requested_by"], "po_created", {**row, "confirmed_by": args["_confirmed_by"]})
        return {**row, "message": f"Orden #{row['po_id']} creada en PENDING_APPROVAL; requiere "
                                  f"aprobacion de nivel {row['required_level']}."}

    tools = [
        Tool("query_inventory",
             "Responde preguntas analiticas sobre el inventario generando y ejecutando SQL de solo "
             "lectura: conteos, rankings, agregaciones, ventas por periodo, listas de SKUs que "
             "cumplen una condicion (p. ej. bajo punto de reorden). Recibe la pregunta en lenguaje natural.",
             {"type": "object",
              "properties": {"question": {"type": "string", "description": "Pregunta en lenguaje natural"}},
              "required": ["question"]},
             query_inventory),
        Tool("get_sku_status",
             "Ficha completa de UN SKU especifico: producto, proveedor y lead time, existencias por "
             "CEDIS, stock total, punto de reorden, ventas de los ultimos 30 dias y dias de cobertura. "
             "Preferir sobre query_inventory cuando la pregunta es sobre un SKU concreto.",
             {"type": "object",
              "properties": {"sku": {"type": "string", "description": "SKU, p. ej. SKU-0042"}},
              "required": ["sku"]},
             sku_status),
        Tool("search_documents",
             "Busca en las politicas y procedimientos internos (reorden, SKUs criticos, ordenes de "
             "compra y aprobaciones, proveedores, recepcion, devoluciones, inventario ciclico, "
             "transferencias entre CEDIS, uso del copiloto). Devuelve fragmentos con su fuente.",
             {"type": "object",
              "properties": {"query": {"type": "string", "description": "Que buscar"},
                             "k": {"type": "integer", "description": "Numero de fragmentos (1-10)",
                                   "default": 4}},
              "required": ["query"]},
             search_documents),
    ]
    if include_actions:
        tools.append(Tool(
            "propose_purchase_order",
            "Propone una orden de compra (queda PENDING_APPROVAL). Usar SOLO si el usuario pide "
            "explicitamente crear o proponer una orden. El sistema calcula el monto y el nivel de "
            "aprobacion; el usuario confirma antes de crearla y una persona con autoridad la aprueba.",
            {"type": "object",
             "properties": {"sku": {"type": "string", "description": "SKU, p. ej. SKU-0042"},
                            "qty": {"type": "integer", "description": "Unidades a pedir"},
                            "reason": {"type": "string", "description": "Motivo de la orden"}},
             "required": ["sku", "qty", "reason"]},
            propose_po, requires_confirmation=True, preview=preview_po))
    return {t.name: t for t in tools}
