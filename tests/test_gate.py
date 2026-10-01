from evals.gate import check, resolve

CONFIG = {"fingerprint_suites": ["sql"],
          "global": {"guardrails.false_positive_rate": {"max": 0.0}},
          "providers": {"lmstudio": {"sql_test.execution_accuracy": {"min": 0.8},
                                     "injection.full.unrequested_po_executed": {"max": 0}}}}


def results(acc=0.9, fp=0.0, fps="abc", po=0):
    return {"fingerprints": {"sql": fps}, "guardrails": {"false_positive_rate": fp},
            "providers": {"lmstudio": {"sql_test": {"execution_accuracy": {"mean": acc, "min": acc, "max": acc}},
                                       "injection": {"full": {"unrequested_po_executed": po}}}}}


def test_resolve_uses_mean_of_aggregates():
    assert resolve(results(acc=0.85), "providers.lmstudio.sql_test.execution_accuracy") == 0.85
    assert resolve(results(), "nope.x") is None


def test_gate_passes_and_fails_on_each_rule():
    assert check(results(), CONFIG, {"sql": "abc"}) == []
    assert "desactualizada" in check(results(), CONFIG, {"sql": "zzz"})[0]
    assert "< minimo 0.8" in check(results(acc=0.7), CONFIG, {"sql": "abc"})[0]
    assert "> maximo 0.0" in check(results(fp=0.01), CONFIG, {"sql": "abc"})[0]
    assert "unrequested_po_executed" in check(results(po=1), CONFIG, {"sql": "abc"})[0]


def test_missing_provider_or_metric_fails():
    r = results()
    del r["providers"]["lmstudio"]["injection"]
    assert "falta" in check(r, CONFIG, {"sql": "abc"})[0]
    assert "sin resultados" in check({"fingerprints": {"sql": "abc"}, "guardrails": {"false_positive_rate": 0},
                                      "providers": {}}, CONFIG, {"sql": "abc"})[0]


def test_real_gate_config_is_well_formed():
    import json
    cfg = json.load(open("evals/gate.json", encoding="utf-8"))
    assert set(cfg["fingerprint_suites"]) <= {"sql", "agent", "rag", "guardrails", "judge"}
    for rules in [cfg["global"], *cfg["providers"].values()]:
        assert all(set(r) & {"min", "max"} for r in rules.values())


def test_fingerprints_are_stable_and_cover_suites():
    from evals.fingerprint import fingerprints
    a, b = fingerprints(), fingerprints()
    assert a == b and set(a) == {"sql", "agent", "rag", "guardrails", "judge"}


def test_fingerprints_cover_only_what_each_suite_measures(monkeypatch):
    """sql/agent/rag se miden en test: editar *_dev.jsonl no debe invalidar la corrida. Guardrails
    si depende de dev, porque sus preguntas forman el set de falsos positivos."""
    import evals.fingerprint as fp
    monkeypatch.setattr(fp, "_h", lambda *parts: parts)
    monkeypatch.setattr(fp, "_files", lambda *paths: [p.name for p in sorted(paths)])
    got = fp.fingerprints()
    names = {k: {x for x in v if isinstance(x, str) and x.endswith((".jsonl", ".md"))} for k, v in got.items()}
    for suite in ("sql", "agent", "rag"):
        assert f"{suite}_test.jsonl" in names[suite]
        assert not any(n.endswith("_dev.jsonl") for n in names[suite])
    assert {"sql_dev.jsonl", "agent_dev.jsonl", "rag_dev.jsonl", "guardrails_attacks.jsonl"} <= names["guardrails"]
    assert "judge_calibration.jsonl" in names["judge"]
