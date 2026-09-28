# ADR-006: Órdenes de compra con human-in-the-loop en dos compuertas y mínimo privilegio

**Estado:** aceptada · **Fecha:** 2026-09-28

## Contexto
El agente debe poder proponer órdenes de compra (OC), la primera acción con efectos. La política
(`ordenes_de_compra.md`) exige aprobación humana según el monto: comprador por debajo de $50,000,
gerente de $50,000 a $250,000 y director por encima. Un modelo puede equivocarse o ser manipulado
por prompt injection, así que el control no puede depender de que el modelo "se porte bien".

## Decisión
**Compuerta 1: confirmación del usuario en el agente.** Es el patrón `requireConfirmation` de
Bedrock Agents.
- Las tools con `requires_confirmation=True` pausan el loop (`stop_reason="confirmation_required"`)
  y muestran una vista previa validada: monto, nivel de aprobación, cobertura y proveedor.
- La acción solo se ejecuta con `agent.resume(approve=True)`. Si la persona la rechaza, el rechazo
  vuelve al modelo como dato.
- Si la vista previa es inválida (cantidad fuera de tope, SKU inexistente, orden abierta
  duplicada), los errores vuelven al modelo sin pausar.
- Al ejecutarse, la tool revalida la propuesta, porque el estado pudo cambiar durante la confirmación.

**Compuerta 2: aprobación por una persona con autoridad** (`scripts/po_review.py`, rol
`copilot_approver`).

**La DB es la fuente de verdad** (`db/init/02_hitl_guardrails.sql`):
- `copilot_po` (el agente) solo tiene `INSERT` sobre las columnas de la propuesta. No puede
  escribir `status`, monto ni nivel, y no puede actualizar ni borrar.
- Un trigger `SECURITY DEFINER` calcula el costo, el monto y el nivel requerido desde `products` y
  fuerza `PENDING_APPROVAL`. Un modelo manipulado no puede declarar un monto menor para bajar el
  nivel de aprobación.
- `copilot_approver` solo puede actualizar las columnas de decisión. Un trigger impide aprobar sin
  el nivel suficiente, modificar la propuesta al decidir o re-decidir una orden cerrada.
- Un índice único parcial permite como máximo una orden pendiente por SKU.
- `audit_log` es append-only: `copilot_audit` solo tiene `INSERT`. Registra las solicitudes de
  confirmación, las confirmaciones y rechazos, las OC creadas, las decisiones y los intentos
  denegados.

**Los argumentos internos no los controla el modelo.** El loop elimina los argumentos con prefijo
`_` que venga del LLM y solo él inyecta `_confirmed_by` / `_requested_by` después de la confirmación.

**El MCP server no expone acciones:** un cliente MCP arbitrario no garantiza la confirmación
humana, así que por MCP solo están las tools de lectura.

## Verificación
Se probó contra PostgreSQL real con cada rol. Todas estas operaciones son rechazadas por la DB:
- insertar con `status`;
- aprobar como `copilot_po`;
- borrar;
- aprobar una OC de nivel gerente como comprador y una de nivel director como gerente;
- cambiar la cantidad al decidir;
- re-decidir una orden aprobada;
- insertar un SKU inexistente o una cantidad negativa;
- leer o borrar la bitácora como `copilot_audit`.

## Consecuencias
- (+) La seguridad de la acción no depende del modelo ni de los guardrails de texto: una inyección
  exitosa como mucho produce una propuesta que una persona ve y puede rechazar dos veces.
- (+) Los conceptos se mapean a AWS: confirmación de action groups (Bedrock Agents), roles IAM de
  mínimo privilegio y una bitácora inmutable (CloudTrail / tablas append-only).
- (−) `decided_by` es texto libre: el rol de DB es compartido y no autentica a la persona. En
  producción, la identidad vendría del IdP (Cognito / IAM Identity Center) y cada aprobador tendría
  credenciales propias.
- (−) Los umbrales viven en la DB y se replican en Python para la vista previa. Una prueba verifica
  que coincidan.
- (−) No existe el estado `RECEIVED`: una OC aprobada bloquea nuevas propuestas del mismo SKU
  hasta que se modele la recepción.
- (−) Cualquier nivel puede rechazar. Es una decisión deliberada: detener una compra no requiere
  autoridad de gasto.
