from datetime import date
from decimal import Decimal

import pytest

from src.tools.purchase_orders import (can_approve, create_purchase_order, decide_purchase_order,
                                       preview_purchase_order, required_level)


@pytest.mark.parametrize("amount,level", [
    (0, "comprador"), (49_999.99, "comprador"), (50_000, "gerente"), (250_000, "gerente"),
    (250_000.01, "director"), (1_757_010, "director"),
])
def test_required_level_matches_policy(amount, level):
    assert required_level(amount) == level


def test_can_approve_hierarchy():
    assert can_approve("director", "gerente") and can_approve("gerente", "gerente")
    assert not can_approve("comprador", "gerente")


def executor_for(open_orders=(), avg=Decimal("22.30")):
    def ex(sql, params):
        if "FROM purchase_orders" in sql:
            assert "LIMIT" in sql  # paso por el guard con allowed_tables
            return ["po_id", "qty", "status", "created_at"], list(open_orders)
        if "FROM products" in sql:
            return (["sku", "name", "category", "unit_cost", "reorder_point", "critical", "supplier",
                     "supplier_country", "lead_time_days"],
                    [("SKU-0009", "Perno", "Tornilleria", Decimal("7.77"), 1036, True, "Bavaria", "DE", 30)])
        if "FROM stock" in sql:
            return ["warehouse", "city", "on_hand"], [("CEDIS Centro", "CDMX", 84)]
        return ["units_30d", "avg_daily_30d"], [(669, avg)]
    return ex


def test_preview_computes_amount_level_and_coverage():
    p = preview_purchase_order("sku-9", "1500", "reorden automatico", executor=executor_for())
    assert p["ok"] and p["errors"] == []
    assert (p["sku"], p["qty"], p["amount"], p["required_level"]) == ("SKU-0009", 1500, 11655.0, "comprador")
    assert p["days_of_demand"] == 67.3 and p["reason"] == "reorden automatico"


@pytest.mark.parametrize("qty,msg", [(0, "mayor que 0"), (-5, "mayor que 0"), (999_999, "excede el tope")])
def test_preview_rejects_bad_quantities(qty, msg):
    p = preview_purchase_order("SKU-0009", qty, executor=executor_for())
    assert not p["ok"] and any(msg in e for e in p["errors"])


def test_preview_rejects_non_numeric_qty_and_unknown_sku():
    assert "Cantidad invalida" in preview_purchase_order("SKU-0009", "mucho", executor=executor_for())["errors"][0]
    assert not preview_purchase_order("no-es-sku", 1, executor=executor_for())["ok"]


def test_preview_blocks_duplicate_open_order():
    p = preview_purchase_order("SKU-0009", 100, "reorden automatico",
                               executor=executor_for(open_orders=[(7, 500, "PENDING_APPROVAL", date(2026, 9, 26))]))
    assert not p["ok"] and "po_id 7" in p["errors"][0]


def test_zero_demand_caps_at_reorder_point():
    p = preview_purchase_order("SKU-0009", 1037, executor=executor_for(avg=None))
    assert not p["ok"] and "tope de 1036" in p["errors"][0]


def test_create_and_decide_use_their_own_roles_sql():
    seen = []

    def writer(sql, params):
        seen.append((sql, params))
        return {"po_id": 1, **params}

    create_purchase_order("SKU-0009", 10, "reorden automatico", "copilot:ana", "ana", writer=writer)
    sql, params = seen[0]
    assert sql.strip().startswith("INSERT") and "status" not in sql.split("VALUES")[0]
    assert params == {"sku": "SKU-0009", "qty": 10, "reason": "reorden automatico", "requested_by": "copilot:ana", "confirmed_by": "ana"}

    decide_purchase_order(1, True, "Luis", "gerente", "ok", writer=writer)
    assert seen[1][1] == {"po_id": 1, "status": "APPROVED", "decided_by": "Luis", "level": "gerente", "note": "ok"}
    decide_purchase_order(1, False, "Ana", "comprador", writer=writer)
    assert seen[2][1]["status"] == "REJECTED"
    with pytest.raises(ValueError):
        decide_purchase_order(1, True, "X", "ceo", writer=writer)


def test_migration_files_exclude_base_schema():
    from scripts.migrate import migration_files
    names = [p.name for p in migration_files()]
    assert "02_hitl_guardrails.sql" in names and not any(n.startswith("01_") for n in names)


def test_migration_sql_enforces_key_rules():
    sql = open("db/init/02_hitl_guardrails.sql", encoding="utf-8").read()
    assert "GRANT INSERT (sku, qty, reason, requested_by, confirmed_by) ON purchase_orders TO copilot_po" in sql
    assert "GRANT UPDATE (status, decided_by, decided_level, decision_note) ON purchase_orders TO copilot_approver" in sql
    assert "GRANT INSERT ON audit_log TO copilot_audit" in sql
    assert "Nivel insuficiente" in sql and "NEW.status := 'PENDING_APPROVAL'" in sql
    # los umbrales de la DB coinciden con los de Python
    assert "amount < 50000 THEN 'comprador'" in sql and "amount <= 250000 THEN 'gerente'" in sql


def test_preview_over_cap_returns_the_capped_alternative_already_computed():
    p = preview_purchase_order("SKU-0009", 3000, executor=executor_for())   # tope: 22.3 x 90 = 2007
    assert not p["ok"] and p["currency"] == "MXN"
    assert (p["max_qty"], p["amount_at_max"], p["required_level_at_max"]) == (2007, 15594.39, "comprador")
    assert p["amount"] == 23310.0                       # el monto pedido sigue reportandose
    assert "hasta 2007 unidades, con un monto de $15,594.39 MXN" in p["errors"][0]


def test_preview_within_cap_has_no_capped_alternative():
    p = preview_purchase_order("SKU-0009", 1500, "compra urgente", executor=executor_for())
    assert p["ok"] and "max_qty" not in p and "amount_at_max" not in p


def executor_above_reorder():
    base = executor_for()

    def ex(sql, params):
        if "FROM stock" in sql:      # 2000 unidades: por encima del punto de reorden (1036)
            return ["warehouse", "city", "on_hand"], [("CEDIS Centro", "CDMX", 2000)]
        return base(sql, params)
    return ex


@pytest.mark.parametrize("reason", ["Reorden Automático", "COMPRA URGENTE", "proyecto especial"])
def test_preview_accepts_policy_reasons_ignoring_case_and_accents(reason):
    from src.tools.purchase_orders import normalize_reason
    p = preview_purchase_order("SKU-0009", 100, reason, executor=executor_for())
    assert p["ok"] and p["reason"] == normalize_reason(reason)


def test_preview_rejects_missing_or_free_text_reason_with_a_hint():
    below = preview_purchase_order("SKU-0009", 100, "", executor=executor_for())
    assert not below["ok"] and "corresponde 'reorden automatico'" in below["errors"][0]
    above = preview_purchase_order("SKU-0009", 100, "reponer stock", executor=executor_above_reorder())
    assert not above["ok"] and "pregunta al usuario el motivo" in above["errors"][0]


def test_automatic_reorder_requires_stock_below_reorder_point():
    p = preview_purchase_order("SKU-0009", 100, "reorden automatico", executor=executor_above_reorder())
    assert not p["ok"] and "no aplica" in p["errors"][0]
    assert preview_purchase_order("SKU-0009", 100, "compra urgente", executor=executor_above_reorder())["ok"]
