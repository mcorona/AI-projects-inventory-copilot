"""Evaluacion de recuperacion RAG sobre evals/rag_golden_set.jsonl.

Uso:
    python -m evals.run_rag_eval            # requiere haber corrido scripts.ingest_docs
    python -m evals.run_rag_eval --k 5

Metricas por documento esperado: hit@1, hit@k y MRR (rango reciproco del primer acierto).
`expected_sources` admite varios documentos cuando la respuesta aparece en mas de uno.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent / "rag_golden_set.jsonl"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def first_hit_rank(sources: list[str], expected: list[str]) -> int | None:
    """Posicion (1-based) del primer resultado de algun documento esperado, o None."""
    for i, s in enumerate(sources, 1):
        if s in expected:
            return i
    return None


def summarize(ranks: list[int | None], k: int) -> dict:
    n = len(ranks) or 1
    return {
        "n": len(ranks), "k": k,
        "hit_at_1": round(sum(r == 1 for r in ranks) / n, 4),
        f"hit_at_{k}": round(sum(r is not None and r <= k for r in ranks) / n, 4),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 4),
    }


def evaluate(golden: list[dict], search_fn, k: int = 4) -> dict:
    items = []
    for g in golden:
        hits = search_fn(g["question"], k)
        sources = [h["source"] for h in hits]
        rank = first_hit_rank(sources, g["expected_sources"])
        items.append({"id": g["id"], "question": g["question"], "expected": g["expected_sources"],
                      "rank": rank, "retrieved": [(h["source"], h.get("section"), h["score"]) for h in hits]})
    return {"summary": summarize([i["rank"] for i in items], k), "items": items}


def main() -> None:
    from dotenv import load_dotenv

    from src.llm import embed_model_id, get_embedder
    from src.rag.store import search

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--provider", default=None, help="proveedor de embeddings")
    args = p.parse_args()

    embedder = get_embedder(args.provider)
    report = evaluate(load_golden(), lambda q, k: search(q, k=k, embedder=embedder), args.k)
    report["summary"]["embed_model"] = embed_model_id(embedder)
    for i in report["items"]:
        mark = "OK " if i["rank"] == 1 else ("~  " if i["rank"] else "XX ")
        got = i["retrieved"][0][0] if i["retrieved"] else "-"
        print(f"{mark}{i['id']}  rank={i['rank']}  top1={got}")
    s = report["summary"]
    print(f"\n[{s['embed_model']}] n={s['n']}  hit@1={s['hit_at_1']:.1%}  "
          f"hit@{s['k']}={s[f'hit_at_{s['k']}']:.1%}  MRR={s['mrr']:.3f}")
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS_DIR / f"rag_eval_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"reporte: {out}")


if __name__ == "__main__":
    main()
