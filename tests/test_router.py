import pytest

from src.llm.router import CascadeRouter, parse_tiers
from src.tools.sql_tool import run_sql_tool
from tests.fakes import ScriptedLLM, call


def test_parse_tiers():
    assert parse_tiers("omniroute:kr/minimax-m2.1, lmstudio") == [
        ("omniroute", "kr/minimax-m2.1"), ("lmstudio", None)]
    assert parse_tiers("omniroute:openrouter/x:free") == [("omniroute", "openrouter/x:free")]
    with pytest.raises(ValueError):
        parse_tiers(" , ")


def test_first_tier_answers_no_escalation():
    cheap, strong = ScriptedLLM(["hola"], model="cheap"), ScriptedLLM([], model="strong")
    r = CascadeRouter([cheap, strong]).chat([{"role": "user", "content": "x"}])
    assert r.model == "cheap" and strong.calls == []
    assert [a.ok for a in r.raw["attempts"]] == [True]


@pytest.mark.parametrize("bad", [
    ConnectionError("caido"),        # proveedor no disponible
    "",                              # respuesta vacia
    "<think>pensando sin fin",       # truncado
])
def test_escalates_on_detectable_failures(bad):
    cheap, strong = ScriptedLLM([bad], model="cheap"), ScriptedLLM(["respuesta"], model="strong")
    r = CascadeRouter([cheap, strong]).chat([{"role": "user", "content": "x"}])
    assert r.model == "strong" and r.text == "respuesta"
    assert [a.ok for a in r.raw["attempts"]] == [False, True]


def test_tool_calls_count_as_valid_answer():
    cheap = ScriptedLLM([("", [call("get_sku_status", sku="SKU-0001")])])
    r = CascadeRouter([cheap, ScriptedLLM([])]).chat([{"role": "user", "content": "x"}])
    assert r.tool_calls[0].name == "get_sku_status"


def test_tokens_and_latency_accumulate_across_attempts():
    r = CascadeRouter([ScriptedLLM([""]), ScriptedLLM(["ok"])]).chat([{"role": "user", "content": "x"}])
    assert (r.input_tokens, r.output_tokens, r.latency_ms) == (200, 20, 10.0)


def test_last_tier_result_returned_even_if_bad_and_all_down_raises():
    r = CascadeRouter([ScriptedLLM([""]), ScriptedLLM([""])]).chat([{"role": "user", "content": "x"}])
    assert r.text == ""
    with pytest.raises(RuntimeError, match="todos los niveles"):
        CascadeRouter([ScriptedLLM([ConnectionError("a")]), ScriptedLLM([ConnectionError("b")])]).chat([])


def test_sql_tool_escalates_on_guard_rejection_and_sums_cost():
    cheap = ScriptedLLM(["```sql\nDELETE FROM products\n```"], model="cheap")
    strong = ScriptedLLM(["```sql\nSELECT sku FROM products\n```"], model="strong")
    executed = []

    def executor(sql):
        executed.append(sql)
        return ["sku"], [("SKU-0001",)]

    r = run_sql_tool("x", llm=CascadeRouter([cheap, strong]), executor=executor)
    assert r.ok and r.model == "strong" and r.escalations == 1
    assert [a["error"] is None for a in r.attempts] == [False, True]
    assert r.input_tokens == 200 and len(executed) == 1


def test_sql_tool_escalates_on_empty_result():
    cheap = ScriptedLLM(["SELECT sku FROM products WHERE 1=0"], model="cheap")
    strong = ScriptedLLM(["SELECT sku FROM products"], model="strong")
    results = iter([(["sku"], []), (["sku"], [("SKU-0001",)])])
    r = run_sql_tool("x", llm=CascadeRouter([cheap, strong]), executor=lambda sql: next(results))
    assert r.escalations == 1 and r.rows == [("SKU-0001",)]


def test_sql_tool_does_not_escalate_plausible_answers():
    """Limite conocido: un SQL valido con filas pero semanticamente incorrecto no escala."""
    cheap = ScriptedLLM(["SELECT sku FROM products"], model="cheap")
    strong = ScriptedLLM([], model="strong")
    r = run_sql_tool("x", llm=CascadeRouter([cheap, strong]), executor=lambda sql: (["sku"], [("S",)]))
    assert r.escalations == 0 and strong.calls == []
