-- Semana 4: ordenes de compra con aprobacion humana + bitacora de auditoria.
-- Idempotente: corre sola en instalaciones nuevas (docker-entrypoint-initdb.d) y se aplica
-- a una DB existente con `python -m scripts.migrate`.

-- ---------------------------------------------------------------- purchase_orders
ALTER TABLE purchase_orders
    ADD COLUMN IF NOT EXISTS unit_cost      NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS amount         NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS required_level TEXT,
    ADD COLUMN IF NOT EXISTS reason         TEXT,
    ADD COLUMN IF NOT EXISTS requested_by   TEXT,
    ADD COLUMN IF NOT EXISTS confirmed_by   TEXT,
    ADD COLUMN IF NOT EXISTS decided_by     TEXT,
    ADD COLUMN IF NOT EXISTS decided_level  TEXT,
    ADD COLUMN IF NOT EXISTS decided_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS decision_note  TEXT;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'po_qty_positive') THEN
        ALTER TABLE purchase_orders ADD CONSTRAINT po_qty_positive CHECK (qty > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'po_levels_valid') THEN
        ALTER TABLE purchase_orders ADD CONSTRAINT po_levels_valid CHECK (
            required_level IN ('comprador', 'gerente', 'director') AND
            (decided_level IS NULL OR decided_level IN ('comprador', 'gerente', 'director')));
    END IF;
END $$;

-- a lo mas una orden pendiente por SKU (politica_reorden.md: no duplicar ordenes abiertas)
CREATE UNIQUE INDEX IF NOT EXISTS po_one_pending_per_sku
    ON purchase_orders (sku) WHERE status = 'PENDING_APPROVAL';

CREATE OR REPLACE FUNCTION po_level_rank(level TEXT) RETURNS INT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE level WHEN 'comprador' THEN 1 WHEN 'gerente' THEN 2 WHEN 'director' THEN 3 END
$$;

-- Nivel de aprobacion por monto (ordenes_de_compra.md). Fuente de verdad: esta funcion;
-- src/tools/purchase_orders.py replica los umbrales solo para mostrar la vista previa.
CREATE OR REPLACE FUNCTION po_required_level(amount NUMERIC) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN amount < 50000 THEN 'comprador'
                WHEN amount <= 250000 THEN 'gerente'
                ELSE 'director' END
$$;

-- Al insertar, la DB (no el agente) fija costo, monto, nivel requerido y estado.
-- SECURITY DEFINER: copilot_po no necesita leer products para que el trigger lo haga.
CREATE OR REPLACE FUNCTION po_before_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    SELECT p.unit_cost INTO NEW.unit_cost FROM products p WHERE p.sku = NEW.sku;
    IF NEW.unit_cost IS NULL THEN
        RAISE EXCEPTION 'SKU inexistente: %', NEW.sku;
    END IF;
    NEW.amount := NEW.qty * NEW.unit_cost;
    NEW.required_level := po_required_level(NEW.amount);
    NEW.status := 'PENDING_APPROVAL';
    NEW.decided_by := NULL; NEW.decided_level := NULL; NEW.decided_at := NULL; NEW.decision_note := NULL;
    NEW.created_at := now();
    RETURN NEW;
END $$;

-- Al decidir: solo desde PENDING_APPROVAL, sin tocar la propuesta, y con autoridad suficiente.
CREATE OR REPLACE FUNCTION po_before_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'PENDING_APPROVAL' THEN
        RAISE EXCEPTION 'La orden % ya fue decidida (%)', OLD.po_id, OLD.status;
    END IF;
    IF NEW.status NOT IN ('APPROVED', 'REJECTED') THEN
        RAISE EXCEPTION 'Estado de decision invalido: %', NEW.status;
    END IF;
    IF (NEW.sku, NEW.qty, NEW.unit_cost, NEW.amount, NEW.required_level, NEW.reason,
        NEW.requested_by, NEW.confirmed_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.sku, OLD.qty, OLD.unit_cost, OLD.amount, OLD.required_level, OLD.reason,
        OLD.requested_by, OLD.confirmed_by, OLD.created_at) THEN
        RAISE EXCEPTION 'No se puede modificar la propuesta al decidir';
    END IF;
    IF NEW.decided_by IS NULL OR NEW.decided_level IS NULL THEN
        RAISE EXCEPTION 'La decision requiere decided_by y decided_level';
    END IF;
    IF NEW.status = 'APPROVED'
       AND po_level_rank(NEW.decided_level) < po_level_rank(OLD.required_level) THEN
        RAISE EXCEPTION 'Nivel insuficiente: la orden % requiere %, quien decide es %',
            OLD.po_id, OLD.required_level, NEW.decided_level;
    END IF;
    NEW.decided_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS po_before_insert ON purchase_orders;
CREATE TRIGGER po_before_insert BEFORE INSERT ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION po_before_insert();
DROP TRIGGER IF EXISTS po_before_update ON purchase_orders;
CREATE TRIGGER po_before_update BEFORE UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION po_before_update();

-- ---------------------------------------------------------------- audit_log (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    event_id  BIGSERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor     TEXT NOT NULL,
    event     TEXT NOT NULL,
    details   JSONB NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------- roles de minimo privilegio
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_po') THEN
        CREATE ROLE copilot_po LOGIN PASSWORD 'copilot_po';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_approver') THEN
        CREATE ROLE copilot_approver LOGIN PASSWORD 'copilot_approver';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_audit') THEN
        CREATE ROLE copilot_audit LOGIN PASSWORD 'copilot_audit';
    END IF;
END $$;

GRANT CONNECT ON DATABASE inventory TO copilot_po, copilot_approver, copilot_audit;
GRANT USAGE ON SCHEMA public TO copilot_po, copilot_approver, copilot_audit;

-- lectura: el agente revisa ordenes abiertas antes de proponer
GRANT SELECT ON purchase_orders TO copilot_ro;

-- el agente solo puede PROPONER: insertar columnas de la propuesta, nunca status ni decision
GRANT SELECT ON purchase_orders TO copilot_po;
GRANT INSERT (sku, qty, reason, requested_by, confirmed_by) ON purchase_orders TO copilot_po;
GRANT USAGE ON SEQUENCE purchase_orders_po_id_seq TO copilot_po;

-- las personas aprobadoras solo pueden DECIDIR
GRANT SELECT ON purchase_orders TO copilot_approver;
GRANT UPDATE (status, decided_by, decided_level, decision_note) ON purchase_orders TO copilot_approver;

-- bitacora: solo insercion (nadie de la app puede editar ni borrar eventos)
GRANT INSERT ON audit_log TO copilot_audit;
GRANT USAGE ON SEQUENCE audit_log_event_id_seq TO copilot_audit;
