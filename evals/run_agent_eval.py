"""Evaluacion del agente sobre evals/datasets/agent_{dev,test}.jsonl.

Uso:
    python -m evals.run_agent_eval
    python -m evals.run_agent_eval --provider omniroute

Seleccion de tools: correcta si llama todas las esperadas y ninguna fuera de
esperadas + opcionales (sin importar orden ni repeticiones).
Exactitud de la respuesta: si la tarea trae `expected_facts` (grupos de alternativas), la
respuesta debe contener al menos una alternativa de cada grupo. Las acciones que requieren
confirmacion (ordenes de compra) se RECHAZAN automaticamente: la eval nunca escribe en la DB.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPORTS_DIR = Path(__file__).parent / "reports"


def load_golden(split: str = "dev") -> list[dict]:
    from evals.datasets import load_dataset
    return load_dataset("agent", split)


def normalize_answer(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"(?<=\d)[,\u202f\u00a0 ](?=\d{3}(?!\d))", "", text)   # 2,326 -> 2326


def fact_present(answer_norm: str, alternative: str) -> bool:
    alt = normalize_answer(alternative)
    if re.fullmatch(r"\d+(\.\d+)?", alt):
        # numeros por valor: 19820 == 19820.00, pero 2 != 2026 y 98 != 98.5
        if any(float(n) == float(alt) for n in re.findall(r"\d+(?:\.\d+)?", answer_norm)):
            return True
        # espanol: el punto tambien separa miles ("3.222 unidades" == 3222)
        if re.fullmatch(r"\d{4,}", alt):
            dotted = f"{int(alt):,}".replace(",", ".")
            return re.search(rf"(?<![\d.]){re.escape(dotted)}(?![\d]|[.,]\d)", answer_norm) is not None
        return False
    return alt in answer_norm


def facts_match(answer: str, groups: list[list[str]]) -> tuple[bool, list[list[str]]]:
    norm = normalize_answer(answer)
    missing = [g for g in groups if not any(fact_present(norm, alt) for alt in g)]
    return not missing, missing


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
        facts_ok, facts_missing = facts_match(r.answer, g.get("expected_facts", []))
        # contexto para el juez de faithfulness: todo lo que las tools devolvieron al modelo
        context = "\n".join(m["content"] for m in (r._state.messages if r._state else [])
                            if m["role"] == "tool")
        items.append({"id": g["id"], "question": g["question"], "expected": g["expected_tools"],
                      "used": used, **score_tools(g["expected_tools"], used, g.get("optional_tools")),
                      "has_facts": bool(g.get("expected_facts")), "facts_ok": facts_ok,
                      "facts_missing": facts_missing,
                      "tool_errors": [s.error for s in r.steps if s.error],
                      "stop_reason": r.stop_reason, "answer": r.answer, "context": context,
                      "llm_trace": r.llm_trace,
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
        "answer_accuracy": (round(sum(i["facts_ok"] for i in items if i["has_facts"])
                                  / sum(i["has_facts"] for i in items), 4)
                            if any(i["has_facts"] for i in items) else None),
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
    p.add_argument("--split", default="dev", choices=["dev", "test"])
    args = p.parse_args()

    report = evaluate(load_golden(args.split)[: args.limit], Agent(get_provider(args.provider)))
    report["summary"]["split"] = args.split
    for i in report["items"]:
        mark = "OK " if i["match"] else "XX "
        detail = "" if i["match"] else f"  faltan={i['missing']} sobran={i['extra']}"
        print(f"{mark}{i['id']}  {i['latency_ms'] / 1000:>5.1f} s  {i['used']}{detail}")
    s = report["summary"]
    models = ", ".join(f"{m} x{c}" for m, c in s["models"].items())
    print(f"\n[{s['provider']}] {models}  n={s['n']}")
    print(f"  tool selection accuracy : {s['tool_selection_accuracy']:.1%}")
    if s["answer_accuracy"] is not None:
        print(f"  exactitud de respuestas : {s['answer_accuracy']:.1%}")
    print(f"  respondidas             : {s['answered_rate']:.1%}  (tools/tarea {s['avg_tool_calls']})")
    print(f"  latencia p50            : {s['latency_p50_ms'] / 1000:.1f} s")
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS_DIR / f"agent_eval_{args.split}_{s['provider'] or 'unknown'}_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  reporte                 : {out}")


if __name__ == "__main__":
    main()
