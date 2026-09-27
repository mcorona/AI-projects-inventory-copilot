import pytest

from src.llm import ChatResult
from src.tools.sql_tool import extract_sql, run_sql_tool, strip_think


class FakeLLM:
    name = "fake"

    def __init__(self, text: str | None = None, exc: Exception | None = None):
        self.text, self.exc, self.calls = text, exc, []

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024):
        self.calls.append({"messages": messages, "system": system})
        if self.exc:
            raise self.exc
        return ChatResult(text=self.text, provider="fake", model="fake-1",
                          input_tokens=120, output_tokens=30, latency_ms=42.0)


class FakeExecutor:
    def __init__(self, rows=None, exc: Exception | None = None):
        self.rows, self.exc, self.sql = rows or [], exc, []

    def __call__(self, sql):
        self.sql.append(sql)
        if self.exc:
            raise self.exc
        return ["sku"], self.rows


@pytest.mark.parametrize("text,expected", [
    ("<think>razono...</think>SELECT 1", "SELECT 1"),
    ("<think>a</think> x <THINK>b</THINK> y", "x  y"),
    ("<think>truncado sin cerrar SELECT * FROM products", ""),
    ("sin bloques", "sin bloques"),
])
def test_strip_think(text, expected):
    assert strip_think(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("```sql\nSELECT sku FROM products;\n```", "SELECT sku FROM products"),
    ("Claro:\n```\nSELECT 1\n```\nListo.", "SELECT 1"),
    ("<think>uso SELECT * FROM x</think>\n```sql\nSELECT 2;\n```", "SELECT 2"),
    ("La consulta es: SELECT sku FROM stock WHERE on_hand = 0;\nEspero ayude",
     "SELECT sku FROM stock WHERE on_hand = 0"),
    ("WITH t AS (SELECT 1 AS a) SELECT a FROM t", "WITH t AS (SELECT 1 AS a) SELECT a FROM t"),
    ("No puedo responder eso.", None),
])
def test_extract_sql(text, expected):
    assert extract_sql(text) == expected


def test_happy_path_validates_executes_and_reports_metrics():
    llm = FakeLLM("<think>hmm</think>```sql\nSELECT sku FROM products WHERE critical;\n```")
    ex = FakeExecutor(rows=[("SKU-0001",), ("SKU-0002",)])
    r = run_sql_tool("¿Qué productos son críticos?", llm=llm, executor=ex)

    assert r.ok and r.error is None
    assert "LIMIT 200" in r.sql  # el guard fuerza LIMIT
    assert ex.sql == [r.sql]
    assert r.columns == ["sku"] and r.rows == [("SKU-0001",), ("SKU-0002",)]
    assert (r.input_tokens, r.output_tokens, r.llm_latency_ms) == (120, 30, 42.0)
    assert r.provider == "fake" and r.model == "fake-1"
    assert r.total_latency_ms >= r.db_latency_ms
    # el prompt de sistema lleva el esquema y la fecha ancla
    system = llm.calls[0]["system"]
    assert "sales_daily" in system and "2026-09-26" in system
    assert "{anchor}" not in system  # todas las plantillas se resolvieron
    assert "units = 0" in system  # sales_daily es densa: sin ventas = fila con 0
    assert "SIN acentos" in system


@pytest.mark.parametrize("sql", [
    "DELETE FROM products",
    "SELECT * FROM purchase_orders",
])
def test_guard_rejection_skips_execution(sql):
    ex = FakeExecutor()
    r = run_sql_tool("borra todo", llm=FakeLLM(f"```sql\n{sql}\n```"), executor=ex)
    assert not r.ok and r.error.startswith("guard_rejected")
    assert ex.sql == [] and r.sql is None and r.raw_sql == sql
    assert r.input_tokens == 120  # las metricas del LLM se conservan


def test_no_sql_in_response():
    ex = FakeExecutor()
    r = run_sql_tool("hola", llm=FakeLLM("<think>...</think>No sé."), executor=ex)
    assert r.error.startswith("no_sql") and ex.sql == []


def test_db_error_is_reported_not_raised():
    ex = FakeExecutor(exc=RuntimeError("column \"foo\" does not exist"))
    r = run_sql_tool("x", llm=FakeLLM("SELECT foo FROM products"), executor=ex)
    assert r.error.startswith("db_error") and "foo" in r.error
    assert r.sql is not None and r.rows == []


def test_llm_error_is_reported_not_raised():
    r = run_sql_tool("x", llm=FakeLLM(exc=ConnectionError("LM Studio apagado")),
                     executor=FakeExecutor())
    assert r.error.startswith("llm_error") and r.sql is None


def test_truncated_thinking_is_reported_distinctly():
    ex = FakeExecutor()
    text = "<think>pensando en SELECT sku FROM products y luego..."
    r = run_sql_tool("x", llm=FakeLLM(text), executor=ex)
    assert r.error.startswith("truncated") and "30 tokens" in r.error
    assert r.sql is None and r.raw_sql is None and ex.sql == []


def test_closed_think_is_not_truncated():
    r = run_sql_tool("x", llm=FakeLLM("<think>a</think>SELECT sku FROM products"),
                     executor=FakeExecutor())
    assert r.ok
