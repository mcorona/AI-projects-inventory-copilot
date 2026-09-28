# Inventory Copilot

Agente de IA generativa para consultar y operar un sistema de inventario con lenguaje natural,
de forma **segura, auditable y agnóstica de proveedor**: corre a $0 en local (LM Studio / OmniRoute)
y en **Amazon Bedrock** cambiando una variable.

> Estado: 🚧 v0.4 — guardrails (PII, prompt injection) y órdenes de compra con aprobación humana.

## Qué demuestra

| Capacidad | Implementación | Estado |
|---|---|---|
| Capa LLM multiproveedor | LM Studio · OmniRoute · Bedrock Converse | ✅ |
| Text-to-SQL seguro | Validador `sqlglot`: solo SELECT, allowlist de tablas, LIMIT forzado, rol read-only | ✅ |
| RAG | PostgreSQL + pgvector (equivalente a Aurora pgvector), chunks por sección | ✅ |
| Agente con herramientas + MCP | Tool calling nativo · SQL, ficha de SKU, RAG · MCP server (stdio) | ✅ |
| Órdenes de compra | Propuesta por el agente → confirmación del usuario → aprobación por nivel de autoridad; roles de DB de mínimo privilegio y bitácora append-only | ✅ |
| Guardrails | PII (formatos MX), prompt injection directa e indirecta (heurísticas + clasificador LLM opcional), spotlighting · adaptador Bedrock ApplyGuardrail | ✅ |
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
python -m scripts.migrate            # roles y tablas de la Semana 4 (si tu DB ya existia)
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

## Órdenes de compra con aprobación humana

```bash
python -m src.agent --user "Ana" "Propón una orden de compra de 1500 unidades del SKU-0009"
#   → muestra monto, nivel de aprobación y cobertura, y pide confirmación [s/N]
python -m scripts.po_review list
python -m scripts.po_review approve 1 --as comprador --by "Luis"
```

Dos compuertas: la persona que pregunta confirma la propuesta y otra con autoridad suficiente la
aprueba (comprador < $50k ≤ gerente ≤ $250k < director). La DB es la fuente de verdad: el agente
(`copilot_po`) solo puede insertar columnas de la propuesta, un trigger calcula monto y nivel, y
otro impide aprobar sin autoridad o re-decidir. Todo queda en `audit_log` (append-only).

## Guardrails

- **PII** (email, teléfono, RFC, CURP, tarjeta, CLABE): se anonimiza antes de enviar al LLM y en la
  respuesta; tarjetas y CLABEs se bloquean.
- **Inyección directa**: heurísticas deterministas (ES/EN) y, opcionalmente, un clasificador LLM
  (`GUARDRAIL_LLM_CLASSIFIER=on`).
- **Inyección indirecta**: las salidas de tools se revisan y se retira el contenido con instrucciones;
  además se delimitan como datos no confiables (*spotlighting*).
- **Bedrock Guardrails**: adaptador `ApplyGuardrail` con la misma interfaz (`BEDROCK_GUARDRAIL_ID`).

```bash
python -m evals.run_guardrails_eval [--llm-classifier omniroute:kr/minimax-m2.1]
python -m evals.run_injection_eval --provider lmstudio
```

## MCP server

Las tres herramientas de lectura (`query_inventory`, `get_sku_status`, `search_documents`) y el
esquema (`inventory://schema`) se exponen por MCP, con las salidas saneadas por los guardrails.
Las órdenes de compra no se exponen: un cliente MCP no garantiza la confirmación humana.

```bash
python -m src.mcp_server        # stdio
```

El repo incluye `.mcp.json`, así que Claude Code lo detecta al abrir el proyecto (pide aprobarlo la primera vez). Su contenido:

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
| Selección de herramientas del agente (16 tareas, incluye proponer o no OC) | 100% · p50 7.1 s | 100% · p50 6.0 s |
| Recuperación RAG, bge-m3 (15 preguntas) | hit@1 100% · MRR 1.0 | — |
| Clasificador de inyección (30 ataques, sobre heurísticas 80%) | 100% · +7.6 s/pregunta | 96.7% · +1.8 s/pregunta |
| Inyección indirecta (4 escenarios): éxito del ataque · OC no pedidas | 0% · 0 | 0% · 0 |

Guardrails de entrada: 0% de falsos positivos en 61 preguntas legítimas y 38 chunks del corpus.

Muestras pequeñas: una pregunta mueve 3–8 puntos. Ver los ADR para limitaciones.

## Decisiones de arquitectura
- [ADR-001: Capa LLM agnóstica de proveedor](docs/adr/001-provider-agnostic-llm.md)
- [ADR-002: Tool text-to-SQL evaluada por execution accuracy](docs/adr/002-text-to-sql-execution-accuracy.md)
- [ADR-003: Tool calling neutro, loop de agente propio y MCP](docs/adr/003-agent-tool-calling.md)
- [ADR-004: RAG sobre pgvector](docs/adr/004-rag-pgvector.md)
- [ADR-005: Router de modelos en cascada](docs/adr/005-model-router-cascade.md)
- [ADR-006: Órdenes de compra con human-in-the-loop](docs/adr/006-hitl-purchase-orders.md)
- [ADR-007: Guardrails en capas](docs/adr/007-layered-guardrails.md)

## Datos
Todos los datos son **sintéticos**. Nunca envíes datos reales a proveedores gratuitos.

## Licencia
MIT
