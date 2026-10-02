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
import unicodedata
from datetime import date, timedelta
from typing import Callable

from src.guardrails.sql_guard import validate_sql
from src.tools.sku_status import ParamExecutor, get_sku_status

LEVELS = ("comprador", "gerente", "director")
MAX_DAYS_OF_DEMAND = 90   # tope de cantidad por orden: 90 dias de venta promedio
PO_TABLES = {"purchase_orders"}  # consultas fijas de este modulo; el LLM nunca las escribe
# motivos validos segun data/docs/ordenes_de_compra.md ("Datos obligatorios de una orden")
REASONS = ("reorden automatico", "compra urgente", "proyecto especial")


def _fold(text) -> str:
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower().strip()


def normalize_reason(reason) -> str | None:
    """Motivo canonico (sin acentos ni mayusculas) o None si no es uno de la politica."""
    text = _fold(reason)
    return text if text in REASONS else None


WAREHOUSES_SQL = "SELECT warehouse_id, name, city FROM warehouses ORDER BY warehouse_id"


def resolve_warehouse(text, warehouses: list[dict]) -> dict | None:
    """CEDIS por nombre completo, nombre corto o ciudad ("CEDIS Norte", "norte", "Monterrey")."""
    t = _fold(text)
    if not t:
        return None
    for w in warehouses:
        name = _fold(w["name"])
        if t in (name, name.removeprefix("cedis "), _fold(w["city"])):
            return w
    return None


def parse_required_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None

OPEN_ORDERS_SQL = """
SELECT po_id, qty, status, created_at FROM purchase_orders
WHERE sku = %(sku)s AND status IN ('PENDING_APPROVAL', 'APPROVED')
ORDER BY po_id"""

INSERT_SQL = """
INSERT INTO purchase_orders (sku, qty, reason, delivery_warehouse_id, required_date, requested_by, confirmed_by)
VALUES (%(sku)s, %(qty)s, %(reason)s, %(delivery_warehouse_id)s, %(required_date)s, %(requested_by)s,
        %(confirmed_by)s)
RETURNING po_id, sku, qty, status, unit_cost, amount, required_level, delivery_warehouse_id, required_date,
          created_at"""

LIST_SQL = """
SELECT po.po_id, po.sku, po.qty, po.status, po.amount, po.required_level, po.reason,
       w.name AS delivery_warehouse, po.required_date, po.requested_by, po.confirmed_by,
       po.decided_by, po.decided_level, po.decided_at, po.decision_note, po.created_at
FROM purchase_orders po LEFT JOIN warehouses w ON w.warehouse_id = po.delivery_warehouse_id
{where} ORDER BY po.po_id"""

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


def preview_purchase_order(sku: str, qty, reason: str = "", warehouse=None, required_date=None,
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
    # sobre el tope, la tool calcula la alternativa: el modelo no debe multiplicar a mano
    amount_at_max = round(cap * status["unit_cost"], 2)
    if qty > cap:
        errors.append(f"La cantidad {qty} excede el tope de {cap} unidades "
                      f"({MAX_DAYS_OF_DEMAND} dias de venta promedio). Se pueden proponer hasta "
                      f"{cap} unidades, con un monto de ${amount_at_max:,.2f} MXN")

    # el motivo es dato obligatorio de la politica; el agente no debe inventarlo
    below_reorder = status["total_on_hand"] < status["reorder_point"]
    canonical = normalize_reason(reason)
    if canonical is None:
        hint = ("el stock esta bajo el punto de reorden, asi que corresponde 'reorden automatico'"
                if below_reorder else "pregunta al usuario el motivo antes de proponer")
        errors.append(f"Motivo invalido o faltante ({reason!r}): debe ser uno de {', '.join(REASONS)}; {hint}")
    elif canonical == "reorden automatico" and not below_reorder:
        errors.append(f"'reorden automatico' no aplica: el stock ({status['total_on_hand']}) no esta bajo el "
                      f"punto de reorden ({status['reorder_point']}); pregunta al usuario el motivo")

    # CEDIS de entrega y fecha requerida (datos obligatorios). Si el usuario no los da, la tool fija
    # valores por defecto deterministas y los marca; la persona los revisa al confirmar.
    from src.tools.sql_tool import ANCHOR_DATE
    defaults, warnings = [], []
    cols, rows = executor(validate_sql(WAREHOUSES_SQL, allowed_tables={"warehouses"}), {})
    warehouses = [dict(zip(cols, r)) for r in rows]
    if warehouse in (None, ""):
        stock = {w["warehouse"]: w["on_hand"] for w in status.get("stock_by_warehouse", [])}
        dest = min(warehouses, key=lambda w: (stock.get(w["name"], 0), w["warehouse_id"])) if warehouses else None
        defaults.append("delivery_warehouse")
    else:
        dest = resolve_warehouse(warehouse, warehouses)
        if dest is None:
            errors.append(f"CEDIS invalido: {warehouse!r}. Opciones: "
                          + ", ".join(f"{w['name']} ({w['city']})" for w in warehouses))
    lead = int(status["lead_time_days"] or 0)
    earliest = ANCHOR_DATE + timedelta(days=lead)
    if required_date in (None, ""):
        needed = earliest
        defaults.append("required_date")
    else:
        needed = parse_required_date(required_date)
        if needed is None:
            errors.append(f"Fecha requerida invalida: {required_date!r} (formato AAAA-MM-DD)")
        elif needed < ANCHOR_DATE:
            errors.append(f"La fecha requerida {needed} ya paso (hoy es {ANCHOR_DATE})")
        elif needed < earliest:
            warnings.append(f"El proveedor tarda {lead} dias: con pedido hoy llegaria el {earliest}, "
                            f"despues de la fecha requerida {needed}")

    cols, rows = executor(validate_sql(OPEN_ORDERS_SQL, allowed_tables=PO_TABLES), {"sku": status["sku"]})
    open_orders = [dict(zip(cols, r)) for r in rows]
    if open_orders:
        ids = ", ".join(str(o["po_id"]) for o in open_orders)
        errors.append(f"Ya existe una orden abierta para {status['sku']} (po_id {ids}); "
                      "la politica de reorden no permite duplicarla")

    amount = round(qty * status["unit_cost"], 2)
    over_cap = {"max_qty": cap, "amount_at_max": amount_at_max,
                "required_level_at_max": required_level(amount_at_max)} if qty > cap else {}
    return {
        "ok": not errors, "errors": errors,
        "sku": status["sku"], "name": status["name"], "qty": qty,
        "unit_cost": status["unit_cost"], "amount": amount, "currency": "MXN",
        "required_level": required_level(amount), **over_cap,
        "reason": canonical or (reason or ""),
        "total_on_hand": status["total_on_hand"], "reorder_point": status["reorder_point"],
        "avg_daily_units_30d": avg,
        "days_of_demand": round(qty / avg, 1) if avg else None,
        "supplier": status["supplier"], "lead_time_days": status["lead_time_days"],
        "delivery_warehouse": dest["name"] if dest else None, "delivery_city": dest["city"] if dest else None,
        "delivery_warehouse_id": dest["warehouse_id"] if dest else None,
        "required_date": needed.isoformat() if needed else None, "earliest_arrival": earliest.isoformat(),
        "defaults": defaults, "warnings": warnings,
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


def create_purchase_order(sku: str, qty: int, reason: str, requested_by: str, confirmed_by: str, *,
                          delivery_warehouse_id: int, required_date: str,
                          writer: Writer | None = None) -> dict:
    """Inserta la propuesta (rol copilot_po). Solo se llama despues de la confirmacion humana."""
    writer = writer or _db_writer("PO_DSN")
    return writer(INSERT_SQL, {"sku": sku, "qty": int(qty), "reason": reason,
                               "delivery_warehouse_id": delivery_warehouse_id, "required_date": required_date,
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
    where, params = ("WHERE po.status = %(status)s", {"status": status}) if status else ("", {})
    cols, rows = executor(validate_sql(LIST_SQL.format(where=where), allowed_tables=PO_TABLES | {"warehouses"}),
                          params)
    return [dict(zip(cols, r)) for r in rows]
