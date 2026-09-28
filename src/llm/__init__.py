"""Capa LLM agnostica de proveedor: una sola interfaz, tres adaptadores.

Formato neutro de mensajes (el que usa el resto del proyecto):
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}   # tool_calls opcional
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

Formato neutro de tools: {"name", "description", "parameters": <JSON Schema>}.
Cada adaptador traduce a su API (OpenAI tools / Bedrock Converse toolConfig).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    raw: dict = field(default_factory=dict, repr=False)


class LLMProvider(Protocol):
    name: str

    def chat(self, messages: list[dict], system: str | None = None,
             temperature: float = 0.0, max_tokens: int = 1024,
             tools: list[dict] | None = None) -> ChatResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _parse_arguments(raw) -> dict:
    """Los modelos a veces devuelven JSON invalido: se conserva el texto para diagnostico."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


# ---------------------------------------------------------------- OpenAI-compatible

def to_openai_messages(messages: list[dict], system: str | None) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [{"id": c.id, "type": "function",
                                        "function": {"name": c.name,
                                                     "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                                       for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": t} for t in tools]


class OpenAICompatibleProvider:
    """LM Studio y OmniRoute exponen la API de OpenAI (/v1/chat/completions, /v1/embeddings)."""

    def __init__(self, name: str, base_url: str, chat_model: str,
                 embed_model: str | None = None, api_key: str = "not-needed"):
        from openai import OpenAI
        self.name = name
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        # las evals lo fijan por repeticion para esquivar caches de respuesta (OmniRoute)
        self.system_suffix = ""

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024, tools=None) -> ChatResult:
        if self.system_suffix:
            system = (system or "") + self.system_suffix
        kw = dict(model=self.chat_model, messages=to_openai_messages(messages, system),
                  temperature=temperature, max_tokens=max_tokens)
        if tools:
            kw["tools"] = to_openai_tools(tools)
        t0 = time.perf_counter()
        completions = self.client.chat.completions
        cache_hit = None
        if hasattr(completions, "with_raw_response"):
            raw = completions.with_raw_response.create(**kw)
            # OmniRoute marca las respuestas servidas desde su cache (latencia no representativa)
            header = raw.headers.get("x-omniroute-cache")
            cache_hit = None if header is None else header.upper() == "HIT"
            r = raw.parse()
        else:
            r = completions.create(**kw)
        choice, usage = r.choices[0], r.usage
        calls = [ToolCall(c.id, c.function.name, _parse_arguments(c.function.arguments))
                 for c in (choice.message.tool_calls or [])]
        return ChatResult(
            text=choice.message.content or "",
            provider=self.name, model=r.model or self.chat_model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            tool_calls=calls, stop_reason=choice.finish_reason or "",
            raw={"cache_hit": cache_hit} if cache_hit is not None else {})

    def embed(self, texts):
        if not self.embed_model:
            raise NotImplementedError(f"{self.name}: no hay modelo de embeddings configurado")
        r = self.client.embeddings.create(model=self.embed_model, input=texts)
        return [d.embedding for d in r.data]


# ---------------------------------------------------------------- Bedrock Converse

def to_converse_messages(messages: list[dict]) -> list[dict]:
    """Converse exige alternar user/assistant: los resultados de tools van en un turno user
    con bloques toolResult, y mensajes consecutivos del mismo rol se fusionan."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "tool":
            role, blocks = "user", [{"toolResult": {"toolUseId": m["tool_call_id"],
                                                    "content": [{"text": m["content"]}]}}]
        elif m["role"] == "assistant":
            role = "assistant"
            blocks = [{"text": m["content"]}] if m.get("content") else []
            blocks += [{"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}}
                       for c in m.get("tool_calls") or []]
        else:
            role, blocks = "user", [{"text": m["content"]}]
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return out


def to_converse_tool_config(tools: list[dict]) -> dict:
    return {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                    "inputSchema": {"json": t["parameters"]}}} for t in tools]}


def from_converse_output(r: dict) -> tuple[str, list[ToolCall]]:
    text, calls = [], []
    for block in r["output"]["message"]["content"]:
        if "text" in block:
            text.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            calls.append(ToolCall(tu["toolUseId"], tu["name"], _parse_arguments(tu["input"])))
    return "".join(text), calls


class BedrockProvider:
    """Amazon Bedrock via Converse API (chat) e InvokeModel (Titan embeddings)."""

    def __init__(self, chat_model: str, embed_model: str, region: str = "us-east-1"):
        import boto3
        from botocore.config import Config
        self.name = "bedrock"
        self.chat_model = chat_model
        self.embed_model = embed_model
        cfg = Config(retries={"max_attempts": 5, "mode": "adaptive"})
        self.client = boto3.client("bedrock-runtime", region_name=region, config=cfg)

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024, tools=None) -> ChatResult:
        kw = dict(modelId=self.chat_model, messages=to_converse_messages(messages),
                  inferenceConfig={"temperature": temperature, "maxTokens": max_tokens})
        if system:
            kw["system"] = [{"text": system}]
        if tools:
            kw["toolConfig"] = to_converse_tool_config(tools)
        t0 = time.perf_counter()
        r = self.client.converse(**kw)
        u = r.get("usage", {})
        text, calls = from_converse_output(r)
        return ChatResult(
            text=text, provider=self.name, model=self.chat_model,
            input_tokens=u.get("inputTokens", 0), output_tokens=u.get("outputTokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
            tool_calls=calls, stop_reason=r.get("stopReason", ""), raw=r)

    def embed(self, texts):
        out = []
        for t in texts:
            r = self.client.invoke_model(
                modelId=self.embed_model,
                body=json.dumps({"inputText": t, "dimensions": 1024, "normalize": True}))
            out.append(json.loads(r["body"].read())["embedding"])
        return out


# ---------------------------------------------------------------- fabrica

def get_provider(name: str | None = None, chat_model: str | None = None) -> LLMProvider:
    """Selecciona el proveedor por variable de entorno LLM_PROVIDER.

    `chat_model` sobreescribe el modelo de chat (lo usa el router para fijar cada nivel).
    """
    name = (name or os.getenv("LLM_PROVIDER", "lmstudio")).lower()
    if name == "lmstudio":
        return OpenAICompatibleProvider(
            "lmstudio", os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            chat_model or os.getenv("LMSTUDIO_CHAT_MODEL", "qwen/qwen3.6-35b-a3b"),
            os.getenv("LMSTUDIO_EMBED_MODEL", "text-embedding-bge-m3"))
    if name == "omniroute":
        return OpenAICompatibleProvider(
            "omniroute", os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1"),
            chat_model or os.getenv("OMNIROUTE_CHAT_MODEL", "auto/best-free"),
            None, os.getenv("OMNIROUTE_API_KEY", "not-needed"))
    if name == "bedrock":
        return BedrockProvider(
            chat_model or os.getenv("BEDROCK_CHAT_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0"),
            os.getenv("AWS_REGION", "us-east-1"))
    if name == "router":
        from src.llm.router import CascadeRouter
        return CascadeRouter.from_env()
    raise ValueError(f"LLM_PROVIDER desconocido: {name}")


def get_embedder(name: str | None = None) -> LLMProvider:
    """Proveedor de embeddings (EMBED_PROVIDER), independiente del de chat:
    OmniRoute no tiene embeddings y el indice RAG debe usar siempre el mismo modelo."""
    return get_provider(name or os.getenv("EMBED_PROVIDER", "lmstudio"))


def embed_model_id(embedder) -> str:
    """Identificador estable del modelo de embeddings para etiquetar el indice."""
    return f"{embedder.name}:{embedder.embed_model}"
