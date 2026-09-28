# Inventory Copilot — contexto para Claude Code

## Objetivo
Portafolio público (GitHub: mcorona/AI-projects-inventory-copilot) que demuestra las
competencias del examen AWS Certified Generative AI Developer – Professional (AIP-C01):
agente GenAI gobernado sobre un sistema de inventario. Costo objetivo: $0 en local.

## Arquitectura
- Capa LLM agnóstica en `src/llm/__init__.py`: `LLM_PROVIDER=lmstudio|omniroute|bedrock|router` (router en cascada: `src/llm/router.py`).
- LM Studio local (localhost:1234): chat `qwen/qwen3.6-35b-a3b`, embeddings `text-embedding-bge-m3` (1024 dim).
- Qwen emite bloques `<think>...</think>`: deben eliminarse antes de usar la respuesta.
- PostgreSQL 16 + pgvector en Docker (`docker compose up -d`); esquema en `db/init/01_schema.sql`.
- El agente consulta SOLO con el rol `copilot_ro` (read-only) y SIEMPRE pasa el SQL por
  `src/guardrails/sql_guard.py::validate_sql`. Las consultas fijas del codigo tambien; la de RAG
  se valida con `allowed_tables={"doc_chunks"}` pero se ejecuta la constante (sqlglot reescribe `<=>`).
- Tools en `src/tools/registry.py`, compartidas por el agente (`src/agent/`) y el MCP server
  (`src/mcp_server/`, SDK `mcp` 2.x: `MCPServer`, no `FastMCP`).
- Mensajes/tools en formato neutro (`src/llm/__init__.py`); cada adaptador traduce a su API.
- Embeddings: `EMBED_PROVIDER` (lmstudio|bedrock); el indice filtra por `metadata.embed_model`.
- OmniRoute cachea respuestas: no confiar en latencias de evals repetidas con prompts identicos
  (las evals de guardrails agregan un id de corrida al prompt del clasificador).
- Ordenes de compra: el agente PROPONE (rol `copilot_po`, solo INSERT de columnas de propuesta) tras
  confirmacion del usuario; aprueba una persona (`copilot_approver`, `scripts/po_review.py`). Monto,
  nivel y estado los fija la DB (`db/init/02_hitl_guardrails.sql`). Bitacora: `audit_log` (`copilot_audit`).
- Argumentos de tools con prefijo `_` son internos: el loop los quita de lo que manda el modelo.
- Guardrails en `src/guardrails/pipeline.py` (PII, inyeccion directa/indirecta, spotlighting); el
  agente los usa por defecto. No ajustar heuristicas mirando `evals/guardrails_set.jsonl` (sobreajuste).
- Migraciones: `python -m scripts.migrate` aplica `db/init/0[2-9]_*.sql` (idempotentes).
- Todos los datos son sintéticos (`scripts/generate_data.py`, seed 42). La fecha "hoy" es fija:
  `ANCHOR_DATE` en `src/tools/sql_tool.py`; el golden set usa fechas literales.
- Los ejemplos few-shot del prompt SQL no deben coincidir con preguntas del golden set.

## Convenciones
- Python 3.13, venv en `.venv`. Dependencias en `requirements.txt`.
- Pruebas: `python -m pytest -q` (no deben requerir LLM ni base de datos; usa mocks).
- Cada funcionalidad nueva lleva pruebas y, si es una decisión de arquitectura, un ADR en `docs/adr/`.
- Commits en inglés, estilo conventional commits (feat:, fix:, docs:, test:, chore:).
- No subir `.env` ni secretos.

## Plan
- [x] Semana 1: capa LLM multiproveedor, esquema, SQL guard, CI.
- [x] Semana 2: generador de datos sintéticos, tool text-to-SQL, golden set v1 + execution accuracy.
- [x] Semana 3: agente con tools, RAG con pgvector, MCP server, router de modelos.
- [x] Semana 4: guardrails (PII, prompt injection), human-in-the-loop para órdenes de compra.
- [ ] Semana 5: evaluaciones completas, CI gate, métricas de costo/latencia.
- [ ] Semana 6: CDK + cdk-nag, corrida comparativa en Bedrock, README final, demo.
