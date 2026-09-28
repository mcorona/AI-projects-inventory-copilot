"""Evaluacion de seleccion de herramientas del agente sobre evals/agent_golden_set.jsonl.

Uso:
    python -m evals.run_agent_eval
    python -m evals.run_agent_eval --provider omniroute

Una tarea es correcta si llama todas las tools esperadas y ninguna fuera de
esperadas + opcionales (sin importar orden ni repeticiones). Las acciones que requieren
confirmacion (ordenes de compra) se RECHAZAN automaticamente: la eval nunca escribe en la DB.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent / "agent_golden_set.jsonl"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def score_tools(expected: list[str], used: list[str], optional: list[str] | None = None) -> dict:
    exp, got, allowed = set(expected), set(used), set(expected) | set(optional or [])
    return {"match": exp <= got <= allowed, "missing": sorted(exp - got), "extra": sorted(got - allowed)}


def evaluate(golden: list[dict], agent) -> dict:
    items = []
    for g in golden:
        r = agent.run(g["question"])
        while r.stop_reason == "confirmation_required":
            r = agent.resume(r, approve=False, note="evaluacion: no se crean ordenes")
        used = r.tools_used  # incluye las acciones rechazadas (quedan como pasos fallidos)
        items.append({"id": g["id"], "question": g["question"], "expected": g["expected_tools"],
                      "used": used, **score_tools(g["expected_tools"], used, g.get("optional_tools")),
                      "tool_errors": [s.error for s in r.steps if s.error],
                      "stop_reason": r.stop_reason, "answer": r.answer,
                      "llm_calls": r.llm_calls, "input_tokens": r.input_tokens,
                      "output_tokens": r.output_tokens, "latency_ms": round(r.latency_ms, 1),
                      "models": r.models})
    n = len(items) or 1
    models: Counter = Counter()
    for i in items:
        models.update(i["models"])
    lat = sorted(i["latency_ms"] for i in items)
    summary = {
        "n": len(items),
        "tool_selection_accuracy": round(sum(i["match"] for i in items) / n, 4),
        "answered_rate": round(sum(i["stop_reason"] == "answer" for i in items) / n, 4),
        "avg_tool_calls": round(sum(len(i["used"]) for i in items) / n, 2),
        "latency_p50_ms": lat[len(lat) // 2] if lat else 0,
        "input_tokens": sum(i["input_tokens"] for i in items),
        "output_tokens": sum(i["output_tokens"] for i in items),
        "provider": getattr(agent.llm, "name", ""), "models": dict(models),
    }
    return {"summary": summary, "items": items}


def main() -> None:
    from dotenv import load_dotenv

    from src.agent import Agent
    from src.llm import get_provider

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default=None)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    report = evaluate(load_golden()[: args.limit], Agent(get_provider(args.provider)))
    for i in report["items"]:
        mark = "OK " if i["match"] else "XX "
        detail = "" if i["match"] else f"  faltan={i['missing']} sobran={i['extra']}"
        print(f"{mark}{i['id']}  {i['latency_ms'] / 1000:>5.1f} s  {i['used']}{detail}")
    s = report["summary"]
    models = ", ".join(f"{m} x{c}" for m, c in s["models"].items())
    print(f"\n[{s['provider']}] {models}  n={s['n']}")
    print(f"  tool selection accuracy : {s['tool_selection_accuracy']:.1%}")
    print(f"  respondidas             : {s['answered_rate']:.1%}  (tools/tarea {s['avg_tool_calls']})")
    print(f"  latencia p50            : {s['latency_p50_ms'] / 1000:.1f} s")
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS_DIR / f"agent_eval_{s['provider'] or 'unknown'}_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  reporte                 : {out}")


if __name__ == "__main__":
    main()
