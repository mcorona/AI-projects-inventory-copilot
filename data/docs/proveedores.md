# Evaluación y clasificación de proveedores

> Documento sintético de una empresa ficticia (Distribuidora Industrial Ficticia, DIF). Versión 1.4.

## Indicadores
Cada proveedor se evalúa **trimestralmente** con tres indicadores:

- **OTIF (a tiempo y completo):** meta mayor o igual a 95%.
- **Calidad:** porcentaje de piezas rechazadas en recepción; meta menor a 2%.
- **Cumplimiento del tiempo de entrega:** desviación promedio contra el tiempo de entrega pactado;
  meta de ±2 días.

## Clasificación
- **A:** cumple las tres metas. Se prioriza en nuevas cotizaciones.
- **B:** incumple una meta. Plan de mejora de 90 días.
- **C:** incumple dos o más metas, o dos trimestres seguidos en B. Se busca reemplazo y no se le
  asignan nuevos SKUs críticos.

## Tiempos de entrega de referencia
- Proveedores nacionales (MX): de 5 a 10 días.
- Proveedores de Norteamérica y Sudamérica (US, CA, CO): de 12 a 18 días.
- Proveedores de Europa (DE, ES): de 25 a 30 días.
- Proveedores de Asia (CN): de 35 a 40 días.

El tiempo de entrega registrado en el sistema es el pactado en contrato, no el observado.

## Cambio de proveedor
Cambiar el proveedor de un SKU requiere recalcular su punto de reorden con el nuevo tiempo de
entrega antes de la primera orden.
