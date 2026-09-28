"""Pruebas de integracion contra PostgreSQL real (roles, triggers, pgvector).

Se saltan salvo que exista INTEGRATION_ADMIN_DSN (superusuario de una DB con el esquema,
las migraciones y los datos sinteticos cargados). En CI se levanta un contenedor pgvector.
Nunca apuntes esto a una DB con datos que te importen: crea y borra ordenes de compra.
"""
import os
from urllib.parse import urlparse, urlunparse

import pytest

ADMIN_DSN = os.getenv("INTEGRATION_ADMIN_DSN")


def role_dsn(role: str) -> str:
    """Mismo host/puerto/DB que el admin, con las credenciales locales de cada rol."""
    u = urlparse(ADMIN_DSN)
    return urlunparse(u._replace(netloc=f"{role}:{role}@{u.hostname}:{u.port or 5432}"))


def pytest_collection_modifyitems(config, items):
    if ADMIN_DSN:
        return
    skip = pytest.mark.skip(reason="define INTEGRATION_ADMIN_DSN para correr pruebas de integracion")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture
def admin():
    import psycopg
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        yield conn


@pytest.fixture
def clean_orders(admin):
    admin.execute("DELETE FROM purchase_orders; DELETE FROM audit_log")
    yield
    admin.execute("DELETE FROM purchase_orders; DELETE FROM audit_log")


@pytest.fixture
def role_env(monkeypatch):
    monkeypatch.setenv("PG_DSN", role_dsn("copilot_ro"))
    monkeypatch.setenv("PO_DSN", role_dsn("copilot_po"))
    monkeypatch.setenv("APPROVER_DSN", role_dsn("copilot_approver"))
    monkeypatch.setenv("AUDIT_DSN", role_dsn("copilot_audit"))
