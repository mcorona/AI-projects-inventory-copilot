"""Adaptador a Amazon Bedrock Guardrails (ApplyGuardrail) con la misma interfaz que los locales.

Permite evaluar entrada/salida con un guardrail administrado (filtros de contenido, ataques
de prompt, PII, temas denegados) sin invocar un modelo. El guardrail se crea con CDK en la
Semana 6; hasta entonces este adaptador se prueba con mocks.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class BedrockGuardrailResult:
    intervened: bool
    text: str                      # texto de salida (enmascarado si hubo ANONYMIZE)
    findings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


class BedrockGuardrail:
    def __init__(self, guardrail_id: str, version: str = "DRAFT", region: str | None = None, client=None):
        if client is None:
            import boto3
            client = boto3.client("bedrock-runtime", region_name=region or os.getenv("AWS_REGION", "us-east-1"))
        self.client, self.guardrail_id, self.version = client, guardrail_id, version

    @classmethod
    def from_env(cls) -> "BedrockGuardrail | None":
        gid = os.getenv("BEDROCK_GUARDRAIL_ID")
        return cls(gid, os.getenv("BEDROCK_GUARDRAIL_VERSION", "DRAFT")) if gid else None

    def apply(self, text: str, source: str = "INPUT") -> BedrockGuardrailResult:
        r = self.client.apply_guardrail(guardrailIdentifier=self.guardrail_id, guardrailVersion=self.version,
                                        source=source, content=[{"text": {"text": text}}])
        intervened = r.get("action") == "GUARDRAIL_INTERVENED"
        outputs = r.get("outputs") or []
        out_text = outputs[0].get("text", text) if outputs else text
        return BedrockGuardrailResult(intervened, out_text, _findings(r.get("assessments") or []), r)


    def check_grounding(self, context: str, query: str, answer: str) -> dict:
        """Contextual grounding check: la respuesta debe estar anclada al contexto (fuente) y ser
        relevante a la pregunta. Contraparte administrada del juez de faithfulness."""
        r = self.client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id, guardrailVersion=self.version, source="OUTPUT",
            content=[{"text": {"text": context, "qualifiers": ["grounding_source"]}},
                     {"text": {"text": query, "qualifiers": ["query"]}},
                     {"text": {"text": answer, "qualifiers": ["guard_content"]}}])
        scores = {}
        for a in r.get("assessments") or []:
            for f in a.get("contextualGroundingPolicy", {}).get("filters", []):
                scores[f["type"].lower()] = {"score": f.get("score"), "threshold": f.get("threshold"),
                                             "action": f.get("action")}
        return {"intervened": r.get("action") == "GUARDRAIL_INTERVENED", "scores": scores,
                "findings": _findings(r.get("assessments") or []), "usage": r.get("usage", {})}


def _findings(assessments: list[dict]) -> list[str]:
    """Aplana las evaluaciones de Bedrock a etiquetas legibles (p. ej. 'PROMPT_ATTACK', 'PII:EMAIL')."""
    out = []
    for a in assessments:
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("action") != "NONE":
                out.append(f.get("type", "CONTENT"))
        for t in a.get("topicPolicy", {}).get("topics", []):
            out.append(f"TOPIC:{t.get('name')}")
        for e in a.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            out.append(f"PII:{e.get('type')}")
        for w in a.get("wordPolicy", {}).get("customWords", []):
            out.append(f"WORD:{w.get('match')}")
    return out
