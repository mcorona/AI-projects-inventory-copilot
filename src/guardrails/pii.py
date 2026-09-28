"""Deteccion y anonimizacion de PII con formatos de Mexico.

Regex + validadores (Luhn, digito de control de CLABE, fecha en RFC/CURP) para no confundir
cantidades, montos o SKUs con datos personales. Acciones con la terminologia de Bedrock
Guardrails (sensitive information filters): ANONYMIZE reemplaza por [TIPO_n]; BLOCK rechaza.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ANONYMIZE, BLOCK = "ANONYMIZE", "BLOCK"

# Datos financieros: no se procesan en absoluto. Contacto e identificacion: se enmascaran.
DEFAULT_INPUT_ACTIONS = {"EMAIL": ANONYMIZE, "PHONE": ANONYMIZE, "RFC": ANONYMIZE,
                         "CURP": ANONYMIZE, "CARD": BLOCK, "CLABE": BLOCK}
DEFAULT_OUTPUT_ACTIONS = {k: ANONYMIZE for k in DEFAULT_INPUT_ACTIONS}

_DATE6 = r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
PATTERNS: list[tuple[str, re.Pattern]] = [   # orden = prioridad ante traslapes
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("CURP", re.compile(rf"\b[A-Z][AEIOUX][A-Z]{{2}}{_DATE6}[HM][A-Z]{{5}}[A-Z\d]\d\b", re.IGNORECASE)),
    ("RFC", re.compile(rf"\b[A-ZÑ&]{{3,4}}{_DATE6}[A-Z\d]{{3}}\b", re.IGNORECASE)),
    ("CLABE", re.compile(r"(?<![\d-])\d{18}(?![\d-])")),
    ("CARD", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")),
    ("PHONE", re.compile(r"(?<![\w-])(?:\+?52[ .-]?)?(?:\(?\d{2,3}\)?[ .-]?)\d{3,4}[ .-]?\d{4}(?![\w-])")),
]


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def clabe_ok(digits: str) -> bool:
    weights = [3, 7, 1] * 6
    s = sum((int(d) * w) % 10 for d, w in zip(digits[:17], weights))
    return (10 - s % 10) % 10 == int(digits[17])


def _valid(kind: str, raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if kind == "CARD":
        return 13 <= len(digits) <= 19 and luhn_ok(digits)
    if kind == "CLABE":
        return clabe_ok(digits)
    if kind == "PHONE":
        return len(digits) in (10, 12)  # 10 digitos, o 52 + 10
    return True


@dataclass
class PIIMatch:
    kind: str
    start: int
    end: int
    value: str


@dataclass
class PIIResult:
    text: str
    matches: list[PIIMatch] = field(default_factory=list)
    blocked_kinds: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_kinds)


def find_pii(text: str) -> list[PIIMatch]:
    taken: list[tuple[int, int]] = []
    found: list[PIIMatch] = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken) or not _valid(kind, m.group()):
                continue
            taken.append((m.start(), m.end()))
            found.append(PIIMatch(kind, m.start(), m.end(), m.group()))
    return sorted(found, key=lambda x: x.start)


def apply_pii_policy(text: str, actions: dict[str, str] | None = None) -> PIIResult:
    """Anonimiza segun la politica; si algun tipo es BLOCK, lo reporta (el texto se enmascara igual)."""
    actions = actions or DEFAULT_INPUT_ACTIONS
    matches = find_pii(text)
    labels: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    out, pos = [], 0
    for m in matches:
        key = (m.kind, m.value)
        if key not in labels:
            counters[m.kind] = counters.get(m.kind, 0) + 1
            labels[key] = f"[{m.kind}_{counters[m.kind]}]"
        out.append(text[pos:m.start])
        out.append(labels[key])
        pos = m.end
    out.append(text[pos:])
    blocked = sorted({m.kind for m in matches if actions.get(m.kind) == BLOCK})
    return PIIResult("".join(out), matches, blocked)
