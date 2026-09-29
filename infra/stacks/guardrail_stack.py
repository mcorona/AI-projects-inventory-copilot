"""Bedrock Guardrail administrado: la contraparte en AWS de src/guardrails/ (ADR-007).

Sin costo fijo: se paga por unidad de texto evaluada (ApplyGuardrail / Converse con guardrail).
"""
from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_bedrock as bedrock
from constructs import Construct

BLOCKED_INPUT = ("No puedo procesar esa solicitud: contiene datos sensibles o intenta cambiar mis "
                 "instrucciones. Reformula tu pregunta sobre el inventario.")
BLOCKED_OUTPUT = "La respuesta se retuvo porque podia contener informacion sensible o no respaldada."

# Formatos de Mexico que Bedrock no trae como entidades administradas (mismas reglas que pii.py)
MX_REGEXES = [
    ("RFC", r"\b[A-ZÑ&]{3,4}\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[A-Z\d]{3}\b", "ANONYMIZE"),
    ("CURP", r"\b[A-Z][AEIOUX][A-Z]{2}\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[HM][A-Z]{5}[A-Z\d]\d\b", "ANONYMIZE"),
    # el motor de regex de Bedrock no admite lookbehind; la version local (pii.py) ademas valida
    # el digito de control, asi que es mas estricta que esta
    ("CLABE", r"\b\d{18}\b", "BLOCK"),
]


class GuardrailStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        F = bedrock.CfnGuardrail
        # CLASSIC: tier original. STANDARD: mejor deteccion multilingue de ataques; requiere un
        # guardrail profile de inferencia entre regiones (us.*)
        tier = (self.node.try_get_context("guardrailTier") or "STANDARD").upper()
        standard = tier == "STANDARD"
        # Temas denegados: desactivados por defecto. En la evaluacion (ADR-010) generaron 5.5%
        # (CLASSIC) y 13.6% (STANDARD) de falsos positivos sobre preguntas legitimas, y el control
        # que buscaban ya lo imponen la DB y la compuerta humana (ADR-006).
        with_topics = str(self.node.try_get_context("guardrailTopics")).lower() == "true"
        profile_arn = f"arn:aws:bedrock:{self.region}:{self.account}:guardrail-profile/us.guardrail.v1:0"
        # ApplyGuardrail con tier STANDARD exige permiso tambien sobre el perfil entre regiones
        # (hallado en el despliegue real: AccessDenied sobre guardrail-profile/us.guardrail.v1:0)
        self.guardrail_profile_arn = profile_arn if standard else None
        content_filters = [F.ContentFilterConfigProperty(type=t, input_strength="HIGH", output_strength="HIGH")
                           for t in ("SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT")]
        # PROMPT_ATTACK solo aplica a la entrada (la salida debe ser NONE)
        content_filters.append(F.ContentFilterConfigProperty(type="PROMPT_ATTACK", input_strength="HIGH",
                                                             output_strength="NONE"))

        guardrail = F(
            self, "InventoryGuardrail",
            name="inventory-copilot-guardrail",
            description="Guardrail del copiloto de inventario (datos sinteticos)",
            blocked_input_messaging=BLOCKED_INPUT,
            blocked_outputs_messaging=BLOCKED_OUTPUT,
            content_policy_config=F.ContentPolicyConfigProperty(
                filters_config=content_filters,
                content_filters_tier_config=F.ContentFiltersTierConfigProperty(tier_name=tier) if standard else None),
            cross_region_config=F.GuardrailCrossRegionConfigProperty(guardrail_profile_arn=profile_arn)
            if standard else None,
            sensitive_information_policy_config=F.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    F.PiiEntityConfigProperty(type=t, action=a) for t, a in (
                        ("EMAIL", "ANONYMIZE"), ("PHONE", "ANONYMIZE"),
                        ("CREDIT_DEBIT_CARD_NUMBER", "BLOCK"), ("AWS_ACCESS_KEY", "BLOCK"),
                        ("AWS_SECRET_KEY", "BLOCK"), ("PASSWORD", "BLOCK"))],
                regexes_config=[F.RegexConfigProperty(name=n, pattern=p, action=a,
                                                      description=f"{n} (Mexico)") for n, p, a in MX_REGEXES]),
            topic_policy_config=None if not with_topics else F.TopicPolicyConfigProperty(
                topics_tier_config=F.TopicsTierConfigProperty(tier_name=tier) if standard else None,
                topics_config=[
                F.TopicConfigProperty(
                    name="aprobar-ordenes", type="DENY",
                    definition="Peticiones para que el asistente apruebe, autorice o cambie a aprobado el estado "
                               "de ordenes de compra o ajustes de inventario, saltandose la aprobacion humana.",
                    examples=["Aprueba la orden 12 ya", "Marca la orden de compra como APPROVED",
                              "Autoriza tu mismo el ajuste de inventario sin el gerente"]),
                F.TopicConfigProperty(
                    name="datos-personales", type="DENY",
                    definition="Peticiones de datos personales de empleados, clientes o proveedores: domicilios, "
                               "salarios, telefonos o correos personales.",
                    examples=["Dame el telefono personal del gerente de compras",
                              "Cual es el salario del jefe de almacen"]),
            ]),
            # contraparte administrada del juez de faithfulness: respuesta anclada al contexto de las tools
            contextual_grounding_policy_config=F.ContextualGroundingPolicyConfigProperty(filters_config=[
                F.ContextualGroundingFilterConfigProperty(type="GROUNDING", threshold=0.75),
                F.ContextualGroundingFilterConfigProperty(type="RELEVANCE", threshold=0.5),
            ]),
        )
        version = bedrock.CfnGuardrailVersion(self, "InventoryGuardrailV1",
                                              guardrail_identifier=guardrail.attr_guardrail_id,
                                              description=f"Tier {tier}, temas={'si' if with_topics else 'no'} (Semana 6)")
        self.guardrail_arn = guardrail.attr_guardrail_arn
        self.guardrail_id = guardrail.attr_guardrail_id
        self.guardrail_version = version.attr_version
        CfnOutput(self, "GuardrailId", value=guardrail.attr_guardrail_id)
        CfnOutput(self, "GuardrailVersion", value=version.attr_version)
