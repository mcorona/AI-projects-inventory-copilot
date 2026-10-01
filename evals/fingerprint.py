"""Huellas (hash) de lo que determina el resultado de cada suite de evaluacion.

Si cambia un prompt, la descripcion de una tool, una regla de guardrail o un dataset que la
corrida usa, cambia la huella de las suites afectadas. El gate de CI compara las huellas del ultimo
reporte con las actuales: un cambio sin reevaluar hace fallar el CI.

Cada huella cubre solo lo que determina los numeros que se reportan: run_all mide sobre el split
test, asi que los archivos *_dev.jsonl no entran en sql/agent/rag (editarlos no cambia ninguna
metrica de test). Si entran en guardrails, porque sus preguntas forman el set de falsos positivos.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "evals" / "datasets"


def _h(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _files(*paths: Path) -> list[bytes]:
    return [p.read_bytes() for p in sorted(paths)]


def fingerprints() -> dict[str, str]:
    from src.agent.core import EMPTY_ANSWER_NUDGE, PROMPT_DEFENSE_RULE, SYSTEM_PROMPT
    from src.guardrails import injection, pii, pipeline, secrets
    from src.rag import chunking
    from src.tools.registry import build_tools
    from src.tools.sql_tool import SCHEMA_PROMPT
    from src.tools.sql_verifier import VERIFIER_PROMPT
    from evals.faithfulness import JUDGE_PROMPT
    from evals.run_guardrails_eval import ATTACKS_PATH, BENIGN_PATHS

    tool_specs = json.dumps([t.spec() for t in build_tools(llm=object()).values()], sort_keys=True)
    return {
        "sql": _h(SCHEMA_PROMPT, VERIFIER_PROMPT, *_files(DATASETS / "sql_test.jsonl")),
        "agent": _h(SYSTEM_PROMPT, PROMPT_DEFENSE_RULE, EMPTY_ANSWER_NUDGE, SCHEMA_PROMPT, tool_specs,
                    *_files(DATASETS / "agent_test.jsonl")),
        "rag": _h(inspect.getsource(chunking), *_files(*(ROOT / "data" / "docs").glob("*.md")),
                  *_files(DATASETS / "rag_test.jsonl")),
        "guardrails": _h(inspect.getsource(injection), inspect.getsource(pii),
                         inspect.getsource(secrets), inspect.getsource(pipeline),
                         *_files(ATTACKS_PATH, *BENIGN_PATHS, *(ROOT / "data" / "docs").glob("*.md"))),
        "judge": _h(JUDGE_PROMPT, *_files(DATASETS / "judge_calibration.jsonl")),
    }


if __name__ == "__main__":
    print(json.dumps(fingerprints(), indent=2))
