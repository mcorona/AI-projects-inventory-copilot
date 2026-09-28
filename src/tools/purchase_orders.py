"""Ordenes de compra con aprobacion humana en dos compuertas.

1. El agente PROPONE (`preview_purchase_order` -> confirmacion del usuario -> `create_purchase_order`).
   Se inserta con el rol copilot_po, que solo puede escribir columnas de la propuesta.
2. Una persona con autoridad DECIDE (`decide_purchase_order`, rol copilot_approver).

La DB es la fuente de verdad: un trigger calcula costo, monto y nivel requerido, fuerza
PENDING_APPROVAL e impide aprobar sin el nivel suficiente (db/init/02_hitl_guardrails.sql).
Aqui se replican los umbrales solo para mostrar la vista previa antes de confirmar.
"""
from __future__ import annotations

import os
from typing import Callable

from src.guardrails.sql_guard import validate_sql
from src.tools.sku_status import ParamExecutor, get_sku_status

LEVELS = ("comprador", "gerente", "director")
MAX_DAYS_OF_DEMAND = 90   # tope de cantidad por orden: 90 dias de venta promedio
PO_TABLES = {"purchase_orders"}  # consultas fijas de este modulo; el LLM nunca las escribe

OPEN_ORDERS_SQL = """
SELECT po_id, qty, status, created_at FROM purchase_orders
WHERE sku = %(sku)s AND status IN ('PENDING_APPROVAL', 'APPROVED')
ORDER BY po_id"""

INSERT_SQL = """
INSERT INTO purchase_orders (sku, qty, reason, requested_by, confirmed_by)
VALUES (%(sku)s, %(qty)s, %(reason)s, %(requested_by)s, %(confirmed_by)s)
RETURNING po_id, sku, qty, status, unit_cost, amount, required_level, created_at"""

LIST_SQL = """
SELECT po_id, sku, qty, status, amount, required_level, reason, requested_by, confirmed_by,
       decided_by, decided_level, decided_at, decision_note, created_at
FROM purchase_orders {where} ORDER BY po_id"""

DECIDE_SQL = """
UPDATE purchase_orders
SET status = %(status)s, decided_by = %(decided_by)s, decided_level = %(level)s,
    decision_note = %(note)s
WHERE po_id = %(po_id)s
RETURNING po_id, sku, qty, status, amount, required_level, decided_by, decided_level, decided_at"""


def required_level(amount: float) -> str:
    """Espejo de po_required_level() en la DB (ordenes_de_compra.md)."""
    if amount < 50_000:
        return "comprador"
    if amount <= 250_000:
        return "gerente"
    return "director"


def can_approve(approver_level: str, required: str) -> bool:
    return LEVELS.index(approver_level) >= LEVELS.index(required)


def _default_ro_executor() -> ParamExecutor:
    from src.tools.sql_tool import execute_readonly
    dsn = os.environ["PG_DSN"]
    return lambda sql, params: execute_readonly(sql, dsn, params)


def preview_purchase_order(sku: str, qty, reason: str = "",
                           executor: ParamExecutor | None = None) -> dict:
    """Valida la propuesta y calcula lo que vera la persona antes de confirmar. No escribe nada."""
    executor = executor or _default_ro_executor()
    status = get_sku_status(sku, executor=executor)
    if "error" in status:
        return {"ok": False, "errors": [status["error"]]}
    if not status["found"]:
        return {"ok": False, "errors": [f"No existe el SKU {status['sku']}"]}

    errors = []
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return {"ok": False, "errors": [f"Cantidad invalida: {qty!r}"]}
    if qty <= 0:
        errors.append("La cantidad debe ser mayor que 0")

    avg = status["avg_daily_units_30d"] or 0
    cap = round(avg * MAX_DAYS_OF_DEMAND) if avg else status["reorder_point"]
    if qty > cap:
        errors.append(f"La cantidad {qty} excede el tope de {cap} unidades "
                      f"({MAX_DAYS_OF_DEMAND} dias de venta promedio)")

    cols, rows = executor(validate_sql(OPEN_ORDERS_SQL, allowed_tables=PO_TABLES), {"sku": status["sku"]})
    open_orders = [dict(zip(cols, r)) for r in rows]
    if open_orders:
        ids = ", ".join(str(o["po_id"]) for o in open_orders)
        errors.append(f"Ya existe una orden abierta para {status['sku']} (po_id {ids}); "
                      "la politica de reorden no permite duplicarla")

    amount = round(qty * status["unit_cost"], 2)
    return {
        "ok": not errors, "errors": errors,
        "sku": status["sku"], "name": status["name"], "qty": qty,
        "unit_cost": status["unit_cost"], "amount": amount,
        "required_level": required_level(amount),
        "reason": reason or "",
        "total_on_hand": status["total_on_hand"], "reorder_point": status["reorder_point"],
        "avg_daily_units_30d": avg,
        "days_of_demand": round(qty / avg, 1) if avg else None,
        "supplier": status["supplier"], "lead_time_days": status["lead_time_days"],
    }


Writer = Callable[[str, dict], dict]


def _db_writer(dsn_env: str) -> Writer:
    def write(sql: str, params: dict) -> dict:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ[dsn_env], row_factory=dict_row) as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                raise LookupError("no se encontro la orden")
            return row
    return write


def create_purchase_order(sku: str, qty: int, reason: str, requested_by: str, confirmed_by: str,
                          writer: Writer | None = None) -> dict:
    """Inserta la propuesta (rol copilot_po). Solo se llama despues de la confirmacion humana."""
    writer = writer or _db_writer("PO_DSN")
    return writer(INSERT_SQL, {"sku": sku, "qty": int(qty), "reason": reason,
                               "requested_by": requested_by, "confirmed_by": confirmed_by})


def decide_purchase_order(po_id: int, approve: bool, decided_by: str, level: str, note: str = "",
                          writer: Writer | None = None) -> dict:
    """Aprueba o rechaza (rol copilot_approver). La DB valida autoridad y estado."""
    if level not in LEVELS:
        raise ValueError(f"nivel invalido: {level}. Opciones: {', '.join(LEVELS)}")
    writer = writer or _db_writer("APPROVER_DSN")
    return writer(DECIDE_SQL, {"po_id": po_id, "status": "APPROVED" if approve else "REJECTED",
                               "decided_by": decided_by, "level": level, "note": note})


def list_purchase_orders(status: str | None = None, executor: ParamExecutor | None = None) -> list[dict]:
    executor = executor or _default_ro_executor()
    where, params = ("WHERE status = %(status)s", {"status": status}) if status else ("", {})
    cols, rows = executor(validate_sql(LIST_SQL.format(where=where), allowed_tables=PO_TABLES), params)
    return [dict(zip(cols, r)) for r in rows]
