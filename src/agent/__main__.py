"""CLI del agente.

Uso:
    python -m src.agent "¿Qué SKUs críticos están bajo su punto de reorden y qué dice la política?"
    python -m src.agent --provider omniroute --trace "¿Cómo está el SKU-0009?"
    python -m src.agent --user "Manuel" "Propón una orden de 1500 unidades del SKU-0009"
    python -m src.agent --yes ...     # confirma automaticamente (solo demos)
"""
import argparse

from dotenv import load_dotenv

from src.agent import Agent
from src.audit import get_audit_sink
from src.guardrails.pipeline import GuardrailPipeline
from src.llm import get_provider


def show_preview(p: dict) -> None:
    print("\n┌─ Confirmación requerida: orden de compra ─────────────────")
    print(f"│ SKU        {p['sku']}  {p['name']}")
    print(f"│ Cantidad   {p['qty']:,} unidades  (~{p['days_of_demand']} días de venta)")
    print(f"│ Monto      ${p['amount']:,.2f} MXN  (costo unitario ${p['unit_cost']:,.2f})")
    print(f"│ Aprobará   nivel {p['required_level']}")
    print(f"│ Stock      {p['total_on_hand']:,} / punto de reorden {p['reorder_point']:,}")
    print(f"│ Proveedor  {p['supplier']} (lead time {p['lead_time_days']} días)")
    print(f"│ Motivo     {p['reason']}")
    print("└────────────────────────────────────────────────────────────")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("question")
    p.add_argument("--provider", default=None)
    p.add_argument("--user", default="usuario", help="quien pregunta y confirma")
    p.add_argument("--trace", action="store_true", help="muestra cada llamada a herramienta")
    p.add_argument("--yes", action="store_true", help="confirma acciones sin preguntar (demos)")
    args = p.parse_args()

    audit = get_audit_sink()
    agent = Agent(get_provider(args.provider), audit=audit,
                  guardrails=GuardrailPipeline.from_env(audit=audit))
    r = agent.run(args.question, user=args.user)
    while r.stop_reason == "confirmation_required":
        show_preview(r.pending.preview)
        approve = args.yes or input("¿Confirmas la propuesta? [s/N] ").strip().lower() in ("s", "si", "sí", "y")
        r = agent.resume(r, approve=approve, approver=args.user)

    if args.trace:
        for i, s in enumerate(r.steps, 1):
            status = "ok" if s.ok else f"ERROR {s.error}"
            print(f"  [{i}] {s.tool}({s.arguments}) {s.latency_ms:.0f} ms {status}")
        if r.guardrail_findings:
            print(f"  guardrails: {r.guardrail_findings}")
        print()
    print(r.answer)
    models = ", ".join(f"{m} x{c}" for m, c in r.models.items())
    print(f"\n— {r.stop_reason} · {r.llm_calls} llamadas LLM · {len(r.steps)} tools · "
          f"tokens {r.input_tokens}/{r.output_tokens} · {r.latency_ms / 1000:.1f} s · {models}")


if __name__ == "__main__":
    main()
