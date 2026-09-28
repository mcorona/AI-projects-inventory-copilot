from src.agent import Agent
from src.llm import ChatResult
from src.tools.registry import MAX_ROWS_TO_MODEL, Tool, build_tools, to_json
from tests.fakes import ScriptedLLM, call, unwrap


def fake_tools(record):
    def mk(name, result=None, exc=None):
        def fn(args):
            record.append((name, args))
            if exc:
                raise exc
            return result if result is not None else {"tool": name, "args": args}
        return Tool(name, f"desc {name}", {"type": "object", "properties": {}}, fn)
    return {t.name: t for t in [mk("get_sku_status", {"sku": "SKU-0009", "total_on_hand": 84}),
                                mk("search_documents", {"results": [{"source": "skus_criticos.md"}]}),
                                mk("query_inventory", exc=RuntimeError("DB caida"))]}


def test_loop_calls_tools_then_answers():
    record = []
    llm = ScriptedLLM([
        ("<think>necesito datos</think>", [call("get_sku_status", "a", sku="SKU-0009"),
                                           call("search_documents", "b", query="criticos")]),
        "<think>listo</think>El SKU-0009 tiene 84 unidades [fuente: skus_criticos.md].",
    ])
    r = Agent(llm, fake_tools(record)).run("¿Cómo está el SKU-0009?")

    assert r.stop_reason == "answer" and r.answer.startswith("El SKU-0009 tiene 84")
    assert r.tools_used == ["get_sku_status", "search_documents"] and all(s.ok for s in r.steps)
    assert record == [("get_sku_status", {"sku": "SKU-0009"}), ("search_documents", {"query": "criticos"})]
    assert (r.llm_calls, r.input_tokens, r.output_tokens) == (2, 200, 20)
    # segunda llamada: historial con assistant(tool_calls) sin <think> y dos resultados de tool
    msgs = llm.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "tool"]
    assert msgs[1]["content"] == "" and msgs[2]["tool_call_id"] == "a"
    assert msgs[2]["content"].startswith('<tool_output tool="get_sku_status" trust="untrusted">')
    assert unwrap(msgs[2]["content"])["total_on_hand"] == 84
    assert {t["name"] for t in llm.calls[0]["tools"]} == {"get_sku_status", "search_documents", "query_inventory"}
    assert all(m["tool_calls"] for m in msgs if m["role"] == "assistant")  # la cola no vacia el historial
    assert "2026-09-26" in llm.calls[0]["system"]


def test_tool_errors_are_returned_to_model_not_raised():
    llm = ScriptedLLM([
        ("", [call("query_inventory", "a", question="x"), call("borrar_todo", "b"),
              call("get_sku_status", "c", _raw="{roto")]),
        "No pude obtener los datos.",
    ])
    r = Agent(llm, fake_tools([])).run("x")
    assert r.stop_reason == "answer" and [s.ok for s in r.steps] == [False, False, False]
    assert "RuntimeError: DB caida" in r.steps[0].error
    assert "herramienta desconocida" in r.steps[1].error
    assert "JSON valido" in r.steps[2].error
    assert all("error" in unwrap(m["content"]) for m in llm.calls[1]["messages"] if m["role"] == "tool")


def test_tool_reporting_error_field_marks_step_failed():
    tools = {"get_sku_status": Tool("get_sku_status", "d", {}, lambda a: {"error": "SKU invalido"})}
    r = Agent(ScriptedLLM([("", [call("get_sku_status", sku="x")]), "SKU invalido."]), tools).run("x")
    assert r.steps[0].ok is False and r.steps[0].error == "SKU invalido"


def test_missing_required_argument_vs_internal_key_error():
    schema = {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}
    tools = {"get_sku_status": Tool("get_sku_status", "d", schema, lambda a: {"sku": a["sku"]})}
    r = Agent(ScriptedLLM([("", [call("get_sku_status")]), "ok"]), tools).run("x")
    assert "falta el argumento requerido" in r.steps[0].error
    import os
    tools = {"get_sku_status": Tool("get_sku_status", "d", schema, lambda a: {"x": os.environ["NO_EXISTE_X"]})}
    r = Agent(ScriptedLLM([("", [call("get_sku_status", sku="S")]), "ok"]), tools).run("x")
    assert r.steps[0].error.startswith("error interno de la herramienta")


def test_max_steps_and_llm_error():
    looping = ScriptedLLM([("", [call("get_sku_status", sku="SKU-0001")])] * 3)
    r = Agent(looping, fake_tools([]), max_steps=3).run("x")
    assert r.stop_reason == "max_steps" and len(r.steps) == 3

    r = Agent(ScriptedLLM([ConnectionError("LM Studio apagado")]), fake_tools([])).run("x")
    assert r.stop_reason == "llm_error" and "LM Studio apagado" in r.answer


def test_generates_ids_for_tool_calls_without_id():
    llm = ScriptedLLM([ChatResult("", "fake", "m", tool_calls=[call("get_sku_status", "", sku="S")]), "ok"])
    Agent(llm, fake_tools([])).run("x")
    tool_msg = llm.calls[1]["messages"][2]
    assert tool_msg["tool_call_id"].startswith("call_")
    assert llm.calls[1]["messages"][1]["tool_calls"][0].id == tool_msg["tool_call_id"]


def test_history_is_prepended():
    llm = ScriptedLLM(["ok"])
    Agent(llm, fake_tools([])).run("y", history=[{"role": "user", "content": "x"},
                                                 {"role": "assistant", "content": "a"}])
    assert [m["content"] for m in llm.calls[0]["messages"]] == ["x", "a", "y"]


def test_registry_query_inventory_caps_rows_for_model():
    rows = [(f"SKU-{i:04d}",) for i in range(120)]
    llm = ScriptedLLM(["SELECT sku FROM products"])
    tools = build_tools(llm=llm, sql_executor=lambda sql: (["sku"], rows))
    out = tools["query_inventory"].fn({"question": "todos los skus"})
    assert out["row_count"] == 120 and len(out["rows"]) == MAX_ROWS_TO_MODEL and "note" in out


def test_registry_specs_are_valid_neutral_tools():
    for t in build_tools(llm=ScriptedLLM([])).values():
        spec = t.spec()
        assert set(spec) == {"name", "description", "parameters"}
        assert spec["parameters"]["type"] == "object" and spec["parameters"]["required"]


def test_to_json_serializes_and_truncates():
    from datetime import date
    from decimal import Decimal
    assert to_json({"a": Decimal("1.5"), "d": date(2026, 9, 26)}) == '{"a": 1.5, "d": "2026-09-26"}'
    assert to_json({"x": "y" * 100}, max_chars=20).endswith("[salida truncada]")
