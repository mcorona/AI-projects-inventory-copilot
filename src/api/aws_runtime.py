"""Configuracion en Lambda: arma los DSN de cada rol de DB desde Secrets Manager (sin secretos
en variables de entorno) y la llave de firma de confirmaciones."""
from __future__ import annotations

import json
import os
from urllib.parse import quote

ROLE_ENV = {"copilot_ro": "PG_DSN", "copilot_po": "PO_DSN", "copilot_approver": "APPROVER_DSN",
            "copilot_audit": "AUDIT_DSN"}


def _secret(client, arn: str) -> dict | str:
    value = client.get_secret_value(SecretId=arn)["SecretString"]
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def dsn(user: str, password: str) -> str:
    return (f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{os.environ['DB_HOST']}:"
            f"{os.environ['DB_PORT']}/{os.environ['DB_NAME']}?sslmode=require")


def load_runtime_env(client=None, admin: bool = False, signing_key: bool = True) -> None:
    """Carga solo lo que cada funcion necesita: el bootstrap no lee la llave de firma (y su rol IAM
    no tiene permiso para hacerlo); la API no lee el secreto de administrador."""
    import boto3
    client = client or boto3.client("secretsmanager")
    for role, arn in json.loads(os.environ["DB_ROLE_SECRETS"]).items():
        s = _secret(client, arn)
        os.environ[ROLE_ENV[role]] = dsn(s["username"], s["password"])
    if admin:
        s = _secret(client, os.environ["DB_ADMIN_SECRET_ARN"])
        os.environ["PG_ADMIN_DSN"] = dsn(s["username"], s["password"])
    if signing_key and os.getenv("SIGNING_KEY_SECRET_ARN"):
        key = _secret(client, os.environ["SIGNING_KEY_SECRET_ARN"])
        os.environ["SIGNING_KEY"] = key if isinstance(key, str) else json.dumps(key)
