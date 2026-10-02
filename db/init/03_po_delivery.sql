-- Ordenes de compra: CEDIS de entrega y fecha requerida (ordenes_de_compra.md, "Datos obligatorios
-- de una orden"). Idempotente: corre en instalaciones nuevas (docker-entrypoint-initdb.d) y se aplica
-- a una DB existente con `python -m scripts.migrate`. Las ordenes anteriores quedan con NULL.

ALTER TABLE purchase_orders
    ADD COLUMN IF NOT EXISTS delivery_warehouse_id INT REFERENCES warehouses (warehouse_id),
    ADD COLUMN IF NOT EXISTS required_date         DATE;

-- Obligatorios en ordenes nuevas. La fecha no se compara con now(): los datos sinteticos usan una
-- fecha "hoy" fija (ANCHOR_DATE) y esa validacion la hace la vista previa.
CREATE OR REPLACE FUNCTION po_require_delivery() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.delivery_warehouse_id IS NULL OR NEW.required_date IS NULL THEN
        RAISE EXCEPTION 'La orden requiere CEDIS de entrega y fecha requerida';
    END IF;
    RETURN NEW;
END $$;

-- Igual que el resto de la propuesta: nadie la cambia al decidir.
CREATE OR REPLACE FUNCTION po_delivery_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.delivery_warehouse_id, NEW.required_date)
       IS DISTINCT FROM (OLD.delivery_warehouse_id, OLD.required_date) THEN
        RAISE EXCEPTION 'No se puede modificar la propuesta al decidir';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS po_require_delivery ON purchase_orders;
CREATE TRIGGER po_require_delivery BEFORE INSERT ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION po_require_delivery();
DROP TRIGGER IF EXISTS po_delivery_immutable ON purchase_orders;
CREATE TRIGGER po_delivery_immutable BEFORE UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION po_delivery_immutable();

-- el agente propone tambien estas dos columnas; las personas aprobadoras no las pueden tocar
GRANT INSERT (delivery_warehouse_id, required_date) ON purchase_orders TO copilot_po;
