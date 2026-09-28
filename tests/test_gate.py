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
