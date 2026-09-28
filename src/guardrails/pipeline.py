"""Pipeline de guardrails en capas: entrada, salidas de tools y respuesta final.

    check_input(text)         PII (anonimizar/bloquear) -> inyeccion directa (reglas, LLM opcional,
                              Bedrock opcional)
    sanitize_tool_result(r)   inyeccion indirecta: retira strings con instrucciones de salidas de tools
    wrap_tool_output(name, s) spotlighting: delimita la salida como datos no confiables
    check_output(text)        PII en la respuesta

Cada intervencion se registra en la bitacora de auditoria.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.audit import AuditSink, NullAuditSink
from src.guardrails.injection import LLMInjectionClassifier, detect_injection
from src.guardrails.pii import DEFAULT_INPUT_ACTIONS, DEFAULT_OUTPUT_ACTIONS, apply_pii_policy

ALLOW, ANONYMIZE, BLOCK = "ALLOW", "ANONYMIZE", "BLOCK"
REMOVED = "[contenido retirado por guardrail: posible instruccion inyectada]"

BLOCK_MESSAGES = {
    "pii": "No puedo procesar esa solicitud porque contiene datos financieros sensibles ({kinds}). "
           "Elimínalos y vuelve a intentarlo.",
    "injection": "No puedo procesar esa solicitud: parece un intento de cambiar mis instrucciones o "
                 "saltarse controles. Reformula tu pregunta sobre el inventario.",
}


@dataclass
class Decision:
    action: str               # ALLOW | ANONYMIZE | BLOCK
    text: str                 # texto a usar (anonimizado si aplica)
    findings: list[str] = field(default_factory=list)
    message: str = ""         # mensaje para el usuario si BLOCK


class GuardrailPipeline:
    def __init__(self, pii_input_actions: dict | None = None, pii_output_actions: dict | None = None,
                 detect_injection_input: bool = True, sanitize_tools: bool = True, spotlight: bool = True,
                 classifier: LLMInjectionClassifier | None = None, bedrock=None,
                 audit: AuditSink | None = None, actor: str = "copilot"):
        self.pii_input_actions = pii_input_actions or DEFAULT_INPUT_ACTIONS
        self.pii_output_actions = pii_output_actions or DEFAULT_OUTPUT_ACTIONS
        self.detect_injection_input = detect_injection_input
        self.sanitize_tools = sanitize_tools
        self.spotlight = spotlight
        self.classifier = classifier
        self.bedrock = bedrock
        self.audit = audit or NullAuditSink()
        self.actor = actor

    @classmethod
    def from_env(cls, audit: AuditSink | None = None) -> "GuardrailPipeline":
        from src.guardrails.bedrock import BedrockGuardrail
        classifier = None
        if os.getenv("GUARDRAIL_LLM_CLASSIFIER", "off").lower() in ("1", "on", "true"):
            from src.llm import get_provider
            spec = os.getenv("GUARDRAIL_CLASSIFIER_PROVIDER")  # p. ej. "omniroute:kr/minimax-m2.1"
            provider, _, model = (spec or "").partition(":")
            classifier = LLMInjectionClassifier(get_provider(provider or None, model or None))
        return cls(classifier=classifier, bedrock=BedrockGuardrail.from_env(), audit=audit)

    # ------------------------------------------------------------ entrada
    def check_input(self, text: str) -> Decision:
        pii = apply_pii_policy(text, self.pii_input_actions)
        findings = [f"pii:{m.kind}" for m in pii.matches]
        if pii.blocked:
            return self._block("pii", text, findings, kinds=", ".join(pii.blocked_kinds))

        if self.detect_injection_input:
            inj = detect_injection(text)
            if not inj.flagged and self.classifier is not None:
                inj = self.classifier.classify(text)
            if inj.flagged:
                return self._block("injection", text, findings + [f"injection:{r}" for r in inj.rules])

        if self.bedrock is not None:
            br = self.bedrock.apply(pii.text, "INPUT")
            if br.intervened:
                return self._block("injection", text, findings + [f"bedrock:{f}" for f in br.findings])

        if pii.matches:
            self.audit.log(self.actor, "guardrail_input_anonymized", {"findings": findings})
            return Decision(ANONYMIZE, pii.text, findings)
        return Decision(ALLOW, text, findings)

    def _block(self, kind: str, text: str, findings: list[str], **fmt) -> Decision:
        self.audit.log(self.actor, "guardrail_input_blocked",
                       {"kind": kind, "findings": findings, "preview": apply_pii_policy(text).text[:200]})
        return Decision(BLOCK, "", findings, BLOCK_MESSAGES[kind].format(**fmt))

    # ------------------------------------------------------------ salidas de tools
    def sanitize_tool_result(self, tool: str, result):
        """Recorre la estructura y reemplaza cada string con instrucciones inyectadas."""
        if not self.sanitize_tools:
            return result, []
        findings: list[str] = []

        def walk(node, path):
            if isinstance(node, dict):
                return {k: walk(v, f"{path}.{k}") for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
            if isinstance(node, str) and len(node) > 12:
                inj = detect_injection(node)
                if inj.flagged:
                    findings.append(f"{path}:{'+'.join(inj.rules)}")
                    return REMOVED
            return node

        clean = walk(result, tool)
        if findings:
            self.audit.log(self.actor, "guardrail_tool_output_sanitized", {"tool": tool, "findings": findings})
        return clean, findings

    def wrap_tool_output(self, tool: str, text: str) -> str:
        if not self.spotlight:
            return text
        # se neutraliza cualquier etiqueta de cierre falsa dentro de los datos
        safe = text.replace("</tool_output", "<\\/tool_output")
        return f'<tool_output tool="{tool}" trust="untrusted">\n{safe}\n</tool_output>'

    # ------------------------------------------------------------ respuesta
    def check_output(self, text: str) -> Decision:
        pii = apply_pii_policy(text, self.pii_output_actions)
        if pii.matches:
            findings = [f"pii:{m.kind}" for m in pii.matches]
            self.audit.log(self.actor, "guardrail_output_anonymized", {"findings": findings})
            return Decision(ANONYMIZE, pii.text, findings)
        return Decision(ALLOW, text)


class NoGuardrails(GuardrailPipeline):
    """Linea base para evals: sin ninguna defensa."""

    def __init__(self):
        super().__init__(detect_injection_input=False, sanitize_tools=False, spotlight=False)

    def check_input(self, text):
        return Decision(ALLOW, text)

    def check_output(self, text):
        return Decision(ALLOW, text)
