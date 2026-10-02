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
- `bedrock:InvokeModel` solo sobre MiniMax M2.1 (on-demand, región del stack) y Titan Embeddings V2.
  Al principio era Haiku 4.5 con su perfil `us.` (3 regiones); se cambió según ADR-010.
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

**Despliegue mínimo** (plan original; lo amplió el *Despliegue real* del 2026-09-28, más abajo):
- Solo se desplegó `InventoryGuardrail`, que no tiene costo fijo, con `LegacyStackSynthesizer`.
  Ese sintetizador no exige `cdk bootstrap`, así que no crea bucket, ECR ni roles de despliegue
  con permisos de administrador en la cuenta.
- `InventoryData` e `InventoryApp` se sintetizan y se verifican en CI, pero no se despliegan.
  Aurora, los endpoints y la Lambda generan costo mientras existen.
- Al cerrar el proyecto (2026-09-28) también se eliminó `InventoryGuardrail`
  (`npx cdk destroy InventoryGuardrail`): no queda ningún recurso del proyecto en la cuenta.
  Sus resultados quedaron en `evals/reports/bedrock_guardrail_eval_*.json`.

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

## Despliegue real (2026-09-28)
Los tres stacks se desplegaron en una cuenta real con `-c allowDestroy=true`, se probaron por la
API con peticiones firmadas con SigV4 y se destruyeron el mismo día.

**Plan gratuito de AWS.** Una cuenta con el plan gratuito no puede crear la arquitectura objetivo:
- Limita la retención de backups: el despliegue temporal usa 1 día; el modo normal conserva 7.
- Para Aurora exige *express configuration* (`WithExpressConfiguration`). Ese parámetro existe en
  la API de RDS pero **no en CloudFormation ni en CDK**, y fija VPC, puerto y security groups.

Por eso se agregó **`-c dbEngine=rds`**: RDS PostgreSQL 16 `db.t4g.micro` (20 GB, una AZ) con la
misma red, seguridad y rotación, compatible con el plan gratuito. Aurora sigue siendo el modo por
defecto.

**Qué se comprobó en AWS (modo RDS):**
- ✅ La API rechaza peticiones sin firma (403) y acepta las firmadas con IAM (200).
- ✅ La Lambda corre en la VPC aislada y alcanza Secrets Manager y Bedrock por los endpoints.
- ✅ El bootstrap aplicó el esquema y las migraciones, reemplazó las contraseñas de los roles por las
  de Secrets Manager y cargó los datos sintéticos.
- ✅ `/orders` lee RDS con el rol `copilot_ro` y su secreto.
- ✅ Las inyecciones se bloquean antes de llegar a cualquier modelo.
- ✅ Bedrock Guardrail evaluado desde la Lambda.
- ❌ **Chat y embeddings de Bedrock:** *"Access to Bedrock models is not allowed for this account"*.
  Es un bloqueo de la cuenta, no de la infraestructura: también ocurre desde fuera de AWS con un
  usuario administrador. El agente lo maneja y responde `stop_reason=llm_error`, sin error 500.
  Quedan sin probar en AWS la respuesta del agente, el flujo de órdenes por la API y el índice RAG
  con Titan.

**Arquitectura objetivo con Aurora (plan de pago):** se desplegó en 13 minutos y dio los mismos
resultados que el modo RDS en la API: 403 sin firma, `/health` y `/orders` en 200, inyección
bloqueada y `llm_error` limpio. Configuración verificada en AWS:
- Aurora PostgreSQL 16.9, cifrada, puerto 5438 y autenticación IAM;
- Serverless v2 con **`MinCapacity: 0` y auto-pause a los 600 s**;
- 5 secretos con rotación activa.

**Bugs que solo aparecieron en el despliegue real** (corregidos, con prueba de regresión):
1. **El bootstrap leía la llave HMAC.** La Lambda de bootstrap comparte variables de entorno con la
   API y `load_runtime_env()` leía la llave de firma. El IAM de mínimo privilegio lo **bloqueó
   correctamente**. Se corrigió en el código: el bootstrap ya no la lee. No se amplió ningún permiso.
2. **`ApplyGuardrail` necesita el perfil entre regiones.** Con el tier STANDARD, `ApplyGuardrail`
   exige permiso sobre el perfil `us.guardrail.v1:0` y sobre el guardrail **en cada región de
   destino** (us-east-1/2, us-west-1/2). El simulador de IAM aprobaba el ARN de `us-east-1`, pero
   la llamada se negaba. Ahora son 8 ARNs concretos, sin comodines.
3. **Una exportación entre stacks bloqueaba el despliegue.** Dejar de usar una exportación que
   consume otro stack desplegado hace fallar el despliegue del productor. Se conserva la
   referencia original.
4. **cdk-nag frenaba el modo temporal.** El modo temporal quita la protección contra borrado. Se
   agregó una excepción justificada solo para `allowDestroy=true`; una prueba verifica que el modo
   normal la sigue exigiendo.

## Consecuencias
- (+) Costo en reposo cercano a cero: el guardrail no cobra por existir, y Aurora con auto-pause
  solo cobra almacenamiento.
- (+) Cada control local tiene su equivalente administrado, y se puede comparar con datos
  (ADR-010).
- (−) La rotación multiusuario crea usuarios `_clone` que alternan. Los permisos se heredan por
  membresía de rol y habría que verificarlos en un despliegue real.
- (−) `decided_by` sigue siendo texto en el flujo de aprobación. En AWS, el aprobador debería
  autenticarse con IAM o Cognito.
- (−) El camino con modelos (respuesta del agente, órdenes por la API, RAG con Titan) no se ha
  probado en AWS mientras la cuenta tenga bloqueado el acceso a Bedrock. Basta un redespliegue de
  ~25 minutos para completarlo.
