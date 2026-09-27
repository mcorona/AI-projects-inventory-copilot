# Inventory Copilot — contexto para Claude Code

## Objetivo
Portafolio público (GitHub: mcorona/AI-projects-inventory-copilot) que demuestra las
competencias del examen AWS Certified Generative AI Developer – Professional (AIP-C01):
agente GenAI gobernado sobre un sistema de inventario. Costo objetivo: $0 en local.

## Arquitectura
- Capa LLM agnóstica en `src/llm/__init__.py`: `LLM_PROVIDER=lmstudio|omniroute|bedrock`.
- LM Studio local (localhost:1234): chat `qwen/qwen3.6-35b-a3b`, embeddings `text-embedding-bge-m3` (1024 dim).
- Qwen emite bloques `<think>...</think>`: deben eliminarse antes de usar la respuesta.
- PostgreSQL 16 + pgvector en Docker (`docker compose up -d`); esquema en `db/init/01_schema.sql`.
- El agente consulta SOLO con el rol `copilot_ro` (read-only) y SIEMPRE pasa el SQL por
  `src/guardrails/sql_guard.py::validate_sql`.
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
- [ ] Semana 3: agente con tools, RAG con pgvector, MCP server, router de modelos.
- [ ] Semana 4: guardrails (PII, prompt injection), human-in-the-loop para órdenes de compra.
- [ ] Semana 5: evaluaciones completas, CI gate, métricas de costo/latencia.
- [ ] Semana 6: CDK + cdk-nag, corrida comparativa en Bedrock, README final, demo.
