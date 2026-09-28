"""Evaluacion en vivo de Amazon Bedrock Guardrails contra las defensas locales.

Uso (requiere el guardrail desplegado con infra/ y credenciales AWS):
    BEDROCK_GUARDRAIL_ID=... python -m evals.run_bedrock_guardrail_eval

1. Entrada: los 30 ataques de guardrails_attacks.jsonl y las preguntas legitimas de todos los
   golden sets -> deteccion y falsos positivos (mismos datos que run_guardrails_eval).
2. Contextual grounding: los 16 casos de judge_calibration.jsonl -> acuerdo con las etiquetas,
   comparable con el juez LLM local.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from evals.run_guardrails_eval import ATTACKS_PATH, benign_questions, load_jsonl

REPORTS_DIR = Path(__file__).parent / "reports"


def input_action(result, blocked_message: str) -> str:
    """Bedrock devuelve GUARDRAIL_INTERVENED tanto al bloquear como al anonimizar."""
    if not result.intervened:
        return "ALLOW"
    return "BLOCK" if result.text.strip() == blocked_message.strip() else "ANONYMIZE"


def evaluate_input(guardrail, attacks: list[dict], benign: list[str], blocked_message: str) -> dict:
    items, by_cat, lat = [], defaultdict(lambda: [0, 0]), []
    for a in attacks:
        t = time.perf_counter()
        r = guardrail.apply(a["text"], "INPUT")
        lat.append((time.perf_counter() - t) * 1000)
        action = input_action(r, blocked_message)
        ok = action == a["expected"]
        by_cat[a["category"]][0] += ok
        by_cat[a["category"]][1] += 1
        items.append({"id": a["id"], "category": a["category"], "expected": a["expected"], "action": action,
                      "ok": ok, "findings": r.findings})
    fps = []
    for q in benign:
        t = time.perf_counter()
        r = guardrail.apply(q, "INPUT")
        lat.append((time.perf_counter() - t) * 1000)
        if input_action(r, blocked_message) == "BLOCK":
            fps.append({"question": q, "findings": r.findings})
    n = len(items) or 1
    return {"detection_rate": round(sum(i["ok"] for i in items) / n, 4),
            "by_category": {c: f"{ok}/{tot}" for c, (ok, tot) in sorted(by_cat.items())},
            "benign_questions": len(benign), "false_positive_rate": round(len(fps) / (len(benign) or 1), 4),
            "false_positives": fps, "latency_p50_ms": round(sorted(lat)[len(lat) // 2], 1), "items": items}


def evaluate_grounding(guardrail, rows: list[dict]) -> dict:
    items = []
    for r in rows:
        g = guardrail.check_grounding(r["context"], r["question"], r["answer"])
        # "fiel" para Bedrock = no intervino por grounding/relevancia
        predicted_faithful = not any(v.get("action") == "BLOCKED" for v in g["scores"].values())
        items.append({"id": r["id"], "label_faithful": r["faithful"], "bedrock_faithful": predicted_faithful,
                      "agree": predicted_faithful == r["faithful"], "scores": g["scores"], "note": r["note"]})
    n = len(items) or 1
    unfaithful = [i for i in items if not i["label_faithful"]]
    return {"n": len(items), "accuracy": round(sum(i["agree"] for i in items) / n, 4),
            "unfaithful_recall": round(sum(not i["bedrock_faithful"] for i in unfaithful) / (len(unfaithful) or 1), 4),
            "items": items}


def main() -> None:
    from dotenv import load_dotenv

    from evals.datasets import load_dataset
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
    from stacks.guardrail_stack import BLOCKED_INPUT
    from src.guardrails.bedrock import BedrockGuardrail

    load_dotenv()
    guardrail = BedrockGuardrail(os.environ["BEDROCK_GUARDRAIL_ID"], os.getenv("BEDROCK_GUARDRAIL_VERSION", "1"))
    inp = evaluate_input(guardrail, load_jsonl(ATTACKS_PATH), benign_questions(), BLOCKED_INPUT)
    grd = evaluate_grounding(guardrail, load_dataset("judge", "calibration"))
    report = {"guardrail_id": guardrail.guardrail_id, "version": guardrail.version,
              "input": inp, "grounding": grd}
    for i in inp["items"]:
        print(f"{'OK ' if i['ok'] else 'XX '}{i['id']} {i['category']:<16} esperado={i['expected']:<9} "
              f"bedrock={i['action']:<9} {i['findings']}")
    for fp in inp["false_positives"]:
        print(f"  FP: {fp['question']}  {fp['findings']}")
    for g in grd["items"]:
        s = {k: v.get("score") for k, v in g["scores"].items()}
        print(f"{'OK ' if g['agree'] else 'XX '}{g['id']} etiqueta={g['label_faithful']!s:<5} "
              f"bedrock={g['bedrock_faithful']!s:<5} {s} {g['note']}")
    print(f"\n[Bedrock Guardrails {guardrail.guardrail_id} v{guardrail.version}]")
    print(f"  entrada   : deteccion {inp['detection_rate']:.1%} {inp['by_category']}")
    print(f"              falsos positivos {inp['false_positive_rate']:.1%} de {inp['benign_questions']} · p50 {inp['latency_p50_ms']:.0f} ms")
    print(f"  grounding : acuerdo {grd['accuracy']:.1%} con 16 etiquetas · detecta infieles {grd['unfaithful_recall']:.1%}")
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS_DIR / f"bedrock_guardrail_eval_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  reporte   : {out}")


if __name__ == "__main__":
    main()
