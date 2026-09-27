# Política de reorden de inventario

> Documento sintético de una empresa ficticia (Distribuidora Industrial Ficticia, DIF). Versión 3.1.

## Alcance
Aplica a los 200 SKUs del catálogo en los tres centros de distribución: CEDIS Centro
(Ciudad de México), CEDIS Occidente (Guadalajara) y CEDIS Norte (Monterrey). El punto de
reorden se evalúa sobre el **stock total**, es decir, la suma de las existencias de los tres CEDIS.

## Cálculo del punto de reorden
El punto de reorden de cada SKU se calcula como:

punto de reorden = demanda diaria promedio × tiempo de entrega del proveedor (días) × factor de seguridad

- Factor de seguridad para SKUs normales: **1.2**.
- Factor de seguridad para SKUs críticos: **1.5**.
- La demanda diaria promedio se toma de los últimos 90 días de ventas.
- El punto de reorden se recalcula cada trimestre o cuando el proveedor cambia su tiempo de entrega.

## Frecuencia de revisión
- Revisión **diaria** de todos los SKUs contra su punto de reorden.
- Excepción: la categoría Empaque se revisa **semanalmente** (los lunes), porque su costo
  unitario es bajo y su proveedor entrega en menos de una semana.

## Cantidad a pedir
Cuando el stock total cae por debajo del punto de reorden se genera una propuesta de orden de compra:

- SKUs normales: pedir lo necesario para cubrir **30 días** de demanda más el stock de seguridad.
- SKUs críticos: pedir lo necesario para cubrir **45 días** de demanda más el stock de seguridad.
- Redondear siempre al múltiplo de empaque del proveedor.
- No generar una nueva propuesta si ya existe una orden abierta (pendiente o aprobada) para el mismo SKU.

## Responsables
El comprador de la categoría revisa las propuestas diarias. Las órdenes se aprueban según los
montos definidos en el procedimiento de órdenes de compra.
