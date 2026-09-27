from types import SimpleNamespace

from src.llm import (OpenAICompatibleProvider, ToolCall, _parse_arguments, from_converse_output,
                     get_provider, to_converse_messages, to_converse_tool_config,
                     to_openai_messages, to_openai_tools)
from src.llm.router import CascadeRouter

TOOLS = [{"name": "get_sku_status", "description": "ficha",
          "parameters": {"type": "object", "properties": {"sku": {"type": "string"}}}}]
CALL = ToolCall("t1", "get_sku_status", {"sku": "SKU-0001"})
HISTORY = [
    {"role": "user", "content": "¿Cómo está SKU-0001?"},
    {"role": "assistant", "content": "", "tool_calls": [CALL, ToolCall("t2", "search_documents", {"query": "x"})]},
    {"role": "tool", "tool_call_id": "t1", "name": "get_sku_status", "content": '{"ok": 1}'},
    {"role": "tool", "tool_call_id": "t2", "name": "search_documents", "content": '{"r": []}'},
]


def test_parse_arguments_tolerates_invalid_json():
    assert _parse_arguments('{"sku": "A"}') == {"sku": "A"}
    assert _parse_arguments({"sku": "A"}) == {"sku": "A"}
    assert _parse_arguments("") == {}
    assert _parse_arguments("{roto") == {"_raw": "{roto"}
    assert _parse_arguments("[1]") == {"_raw": "[1]"}


def test_openai_message_translation():
    out = to_openai_messages(HISTORY, system="sys")
    assert out[0] == {"role": "system", "content": "sys"}
    asst = out[2]
    assert asst["content"] is None  # contenido vacio -> None, como espera la API
    assert asst["tool_calls"][0] == {"id": "t1", "type": "function",
                                     "function": {"name": "get_sku_status", "arguments": '{"sku": "SKU-0001"}'}}
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": '{"ok": 1}'}
    assert to_openai_tools(TOOLS)[0] == {"type": "function", "function": TOOLS[0]}


def test_converse_translation_merges_tool_results_into_one_user_turn():
    out = to_converse_messages(HISTORY)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"] == [  # sin bloque de texto vacio (Converse lo rechaza)
        {"toolUse": {"toolUseId": "t1", "name": "get_sku_status", "input": {"sku": "SKU-0001"}}},
        {"toolUse": {"toolUseId": "t2", "name": "search_documents", "input": {"query": "x"}}}]
    assert [b["toolResult"]["toolUseId"] for b in out[2]["content"]] == ["t1", "t2"]


def test_converse_tool_config_and_output_parsing():
    cfg = to_converse_tool_config(TOOLS)
    assert cfg["tools"][0]["toolSpec"]["inputSchema"] == {"json": TOOLS[0]["parameters"]}
    text, calls = from_converse_output({"output": {"message": {"content": [
        {"text": "Reviso."}, {"toolUse": {"toolUseId": "u1", "name": "get_sku_status", "input": {"sku": "A"}}}]}}})
    assert text == "Reviso." and calls == [ToolCall("u1", "get_sku_status", {"sku": "A"})]


def _fake_openai_response(tool_calls=None, content="", finish="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=50, completion_tokens=7), model="m")


def test_openai_provider_parses_tool_calls():
    p = OpenAICompatibleProvider("lmstudio", "http://x/v1", "m")
    captured = {}

    def create(**kw):
        captured.update(kw)
        tc = SimpleNamespace(id="t1", function=SimpleNamespace(name="get_sku_status", arguments='{"sku": "SKU-0001"}'))
        return _fake_openai_response([tc], finish="tool_calls")

    p.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    r = p.chat([{"role": "user", "content": "hola"}], tools=TOOLS)
    assert captured["tools"][0]["function"]["name"] == "get_sku_status"
    assert r.tool_calls == [CALL] and r.stop_reason == "tool_calls"
    assert (r.input_tokens, r.output_tokens) == (50, 7)


def test_openai_provider_omits_tools_when_none():
    p = OpenAICompatibleProvider("lmstudio", "http://x/v1", "m")
    captured = {}
    p.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: captured.update(kw) or _fake_openai_response(content="hola"))))
    assert p.chat([{"role": "user", "content": "x"}]).text == "hola"
    assert "tools" not in captured


def test_get_provider_model_override_and_router(monkeypatch):
    assert get_provider("lmstudio", "otro/modelo").chat_model == "otro/modelo"
    monkeypatch.setenv("ROUTER_TIERS", "omniroute:kr/minimax-m2.1:free, lmstudio")
    r = get_provider("router")
    assert isinstance(r, CascadeRouter)
    assert [(t.name, t.chat_model) for t in r.tiers] == [
        ("omniroute", "kr/minimax-m2.1:free"), ("lmstudio", "qwen/qwen3.6-35b-a3b")]
