import base64
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.agent import Agent
from src.api.app import Services, app, get_services, sign_action, verify_action
from src.audit import ListAuditSink
from src.tools.registry import Tool
from tests.fakes import ScriptedLLM, call

PREVIEW = {"ok": True, "errors": [], "sku": "SKU-0009", "name": "Perno", "qty": 1500, "amount": 11655.0,
           "required_level": "comprador", "total_on_hand": 84, "reorder_point": 1036, "days_of_demand": 67.3,
           "reason": "r"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SIGNING_KEY", "k" * 64)
    created, audit = [], ListAuditSink()

    def propose(args):
        created.append(args)
        return {"po_id": 7, "status": "PENDING_APPROVAL", "message": "Orden #7 creada en PENDING_APPROVAL"}

    tools = {"propose_purchase_order": Tool("propose_purchase_order", "d", {}, propose,
                                            requires_confirmation=True, preview=lambda a: dict(PREVIEW))}
    llm = ScriptedLLM([("", [call("propose_purchase_order", sku="SKU-0009", qty=1500, reason="r",
                                  _confirmed_by="director")])])
    svc = Services(agent=Agent(llm, tools, audit=audit), tools=tools, audit=audit,
                   list_orders=lambda status: [{"po_id": 7, "status": "PENDING_APPROVAL"}])
    app.dependency_overrides[get_services] = lambda: svc
    yield TestClient(app), created, audit
    app.dependency_overrides.clear()


def test_tokens_detect_tampering_expiry_and_other_users(monkeypatch):
    monkeypatch.setenv("SIGNING_KEY", "k" * 64)
    tok = sign_action("propose_purchase_order", {"sku": "SKU-0009", "qty": 10}, "ana", now=1000)
    assert verify_action(tok, "ana", now=1001)["args"]["qty"] == 10
    body, sig = tok.rsplit(".", 1)
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    data["args"]["qty"] = 999_999
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=") + "." + sig
    for token, user, now, code in [(forged, "ana", 1001, 403), (tok, "luis", 1001, 403),
                                   (tok, "ana", 1000 + 601, 410), ("basura", "ana", 1001, 400)]:
        with pytest.raises(HTTPException) as e:
            verify_action(token, user, now=now)
        assert e.value.status_code == code


def test_ask_returns_signed_pending_action_without_internal_args(env):
    client, created, _ = env
    r = client.post("/ask", json={"question": "propón", "user": "ana"}).json()
    assert r["stop_reason"] == "confirmation_required" and created == []
    assert r["pending"]["preview"]["amount"] == 11655.0
    action = verify_action(r["pending"]["token"], "ana")
    assert action["args"] == {"sku": "SKU-0009", "qty": 1500, "reason": "r"}   # sin _confirmed_by del modelo


def test_confirm_creates_order_as_the_confirming_user(env):
    client, created, audit = env
    token = client.post("/ask", json={"question": "propón", "user": "ana"}).json()["pending"]["token"]
    assert client.post("/confirm", json={"token": token, "approve": True, "user": "luis"}).status_code == 403
    r = client.post("/confirm", json={"token": token, "approve": True, "user": "ana"})
    assert r.status_code == 200 and r.json()["status"] == "created"
    assert created[0]["_confirmed_by"] == "ana" and created[0]["_requested_by"] == "copilot:ana"
    assert [e["event"] for e in audit.events][-1] == "confirmation_approved"


def test_reject_creates_nothing(env):
    client, created, audit = env
    token = client.post("/ask", json={"question": "propón", "user": "ana"}).json()["pending"]["token"]
    r = client.post("/confirm", json={"token": token, "approve": False, "user": "ana"}).json()
    assert r["status"] == "rejected" and created == [] and audit.events[-1]["event"] == "confirmation_rejected"


def test_health_index_orders_and_validation(env):
    client, _, _ = env
    assert client.get("/health").json() == {"status": "ok"}
    assert "Inventory Copilot" in client.get("/").text
    assert client.get("/orders").json()["orders"][0]["po_id"] == 7
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_caller_prefers_iam_identity_from_api_gateway():
    from src.api.app import caller

    class Req:
        scope = {"aws.event": {"requestContext": {"authorizer": {"iam": {"userArn": "arn:aws:iam::1:user/ana"}}}}}
    assert caller(Req(), "impostor") == "arn:aws:iam::1:user/ana"
    Req.scope = {}
    assert caller(Req(), "ana") == "ana" and caller(Req(), None) == "demo"


def test_runtime_env_builds_dsns_from_secrets(monkeypatch):
    from src.api.aws_runtime import load_runtime_env
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5438")
    monkeypatch.setenv("DB_NAME", "inventory")
    monkeypatch.setenv("DB_ROLE_SECRETS", json.dumps({"copilot_ro": "arn:ro", "copilot_po": "arn:po"}))
    monkeypatch.setenv("SIGNING_KEY_SECRET_ARN", "arn:key")
    secrets = {"arn:ro": {"username": "copilot_ro", "password": "p@ss/1"},
               "arn:po": {"username": "copilot_po", "password": "x"}, "arn:key": "abc"}

    class SM:
        def get_secret_value(self, SecretId):
            v = secrets[SecretId]
            return {"SecretString": v if isinstance(v, str) else json.dumps(v)}

    load_runtime_env(client=SM())
    import os
    assert os.environ["PG_DSN"] == "postgresql://copilot_ro:p%40ss%2F1@db.internal:5438/inventory?sslmode=require"
    assert os.environ["PO_DSN"].startswith("postgresql://copilot_po:x@") and os.environ["SIGNING_KEY"] == "abc"


def test_bootstrap_does_not_read_signing_key(monkeypatch):
    """Regresion encontrada en el despliegue real: el rol del bootstrap no puede leer la llave HMAC."""
    from src.api.aws_runtime import load_runtime_env
    for k, v in {"DB_HOST": "h", "DB_PORT": "5438", "DB_NAME": "inventory",
                 "DB_ROLE_SECRETS": json.dumps({"copilot_ro": "arn:ro"}),
                 "DB_ADMIN_SECRET_ARN": "arn:admin", "SIGNING_KEY_SECRET_ARN": "arn:key"}.items():
        monkeypatch.setenv(k, v)
    read = []

    class SM:
        def get_secret_value(self, SecretId):
            read.append(SecretId)
            return {"SecretString": json.dumps({"username": "u", "password": "p"})}

    load_runtime_env(client=SM(), admin=True, signing_key=False)
    assert "arn:key" not in read and set(read) == {"arn:ro", "arn:admin"}
