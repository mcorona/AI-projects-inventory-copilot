"""Faithfulness de las respuestas del agente con un juez LLM (validado) + chequeo de cifras.

Uso:
    python -m evals.run_faithfulness_eval --calibrate                    # valida al juez
    python -m evals.run_faithfulness_eval --from-report evals/reports/agent_eval_test_lmstudio_*.json

--calibrate: mide el acuerdo del juez con evals/datasets/judge_calibration.jsonl (respuestas
fieles y con errores plantados). Sin esto, el numero del juez no significa nada.
--from-report: juzga las respuestas reales guardadas por run_agent_eval (con su contexto).
El juez por defecto es Qwen en LM Studio (--judge proveedor:modelo para cambiarlo).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.faithfulness import LLMJudge, numeric_grounding

REPORTS_DIR = Path(__file__).parent / "reports"
DEFAULT_JUDGE = "lmstudio:qwen/qwen3.6-35b-a3b"


def _agreement(items: list[dict]) -> dict:
    """Acuerdo con las etiquetas; positivo = "infiel" detectado."""
    cm = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for i in items:
        pred, actual = i["judge_faithful"] is False, not i["label_faithful"]
        cm[("t" if pred == actual else "f") + ("p" if pred else "n")] += 1
    n = len(items) or 1
    return {"n": len(items), "judge_accuracy": round(sum(i["agree"] for i in items) / n, 4),
            "unfaithful_recall": round(cm["tp"] / ((cm["tp"] + cm["fn"]) or 1), 4),
            "unfaithful_precision": round(cm["tp"] / ((cm["tp"] + cm["fp"]) or 1), 4),
            "confusion": cm}


def calibrate(judge: LLMJudge, rows: list[dict]) -> dict:
    items = []
    for r in rows:
        j = judge.judge(r["question"], r["context"], r["answer"])
        g = numeric_grounding(r["answer"], r["context"], r["question"])
        items.append({"id": r["id"], "label_faithful": r["faithful"], "judge_faithful": j.faithful,
                      "agree": j.faithful == r["faithful"], "numeric_grounded": g.grounded,
                      "difficulty": r.get("difficulty", "obvious"), "note": r["note"], "claims": j.claims})
    n = len(items) or 1
    # "obvious": errores plantados evidentes (v1); "subtle": cifras reales mal atribuidas, limites de
    # rangos, excepciones omitidas y parafrasis fieles que un juez estricto podria rechazar
    by_difficulty = {d: _agreement([i for i in items if i["difficulty"] == d])
                     for d in sorted({i["difficulty"] for i in items})}
    return {"summary": {**_agreement(items),
                        "unparseable": sum(i["judge_faithful"] is None for i in items),
                        "numeric_grounding_accuracy": round(sum(i["numeric_grounded"] == i["label_faithful"]
                                                                for i in items) / n, 4),
                        "by_difficulty": by_difficulty},
            "items": items}


def judge_report(judge: LLMJudge, agent_report: dict) -> dict:
    items = []
    for it in agent_report["items"]:
        if it.get("stop_reason") != "answer" or not it.get("answer"):
            continue
        j = judge.judge(it["question"], it.get("context", ""), it["answer"])
        g = numeric_grounding(it["answer"], it.get("context", ""), it["question"])
        items.append({"id": it["id"], "judge_faithful": j.faithful, "support_rate": j.support_rate,
                      "unsupported_claims": [c for c in j.claims if c.get("verdict") != "supported"],
                      "numeric_grounded": g.grounded, "unsupported_numbers": g.unsupported})
    judged = [i for i in items if i["judge_faithful"] is not None]
    n = len(judged) or 1
    return {"summary": {"n": len(items), "faithfulness_rate": round(sum(i["judge_faithful"] for i in judged) / n, 4),
                        "numeric_grounding_rate": round(sum(i["numeric_grounded"] for i in items) / (len(items) or 1), 4),
                        "unparseable": len(items) - len(judged),
                        "agent_provider": agent_report["summary"].get("provider"),
                        "agent_split": agent_report["summary"].get("split")},
            "items": items}


def main() -> None:
    from dotenv import load_dotenv

    from evals.datasets import load_dataset
    from src.llm import get_provider

    load_dotenv()
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--from-report", type=Path)
    p.add_argument("--judge", default=DEFAULT_JUDGE, help="proveedor:modelo del juez")
    args = p.parse_args()

    provider, _, model = args.judge.partition(":")
    judge = LLMJudge(get_provider(provider, model or None))
    if args.calibrate:
        report = calibrate(judge, load_dataset("judge", "calibration"))
        for i in report["items"]:
            print(f"{'OK ' if i['agree'] else 'XX '}{i['id']} etiqueta={i['label_faithful']!s:<5} "
                  f"juez={i['judge_faithful']!s:<5} cifras={i['numeric_grounded']!s:<5} {i['note']}")
        s = report["summary"]
        print(f"\n[juez {args.judge}] acuerdo {s['judge_accuracy']:.1%} · detecta infieles "
              f"{s['unfaithful_recall']:.1%} · precision {s['unfaithful_precision']:.1%} · "
              f"ilegibles {s['unparseable']} · (chequeo de cifras solo: {s['numeric_grounding_accuracy']:.1%})")
        for d, m in s["by_difficulty"].items():
            print(f"  {d:<8} n={m['n']:<3} acuerdo {m['judge_accuracy']:.1%} · detecta infieles "
                  f"{m['unfaithful_recall']:.1%} · precision {m['unfaithful_precision']:.1%}")
        name = "judge_calibration"
    else:
        agent_report = json.loads(args.from_report.read_text(encoding="utf-8"))
        report = judge_report(judge, agent_report)
        for i in report["items"]:
            print(f"{'OK ' if i['judge_faithful'] else 'XX '}{i['id']} cifras={'ok' if i['numeric_grounded'] else i['unsupported_numbers']}"
                  + (f"  no respaldado: {[c.get('claim') for c in i['unsupported_claims']][:2]}" if i['unsupported_claims'] else ""))
        s = report["summary"]
        print(f"\n[{s['agent_provider']} · {s['agent_split']} · juez {args.judge}] faithfulness "
              f"{s['faithfulness_rate']:.1%} · cifras respaldadas {s['numeric_grounding_rate']:.1%} · ilegibles {s['unparseable']}")
        name = f"faithfulness_{s['agent_split']}_{s['agent_provider']}"
    report["summary"]["judge"] = args.judge
    report["summary"]["judge_tokens"] = {"input": judge.input_tokens, "output": judge.output_tokens}
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS_DIR / f"{name}_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"reporte: {out}")


if __name__ == "__main__":
    main()
