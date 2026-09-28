"""Pruebas de la infraestructura: propiedades de seguridad + cero hallazgos de cdk-nag.

    cd infra && ../.venv/bin/python -m pytest -q tests
"""
import json
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template
from cdk_nag import AwsSolutionsChecks

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stacks.app_stack import AppStack  # noqa: E402
from stacks.data_stack import DataStack  # noqa: E402
from stacks.guardrail_stack import GuardrailStack  # noqa: E402
from stacks.nag import apply_suppressions  # noqa: E402


@pytest.fixture(scope="module")
def stacks():
    app = cdk.App(context={"@aws-cdk/core:defaultCrossStackReferences": "strong"})
    env = cdk.Environment(account="123456789012", region="us-east-1")
    g = GuardrailStack(app, "G", env=env)
    d = DataStack(app, "D", env=env)
    a = AppStack(app, "A", env=env, data=d, guardrail_arn=g.guardrail_arn, guardrail_id=g.guardrail_id,
                 guardrail_version=g.guardrail_version)
    apply_suppressions(d, a)
    cdk.Aspects.of(app).add(AwsSolutionsChecks())
    app.synth()
    return g, d, a


def test_no_unsuppressed_cdk_nag_findings(stacks):
    for stack in stacks:
        for level in ("find_error", "find_warning"):
            found = getattr(Annotations.from_stack(stack), level)("*", Match.string_like_regexp("AwsSolutions-.*"))
            assert found == [], f"{stack.stack_name}: {[f.entry.data for f in found][:3]}"


def test_guardrail_policies(stacks):
    t = Template.from_stack(stacks[0])
    props = t.find_resources("AWS::Bedrock::Guardrail")
    cfg = next(iter(props.values()))["Properties"]
    filters = {f["Type"]: f for f in cfg["ContentPolicyConfig"]["FiltersConfig"]}
    assert filters["PROMPT_ATTACK"]["InputStrength"] == "HIGH" and filters["PROMPT_ATTACK"]["OutputStrength"] == "NONE"
    pii = {e["Type"]: e["Action"] for e in cfg["SensitiveInformationPolicyConfig"]["PiiEntitiesConfig"]}
    assert pii["CREDIT_DEBIT_CARD_NUMBER"] == "BLOCK" and pii["EMAIL"] == "ANONYMIZE"
    regex = {r["Name"]: r["Action"] for r in cfg["SensitiveInformationPolicyConfig"]["RegexesConfig"]}
    assert regex == {"RFC": "ANONYMIZE", "CURP": "ANONYMIZE", "CLABE": "BLOCK"}
    grounding = {f["Type"] for f in cfg["ContextualGroundingPolicyConfig"]["FiltersConfig"]}
    assert grounding == {"GROUNDING", "RELEVANCE"}
    t.resource_count_is("AWS::Bedrock::GuardrailVersion", 1)


def test_database_is_private_encrypted_and_rotated(stacks):
    t = Template.from_stack(stacks[1])
    t.has_resource_properties("AWS::RDS::DBCluster", {
        "StorageEncrypted": True, "DeletionProtection": True, "Port": 5438,
        "EnableIAMDatabaseAuthentication": True,
        "ServerlessV2ScalingConfiguration": {"MinCapacity": 0, "MaxCapacity": 2}})
    t.resource_count_is("AWS::EC2::NatGateway", 0)
    t.resource_count_is("AWS::EC2::InternetGateway", 0)
    t.resource_count_is("AWS::SecretsManager::RotationSchedule", 5)   # admin + 4 roles


def _policy_statements(template):
    for pol in template.find_resources("AWS::IAM::Policy").values():
        yield from pol["Properties"]["PolicyDocument"]["Statement"]


def test_lambda_iam_is_least_privilege(stacks):
    t = Template.from_stack(stacks[2])
    statements = list(_policy_statements(t))
    bedrock = [s for s in statements if any(str(a).startswith("bedrock:") for a in
                                            (s["Action"] if isinstance(s["Action"], list) else [s["Action"]]))]
    assert bedrock and all(s["Resource"] != "*" for s in bedrock)          # modelos y guardrail concretos
    wildcard = [s for s in statements if s["Resource"] == "*"]
    allowed = {"ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface",
               "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses",
               "xray:PutTraceSegments", "xray:PutTelemetryRecords"}
    for s in wildcard:
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert set(actions) <= allowed, actions
    for role in t.find_resources("AWS::IAM::Role").values():
        assert not role["Properties"].get("ManagedPolicyArns"), "sin politicas administradas de AWS"


def test_api_requires_iam_auth_and_logs_access(stacks):
    t = Template.from_stack(stacks[2])
    t.has_resource_properties("AWS::ApiGatewayV2::Route", {"AuthorizationType": "AWS_IAM"})
    t.has_resource_properties("AWS::ApiGatewayV2::Stage", {"AccessLogSettings": Match.object_like({})})
    t.has_resource_properties("AWS::Lambda::Function", {"ReservedConcurrentExecutions": 5})
    env = next(iter(t.find_resources("AWS::Lambda::Function").values()))["Properties"]["Environment"]["Variables"]
    assert env["LLM_PROVIDER"] == "bedrock" and "DB_PASSWORD" not in env   # secretos solo por ARN
    assert json.loads(env["DB_ROLE_SECRETS"]) if isinstance(env["DB_ROLE_SECRETS"], str) else True


def test_chat_model_is_minimax_and_bedrock_iam_is_exact(stacks):
    t = Template.from_stack(stacks[2])
    env = next(iter(t.find_resources("AWS::Lambda::Function").values()))["Properties"]["Environment"]["Variables"]
    assert env["BEDROCK_CHAT_MODEL"] == "minimax.minimax-m2.1"   # ADR-010
    bedrock = [s for s in _policy_statements(t) if s["Action"] == "bedrock:InvokeModel"]
    assert bedrock, "falta el permiso de Bedrock"
    for s in bedrock:
        resources = sorted(json.dumps(r) for r in (s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]))
        assert len(resources) == 2 and all("foundation-model/" in r for r in resources)
        assert any("minimax.minimax-m2.1" in r for r in resources) and any("titan-embed-text-v2" in r for r in resources)
        assert not any("inference-profile" in r or "haiku" in r for r in resources)
