"""Evaluacion de guardrails de entrada: deteccion de ataques y falsos positivos.

Uso:
    python -m evals.run_guardrails_eval                                  # solo heuristicas (sin LLM)
    python -m evals.run_guardrails_eval --llm-classifier omniroute:kr/minimax-m2.1

- Ataques: evals/datasets/guardrails_attacks.jsonl (inyeccion directa, jailbreak, fuga de prompt, bypass de
  aprobaciones, SQL de escritura, ofuscacion, PII). Correcto = la accion esperada (BLOCK/ANONYMIZE).
- Falsos positivos: las preguntas legitimas de los golden sets (SQL, agente, RAG) deben pasar sin
  bloqueo, y los chunks del corpus de politicas no deben marcarse como inyeccion indirecta.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).parent
ATTACKS_PATH = EVALS / "datasets" / "guardrails_attacks.jsonl"
BENIGN_PATHS = [EVALS / "datasets" / f"{suite}_{split}.jsonl"
                for suite in ("sql", "agent", "rag") for split in ("dev", "test")]
REPORTS_DIR = EVALS / "reports"


def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def benign_questions() -> list[str]:
    return [q["question"] for p in BENIGN_PATHS for q in load_jsonl(p)]


def evaluate(pipeline, attacks: list[dict], benign: list[str], corpus: list[str]) -> dict:
    items, by_cat = [], defaultdict(lambda: [0, 0])
    for a in attacks:
        t = time.perf_counter()
        d = pipeline.check_input(a["text"])
        ok = d.action == a["expected"]
        by_cat[a["category"]][0] += ok
        by_cat[a["category"]][1] += 1
        items.append({"id": a["id"], "category": a["category"], "expected": a["expected"],
                      "action": d.action, "ok": ok, "findings": d.findings,
                      "latency_ms": round((time.perf_counter() - t) * 1000, 1)})
    fp_questions, benign_lat = [], []
    for q in benign:   # costo que paga CADA pregunta legitima (con clasificador LLM, una llamada extra)
        t = time.perf_counter()
        if pipeline.check_input(q).action == "BLOCK":
            fp_questions.append(q)
        benign_lat.append((time.perf_counter() - t) * 1000)
    fp_chunks = [c[:80] for c in corpus if pipeline.sanitize_tool_result("doc", {"c": c})[1]]
    n = len(items) or 1
    return {
        "summary": {
            "attacks": len(items), "detection_rate": round(sum(i["ok"] for i in items) / n, 4),
            "by_category": {c: f"{ok}/{tot}" for c, (ok, tot) in sorted(by_cat.items())},
            "benign_questions": len(benign), "false_positive_rate": round(len(fp_questions) / (len(benign) or 1), 4),
            "corpus_chunks": len(corpus), "corpus_false_positives": len(fp_chunks),
            "attack_latency_p50_ms": sorted(i["latency_ms"] for i in items)[len(items) // 2] if items else 0,
            "benign_latency_p50_ms": round(sorted(benign_lat)[len(benign_lat) // 2], 1) if benign_lat else 0,
        },
        "items": items, "false_positive_questions": fp_questions, "false_positive_chunks": fp_chunks,
    }


def main() -> None:
    from dotenv import load_dotenv

    from scripts.ingest_docs import load_chunks
    from src.guardrails.injection import LLMInjectionClassifier
    from src.guardrails.pipeline import GuardrailPipeline

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--llm-classifier", default=None, help="proveedor:modelo, p. ej. lmstudio o omniroute:kr/minimax-m2.1")
    args = p.parse_args()

    classifier, label = None, "heuristics"
    if args.llm_classifier:
        from src.llm import get_provider
        provider, _, model = args.llm_classifier.partition(":")
        import uuid
        # id de corrida en el prompt de sistema: evita respuestas cacheadas (OmniRoute) y latencias falsas
        classifier = LLMInjectionClassifier(get_provider(provider, model or None),
                                            system_suffix=f"\n(corrida de evaluacion {uuid.uuid4().hex[:8]})")
        label = f"heuristics+llm:{args.llm_classifier}"

    report = evaluate(GuardrailPipeline(classifier=classifier), load_jsonl(ATTACKS_PATH),
                      benign_questions(), [c.content for c in load_chunks()])
    report["summary"]["config"] = label
    for i in report["items"]:
        print(f"{'OK ' if i['ok'] else 'XX '}{i['id']} {i['category']:<16} esperado={i['expected']:<9} "
              f"obtenido={i['action']:<9} {i['latency_ms']:>7.0f} ms")
    s = report["summary"]
    print(f"\n[{label}]")
    print(f"  deteccion        : {s['detection_rate']:.1%} de {s['attacks']} ataques  {s['by_category']}")
    print(f"  falsos positivos : {s['false_positive_rate']:.1%} de {s['benign_questions']} preguntas legitimas; "
          f"{s['corpus_false_positives']}/{s['corpus_chunks']} chunks del corpus")
    print(f"  latencia agregada a preguntas legitimas (p50): {s['benign_latency_p50_ms']:.0f} ms")
    for q in report["false_positive_questions"]:
        print(f"    FP: {q}")
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"llm_{args.llm_classifier.split(':')[0]}" if classifier else "heuristics"
    out = REPORTS_DIR / f"guardrails_eval_{tag}_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  reporte          : {out}")


if __name__ == "__main__":
    main()
