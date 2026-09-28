"""Excepciones de cdk-nag, cada una con su justificacion (se revisan en cada synth)."""
from cdk_nag import NagSuppressions


WILDCARD_EVIDENCE = (
    "Acciones sin permisos a nivel de recurso segun la Service Authorization Reference: "
    "ec2:*NetworkInterface* (ENIs de Lambda en VPC) y xray:PutTraceSegments/PutTelemetryRecords. "
    "El resto de permisos (Bedrock, ApplyGuardrail, secretos, logs) esta acotado a ARNs concretos.")


def apply_suppressions(data, api) -> None:
    for name in ("ApiFnRole", "BootstrapFnRole"):
        NagSuppressions.add_resource_suppressions(
            api.node.find_child(name), [{"id": "AwsSolutions-IAM5", "reason": WILDCARD_EVIDENCE,
                                         "applies_to": ["Resource::*"]}], apply_to_children=True)
    NagSuppressions.add_resource_suppressions(api.node.find_child("SigningKey"), [{
        "id": "AwsSolutions-SMG4",
        "reason": "Llave HMAC para firmar acciones pendientes de confirmacion (vida de minutos). Rotarla "
                  "invalidaria confirmaciones en curso; se rota manualmente en cada despliegue.",
    }])
