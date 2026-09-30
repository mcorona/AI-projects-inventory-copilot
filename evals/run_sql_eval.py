"""Evaluacion de text-to-SQL por execution accuracy sobre evals/datasets/sql_{dev,test}.jsonl.

Uso:
    python -m evals.run_sql_eval                       # proveedor de LLM_PROVIDER
    python -m evals.run_sql_eval --provider bedrock --limit 5
    python -m evals.run_sql_eval --split test          # set de prueba (nunca se usa para ajustar)

Por cada pregunta se ejecutan el SQL de referencia y el generado (ambos con PG_DSN,
rol copilot_ro) y se comparan los resultados, no el texto del SQL:
- estricto: mismas columnas (en cualquier orden) y mismas filas.
- execution accuracy (principal): ademas tolera columnas extra en la prediccion.
Las filas se comparan como multiconjunto salvo que la pregunta tenga order_matters.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import permutations
from pathlib import Path

REPORTS_DIR = Path(__file__).parent / "reports"
FLOAT_DIGITS = 2
MAX_EXTRA_COLS = 8  # limite de columnas para buscar proyecciones (costo combinatorio)


def load_golden(split: str = "dev") -> list[dict]:
    from evals.datasets import load_dataset
    return load_dataset("sql", split)


def normalize_value(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (Decimal, float, int)):
        return round(float(v), FLOAT_DIGITS)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def normalize_rows(rows) -> list[tuple]:
    return [tuple(normalize_value(v) for v in r) for r in rows]


def _same_rows(a: list[tuple], b: list[tuple], order_matters: bool) -> bool:
    return a == b if order_matters else Counter(a) == Counter(b)


def results_match(gold_rows, pred_rows, order_matters: bool = False,
                  allow_extra_columns: bool = True) -> bool:
    """True si alguna proyeccion/permutacion de columnas de pred reproduce gold."""
    gold, pred = normalize_rows(gold_rows), normalize_rows(pred_rows)
    if len(gold) != len(pred):
        return False
    if not gold:
        return True
    k, n = len(gold[0]), len(pred[0])
    if n < k or (n > k and not allow_extra_columns) or n > MAX_EXTRA_COLS:
        return n == k and _same_rows(gold, pred, order_matters)
    for idx in permutations(range(n), k):
        if _same_rows(gold, [tuple(r[i] for i in idx) for r in pred], order_matters):
            return True
    return False


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


def evaluate(golden: list[dict], llm, executor, max_tokens: int = 8192, verifier=None) -> dict:
    """Corre la tool sobre cada pregunta y compara contra el SQL de referencia."""
    from src.guardrails.sql_guard import validate_sql
    from src.tools.sql_tool import run_sql_tool

    items = []
    for g in golden:
        _, gold_rows = executor(validate_sql(g["sql"]))
        r = run_sql_tool(g["question"], llm=llm, executor=executor, max_tokens=max_tokens, verifier=verifier)
        ok = r.ok and results_match(gold_rows, r.rows, g.get("order_matters", False))
        strict = r.ok and results_match(gold_rows, r.rows, g.get("order_matters", False),
                                        allow_extra_columns=False)
        items.append({
            "id": g["id"], "question": g["question"], "tags": g.get("tags", []),
            "gold_sql": g["sql"], "pred_sql": r.sql or r.raw_sql, "error": r.error,
            "match": ok, "match_strict": strict,
            "gold_rows": len(gold_rows), "pred_rows": len(r.rows),
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "llm_latency_ms": round(r.llm_latency_ms, 1),
            "total_latency_ms": round(r.total_latency_ms, 1),
            "provider": r.provider, "model": r.model,
            "escalations": r.escalations, "attempts": r.attempts, "cache_hit": r.cache_hit,
            "verifier": _verifier_outcomes(r.attempts, gold_rows, g.get("order_matters", False), executor),
        })
    summary = summarize(items)
    # con el router, cada item puede venir de un proveedor distinto: se reporta el router
    summary["provider"] = getattr(llm, "name", summary["provider"])
    return {"summary": summary, "items": items}


def _verifier_outcomes(attempts, gold_rows, order_matters: bool, executor) -> list[str]:
    """Clasifica cada veredicto del verificador contra la referencia: re-ejecuta el SQL del intento.

    accept_ok / accept_wrong (fallo no detectado) / reject_ok (falsa alarma) / reject_wrong (acierto).
    """
    out = []
    for a in attempts or []:
        if a.get("verifier_ok") is None or not a.get("sql"):
            continue
        try:
            _, rows = executor(a["sql"])
            correct = results_match(gold_rows, rows, order_matters)
        except Exception:
            correct = False
        out.append(("accept_" if a["verifier_ok"] else "reject_") + ("ok" if correct else "wrong"))
    return out


def summarize(items: list[dict]) -> dict:
    n = len(items) or 1
    lat = [i["total_latency_ms"] for i in items]
    errors = [i["error"] or "" for i in items]
    return {
        "n": len(items),
        "execution_accuracy": round(sum(i["match"] for i in items) / n, 4),
        "execution_accuracy_strict": round(sum(i["match_strict"] for i in items) / n, 4),
        "guard_rejected_rate": round(sum(e.startswith("guard_rejected") for e in errors) / n, 4),
        "truncated_rate": round(sum(e.startswith("truncated") for e in errors) / n, 4),
        "escalation_rate": round(sum(i.get("escalations", 0) > 0 for i in items) / n, 4),
        # si hay aciertos de cache, la latencia de esa corrida no es representativa
        "cache_hit_rate": round(sum(bool(i.get("cache_hit")) for i in items) / n, 4),
        "error_rate": round(sum(bool(e) for e in errors) / n, 4),
        "latency_p50_ms": round(_percentile(lat, 50), 1),
        "latency_p95_ms": round(_percentile(lat, 95), 1),
        "input_tokens": sum(i["input_tokens"] for i in items),
        "output_tokens": sum(i["output_tokens"] for i in items),
        "provider": items[0]["provider"] if items else "",
        "model": items[0]["model"] if items else "",
        # los routers (p. ej. OmniRoute auto/*) pueden responder con modelos distintos
        "models": dict(Counter(i["model"] for i in items if i["model"])),
        # matriz del verificador (solo con --verifier): falsas alarmas = reject_ok
        "verifier_outcomes": dict(Counter(o for i in items for o in i.get("verifier", []))),
    }


def main() -> None:
    from dotenv import load_dotenv

    from src.llm import get_provider
    from src.tools.sql_tool import execute_readonly

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--split", default="dev", choices=["dev", "test"])
    p.add_argument("--verifier", action="store_true",
                   help="con --provider router: verifica con el nivel barato y escala si no responde la pregunta")
    p.add_argument("--max-tokens", type=int, default=8192,
                   help="los modelos de razonamiento necesitan margen para <think>")
    args = p.parse_args()

    golden = load_golden(args.split)[: args.limit]
    llm = get_provider(args.provider)
    dsn = os.environ["PG_DSN"]
    verifier = None
    if args.verifier:
        from src.tools.sql_verifier import SQLVerifier
        from src.tools.sql_tool import schema_description
        verifier = SQLVerifier(llm.tiers[0], schema_hint=schema_description())
    report = evaluate(golden, llm, lambda sql: execute_readonly(sql, dsn), args.max_tokens, verifier)
    report["summary"]["verifier"] = bool(verifier)

    for i in report["items"]:
        mark = "OK " if i["match"] else "XX "
        print(f"{mark}{i['id']}  {i['total_latency_ms']:>8.0f} ms  {i['error'] or ''}")
    s = report["summary"]
    s["split"] = args.split
    models = ", ".join(f"{m} x{c}" for m, c in s["models"].items())
    print(f"\n[{s['provider']}] {models}  n={s['n']}")
    print(f"  execution accuracy : {s['execution_accuracy']:.1%} "
          f"(estricta {s['execution_accuracy_strict']:.1%})")
    print(f"  errores            : {s['error_rate']:.1%} (guard {s['guard_rejected_rate']:.1%})")
    print(f"  latencia p50/p95   : {s['latency_p50_ms']:.0f} / {s['latency_p95_ms']:.0f} ms")
    print(f"  tokens in/out      : {s['input_tokens']} / {s['output_tokens']}")
    if s["escalation_rate"]:
        print(f"  escaladas (router) : {s['escalation_rate']:.1%}")
    if s["verifier_outcomes"]:
        print(f"  verificador        : {s['verifier_outcomes']}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS_DIR / f"sql_eval_{args.split}_{s['provider'] or 'unknown'}_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  reporte            : {out}")


if __name__ == "__main__":
    main()
