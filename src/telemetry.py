"""Telemetria por turno del agente y estimacion de costo.

Cada turno produce un registro JSONL (TELEMETRY_PATH) con latencia por etapa, tokens por
llamada, tools y costo: el real (LM Studio / OmniRoute gratuito = $0) y el equivalente en
Bedrock segun config/pricing.json (precios de lista de la AWS Price List, con fuente y fecha).
Los campos se corresponden con metricas de CloudWatch / spans de AgentCore Observability.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.json"


@lru_cache(maxsize=1)
def load_pricing(path: str = str(PRICING_PATH)) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class CostEstimate:
    actual_usd: float
    bedrock_usd: float | None
    bedrock_model: str | None
    exact_equivalent: bool


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int,
                  pricing: dict | None = None) -> CostEstimate:
    pricing = pricing or load_pricing()
    eq = pricing["equivalents"].get(model)
    bedrock_usd = label = None
    if eq:
        price = pricing["models"][eq["price"]]
        bedrock_usd = (input_tokens * price["input_per_1m"] + output_tokens * price["output_per_1m"]) / 1e6
        label = price["label"]
    if provider in pricing["zero_cost_providers"]:
        actual = 0.0
    elif provider == "bedrock" and bedrock_usd is not None:
        actual = bedrock_usd
    else:
        actual = float("nan")   # proveedor de pago sin precio conocido: no se inventa
    return CostEstimate(actual, bedrock_usd, label, bool(eq and eq.get("exact")))


def turn_record(result, provider: str) -> dict:
    """Registro de telemetria de un AgentResult (sin el texto de la pregunta: puede traer PII)."""
    calls = []
    actual = bedrock = 0.0
    bedrock_known = True
    for c in result.llm_trace:
        cost = estimate_cost(c["provider"], c["model"], c["input_tokens"], c["output_tokens"])
        actual += cost.actual_usd
        if cost.bedrock_usd is None:
            bedrock_known = False
        else:
            bedrock += cost.bedrock_usd
        calls.append({**c, "bedrock_usd": cost.bedrock_usd})
    # el LLM interno de una tool (text-to-SQL) ya esta dentro de la latencia de la tool
    llm_ms = sum(c["latency_ms"] for c in result.llm_trace if c.get("source") == "agent")
    tool_llm_ms = sum(c["latency_ms"] for c in result.llm_trace if c.get("source") != "agent")
    tool_ms = sum(s.latency_ms for s in result.steps)
    return {
        "ts": datetime.now(timezone.utc).isoformat(), "provider": provider, "models": result.models,
        "stop_reason": result.stop_reason, "latency_ms": round(result.latency_ms, 1),
        "latency_breakdown_ms": {"agent_llm": round(llm_ms, 1), "tools": round(tool_ms, 1),
                                 "tools_llm_subset": round(tool_llm_ms, 1),
                                 "guardrails": round(result.guardrail_ms, 1),
                                 "other": round(max(result.latency_ms - llm_ms - tool_ms - result.guardrail_ms, 0), 1)},
        "llm_calls": calls, "tools": [{"tool": s.tool, "ok": s.ok, "latency_ms": round(s.latency_ms, 1)}
                                      for s in result.steps],
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "guardrail_findings": result.guardrail_findings,
        "cost_actual_usd": actual, "cost_bedrock_equiv_usd": bedrock if bedrock_known else None,
    }


class TelemetrySink:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def get_telemetry_sink() -> TelemetrySink | None:
    path = os.getenv("TELEMETRY_PATH")
    return TelemetrySink(path) if path else None


def as_dict(cost: CostEstimate) -> dict:
    return asdict(cost)
