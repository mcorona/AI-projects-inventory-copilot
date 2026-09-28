"""API HTTP del copiloto (FastAPI). Corre igual en local (uvicorn) y en Lambda (Mangum).

    uvicorn src.api.app:app --reload        # http://localhost:8000

Stateless a proposito (Lambda no garantiza la misma instancia entre peticiones): una accion que
requiere confirmacion se devuelve como token firmado con HMAC (tool, argumentos, usuario,
expiracion). /confirm verifica la firma, asi que nadie puede cambiar SKU o cantidad entre la
vista previa y la confirmacion, ni confirmar la accion de otra persona.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv()   # local; en Lambda la configuracion viene de Secrets Manager (aws_runtime)

TOKEN_TTL_S = 600
STATIC = Path(__file__).parent / "static"


# ------------------------------------------------------------ tokens de confirmacion

def _key() -> bytes:
    key = os.getenv("SIGNING_KEY")
    if not key:   # local: llave aleatoria por proceso (los tokens mueren al reiniciar)
        key = os.environ["SIGNING_KEY"] = secrets.token_hex(32)
    return key.encode()


def sign_action(tool: str, args: dict, user: str, now: float | None = None) -> str:
    payload = json.dumps({"tool": tool, "args": args, "user": user,
                          "exp": int((now or time.time()) + TOKEN_TTL_S)}, sort_keys=True, ensure_ascii=False)
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_action(token: str, user: str, now: float | None = None) -> dict:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        raise HTTPException(400, "token mal formado")
    if not hmac.compare_digest(sig, hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()):
        raise HTTPException(403, "firma invalida: la accion fue alterada")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if data["exp"] < (now or time.time()):
        raise HTTPException(410, "la confirmacion expiro; vuelve a pedir la accion")
    if data["user"] != user:
        raise HTTPException(403, "solo quien pidio la accion puede confirmarla")
    return data


# ------------------------------------------------------------ dependencias

class Services:
    """Agente y tools configurados por entorno; inyectable en pruebas."""

    def __init__(self, agent=None, tools=None, audit=None, list_orders=None):
        from src.audit import get_audit_sink
        self.audit = audit or get_audit_sink()
        if agent is None:
            from src.agent import Agent
            from src.guardrails.pipeline import GuardrailPipeline
            from src.llm import get_provider
            from src.telemetry import get_telemetry_sink
            agent = Agent(get_provider(), audit=self.audit, telemetry=get_telemetry_sink(),
                          guardrails=GuardrailPipeline.from_env(audit=self.audit))
        self.agent = agent
        self.tools = tools if tools is not None else agent.tools
        if list_orders is None:
            from src.tools.purchase_orders import list_purchase_orders
            list_orders = list_purchase_orders
        self.list_orders = list_orders


@lru_cache(maxsize=1)
def get_services() -> Services:
    return Services()


def caller(request: Request, body_user: str | None) -> str:
    """En AWS la identidad viene de la firma IAM (API Gateway); en local, del cuerpo (demo)."""
    event = request.scope.get("aws.event") or {}
    arn = (((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}).get("userArn")
    return arn or body_user or "demo"


# ------------------------------------------------------------ API

class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    user: str | None = None


class ConfirmIn(BaseModel):
    token: str
    approve: bool
    user: str | None = None
    note: str = ""


app = FastAPI(title="Inventory Copilot", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/ask")
def ask(body: AskIn, request: Request, svc: Services = Depends(get_services)):
    user = caller(request, body.user)
    r = svc.agent.run(body.question, user=user)
    out = {"answer": r.answer, "stop_reason": r.stop_reason,
           "steps": [{"tool": s.tool, "arguments": s.arguments, "ok": s.ok, "error": s.error,
                      "latency_ms": round(s.latency_ms)} for s in r.steps],
           "guardrail_findings": r.guardrail_findings,
           "metrics": {"latency_ms": round(r.latency_ms), "llm_calls": r.llm_calls,
                       "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "models": r.models}}
    if r.pending:
        args = {k: v for k, v in r.pending.call.arguments.items() if not k.startswith("_")}
        out["pending"] = {"tool": r.pending.call.name, "preview": r.pending.preview,
                          "token": sign_action(r.pending.call.name, args, user)}
        out["answer"] = "Revisa la propuesta y confirma o rechaza."
    _emit_metrics(r)
    return out


@app.post("/confirm")
def confirm(body: ConfirmIn, request: Request, svc: Services = Depends(get_services)):
    user = caller(request, body.user)
    action = verify_action(body.token, user)
    tool = svc.tools.get(action["tool"])
    if tool is None or not tool.requires_confirmation:
        raise HTTPException(400, "accion no confirmable")
    if not body.approve:
        svc.audit.log(user, "confirmation_rejected", {"tool": action["tool"], "args": action["args"], "note": body.note})
        return {"status": "rejected", "message": "Propuesta rechazada; no se creo nada."}
    svc.audit.log(user, "confirmation_approved", {"tool": action["tool"], "args": action["args"]})
    result = tool.fn({**action["args"], "_confirmed_by": user, "_requested_by": f"copilot:{user}"})
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return {"status": "created", "order": result, "message": result.get("message", "")}


@app.get("/orders")
def orders(status: str | None = None, svc: Services = Depends(get_services)):
    return {"orders": svc.list_orders(status)}


def _emit_metrics(r) -> None:
    """En Lambda: metricas de CloudWatch en formato EMF (una linea JSON en los logs)."""
    namespace = os.getenv("METRICS_NAMESPACE")
    if not namespace:
        return
    from src.telemetry import turn_record
    rec = turn_record(r, os.getenv("LLM_PROVIDER", ""))
    print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": namespace, "Dimensions": [[]],
        "Metrics": [{"Name": "LatencyMs", "Unit": "Milliseconds"}, {"Name": "CostUsd", "Unit": "None"},
                    {"Name": "InputTokens", "Unit": "Count"}, {"Name": "OutputTokens", "Unit": "Count"}]}]},
        "LatencyMs": rec["latency_ms"], "CostUsd": rec["cost_bedrock_equiv_usd"] or 0,
        "InputTokens": rec["input_tokens"], "OutputTokens": rec["output_tokens"],
        "StopReason": rec["stop_reason"]}))
