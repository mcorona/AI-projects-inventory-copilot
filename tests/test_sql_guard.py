import pytest

from src.guardrails.sql_guard import SQLRejected, validate_sql


def test_simple_select_gets_limit():
    out = validate_sql("SELECT sku, name FROM products WHERE critical = true")
    assert "LIMIT 200" in out


def test_join_allowed():
    out = validate_sql(
        "SELECT p.sku, s.on_hand FROM products p JOIN stock s ON p.sku = s.sku "
        "WHERE s.on_hand < p.reorder_point")
    assert out.upper().startswith("SELECT")


def test_cte_allowed():
    out = validate_sql(
        "WITH v AS (SELECT sku, SUM(units) u FROM sales_daily GROUP BY sku) "
        "SELECT * FROM v ORDER BY u DESC LIMIT 10")
    assert "LIMIT 10" in out


def test_large_limit_capped():
    assert "LIMIT 200" in validate_sql("SELECT * FROM products LIMIT 100000")


@pytest.mark.parametrize("sql", [
    "DELETE FROM products",
    "UPDATE stock SET on_hand = 0",
    "DROP TABLE products",
    "INSERT INTO purchase_orders (sku, qty) VALUES ('A', 1)",
    "SELECT 1; DROP TABLE products",
    "SELECT * FROM purchase_orders",
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM doc_chunks",
])
def test_rejected(sql):
    with pytest.raises(SQLRejected):
        validate_sql(sql)
