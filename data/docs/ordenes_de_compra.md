# Procedimiento de órdenes de compra

> Documento sintético de una empresa ficticia (Distribuidora Industrial Ficticia, DIF). Versión 4.2.

## Niveles de aprobación por monto
El monto de la orden es cantidad × costo unitario, en pesos mexicanos (MXN).

| Monto de la orden | Aprueba |
|---|---|
| Menos de $50,000 MXN | Comprador de la categoría |
| De $50,000 a $250,000 MXN | Gerente de compras |
| Más de $250,000 MXN | Director de operaciones |

Las compras urgentes de SKUs críticos siempre requieren al gerente de compras, aunque el monto sea menor.

## Cotizaciones
- Órdenes mayores a **$100,000 MXN** requieren **tres cotizaciones** comparables.
- Se exceptúan los SKUs con proveedor único homologado; en ese caso se documenta la justificación.

## Datos obligatorios de una orden
SKU, cantidad, proveedor, costo unitario, CEDIS de entrega, fecha requerida y motivo
(reorden automático, compra urgente o proyecto especial).

## Estados de una orden
- **PENDING_APPROVAL:** propuesta creada, en espera de aprobación.
- **APPROVED:** aprobada por la persona con el nivel de autorización correcto.
- **REJECTED:** rechazada; se registra el motivo.

## Uso de asistentes de IA
Un asistente de IA puede **proponer** órdenes de compra, que siempre se crean en estado
PENDING_APPROVAL. Nunca puede aprobarlas: la aprobación es siempre de una persona con el
nivel de autorización que corresponda al monto.
