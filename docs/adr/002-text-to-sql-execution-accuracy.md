# ADR-002: Tool text-to-SQL evaluada por execution accuracy

**Estado:** aceptada · **Fecha:** 2026-09-27

## Contexto
El agente responde preguntas de inventario generando SQL con un LLM. Necesitamos
(1) que ese SQL nunca pueda dañar datos ni leer tablas fuera de alcance y (2) una
métrica reproducible para comparar modelos y proveedores (LM Studio vs Bedrock).

## Decisión
**Pipeline de la tool** (`src/tools/sql_tool.py`):
pregunta → LLM (prompt con esquema + 2 ejemplos few-shot) → quitar `<think>` →
extraer SQL (bloque ```` ```sql ```` o desde `SELECT`/`WITH`) → `validate_sql` →
ejecución con el rol `copilot_ro` en transacción `READ ONLY` con `statement_timeout`.
Los errores (LLM, guard, DB) se devuelven en el resultado, no como excepciones, para
que el agente y las evals los puedan contar.

**Defensa en profundidad:** guard sintáctico (sqlglot) + rol de solo lectura +
transacción `READ ONLY` + timeout. Cualquiera de las capas bloquea por sí sola una escritura.

**Métrica:** *execution accuracy* sobre `evals/datasets/sql_dev.jsonl` (antes `evals/golden_set.jsonl`; 30 preguntas en
español). Se comparan los resultados de ejecutar el SQL de referencia y el generado,
no el texto del SQL, porque hay muchas consultas equivalentes.
- Filas como multiconjunto; lista ordenada solo si `order_matters`.
- Números redondeados a 2 decimales; fechas como ISO.
- Se ignora el orden de columnas. La métrica principal tolera columnas extra
  (p. ej. devolver `sku, name` cuando se pidió `sku`); también se reporta la estricta.

**Datos reproducibles:** `scripts/generate_data.py` usa seed fija y una fecha ancla fija
(`ANCHOR_DATE = 2026-09-26`). El prompt declara esa fecha como "hoy" y el golden set usa
fechas literales, de modo que los resultados de referencia no cambian con el tiempo.

## Consecuencias
- (+) La métrica es comparable entre modelos y corridas; el reporte JSON queda en `evals/reports/`.
- (+) Los ejemplos few-shot se mantienen fuera del golden set para no inflar la métrica.
- (−) Tolerar columnas extra puede dar por buena una respuesta con información de más.
- (−) 30 preguntas son una muestra pequeña: sirve como gate de regresión, no como benchmark.
