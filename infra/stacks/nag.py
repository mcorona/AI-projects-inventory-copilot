"""Excepciones de cdk-nag, cada una con su justificacion (se revisan en cada synth)."""
from cdk_nag import NagSuppressions


WILDCARD_EVIDENCE = (
    "Acciones sin permisos a nivel de recurso segun la Service Authorization Reference: "
    "ec2:*NetworkInterface* (ENIs de Lambda en VPC) y xray:PutTraceSegments/PutTelemetryRecords. "
    "El resto de permisos (Bedrock, ApplyGuardrail, secretos, logs) esta acotado a ARNs concretos.")


def apply_suppressions(data, api) -> None:
    if data.engine == "rds":
        NagSuppressions.add_resource_suppressions(data.db, [{
            "id": "AwsSolutions-RDS3",
            "reason": "Modo dbEngine=rds para el plan gratuito de AWS: solo admite una AZ (sin Multi-AZ). "
                      "La arquitectura objetivo (Aurora) no usa este modo.",
        }], apply_to_children=True)
    if data.allow_destroy:
        # solo en despliegues temporales de prueba (-c allowDestroy=true): se destruyen el mismo dia
        NagSuppressions.add_resource_suppressions(data.db, [{
            "id": "AwsSolutions-RDS10",
            "reason": "Despliegue temporal de prueba (-c allowDestroy=true) que se destruye el mismo dia; "
                      "sin proteccion contra borrado para no dejar snapshots. El modo normal la exige.",
        }], apply_to_children=True)
    for name in ("ApiFnRole", "BootstrapFnRole"):
        NagSuppressions.add_resource_suppressions(
            api.node.find_child(name), [{"id": "AwsSolutions-IAM5", "reason": WILDCARD_EVIDENCE,
                                         "applies_to": ["Resource::*"]}], apply_to_children=True)
    NagSuppressions.add_resource_suppressions(api.node.find_child("SigningKey"), [{
        "id": "AwsSolutions-SMG4",
        "reason": "Llave HMAC para firmar acciones pendientes de confirmacion (vida de minutos). Rotarla "
                  "invalidaria confirmaciones en curso; se rota manualmente en cada despliegue.",
    }])
