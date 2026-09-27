-- Esquema de inventario sintetico + pgvector + rol de solo lectura (least privilege)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE suppliers (
    supplier_id   SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    country       TEXT NOT NULL,
    lead_time_days INT NOT NULL CHECK (lead_time_days > 0)
);

CREATE TABLE products (
    sku           TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    unit_cost     NUMERIC(12,2) NOT NULL,
    reorder_point INT NOT NULL,
    critical      BOOLEAN NOT NULL DEFAULT FALSE,
    supplier_id   INT REFERENCES suppliers(supplier_id)
);

CREATE TABLE warehouses (
    warehouse_id  SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    city          TEXT NOT NULL
);

CREATE TABLE stock (
    sku           TEXT REFERENCES products(sku),
    warehouse_id  INT REFERENCES warehouses(warehouse_id),
    on_hand       INT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sku, warehouse_id)
);

CREATE TABLE sales_daily (
    sku           TEXT REFERENCES products(sku),
    day           DATE NOT NULL,
    units         INT NOT NULL,
    PRIMARY KEY (sku, day)
);

CREATE TABLE purchase_orders (
    po_id         SERIAL PRIMARY KEY,
    sku           TEXT REFERENCES products(sku),
    qty           INT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'PENDING_APPROVAL'
                  CHECK (status IN ('PENDING_APPROVAL','APPROVED','REJECTED')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Chunks de documentos (manuales, politicas) para RAG
CREATE TABLE doc_chunks (
    chunk_id      BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,
    chunk_index   INT NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}',
    embedding     vector(1024)
);
CREATE INDEX ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- Rol de solo lectura para el agente: solo SELECT sobre tablas de negocio
CREATE ROLE copilot_ro LOGIN PASSWORD 'copilot_ro';
GRANT CONNECT ON DATABASE inventory TO copilot_ro;
GRANT USAGE ON SCHEMA public TO copilot_ro;
GRANT SELECT ON suppliers, products, warehouses, stock, sales_daily, doc_chunks TO copilot_ro;
