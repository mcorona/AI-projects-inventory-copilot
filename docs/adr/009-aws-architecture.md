# ADR-009: Arquitectura en AWS con CDK y cdk-nag

**Estado:** aceptada · **Fecha:** 2026-09-28

## Contexto
El proyecto corre a $0 en local (LM Studio, OmniRoute, Postgres en Docker). Para demostrar el
camino a producción en AWS hace falta infraestructura como código, revisada contra buenas
prácticas de seguridad, que traslade cada decisión local a su servicio administrado y cuyo
costo en reposo sea cercano a cero.

## Decisión
Tres stacks de CDK en Python (`infra/`), revisados en cada synth con cdk-nag (`AwsSolutions`):

| Local | AWS | Stack |
|---|---|---|
| `src/guardrails/` (heurísticas, PII, grounding con juez) | **Bedrock Guardrail**: filtros de contenido + `PROMPT_ATTACK`, PII y regex MX, contextual grounding | `InventoryGuardrail` |
| Postgres 16 + pgvector (Docker) | **Aurora PostgreSQL Serverless v2** 16.9 con pgvector y auto-pause a 0 ACU | `InventoryData` |
| Roles `copilot_ro/po/approver/audit` | Un secreto por rol en Secrets Manager, **rotación multiusuario** cada 30 días | `InventoryData` |
| API FastAPI + agente | **Lambda** (imagen de contenedor, ARM64) + **API Gateway HTTP** con autenticación IAM | `InventoryApp` |
| Telemetría JSONL | **CloudWatch**: métricas EMF (latencia, costo, tokens), alarmas y tablero; X-Ray | `InventoryApp` |

**Red:**
- VPC con subredes **aisladas** y **sin NAT**.
- Bedrock Runtime, Secrets Manager y CloudWatch Logs se alcanzan por **VPC endpoints**.
- Menor superficie de ataque y sin el costo fijo de un NAT Gateway.

**IAM de mínimo privilegio:**
- `bedrock:InvokeModel` solo sobre Haiku 4.5 (perfil `us.` y sus regiones) y Titan Embeddings V2.
- `bedrock:ApplyGuardrail` solo sobre el guardrail del proyecto.
- Lectura solo de los secretos que usa cada función.
- Roles propios en lugar de `AWSLambdaBasicExecutionRole` y `AWSLambdaVPCAccessExecutionRole`.
- Solo tienen `*` las acciones que AWS no permite acotar por recurso (ENIs de Lambda en VPC y
  X-Ray). Están suprimidas en cdk-nag con esa justificación.

**API sin estado:** una acción que requiere confirmación viaja como token firmado con HMAC. Así
funciona en Lambda sin sesión, y nadie puede alterar SKU ni cantidad entre la vista previa y la
confirmación. En AWS, la identidad de quien confirma sale de la firma IAM, no del cuerpo de la
petición.

**Bootstrap:** una Lambda aparte aplica las migraciones, reemplaza las contraseñas de los roles
por las de Secrets Manager, carga los datos sintéticos e indexa el corpus con Titan.

**Despliegue mínimo:**
- Solo se desplegó `InventoryGuardrail`, que no tiene costo fijo, con `LegacyStackSynthesizer`.
  Ese sintetizador no exige `cdk bootstrap`, así que no crea bucket, ECR ni roles de despliegue
  con permisos de administrador en la cuenta.
- `InventoryData` e `InventoryApp` se sintetizan y se verifican en CI, pero no se despliegan.
  Aurora, los endpoints y la Lambda generan costo mientras existen.

## Verificación
- **cdk-nag:** 0 hallazgos sin justificar en los 3 stacks. La única excepción, además de los
  comodines inevitables, es la llave HMAC, que no se rota automáticamente (se documenta por qué).
- **`infra/tests/test_infra.py`** comprueba:
  - PROMPT_ATTACK solo en la entrada;
  - PII y regex MX;
  - grounding activo;
  - DB cifrada, sin NAT ni IGW y con 5 rotaciones;
  - que ninguna política de Bedrock usa `*`;
  - rutas con `AWS_IAM`;
  - concurrencia reservada.
- **El CI falla si aparece un hallazgo:** se comprobó quitando la excepción de la llave HMAC, y el
  `synth` salió con código 1.
- **Hallazgos al desplegar:**
  - El motor de regex de Bedrock **no admite lookbehind**: la regex de CLABE se simplificó.
  - `BootstraplessSynthesizer` sigue exigiendo los roles del bootstrap; el sintetizador correcto
    para desplegar sin bootstrap es `LegacyStackSynthesizer`.

## Consecuencias
- (+) Costo en reposo cercano a cero: el guardrail no cobra por existir, y Aurora con auto-pause
  solo cobra almacenamiento.
- (+) Cada control local tiene su equivalente administrado, y se puede comparar con datos
  (ADR-010).
- (−) La rotación multiusuario crea usuarios `_clone` que alternan. Los permisos se heredan por
  membresía de rol y habría que verificarlos en un despliegue real.
- (−) `decided_by` sigue siendo texto en el flujo de aprobación. En AWS, el aprobador debería
  autenticarse con IAM o Cognito.
- (−) El despliegue completo (Data + App) no se probó en vivo: solo `synth`, cdk-nag y pruebas
  de plantilla.
