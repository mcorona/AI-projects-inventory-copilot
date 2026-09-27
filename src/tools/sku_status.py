"""Tool determinista: ficha de un SKU (producto, proveedor, stock por almacen, ventas 30 dias).

Es la pregunta mas frecuente del agente; resolverla con consultas fijas y parametrizadas
es mas rapido y confiable que pedirle SQL al LLM. Las consultas tambien pasan por el guard.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Callable

from src.guardrails.sql_guard import validate_sql
from src.tools.sql_tool import ANCHOR_DATE, execute_readonly

ParamExecutor = Callable[[str, dict], tuple[list[str], list[tuple]]]

_SKU_RE = re.compile(r"^\s*SKU[-_ ]?(\d{1,4})\s*$", re.IGNORECASE)

PRODUCT_SQL = """
SELECT p.sku, p.name, p.category, p.unit_cost, p.reorder_point, p.critical,
       s.name AS supplier, s.country AS supplier_country, s.lead_time_days
FROM products p JOIN suppliers s ON s.supplier_id = p.supplier_id
WHERE p.sku = %(sku)s"""

STOCK_SQL = """
SELECT w.name AS warehouse, w.city, st.on_hand
FROM stock st JOIN warehouses w ON w.warehouse_id = st.warehouse_id
WHERE st.sku = %(sku)s
ORDER BY w.name"""

SALES_SQL = """
SELECT COALESCE(SUM(units), 0) AS units_30d, ROUND(AVG(units), 2) AS avg_daily_30d
FROM sales_daily
WHERE sku = %(sku)s AND day > %(anchor)s::date - 30 AND day <= %(anchor)s::date"""


def normalize_sku(sku: str) -> str | None:
    """'sku-42', 'SKU 0042', 'SKU-0042' -> 'SKU-0042'; None si no tiene formato de SKU."""
    m = _SKU_RE.match(sku or "")
    return f"SKU-{int(m.group(1)):04d}" if m else None


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _as_dicts(columns: list[str], rows: list[tuple]) -> list[dict]:
    return [{c: _num(v) for c, v in zip(columns, r)} for r in rows]


def get_sku_status(sku: str, executor: ParamExecutor | None = None) -> dict:
    norm = normalize_sku(sku)
    if not norm:
        return {"error": f"SKU invalido: {sku!r}. Formato esperado: SKU-0001"}
    if executor is None:
        dsn = os.environ["PG_DSN"]
        executor = lambda sql, params: execute_readonly(sql, dsn, params)  # noqa: E731

    params = {"sku": norm, "anchor": ANCHOR_DATE}
    cols, rows = executor(validate_sql(PRODUCT_SQL), params)
    if not rows:
        return {"sku": norm, "found": False}
    product = _as_dicts(cols, rows)[0]
    stock = _as_dicts(*executor(validate_sql(STOCK_SQL), params))
    sales = _as_dicts(*executor(validate_sql(SALES_SQL), params))[0]

    total = sum(s["on_hand"] for s in stock)
    avg = sales["avg_daily_30d"] or 0
    return {
        **product, "found": True,
        "stock_by_warehouse": stock,
        "total_on_hand": total,
        "below_reorder_point": total < product["reorder_point"],
        "units_last_30d": sales["units_30d"],
        "avg_daily_units_30d": avg,
        # dias que alcanza el stock total al ritmo de venta de los ultimos 30 dias
        "days_of_cover": round(total / avg, 1) if avg else None,
        "as_of": ANCHOR_DATE.isoformat(),
    }
