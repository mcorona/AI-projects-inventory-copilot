"""Bitacora de auditoria append-only (tabla audit_log, rol copilot_audit con solo INSERT)."""
from __future__ import annotations

import json
import os
from typing import Protocol


class AuditSink(Protocol):
    def log(self, actor: str, event: str, details: dict | None = None) -> None: ...


class NullAuditSink:
    def log(self, actor, event, details=None):
        pass


class ListAuditSink:
    """Para pruebas y evals: guarda los eventos en memoria."""

    def __init__(self):
        self.events: list[dict] = []

    def log(self, actor, event, details=None):
        self.events.append({"actor": actor, "event": event, "details": details or {}})


class DbAuditSink:
    """Escribe en audit_log. Un fallo de auditoria no debe tumbar la respuesta al usuario,
    pero tampoco pasar en silencio: se reporta por stderr."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def log(self, actor, event, details=None):
        import sys

        import psycopg
        try:
            with psycopg.connect(self.dsn) as conn:
                conn.execute("INSERT INTO audit_log (actor, event, details) VALUES (%s, %s, %s)",
                             (actor, event, json.dumps(details or {}, ensure_ascii=False, default=str)))
        except Exception as e:
            print(f"[audit] no se pudo registrar {event}: {e}", file=sys.stderr)


def get_audit_sink() -> AuditSink:
    dsn = os.getenv("AUDIT_DSN")
    return DbAuditSink(dsn) if dsn else NullAuditSink()
