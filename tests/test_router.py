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


class FakeVerifier:
    def __init__(self, verdicts):
        from src.tools.sql_verifier import Verdict
        self.verdicts = [Verdict(ok, "r", 30, 5, 2.0) for ok in verdicts]
        self.calls = []

    def verify(self, question, sql, columns, rows):
        self.calls.append((question, sql, len(rows)))
        return self.verdicts.pop(0)


def test_verifier_escalates_plausible_but_wrong_answers_and_counts_cost():
    cheap = ScriptedLLM(["SELECT sku FROM products"], model="cheap")
    strong = ScriptedLLM(["SELECT sku FROM stock"], model="strong")
    verifier = FakeVerifier([False, True])
    r = run_sql_tool("x", llm=CascadeRouter([cheap, strong]), verifier=verifier,
                     executor=lambda sql: (["sku"], [("S",)]))
    assert r.model == "strong" and r.escalations == 1
    assert [a["verifier_ok"] for a in r.attempts] == [False, True]
    assert r.input_tokens == 100 + 100 + 30 + 30   # dos intentos + dos verificaciones
    assert verifier.calls[0][1].startswith("SELECT sku FROM products")


def test_verifier_not_called_when_result_already_fails():
    cheap = ScriptedLLM(["DELETE FROM products"], model="cheap")
    strong = ScriptedLLM(["SELECT sku FROM products"], model="strong")
    verifier = FakeVerifier([True])
    r = run_sql_tool("x", llm=CascadeRouter([cheap, strong]), verifier=verifier,
                     executor=lambda sql: (["sku"], [("S",)]))
    assert r.model == "strong" and len(verifier.calls) == 1


def test_sql_verifier_parses_and_fails_open():
    from src.tools.sql_verifier import SQLVerifier
    judge_llm = ScriptedLLM(['<think>x</think>{"ok": false, "reason": "devuelve 200 filas"}'])
    out = SQLVerifier(judge_llm).verify("¿que almacen?", "SELECT ...", ["name"], [("a",)] * 200)
    assert not out.ok and out.reason == "devuelve 200 filas" and out.input_tokens == 100
    prompt = judge_llm.calls[0]["messages"][0]["content"]
    assert "200 filas; muestra de 10" in prompt   # la muestra se acota
    assert SQLVerifier(ScriptedLLM(["no se"])).verify("q", "s", [], []).ok   # fail-open


def test_attempts_keep_sql_for_offline_verifier_analysis():
    cheap = ScriptedLLM(["SELECT sku FROM products"], model="cheap")
    strong = ScriptedLLM(["SELECT sku FROM stock"], model="strong")
    r = run_sql_tool("x", llm=CascadeRouter([cheap, strong]), verifier=FakeVerifier([False, True]),
                     executor=lambda sql: (["sku"], [("S",)]))
    assert [a["sql"].split(" FROM ")[1].split()[0] for a in r.attempts] == ["products", "stock"]


def test_sql_verifier_receives_schema_hint():
    from src.tools.sql_tool import schema_description
    from src.tools.sql_verifier import SQLVerifier
    judge_llm = ScriptedLLM(['{"ok": true}'])
    SQLVerifier(judge_llm, schema_hint=schema_description()).verify("q", "SELECT 1", [], [])
    system = judge_llm.calls[0]["system"]
    assert "UNA fila por cada SKU y cada dia" in system   # sabe que sales_daily es densa
    assert "CEDIS Norte" in system and "{anchor}" not in system
