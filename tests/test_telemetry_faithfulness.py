import json
import math

from evals.faithfulness import LLMJudge, numbers_in, numeric_grounding
from evals.run_all import agg, percentile, to_markdown
from src.agent import Agent
from src.telemetry import TelemetrySink, estimate_cost, load_pricing, turn_record
from tests.fakes import ScriptedLLM, call
from src.tools.registry import Tool


def test_pricing_config_has_source_and_consistent_references():
    p = load_pricing()
    assert p["source"]["catalogs"] and p["source"]["publication_dates"]
    assert all(eq["price"] in p["models"] for eq in p["equivalents"].values())


def test_cost_estimates():
    c = estimate_cost("omniroute", "minimax-m2.1", 1_000_000, 1_000_000)
    assert c.actual_usd == 0.0 and math.isclose(c.bedrock_usd, 0.30 + 1.20) and c.exact_equivalent
    q = estimate_cost("lmstudio", "qwen/qwen3.6-35b-a3b", 2_000_000, 0)
    assert math.isclose(q.bedrock_usd, 0.30) and not q.exact_equivalent
    b = estimate_cost("bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0", 1_000_000, 0)
    assert math.isclose(b.actual_usd, 1.10)
    unknown = estimate_cost("bedrock", "otro-modelo", 10, 10)
    assert unknown.bedrock_usd is None and math.isnan(unknown.actual_usd)   # no se inventan precios


def test_agent_counts_tool_internal_llm_usage_and_writes_telemetry(tmp_path):
    tools = {"query_inventory": Tool("query_inventory", "d", {}, lambda a: {
        "rows": [[12]], "_usage": [{"provider": "omniroute", "model": "minimax-m2.1", "input_tokens": 400,
                                    "output_tokens": 20, "latency_ms": 50.0, "cache_hit": False}]})}
    llm = ScriptedLLM([("", [call("query_inventory", question="x")]), "Hay 12."], name="omniroute", model="minimax-m2.1")
    sink = TelemetrySink(tmp_path / "t.jsonl")
    r = Agent(llm, tools, telemetry=sink).run("¿cuantos?")
    assert [c["source"] for c in r.llm_trace] == ["agent", "tool:query_inventory", "agent"]
    assert r.input_tokens == 100 + 400 + 100
    assert "_usage" not in str(llm.calls[1]["messages"])   # el canal interno no llega al modelo
    rec = json.loads((tmp_path / "t.jsonl").read_text())
    assert rec["cost_actual_usd"] == 0.0 and rec["cost_bedrock_equiv_usd"] > 0
    b = rec["latency_breakdown_ms"]
    assert b["tools_llm_subset"] == 50.0 and set(b) == {"agent_llm", "tools", "tools_llm_subset", "guardrails", "other"}
    assert "question" not in rec   # la pregunta puede traer PII: no se registra


def test_telemetry_skips_paused_turns(tmp_path):
    tools = {"propose_purchase_order": Tool("propose_purchase_order", "d", {}, lambda a: {},
                                            requires_confirmation=True, preview=lambda a: {"ok": True})}
    sink = TelemetrySink(tmp_path / "t.jsonl")
    r = Agent(ScriptedLLM([("", [call("propose_purchase_order", sku="S", qty=1)])]), tools, telemetry=sink).run("x")
    assert r.stop_reason == "confirmation_required" and not (tmp_path / "t.jsonl").exists()


def test_numeric_grounding():
    ctx = '{"total_on_hand": 84, "reorder_point": 1036, "days_of_cover": 3.8, "as_of": "2026-09-26"}'
    assert numeric_grounding("Tiene 84 unidades; punto de reorden 1,036.", ctx).grounded
    assert numeric_grounding("Datos al 27 de septiembre", ctx).unsupported == [27.0]
    assert numeric_grounding("Pediste 120000 pesos", ctx, question="orden de 120000").grounded
    assert numeric_grounding("El SKU-0009 esta bajo", ctx).numbers == []   # los SKU no son cifras
    assert numbers_in("2,326 y 3.8") == {2326.0, 3.8}


def test_llm_judge_recomputes_verdict_from_claims():
    j = LLMJudge(ScriptedLLM(['<think>x</think>{"claims": [{"claim": "84", "verdict": "supported"}, '
                              '{"claim": "27 sep", "verdict": "unsupported"}], "faithful": true}']))
    r = j.judge("q", "ctx", "a")
    assert r.faithful is False and r.support_rate == 0.5 and j.input_tokens == 100
    assert LLMJudge(ScriptedLLM(["sin json"])).judge("q", "c", "a").faithful is None
    assert LLMJudge(ScriptedLLM(['{"claims": [], "faithful": true}'])).judge("q", "c", "a").faithful is True


def test_run_all_aggregation_and_markdown():
    assert agg([0.9, 1.0, None]) == {"mean": 0.95, "min": 0.9, "max": 1.0, "n": 2}
    assert agg([None]) == {"mean": None, "min": None, "max": None, "n": 0}
    assert percentile([1, 2, 3, 4], 50) == 2.5 and percentile([], 95) == 0.0
    a = agg([0.9, 1.0])
    summary = {"run_id": "r", "split": "test", "repeats": 2, "judge": "j", "fingerprints": {"sql": "x"},
               "providers": {"p": {"model": "m", "sql_test": {"execution_accuracy": a}}}}
    md = to_markdown(summary)
    assert "95.0% (90.0%–100.0%)" in md and "| p · `m` (2x) |" in md


def test_parse_provider_spec_and_bedrock_prices():
    from evals.run_all import parse_provider_spec
    assert parse_provider_spec("lmstudio") == ("lmstudio", "lmstudio", "qwen/qwen3.6-35b-a3b")
    assert parse_provider_spec("haiku=bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0") == (
        "haiku", "bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    c = estimate_cost("bedrock", "minimax.minimax-m2.1", 1_000_000, 0)
    assert math.isclose(c.actual_usd, 0.30) and c.exact_equivalent
    assert math.isclose(estimate_cost("bedrock", "qwen.qwen3-32b-v1:0", 0, 1_000_000).actual_usd, 0.60)


def test_calibration_reports_agreement_by_difficulty():
    from evals.faithfulness import JudgeResult
    from evals.run_faithfulness_eval import calibrate

    class StubJudge:
        def __init__(self, verdicts):
            self.verdicts = iter(verdicts)

        def judge(self, question, context, answer):
            return JudgeResult(faithful=next(self.verdicts), claims=[])

    rows = [{"id": "a", "question": "q", "context": "84", "answer": "84", "faithful": True, "note": ""},
            {"id": "b", "question": "q", "context": "84", "answer": "85", "faithful": False, "note": ""},
            {"id": "c", "question": "q", "context": "84", "answer": "84", "faithful": False, "note": "",
             "difficulty": "subtle"},
            {"id": "d", "question": "q", "context": "84", "answer": "84", "faithful": True, "note": "",
             "difficulty": "subtle"}]
    s = calibrate(StubJudge([True, False, True, False]), rows)["summary"]
    assert s["judge_accuracy"] == 0.5
    assert s["by_difficulty"]["obvious"]["judge_accuracy"] == 1.0
    sub = s["by_difficulty"]["subtle"]
    assert sub["judge_accuracy"] == 0.0 and sub["confusion"] == {"tp": 0, "fp": 1, "fn": 1, "tn": 0}
