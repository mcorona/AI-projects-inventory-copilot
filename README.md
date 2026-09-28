# Inventory Copilot

Agente de IA generativa para consultar y operar un sistema de inventario con lenguaje natural,
de forma **segura, auditable y agnóstica de proveedor**: corre a $0 en local (LM Studio / OmniRoute)
y en **Amazon Bedrock** cambiando una variable.

> Estado: 🚧 v0.5 — evaluaciones con sets dev/test, repeticiones, juez validado, costo por consulta y gate en CI.

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
| Evaluación | Sets dev/test, 3 repeticiones, execution accuracy, exactitud de respuestas, faithfulness con juez LLM validado, inyección indirecta por capas · gate en CI | ✅ |
| Observabilidad | Telemetría por turno: latencia por etapa, tokens por llamada y costo real vs. equivalente en Bedrock (AWS Price List) | ✅ |
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

Split **test** (nunca usado para ajustar), 3 repeticiones, media (mín–máx). Detalle en
[`evals/results/`](evals/results/) y [ADR-008](docs/adr/008-evaluation-strategy-and-ci-gate.md).

| Métrica | LM Studio · Qwen3.6-35B-A3B | OmniRoute · minimax-m2.1 |
|---|---|---|
| Text-to-SQL, execution accuracy (30) | 95.6% (93.3–96.7) · p50 16.8 s | 92.2% (86.7–96.7) · p50 2.3 s |
| Agente: tools · exactitud · faithfulness (10) | 100% · 100% · 100% | 100% · 100% · 100% |
| Agente: latencia p50 / p95 | 6.6 s / 16.4 s | 4.0 s / 8.8 s |
| Agente: costo equivalente en Bedrock por consulta | $0.0009 | $0.0041* |
| Inyección indirecta, 7 escenarios: éxito con defensas · OC no pedidas | 29% · 0 | 29% · 0 |

- **Router con verificador** (minimax → Qwen): 96.7% en SQL, 10% de escaladas, p50 de 6.6 s.
- **RAG** (bge-m3): hit@1 87.5% en test y 100% en dev.
- **Guardrails:** 80% de detección y 0 falsos positivos en 109 preguntas legítimas.
- **Juez de faithfulness:** 100% de acuerdo con 16 casos etiquetados.

\* OmniRoute agrega su propio contexto a cada llamada; llamando a MiniMax M2.1 directo en Bedrock,
el costo sería menor. Los ataques exitosos fueron la exfiltración por "norma de formato" y la
desinformación en los datos: ver [ADR-007](docs/adr/007-layered-guardrails.md).

```bash
python -m evals.run_all                # ~2 h con Qwen: 3 repeticiones, juez, costos, resumen
python -m evals.gate                   # lo que corre el CI: huellas + umbrales
```

## Decisiones de arquitectura
- [ADR-001: Capa LLM agnóstica de proveedor](docs/adr/001-provider-agnostic-llm.md)
- [ADR-002: Tool text-to-SQL evaluada por execution accuracy](docs/adr/002-text-to-sql-execution-accuracy.md)
- [ADR-003: Tool calling neutro, loop de agente propio y MCP](docs/adr/003-agent-tool-calling.md)
- [ADR-004: RAG sobre pgvector](docs/adr/004-rag-pgvector.md)
- [ADR-005: Router de modelos en cascada](docs/adr/005-model-router-cascade.md)
- [ADR-006: Órdenes de compra con human-in-the-loop](docs/adr/006-hitl-purchase-orders.md)
- [ADR-007: Guardrails en capas](docs/adr/007-layered-guardrails.md)
- [ADR-008: Estrategia de evaluación, costo y gate de CI](docs/adr/008-evaluation-strategy-and-ci-gate.md)

## Datos
Todos los datos son **sintéticos**. Nunca envíes datos reales a proveedores gratuitos.

## Licencia
MIT
