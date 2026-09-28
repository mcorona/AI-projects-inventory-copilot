from datetime import date
from decimal import Decimal

import pytest

from evals.run_sql_eval import evaluate, load_golden, normalize_rows, results_match
from src.guardrails.sql_guard import validate_sql
from src.llm import ChatResult

GOLDEN = load_golden()


def test_golden_set_shape():
    assert len(GOLDEN) == 31
    assert len({g["id"] for g in GOLDEN}) == 31
    for g in GOLDEN:
        assert g["question"].strip() and g["sql"].strip()
        assert isinstance(g["order_matters"], bool)


@pytest.mark.parametrize("g", GOLDEN, ids=[g["id"] for g in GOLDEN])
def test_golden_sql_passes_guard(g):
    validate_sql(g["sql"])


def test_golden_sql_has_no_relative_dates():
    for g in GOLDEN:
        low = g["sql"].lower()
        assert "now()" not in low and "current_date" not in low, g["id"]


def test_normalize_rows_handles_decimal_float_and_dates():
    assert normalize_rows([(Decimal("1.005"), 2, date(2026, 9, 1), None, True)]) == \
        normalize_rows([(1.0049999, 2.0, date(2026, 9, 1), None, True)])


@pytest.mark.parametrize("gold,pred,order,expected", [
    ([(1,), (2,)], [(2,), (1,)], False, True),          # orden ignorado
    ([(1,), (2,)], [(2,), (1,)], True, False),          # orden importa
    ([(1,), (1,)], [(1,)], False, False),               # duplicados cuentan
    ([(Decimal("454.52"),)], [(454.52,)], False, True),  # Decimal vs float
    ([("A", 1)], [(1, "A")], False, True),               # columnas reordenadas
    ([("A",)], [("A", "Nombre A")], False, True),        # columna extra tolerada
    ([("A", 1)], [("A",)], False, False),                # falta columna
    ([], [], False, True),
    ([(1,)], [(2,)], False, False),
])
def test_results_match(gold, pred, order, expected):
    assert results_match(gold, pred, order) is expected


def test_strict_mode_rejects_extra_columns():
    assert results_match([("A",)], [("A", "x")], allow_extra_columns=False) is False
    assert results_match([("A", 1)], [(1, "A")], allow_extra_columns=False) is True


def test_evaluate_end_to_end_with_mocks():
    golden = [
        {"id": "a", "question": "q1", "sql": "SELECT sku FROM products", "order_matters": False},
        {"id": "b", "question": "q2", "sql": "SELECT COUNT(*) FROM products", "order_matters": False},
    ]
    answers = {"q1": "```sql\nSELECT sku, name FROM products\n```", "q2": "```sql\nDROP TABLE products\n```"}

    class LLM:
        name = "fake"

        def chat(self, messages, **kw):
            return ChatResult(text=answers[messages[0]["content"]], provider="fake",
                              model="m", input_tokens=10, output_tokens=5, latency_ms=1.0)

    def executor(sql):
        if "name" in sql:
            return ["sku", "name"], [("S2", "n2"), ("S1", "n1")]
        if "COUNT" in sql:
            return ["count"], [(2,)]
        return ["sku"], [("S1",), ("S2",)]

    report = evaluate(golden, LLM(), executor)
    s = report["summary"]
    assert s["n"] == 2
    assert s["execution_accuracy"] == 0.5
    assert s["execution_accuracy_strict"] == 0.0
    assert s["guard_rejected_rate"] == 0.5
    assert s["input_tokens"] == 20 and s["output_tokens"] == 10
    assert s["models"] == {"m": 2}
    assert [i["match"] for i in report["items"]] == [True, False]
