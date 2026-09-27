"""Prueba de humo: verifica que el proveedor LLM configurado responde.

Uso:
    python -m scripts.smoke_test                  # usa LLM_PROVIDER del .env
    python -m scripts.smoke_test --provider omniroute
"""
import argparse

from dotenv import load_dotenv

from src.llm import get_provider

load_dotenv()

p = argparse.ArgumentParser()
p.add_argument("--provider", default=None)
p.add_argument("--no-embed", action="store_true")
args = p.parse_args()

llm = get_provider(args.provider)
r = llm.chat([{"role": "user", "content": "Responde en una linea: que es un punto de reorden en inventarios?"}],
             max_tokens=800)
print(f"[{r.provider}] {r.model}")
print(f"  respuesta : {r.text.strip()}")
print(f"  tokens    : in={r.input_tokens} out={r.output_tokens}")
print(f"  latencia  : {r.latency_ms:.0f} ms")

if not args.no_embed:
    try:
        v = llm.embed(["stock critico"])[0]
        print(f"  embedding : dim={len(v)}")
    except NotImplementedError as e:
        print(f"  embedding : omitido ({e})")
