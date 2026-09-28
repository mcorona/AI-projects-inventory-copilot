"""Corrida completa de evaluaciones con repeticiones, costo y un reporte consolidado.

Uso:
    python -m evals.run_all                                   # lmstudio + omniroute, 3 repeticiones
    python -m evals.run_all --providers omniroute --repeats 2 --suites sql,agent
    python -m evals.run_all --providers "bedrock-haiku=bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0"

Por proveedor y repeticion: text-to-SQL (split test), agente (split test) + faithfulness con
juez Qwen, e inyeccion indirecta (1 repeticion). Una sola vez: recuperacion RAG, guardrails
(heuristicas) y calibracion del juez. Cada repeticion usa un sufijo de sistema distinto para
esquivar la cache de OmniRoute; los aciertos de cache se reportan (deben ser 0).

Salida: evals/results/<ts>/summary.{json,md} (+ raw/ con los reportes por corrida, no versionado)
y evals/results/latest.json, que es lo que revisa el gate de CI (evals/gate.py).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
PROVIDER_MODELS = {"lmstudio": "qwen/qwen3.6-35b-a3b", "omniroute": "kr/minimax-m2.1"}
JUDGE = "lmstudio:qwen/qwen3.6-35b-a3b"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def agg(values: list[float | None]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "min": None, "max": None, "n": 0}
    return {"mean": round(statistics.fmean(vals), 4), "min": round(min(vals), 4),
            "max": round(max(vals), 4), "n": len(vals)}


def parse_provider_spec(spec: str) -> tuple[str, str, str]:
    """'lmstudio' | 'etiqueta=proveedor:modelo' -> (etiqueta, proveedor, modelo)."""
    label, _, target = spec.partition("=") if "=" in spec else (spec, "", spec)
    provider, _, model = target.partition(":")
    return label.strip(), provider.strip(), (model.strip() or PROVIDER_MODELS.get(provider.strip(), ""))


def cost_of_calls(calls: list[dict]) -> tuple[float, float | None]:
    from src.telemetry import estimate_cost
    actual, bedrock, known = 0.0, 0.0, True
    for c in calls:
        e = estimate_cost(c["provider"], c["model"], c["input_tokens"], c["output_tokens"])
        actual += e.actual_usd
        if e.bedrock_usd is None:
            known = False
        else:
            bedrock += e.bedrock_usd
    return actual, (bedrock if known else None)


def sql_run_metrics(report: dict) -> dict:
    items = report["items"]
    calls = [{"provider": i["provider"], "model": i["model"], "input_tokens": i["input_tokens"],
              "output_tokens": i["output_tokens"]} for i in items if i["model"]]
    actual, bedrock = cost_of_calls(calls)
    lat = [i["total_latency_ms"] for i in items]
    s = report["summary"]
    return {"execution_accuracy": s["execution_accuracy"], "execution_accuracy_strict": s["execution_accuracy_strict"],
            "error_rate": s["error_rate"], "latency_p50_ms": percentile(lat, 50), "latency_p95_ms": percentile(lat, 95),
            "cache_hit_rate": s.get("cache_hit_rate", 0.0),
            "bedrock_usd_per_query": None if bedrock is None else bedrock / len(items),
            "tokens_per_query": (s["input_tokens"] + s["output_tokens"]) / len(items)}


def agent_run_metrics(report: dict, faith: dict | None) -> dict:
    items = report["items"]
    calls = [c for i in items for c in i.get("llm_trace", [])]
    actual, bedrock = cost_of_calls(calls)
    lat = [i["latency_ms"] for i in items]
    s = report["summary"]
    out = {"tool_selection_accuracy": s["tool_selection_accuracy"], "answer_accuracy": s["answer_accuracy"],
           "latency_p50_ms": percentile(lat, 50), "latency_p95_ms": percentile(lat, 95),
           "cache_hit_rate": (sum(bool(c.get("cache_hit")) for c in calls) / len(calls)) if calls else 0.0,
           "bedrock_usd_per_query": None if bedrock is None else bedrock / len(items),
           "tokens_per_query": sum(c["input_tokens"] + c["output_tokens"] for c in calls) / len(items),
           "llm_calls_per_query": len(calls) / len(items)}
    if faith:
        out["faithfulness_rate"] = faith["summary"]["faithfulness_rate"]
        out["numeric_grounding_rate"] = faith["summary"]["numeric_grounding_rate"]
    return out


def main() -> None:
    from dotenv import load_dotenv

    from evals import run_agent_eval, run_guardrails_eval, run_injection_eval, run_rag_eval, run_sql_eval
    from evals.faithfulness import LLMJudge
    from evals.fingerprint import fingerprints
    from evals.run_faithfulness_eval import calibrate, judge_report
    from evals.datasets import load_dataset
    from src.agent import Agent
    from src.llm import get_embedder, get_provider
    from src.tools.sql_tool import execute_readonly

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--providers", default="lmstudio,omniroute")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--split", default="test", choices=["dev", "test"])
    p.add_argument("--suites", default="sql,agent,rag,guardrails,injection,judge")
    p.add_argument("--limit", type=int, default=None, help="solo las primeras N preguntas (prueba rapida)")
    p.add_argument("--out", default=None, help="carpeta de salida (por defecto evals/results/<ts>)")
    p.add_argument("--resume", type=Path, default=None,
                   help="reutiliza los reportes crudos existentes en esa carpeta y corre solo lo que falta")
    p.add_argument("--repeats-for", default="",
                   help="repeticiones por etiqueta, p. ej. 'bedrock-haiku=1,bedrock-qwen3=1'")
    p.add_argument("--rescore", type=Path, default=None,
                   help="recalcula metricas desde <carpeta>/raw sin volver a llamar modelos "
                        "(para corregir la forma de calificar, nunca las respuestas)")
    args = p.parse_args()
    if args.rescore:
        rescore(args.rescore)
        return
    suites = set(args.suites.split(","))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else RESULTS_DIR / run_id
    if args.resume:
        out_dir, run_id = args.resume, args.resume.name
    repeats_for = {k: int(v) for k, v in (x.split("=") for x in args.repeats_for.split(",") if x)}
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dsn = os.environ["PG_DSN"]
    executor = lambda sql: execute_readonly(sql, dsn)  # noqa: E731

    def cached(name: str) -> dict | None:
        path = raw_dir / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save(name: str, report: dict) -> None:
        (raw_dir / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                              encoding="utf-8")

    judge_provider, _, judge_model = JUDGE.partition(":")
    judge = LLMJudge(get_provider(judge_provider, judge_model))
    summary: dict = {"run_id": run_id, "split": args.split, "repeats": args.repeats, "repeats_for": repeats_for,
                     "fingerprints": fingerprints(), "judge": JUDGE, "providers": {}}

    for spec in args.providers.split(","):
        name, provider_name, model = parse_provider_spec(spec)
        runs: dict[str, list[dict]] = {"sql": [], "agent": []}
        repeats = repeats_for.get(name, args.repeats)
        for rep in range(repeats):
            llm = get_provider(provider_name, model)
            llm.system_suffix = f"\n(corrida {run_id}-{rep} {uuid.uuid4().hex[:6]})"  # anti-cache
            print(f"[{name}] repeticion {rep + 1}/{repeats}", flush=True)
            if "sql" in suites:
                r = cached(f"sql_{name}_{rep}")
                if r is None:
                    r = run_sql_eval.evaluate(run_sql_eval.load_golden(args.split)[: args.limit], llm, executor)
                    save(f"sql_{name}_{rep}", r)
                runs["sql"].append(sql_run_metrics(r))
                print(f"  sql   exec_acc={r['summary']['execution_accuracy']:.1%}", flush=True)
            if "agent" in suites:
                r, faith = cached(f"agent_{name}_{rep}"), cached(f"faithfulness_{name}_{rep}")
                if r is None or faith is None:
                    r = run_agent_eval.evaluate(run_agent_eval.load_golden(args.split)[: args.limit], Agent(llm))
                    save(f"agent_{name}_{rep}", r)
                    faith = judge_report(judge, r)
                    save(f"faithfulness_{name}_{rep}", faith)
                runs["agent"].append(agent_run_metrics(r, faith))
                print(f"  agent tools={r['summary']['tool_selection_accuracy']:.1%} "
                      f"answer={r['summary']['answer_accuracy']:.1%} "
                      f"faithful={faith['summary']['faithfulness_rate']:.1%}", flush=True)
        prov = {"model": model, "provider": provider_name, "repeats": repeats}
        for suite, rows in runs.items():
            if rows:
                prov[f"{suite}_{args.split}"] = {k: agg([row[k] for row in rows]) for k in rows[0]}
        if "injection" in suites:
            llm = get_provider(provider_name, model)
            llm.system_suffix = f"\n(corrida {run_id}-inj {uuid.uuid4().hex[:6]})"
            inj = cached(f"injection_{name}")
            if inj is None:
                inj = run_injection_eval.run(llm)
                save(f"injection_{name}", inj)
            prov["injection"] = inj["summary"]
            print(f"  injection {json.dumps({k: v['attack_success_rate'] for k, v in inj['summary'].items() if isinstance(v, dict)})}", flush=True)
        summary["providers"][name] = prov

    if "rag" in suites:
        embedder = get_embedder()
        from src.rag.store import search
        r = run_rag_eval.evaluate(load_dataset("rag", args.split), lambda q, k: search(q, k=k, embedder=embedder))
        save("rag", r)
        summary[f"rag_{args.split}"] = r["summary"]
    if "guardrails" in suites:
        from scripts.ingest_docs import load_chunks
        from src.guardrails.pipeline import GuardrailPipeline
        r = run_guardrails_eval.evaluate(GuardrailPipeline(), run_guardrails_eval.load_jsonl(run_guardrails_eval.ATTACKS_PATH),
                                         run_guardrails_eval.benign_questions(), [c.content for c in load_chunks()])
        save("guardrails", r)
        summary["guardrails"] = r["summary"]
    if "judge" in suites:
        r = calibrate(judge, load_dataset("judge", "calibration"))
        save("judge_calibration", r)
        summary["judge_calibration"] = r["summary"]

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(to_markdown(summary), encoding="utf-8")
    if not args.limit and not args.out:   # solo una corrida completa alimenta al gate
        shutil.copyfile(out_dir / "summary.json", RESULTS_DIR / "latest.json")
    print(to_markdown(summary))
    print(f"resultados: {out_dir}")


def rescore(run_dir: Path) -> None:
    """Recalifica sql/agent desde los reportes crudos con el codigo de calificacion actual."""
    from evals.run_agent_eval import facts_match
    from evals.run_sql_eval import summarize as sql_summarize

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    raw = run_dir / "raw"
    split = summary["split"]
    for name, prov in summary["providers"].items():
        sql_rows, agent_rows = [], []
        for rep in range(summary["repeats"]):
            sql_path, agent_path = raw / f"sql_{name}_{rep}.json", raw / f"agent_{name}_{rep}.json"
            if sql_path.exists():
                r = json.loads(sql_path.read_text(encoding="utf-8"))
                r["summary"] = {**r["summary"], **sql_summarize(r["items"])}
                sql_rows.append(sql_run_metrics(r))
            if agent_path.exists():
                r = json.loads(agent_path.read_text(encoding="utf-8"))
                golden = {g["id"]: g for g in run_agent_eval_golden(split)}
                for it in r["items"]:
                    it["facts_ok"], it["facts_missing"] = facts_match(it["answer"], golden[it["id"]].get("expected_facts", []))
                facts = [i for i in r["items"] if i["has_facts"]]
                r["summary"]["answer_accuracy"] = round(sum(i["facts_ok"] for i in facts) / len(facts), 4) if facts else None
                agent_path.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                faith_path = raw / f"faithfulness_{name}_{rep}.json"
                faith = json.loads(faith_path.read_text(encoding="utf-8")) if faith_path.exists() else None
                agent_rows.append(agent_run_metrics(r, faith))
        for suite, rows in (("sql", sql_rows), ("agent", agent_rows)):
            if rows:
                prov[f"{suite}_{split}"] = {k: agg([row[k] for row in rows]) for k in rows[0]}
    summary["rescored_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(to_markdown(summary), encoding="utf-8")
    if (RESULTS_DIR / "latest.json").exists() and json.loads((RESULTS_DIR / "latest.json").read_text())["run_id"] == summary["run_id"]:
        shutil.copyfile(run_dir / "summary.json", RESULTS_DIR / "latest.json")
    print(to_markdown(summary))


def run_agent_eval_golden(split: str) -> list[dict]:
    from evals.datasets import load_dataset
    return load_dataset("agent", split)


def fmt(a: dict | None, pct: bool = True, digits: int = 1) -> str:
    if not a or a.get("mean") is None:
        return "—"
    f = (lambda v: f"{v * 100:.{digits}f}%") if pct else (lambda v: f"{v:,.{digits}f}")
    return f"{f(a['mean'])} ({f(a['min'])}–{f(a['max'])})" if a["n"] > 1 and a["min"] != a["max"] else f(a["mean"])


def to_markdown(s: dict) -> str:
    split = s["split"]
    lines = [f"# Resultados de evaluacion · corrida {s['run_id']}", "",
             f"Split **{split}** · {s['repeats']} repeticiones por proveedor · media (min–max) · juez: `{s['judge']}`", ""]
    if s.get("rescored_at"):
        lines += [f"> Recalificado el {s['rescored_at'][:10]} desde las respuestas guardadas (se corrigio el "
                  "evaluador, no las respuestas de los modelos).", ""]
    names = list(s["providers"])
    lines += ["| Metrica | " + " | ".join(f"{n} · `{s['providers'][n]['model']}` ({s['providers'][n].get('repeats', s['repeats'])}x)"
                                          for n in names) + " |",
              "|---|" + "---|" * len(names)]

    def row(label, suite, key, pct=True, digits=1, scale=1.0):
        cells = []
        for n in names:
            a = s["providers"][n].get(f"{suite}_{split}", {}).get(key)
            if a and a.get("mean") is not None and scale != 1.0:
                a = {k: (v * scale if isinstance(v, float) else v) for k, v in a.items()}
            cells.append(fmt(a, pct, digits))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    row("SQL: execution accuracy", "sql", "execution_accuracy")
    row("SQL: latencia p50 (s)", "sql", "latency_p50_ms", pct=False, scale=1 / 1000)
    row("SQL: costo equivalente en Bedrock por consulta (USD)", "sql", "bedrock_usd_per_query", pct=False, digits=5)
    row("Agente: seleccion de tools", "agent", "tool_selection_accuracy")
    row("Agente: exactitud de respuestas (hechos)", "agent", "answer_accuracy")
    row("Agente: faithfulness (juez)", "agent", "faithfulness_rate")
    row("Agente: cifras respaldadas (determinista)", "agent", "numeric_grounding_rate")
    row("Agente: latencia p50 (s)", "agent", "latency_p50_ms", pct=False, scale=1 / 1000)
    row("Agente: latencia p95 (s)", "agent", "latency_p95_ms", pct=False, scale=1 / 1000)
    row("Agente: tokens por consulta", "agent", "tokens_per_query", pct=False, digits=0)
    row("Agente: costo equivalente en Bedrock por consulta (USD)", "agent", "bedrock_usd_per_query", pct=False, digits=5)
    row("Aciertos de cache del gateway (debe ser 0)", "agent", "cache_hit_rate")
    if any("injection" in s["providers"][n] for n in names):
        for cfg in ("none", "prompt", "full"):
            cells = [f"{s['providers'][n]['injection'][cfg]['attack_success_rate']:.0%}"
                     if cfg in s["providers"][n].get("injection", {}) else "—" for n in names]
            lines.append(f"| Inyeccion indirecta: exito del ataque ({cfg}) | " + " | ".join(cells) + " |")
        cells = [str(sum(s["providers"][n]["injection"][c]["unrequested_po_executed"]
                         for c in ("none", "prompt", "full") if c in s["providers"][n].get("injection", {})))
                 for n in names]
        lines.append("| Inyeccion indirecta: OC no pedidas ejecutadas | " + " | ".join(cells) + " |")
    lines.append("")
    if f"rag_{split}" in s:
        r = s[f"rag_{split}"]
        lines.append(f"- **RAG** ({r['n']} preguntas, bge-m3): hit@1 {r['hit_at_1']:.1%} · MRR {r['mrr']:.3f}")
    if "guardrails" in s:
        g = s["guardrails"]
        lines.append(f"- **Guardrails (heuristicas)**: deteccion {g['detection_rate']:.1%} de {g['attacks']} ataques · "
                     f"falsos positivos {g['false_positive_rate']:.1%} de {g['benign_questions']} preguntas legitimas")
    if "judge_calibration" in s:
        j = s["judge_calibration"]
        lines.append(f"- **Calibracion del juez** ({j['n']} casos etiquetados): acuerdo {j['judge_accuracy']:.1%} · "
                     f"detecta infieles {j['unfaithful_recall']:.1%}")
    lines += ["", "Huellas: " + ", ".join(f"`{k}={v}`" for k, v in s["fingerprints"].items()), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
