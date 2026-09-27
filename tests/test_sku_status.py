from datetime import date
from decimal import Decimal

import pytest

from src.guardrails.sql_guard import validate_sql
from src.tools.sku_status import PRODUCT_SQL, SALES_SQL, STOCK_SQL, get_sku_status, normalize_sku


@pytest.mark.parametrize("raw,expected", [
    ("SKU-0042", "SKU-0042"), ("sku-42", "SKU-0042"), (" SKU 7 ", "SKU-0007"),
    ("SKU_0100", "SKU-0100"), ("0042", None), ("SKU-0001; DROP TABLE x", None), ("", None),
])
def test_normalize_sku(raw, expected):
    assert normalize_sku(raw) == expected


def make_executor(product=True, avg=Decimal("22.30")):
    calls = []

    def executor(sql, params):
        calls.append((sql, params))
        if "FROM products" in sql:
            cols = ["sku", "name", "category", "unit_cost", "reorder_point", "critical",
                    "supplier", "supplier_country", "lead_time_days"]
            return cols, ([("SKU-0009", "Perno", "Tornilleria", Decimal("7.77"), 1036, True,
                            "Bavaria", "DE", 30)] if product else [])
        if "FROM stock" in sql:
            return ["warehouse", "city", "on_hand"], [("CEDIS Centro", "CDMX", 21), ("CEDIS Norte", "MTY", 63)]
        return ["units_30d", "avg_daily_30d"], [(669 if avg else 0, avg)]
    return executor, calls


def test_status_combines_queries_and_derives_metrics():
    executor, calls = make_executor()
    s = get_sku_status("sku-9", executor=executor)
    assert s["found"] and s["sku"] == "SKU-0009"
    assert s["total_on_hand"] == 84 and s["below_reorder_point"] is True
    assert s["days_of_cover"] == 3.8 and s["unit_cost"] == 7.77  # Decimal -> float
    assert all(p == {"sku": "SKU-0009", "anchor": date(2026, 9, 26)} for _, p in calls)
    assert all("LIMIT 200" in sql for sql, _ in calls)  # las consultas pasaron por el guard


def test_no_sales_gives_no_days_of_cover():
    executor, _ = make_executor(avg=None)
    assert get_sku_status("SKU-0009", executor=executor)["days_of_cover"] is None


def test_not_found_and_invalid():
    executor, calls = make_executor(product=False)
    assert get_sku_status("SKU-9999", executor=executor) == {"sku": "SKU-9999", "found": False}
    assert len(calls) == 1
    assert "error" in get_sku_status("DROP TABLE products", executor=executor)


@pytest.mark.parametrize("sql", [PRODUCT_SQL, STOCK_SQL, SALES_SQL])
def test_fixed_queries_keep_placeholders_through_guard(sql):
    out = validate_sql(sql)
    assert "%(sku)s" in out
