"""Dobles de prueba compartidos (sin LLM ni DB)."""
from src.llm import ChatResult, ToolCall


class ScriptedLLM:
    """Devuelve respuestas predefinidas en orden y registra cada llamada."""

    def __init__(self, responses, name="fake", model="fake-1"):
        self.responses, self.name, self.chat_model, self.calls = list(responses), name, model, []

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "system": system, "tools": tools})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ChatResult):
            return item
        text, calls = item if isinstance(item, tuple) else (item, [])
        return ChatResult(text=text, provider=self.name, model=self.chat_model,
                          input_tokens=100, output_tokens=10, latency_ms=5.0, tool_calls=calls)


def call(name, cid="c1", **arguments):
    return ToolCall(cid, name, arguments)
