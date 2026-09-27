# Inventory Copilot

Agente de IA generativa para consultar y operar un sistema de inventario con lenguaje natural,
de forma **segura, auditable y agnóstica de proveedor**: corre a $0 en local (LM Studio / OmniRoute)
y en **Amazon Bedrock** cambiando una variable.

> Estado: 🚧 v0.3 — agente con tools, RAG, MCP server y router de modelos.

## Qué demuestra

| Capacidad | Implementación | Estado |
|---|---|---|
| Capa LLM multiproveedor | LM Studio · OmniRoute · Bedrock Converse | ✅ |
| Text-to-SQL seguro | Validador `sqlglot`: solo SELECT, allowlist de tablas, LIMIT forzado, rol read-only | ✅ |
| RAG | PostgreSQL + pgvector (equivalente a Aurora pgvector), chunks por sección | ✅ |
| Agente con herramientas + MCP | Tool calling nativo · SQL, ficha de SKU, RAG · MCP server (stdio) | ✅ |
| Órdenes de compra | Propuesta por el agente con aprobación humana | ⏳ |
| Guardrails | PII, prompt injection (directa e indirecta) · Bedrock ApplyGuardrail | ⏳ |
| Router de modelos (cascada) | Modelo rápido por defecto, escala a uno más capaz ante fallos detectables | ✅ |
| Evaluación | Execution accuracy (SQL), hit@k/MRR (RAG), tool selection (agente) · faithfulness y LLM-as-judge pendientes | 🟡 |
| Observabilidad | Tokens, latencia p95 y costo estimado por consulta | ⏳ |
| IaC | AWS CDK + cdk-nag (`cdk synth` en CI) | ⏳ |

## Arquitectura

```
Usuario ─► API ─► Agente ─► Capa LLM ─┬─► LM Studio  (local)
                    │                  ├─► OmniRoute  (modelos gratuitos, fallback)
                    │                  └─► Amazon Bedrock (Converse)
                    ├─ Tool SQL ─► SQL guard ─► PostgreSQL (rol read-only)
                    ├─ Tool RAG ─► pgvector
                    ├─ MCP server de inventario
                    └─ Tool orden de compra ─► aprobación humana
```

## Arranque rápido (5 min)

```bash
git clone https://github.com/mcorona/AI-projects-inventory-copilot.git
cd AI-projects-inventory-copilot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
python -m pytest -q
python -m scripts.smoke_test
python -m scripts.generate_data      # datos sinteticos reproducibles (seed 42)
python -m scripts.ingest_docs        # indexa data/docs en pgvector (embeddings bge-m3)
python -m evals.run_sql_eval         # execution accuracy del text-to-SQL
python -m evals.run_rag_eval         # hit@k y MRR de la recuperacion
python -m evals.run_agent_eval       # seleccion de herramientas del agente
python -m src.agent --trace "¿Cómo está el SKU-0009 y qué dice la política de SKUs críticos?"
```

En LM Studio: carga el modelo de chat y el de embeddings, y activa el servidor local
(pestaña **Developer → Start Server**, puerto 1234).

### Modelos recomendados (MacBook M5 Pro, 35 GB RAM unificada)

| Rol | Modelo | Memoria aprox. (Q4) |
|---|---|---|
| Chat / SQL (principal) | Qwen3.6-35B-A3B (MoE) | ~20 GB |
| Chat (alternativa) | gpt-oss-20b | ~12 GB |
| Embeddings | bge-m3 (multilingüe, 1024 dim) | ~1 GB |

## Cambiar de proveedor

```bash
LLM_PROVIDER=omniroute python -m scripts.smoke_test --no-embed
LLM_PROVIDER=bedrock   python -m scripts.smoke_test
```

Router en cascada (modelo rápido primero, escala al capaz si falla):

```bash
ROUTER_TIERS="omniroute:kr/minimax-m2.1,lmstudio:qwen/qwen3.6-35b-a3b" \
  python -m evals.run_sql_eval --provider router
```

## MCP server

Las tres herramientas del agente (`query_inventory`, `get_sku_status`, `search_documents`) y el
esquema (`inventory://schema`) se exponen por MCP, todas de solo lectura:

```bash
python -m src.mcp_server        # stdio
```

Para Claude Code, agrega a `.mcp.json` en la raíz del proyecto:

```json
{
  "mcpServers": {
    "inventory": {
      "command": ".venv/bin/python",
      "args": ["-m", "src.mcp_server"]
    }
  }
}
```

## Resultados actuales

| Eval | LM Studio · Qwen3.6-35B-A3B | OmniRoute · minimax-m2.1 |
|---|---|---|
| Text-to-SQL, execution accuracy (30 preguntas) | 100.0% · p50 15 s | 93.3% · p50 1.7 s |
| Selección de herramientas del agente (12 tareas) | 100% · p50 5.4 s | 100% · p50 4.1 s |
| Recuperación RAG, bge-m3 (15 preguntas) | hit@1 100% · MRR 1.0 | — |

Muestras pequeñas: una pregunta mueve 3–8 puntos. Ver los ADR para limitaciones.

## Decisiones de arquitectura
- [ADR-001: Capa LLM agnóstica de proveedor](docs/adr/001-provider-agnostic-llm.md)
- [ADR-002: Tool text-to-SQL evaluada por execution accuracy](docs/adr/002-text-to-sql-execution-accuracy.md)
- [ADR-003: Tool calling neutro, loop de agente propio y MCP](docs/adr/003-agent-tool-calling.md)
- [ADR-004: RAG sobre pgvector](docs/adr/004-rag-pgvector.md)
- [ADR-005: Router de modelos en cascada](docs/adr/005-model-router-cascade.md)

## Datos
Todos los datos son **sintéticos**. Nunca envíes datos reales a proveedores gratuitos.

## Licencia
MIT
