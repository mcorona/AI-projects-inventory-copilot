"""Tool text-to-SQL: pregunta en lenguaje natural -> SQL validado -> filas + metricas."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from src.guardrails.sql_guard import SQLRejected, validate_sql
from src.llm import LLMProvider, get_provider

SCHEMA_PROMPT = """Eres un experto en PostgreSQL 16. Traduce la pregunta del usuario a UNA sola
consulta SELECT sobre este esquema de inventario (todas las fechas estan en sales_daily.day):

suppliers(supplier_id INT PK, name TEXT, country TEXT -- codigo ISO de 2 letras: MX, US, CN, DE, CO, ES, CA,
          lead_time_days INT)
products(sku TEXT PK -- formato 'SKU-0001', name TEXT, category TEXT, unit_cost NUMERIC(12,2),
         reorder_point INT -- umbral de reorden sobre el stock TOTAL (suma de almacenes),
         critical BOOLEAN, supplier_id INT FK -> suppliers)
warehouses(warehouse_id INT PK, name TEXT, city TEXT)
stock(sku TEXT FK -> products, warehouse_id INT FK -> warehouses, on_hand INT) -- PK (sku, warehouse_id)
sales_daily(sku TEXT FK -> products, day DATE, units INT) -- PK (sku, day), unidades vendidas por dia
  -- hay UNA fila por cada SKU y cada dia del periodo; un dia sin ventas tiene units = 0
  -- (no hay huecos: nunca generes series de fechas para buscar dias faltantes)

Categorias: Tornilleria, Electrico, Hidraulico, Neumatico, Rodamientos, Seguridad, Herramientas, Empaque.
Ciudades: Ciudad de Mexico, Guadalajara, Monterrey.
Los valores de texto (categorias, ciudades, paises) se guardan SIN acentos y exactamente como
aparecen arriba: si la pregunta dice "neumáticos" o "Neumático", usa category = 'Neumatico'.
La fecha de referencia ("hoy") es {anchor}; usa fechas literales, no now() ni current_date.
"Los ultimos N dias" incluye hoy y abarca exactamente N dias:
day > DATE '{anchor}' - N AND day <= DATE '{anchor}'.

Reglas:
- Solo SELECT (se permiten CTE con WITH). Nunca modifiques datos.
- Usa solo las tablas listadas.
- Devuelve unicamente el SQL dentro de un bloque ```sql ... ```, sin explicaciones.

Ejemplo 1
Pregunta: Cuantos proveedores hay por pais?
```sql
SELECT country, COUNT(*) AS proveedores FROM suppliers GROUP BY country ORDER BY country;
```

Ejemplo 2
Pregunta: Cuanto stock tiene el SKU-0001 en cada almacen?
```sql
SELECT w.name, s.on_hand
FROM stock s JOIN warehouses w ON w.warehouse_id = s.warehouse_id
WHERE s.sku = 'SKU-0001'
ORDER BY w.name;
```"""
# Los ejemplos few-shot NO deben coincidir con preguntas de evals/golden_set.jsonl.

# fecha "hoy" de los datos sinteticos; scripts/generate_data.py la usa como fin de sales_daily
ANCHOR_DATE = date(2026, 9, 26)

_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:sql|postgresql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_START_RE = re.compile(r"\b(WITH|SELECT)\b", re.IGNORECASE)
_UNCLOSED_THINK_RE = re.compile(r"<think>(?!.*</think>)", re.DOTALL | re.IGNORECASE)


@dataclass
class SQLToolResult:
    question: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None
    raw_sql: str | None = None  # SQL extraido antes del guard
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    llm_latency_ms: float = 0.0
    db_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def strip_think(text: str) -> str:
    """Elimina bloques <think>...</think> (incluido uno sin cerrar por truncamiento)."""
    return _THINK_RE.sub("", text).strip()


def extract_sql(text: str) -> str | None:
    """Extrae el SQL de la respuesta: primero un bloque ```sql```, si no desde SELECT/WITH."""
    text = strip_think(text)
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1)
    else:
        start = _START_RE.search(text)
        if not start:
            return None
        candidate = text[start.start():]
    candidate = candidate.strip()
    # conserva solo la primera sentencia; el guard rechazaria varias de todos modos
    if ";" in candidate:
        candidate = candidate[:candidate.index(";")]
    return candidate.strip() or None


def execute_readonly(sql: str, dsn: str, timeout_ms: int = 5000) -> tuple[list[str], list[tuple]]:
    """Ejecuta con el rol de solo lectura en una transaccion READ ONLY con timeout."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            cur.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            return columns, cur.fetchall()


Executor = Callable[[str], tuple[list[str], list[tuple]]]


def run_sql_tool(question: str, llm: LLMProvider | None = None,
                 executor: Executor | None = None, max_tokens: int = 8192) -> SQLToolResult:
    """Pregunta -> LLM -> strip_think -> extract_sql -> validate_sql -> ejecucion.

    Nunca lanza por errores del modelo, del guard o de la DB: los reporta en `error`.
    `llm` y `executor` son inyectables para pruebas.
    """
    t0 = time.perf_counter()
    res = SQLToolResult(question=question)
    llm = llm or get_provider()
    if executor is None:
        dsn = os.environ["PG_DSN"]
        executor = lambda sql: execute_readonly(sql, dsn)  # noqa: E731

    try:
        chat = llm.chat([{"role": "user", "content": question}],
                        system=SCHEMA_PROMPT.format(anchor=ANCHOR_DATE.isoformat()),
                        temperature=0.0, max_tokens=max_tokens)
    except Exception as e:  # red, modelo no cargado, etc.
        res.error = f"llm_error: {e}"
        res.total_latency_ms = (time.perf_counter() - t0) * 1000
        return res

    res.provider, res.model = chat.provider, chat.model
    res.input_tokens, res.output_tokens = chat.input_tokens, chat.output_tokens
    res.llm_latency_ms = chat.latency_ms

    res.raw_sql = extract_sql(chat.text)
    if _UNCLOSED_THINK_RE.search(chat.text):
        # modelos de razonamiento: si se agota max_tokens pensando, no hay respuesta
        res.error = f"truncated: <think> sin cerrar tras {chat.output_tokens} tokens (sube max_tokens)"
        res.raw_sql = None
    elif not res.raw_sql:
        res.error = "no_sql: la respuesta del modelo no contiene SQL"
    else:
        try:
            res.sql = validate_sql(res.raw_sql)
        except SQLRejected as e:
            res.error = f"guard_rejected: {e}"

    if res.sql:
        t_db = time.perf_counter()
        try:
            res.columns, rows = executor(res.sql)
            res.rows = [tuple(r) for r in rows]
        except Exception as e:
            res.error = f"db_error: {e}"
        res.db_latency_ms = (time.perf_counter() - t_db) * 1000

    res.total_latency_ms = (time.perf_counter() - t0) * 1000
    return res

