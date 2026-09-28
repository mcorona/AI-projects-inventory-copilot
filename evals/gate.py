"""Gate de CI sobre los resultados de evaluacion (no necesita LLM ni base de datos).

Falla si:
- las huellas de evals/results/latest.json no coinciden con las actuales (se cambio un prompt,
  una tool, un guardrail o un dataset sin volver a evaluar: `python -m evals.run_all`);
- alguna metrica cae fuera de los umbrales de evals/gate.json.

Uso:  python -m evals.gate
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EVALS = Path(__file__).parent
LATEST = EVALS / "results" / "latest.json"
CONFIG = EVALS / "gate.json"


def resolve(data: dict, path: str):
    """'agent_test.faithfulness_rate' -> valor; si es un agregado {mean,min,max} usa la media."""
    node = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node["mean"] if isinstance(node, dict) and "mean" in node else node


def check(results: dict, config: dict, current_fp: dict) -> list[str]:
    failures = []
    for suite in config["fingerprint_suites"]:
        old, new = results.get("fingerprints", {}).get(suite), current_fp.get(suite)
        if old != new:
            failures.append(f"huella '{suite}' desactualizada ({old} != {new}): reevalua con python -m evals.run_all")

    def apply(scope: str, data: dict, rules: dict):
        for path, rule in rules.items():
            value = resolve(data, path)
            if value is None:
                failures.append(f"{scope}{path}: falta en los resultados")
            elif "min" in rule and value < rule["min"]:
                failures.append(f"{scope}{path} = {value:.4g} < minimo {rule['min']}")
            elif "max" in rule and value > rule["max"]:
                failures.append(f"{scope}{path} = {value:.4g} > maximo {rule['max']}")

    apply("", results, config["global"])
    for provider, rules in config["providers"].items():
        if provider not in results.get("providers", {}):
            failures.append(f"{provider}: sin resultados")
            continue
        apply(f"{provider}.", results["providers"][provider], rules)
    return failures


def main() -> int:
    from evals.fingerprint import fingerprints
    if not LATEST.exists():
        print(f"gate: no existe {LATEST}; corre python -m evals.run_all", file=sys.stderr)
        return 1
    results = json.loads(LATEST.read_text(encoding="utf-8"))
    failures = check(results, json.loads(CONFIG.read_text(encoding="utf-8")), fingerprints())
    print(f"gate: resultados de la corrida {results.get('run_id')} (split {results.get('split')}, "
          f"{results.get('repeats')} repeticiones)")
    for f in failures:
        print(f"  FALLA  {f}")
    print("gate: OK" if not failures else f"gate: {len(failures)} fallas")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
