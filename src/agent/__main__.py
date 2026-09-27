"""CLI del agente.

Uso:
    python -m src.agent "¿Qué SKUs críticos están bajo su punto de reorden y qué dice la política?"
    python -m src.agent --provider omniroute --trace "¿Cómo está el SKU-0009?"
"""
import argparse

from dotenv import load_dotenv

from src.agent import Agent
from src.llm import get_provider

load_dotenv()
p = argparse.ArgumentParser()
p.add_argument("question")
p.add_argument("--provider", default=None)
p.add_argument("--trace", action="store_true", help="muestra cada llamada a herramienta")
args = p.parse_args()

r = Agent(get_provider(args.provider)).run(args.question)
if args.trace:
    for i, s in enumerate(r.steps, 1):
        status = "ok" if s.ok else f"ERROR {s.error}"
        print(f"  [{i}] {s.tool}({s.arguments}) {s.latency_ms:.0f} ms {status}")
    print()
print(r.answer)
models = ", ".join(f"{m} x{c}" for m, c in r.models.items())
print(f"\n— {r.stop_reason} · {r.llm_calls} llamadas LLM · {len(r.steps)} tools · "
      f"tokens {r.input_tokens}/{r.output_tokens} · {r.latency_ms / 1000:.1f} s · {models}")
