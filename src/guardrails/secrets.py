"""Filtro de salida contra fuga de secretos (DLP), ultima capa ante inyeccion indirecta.

La leccion de la Semana 5: un documento envenenado logro que el modelo copiara un identificador
del prompt de sistema a la respuesta, y ninguna defensa de texto a la ENTRADA lo detuvo. La
mitigacion correcta tiene dos partes:
1. no poner secretos en el contexto del modelo (el prompt de produccion no tiene ninguno);
2. revisar la SALIDA contra secretos conocidos y patrones de credenciales, sin importar como
   llego ahi el contenido.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

REDACTED = "[dato sensible retirado]"

# Patrones de credenciales comunes (no dependen de conocer el secreto)
CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("connection_string", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\S+", re.IGNORECASE)),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.IGNORECASE)),
]


@dataclass
class SecretScanResult:
    text: str
    findings: list[str] = field(default_factory=list)


def redact_secrets(text: str, known_secrets: list[str] | None = None) -> SecretScanResult:
    """Retira secretos registrados (coincidencia exacta, sin distinguir mayusculas) y credenciales."""
    findings = []
    for secret in known_secrets or []:
        if secret and re.search(re.escape(secret), text, re.IGNORECASE):
            text = re.sub(re.escape(secret), REDACTED, text, flags=re.IGNORECASE)
            findings.append("known_secret")
    for name, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(REDACTED, text)
            findings.append(name)
    return SecretScanResult(text, findings)
