from collections import Counter
from datetime import timedelta

from scripts.generate_data import COLUMNS, N_DAYS, build_dataset
from src.tools.sql_tool import ANCHOR_DATE

DATA = build_dataset()


def test_deterministic_with_same_seed():
    assert build_dataset(42) == DATA


def test_different_seed_changes_data():
    assert build_dataset(7)["stock"] != DATA["stock"]


def test_row_counts():
    assert len(DATA["products"]) == 200
    assert len(DATA["warehouses"]) == 3
    assert len(DATA["stock"]) == 200 * 3
    assert len(DATA["sales_daily"]) == 200 * N_DAYS


def test_critical_ratio_is_15_percent():
    assert sum(p[5] for p in DATA["products"]) == 30


def test_rows_match_column_definitions():
    for table, rows in DATA.items():
        assert all(len(r) == len(COLUMNS[table]) for r in rows), table


def test_some_skus_below_reorder_point():
    totals = Counter()
    for sku, _, qty in DATA["stock"]:
        totals[sku] += qty
    below = [p for p in DATA["products"] if totals[p[0]] < p[4]]
    assert 10 <= len(below) <= 40
    assert any(p[5] for p in below), "al menos un SKU critico bajo reorden"


def test_sales_window_ends_at_anchor():
    days = {d for _, d, _ in DATA["sales_daily"]}
    assert max(days) == ANCHOR_DATE
    assert min(days) == ANCHOR_DATE - timedelta(days=N_DAYS - 1)
    assert len(days) == N_DAYS


def test_referential_integrity_and_valid_values():
    supplier_ids = {s[0] for s in DATA["suppliers"]}
    warehouse_ids = {w[0] for w in DATA["warehouses"]}
    skus = {p[0] for p in DATA["products"]}
    assert len(skus) == 200
    assert all(p[6] in supplier_ids and p[3] > 0 and p[4] > 0 for p in DATA["products"])
    assert all(s[0] in skus and s[1] in warehouse_ids and s[2] >= 0 for s in DATA["stock"])
    assert all(s[0] in skus and s[2] >= 0 for s in DATA["sales_daily"])
    assert all(s[3] > 0 for s in DATA["suppliers"])
