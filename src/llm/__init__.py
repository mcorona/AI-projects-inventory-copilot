"""Capa LLM agnostica de proveedor: una sola interfaz, tres adaptadores."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)


class LLMProvider(Protocol):
    name: str

    def chat(self, messages: list[dict], system: str | None = None,
             temperature: float = 0.0, max_tokens: int = 1024) -> ChatResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleProvider:
    """LM Studio y OmniRoute exponen la API de OpenAI (/v1/chat/completions, /v1/embeddings)."""

    def __init__(self, name: str, base_url: str, chat_model: str,
                 embed_model: str | None = None, api_key: str = "not-needed"):
        from openai import OpenAI
        self.name = name
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024) -> ChatResult:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        t0 = time.perf_counter()
        r = self.client.chat.completions.create(
            model=self.chat_model, messages=msgs,
            temperature=temperature, max_tokens=max_tokens)
        usage = r.usage
        return ChatResult(
            text=r.choices[0].message.content or "",
            provider=self.name, model=r.model or self.chat_model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=(time.perf_counter() - t0) * 1000)

    def embed(self, texts):
        if not self.embed_model:
            raise NotImplementedError(f"{self.name}: no hay modelo de embeddings configurado")
        r = self.client.embeddings.create(model=self.embed_model, input=texts)
        return [d.embedding for d in r.data]


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

    def chat(self, messages, system=None, temperature=0.0, max_tokens=1024) -> ChatResult:
        conv = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages]
        kw = dict(modelId=self.chat_model, messages=conv,
                  inferenceConfig={"temperature": temperature, "maxTokens": max_tokens})
        if system:
            kw["system"] = [{"text": system}]
        t0 = time.perf_counter()
        r = self.client.converse(**kw)
        u = r.get("usage", {})
        return ChatResult(
            text=r["output"]["message"]["content"][0]["text"],
            provider=self.name, model=self.chat_model,
            input_tokens=u.get("inputTokens", 0), output_tokens=u.get("outputTokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000, raw=r)

    def embed(self, texts):
        import json
        out = []
        for t in texts:
            r = self.client.invoke_model(
                modelId=self.embed_model,
                body=json.dumps({"inputText": t, "dimensions": 1024, "normalize": True}))
            out.append(json.loads(r["body"].read())["embedding"])
        return out


def get_provider(name: str | None = None) -> LLMProvider:
    """Selecciona el proveedor por variable de entorno LLM_PROVIDER."""
    name = (name or os.getenv("LLM_PROVIDER", "lmstudio")).lower()
    if name == "lmstudio":
        return OpenAICompatibleProvider(
            "lmstudio", os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            os.getenv("LMSTUDIO_CHAT_MODEL", "qwen/qwen3-30b-a3b"),
            os.getenv("LMSTUDIO_EMBED_MODEL", "text-embedding-bge-m3"))
    if name == "omniroute":
        return OpenAICompatibleProvider(
            "omniroute", os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1"),
            os.getenv("OMNIROUTE_CHAT_MODEL", "auto/best-free"),
            None, os.getenv("OMNIROUTE_API_KEY", "not-needed"))
    if name == "bedrock":
        return BedrockProvider(
            os.getenv("BEDROCK_CHAT_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0"),
            os.getenv("AWS_REGION", "us-east-1"))
    raise ValueError(f"LLM_PROVIDER desconocido: {name}")
