import psycopg
import pytest

from tests.integration.conftest import role_dsn


def run_as(role: str, sql: str, params=None):
    with psycopg.connect(role_dsn(role), autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


def denied(role: str, sql: str, match: str = "permission denied"):
    with pytest.raises(psycopg.Error, match=match):
        run_as(role, sql)


# ------------------------------------------------------------ minimo privilegio

def test_agent_role_can_only_propose(clean_orders):
    row = run_as("copilot_po", "INSERT INTO purchase_orders (sku, qty, requested_by, confirmed_by) "
                               "VALUES ('SKU-0009', 10000, 'itest', 'itest') RETURNING status, amount, required_level")
    assert row == [("PENDING_APPROVAL", 77700, "gerente")]   # la DB calcula monto y nivel
    denied("copilot_po", "INSERT INTO purchase_orders (sku, qty, status) VALUES ('SKU-0001', 1, 'APPROVED')")
    denied("copilot_po", "UPDATE purchase_orders SET status = 'APPROVED'")
    denied("copilot_po", "DELETE FROM purchase_orders")
    denied("copilot_ro", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-0001', 1)")


def test_one_pending_order_per_sku_and_input_validation(clean_orders):
    run_as("copilot_po", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-0009', 1)")
    denied("copilot_po", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-0009', 2)", "po_one_pending_per_sku")
    denied("copilot_po", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-9999', 1)", "SKU inexistente")
    denied("copilot_po", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-0001', -5)", "po_qty_positive")


def test_approval_requires_authority_and_is_final(clean_orders):
    run_as("copilot_po", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-0009', 10000)")   # gerente
    run_as("copilot_po", "INSERT INTO purchase_orders (sku, qty) VALUES ('SKU-0179', 1000)")    # director
    denied("copilot_approver", "UPDATE purchase_orders SET status='APPROVED', decided_by='a', decided_level='comprador' "
                               "WHERE sku='SKU-0009'", "Nivel insuficiente")
    denied("copilot_approver", "UPDATE purchase_orders SET status='APPROVED', decided_by='l', decided_level='gerente' "
                               "WHERE sku='SKU-0179'", "Nivel insuficiente")
    denied("copilot_approver", "UPDATE purchase_orders SET qty=1 WHERE sku='SKU-0009'")
    assert run_as("copilot_approver", "UPDATE purchase_orders SET status='APPROVED', decided_by='s', "
                                      "decided_level='director' WHERE sku='SKU-0179' RETURNING status") == [("APPROVED",)]
    denied("copilot_approver", "UPDATE purchase_orders SET status='REJECTED', decided_by='s', decided_level='director' "
                               "WHERE sku='SKU-0179'", "ya fue decidida")
    assert run_as("copilot_approver", "UPDATE purchase_orders SET status='REJECTED', decided_by='a', "
                                      "decided_level='comprador' WHERE sku='SKU-0009' RETURNING status") == [("REJECTED",)]


def test_audit_log_is_append_only(clean_orders):
    run_as("copilot_audit", "INSERT INTO audit_log (actor, event) VALUES ('itest', 'probe')")
    denied("copilot_audit", "SELECT * FROM audit_log")
    denied("copilot_audit", "DELETE FROM audit_log")
    denied("copilot_audit", "UPDATE audit_log SET actor = 'x'")


# ------------------------------------------------------------ flujo Python con roles reales

def test_purchase_order_flow_through_python(clean_orders, role_env, admin):
    from src.audit import DbAuditSink
    from src.tools.purchase_orders import create_purchase_order, decide_purchase_order, preview_purchase_order

    p = preview_purchase_order("sku-9", 1500, "reorden automatico")
    assert p["ok"] and p["required_level"] == "comprador"
    row = create_purchase_order(p["sku"], p["qty"], p["reason"], "copilot:itest", "itest")
    assert row["status"] == "PENDING_APPROVAL" and float(row["amount"]) == p["amount"]   # vista previa == DB
    assert any("orden abierta" in e for e in preview_purchase_order("SKU-0009", 10, "reorden automatico")["errors"])
    decided = decide_purchase_order(row["po_id"], True, "itest", "comprador")
    assert decided["status"] == "APPROVED"
    DbAuditSink(role_dsn("copilot_audit")).log("itest", "po_decided", {"po_id": row["po_id"]})
    assert admin.execute("SELECT count(*) FROM audit_log WHERE actor='itest'").fetchone() == (1,)


# ------------------------------------------------------------ datos y consultas fijas

@pytest.mark.parametrize("split", ["dev", "test"])
def test_reference_sql_runs_as_read_only_role_and_is_not_empty(split):
    from evals.datasets import load_dataset
    from src.guardrails.sql_guard import validate_sql
    from src.tools.sql_tool import execute_readonly
    for row in load_dataset("sql", split):
        cols, rows = execute_readonly(validate_sql(row["sql"]), role_dsn("copilot_ro"))
        assert rows, row["id"]


def test_sku_status_fixed_queries(role_env):
    from src.tools.sku_status import get_sku_status
    s = get_sku_status("SKU-0009")
    assert s["found"] and s["total_on_hand"] == 84 and s["below_reorder_point"] and s["days_of_cover"] == 3.8


def test_pgvector_search_orders_by_cosine_distance(admin, role_env):
    """El operador <=> debe llegar intacto a Postgres (sqlglot lo reescribia)."""
    from src.rag.store import search, to_vector_literal

    def onehot(i):
        v = [0.0] * 1024
        v[i] = 1.0
        return v

    admin.execute("DELETE FROM doc_chunks WHERE metadata->>'embed_model' = 'itest:fake'")
    for i, (src, vec) in enumerate([("a.md", onehot(0)), ("b.md", onehot(1))]):
        admin.execute("INSERT INTO doc_chunks (source, chunk_index, content, metadata, embedding) "
                      "VALUES (%s, %s, %s, %s, %s::vector)",
                      (src, i, f"contenido {src}", '{"embed_model": "itest:fake", "section": "S"}', to_vector_literal(vec)))

    class Embedder:
        name, embed_model = "itest", "fake"

        def embed(self, texts):
            v = [0.0] * 1024
            v[1], v[0] = 0.9, 0.1          # mucho mas cerca de b.md
            return [v]

    try:
        hits = search("x", k=2, embedder=Embedder())
        assert [h["source"] for h in hits] == ["b.md", "a.md"] and hits[0]["score"] > hits[1]["score"]
    finally:
        admin.execute("DELETE FROM doc_chunks WHERE metadata->>'embed_model' = 'itest:fake'")
