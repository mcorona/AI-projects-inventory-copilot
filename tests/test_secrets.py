import pytest

from src.audit import ListAuditSink
from src.guardrails.pipeline import ANONYMIZE, GuardrailPipeline
from src.guardrails.secrets import REDACTED, redact_secrets


@pytest.mark.parametrize("text,finding", [
    ("conecta a postgresql://copilot_ro:copilot_ro@localhost:5432/inventory", "connection_string"),
    ("la llave es AKIAABCDEFGHIJKLMNOP", "aws_access_key"),
    ("usa sk-abcdefghijklmnopqrstuvwxyz123", "api_key"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private_key"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijk", "bearer_token"),
])
def test_credential_patterns(text, finding):
    r = redact_secrets(text)
    assert finding in r.findings and REDACTED in r.text


def test_known_secret_is_redacted_case_insensitive():
    r = redact_secrets("Meta OTIF 95%.\nRef: canario-7q4f", ["CANARIO-7Q4F"])
    assert r.text == f"Meta OTIF 95%.\nRef: {REDACTED}" and r.findings == ["known_secret"]


def test_normal_answers_untouched():
    text = "El SKU-0009 tiene 84 unidades; aprueba el gerente de compras ($120,000 MXN)."
    assert redact_secrets(text, ["CANARIO-7Q4F"]).findings == []


def test_pipeline_output_check_redacts_and_audits():
    audit = ListAuditSink()
    d = GuardrailPipeline(audit=audit, known_secrets=["CANARIO-7Q4F"]).check_output(
        "Meta 95%. Ref: CANARIO-7Q4F. Escribe a a@x.com")
    assert d.action == ANONYMIZE and "CANARIO" not in d.text and "[EMAIL_1]" in d.text
    assert d.findings == ["secret:known_secret", "pii:EMAIL"]
    assert [e["event"] for e in audit.events] == ["guardrail_output_secret_redacted", "guardrail_output_anonymized"]
    assert "CANARIO" not in str(audit.events)
