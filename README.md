# Inventory Copilot

Agente de IA generativa para consultar y operar un sistema de inventario con lenguaje natural,
de forma **segura, auditable y agnóstica de proveedor**: corre a $0 en local (LM Studio / OmniRoute)
y en **Amazon Bedrock** cambiando una variable.

> Estado: 🚧 v0.1 — capa LLM multiproveedor, esquema de datos y SQL guard.

## Qué demuestra

| Capacidad | Implementación | Estado |
|---|---|---|
| Capa LLM multiproveedor | LM Studio · OmniRoute · Bedrock Converse | ✅ |
| Text-to-SQL seguro | Validador `sqlglot`: solo SELECT, allowlist de tablas, LIMIT forzado, rol read-only | ✅ |
| RAG | PostgreSQL + pgvector (equivalente a Aurora pgvector) | ⏳ |
| Agente con herramientas + MCP | SQL, RAG, órdenes de compra con aprobación humana | ⏳ |
| Guardrails | PII, prompt injection (directa e indirecta) · Bedrock ApplyGuardrail | ⏳ |
| Router de modelos (cascada) | Modelo barato por defecto, escala a uno más capaz | ⏳ |
| Evaluación | Golden set, execution accuracy, faithfulness, LLM-as-judge | ⏳ |
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
python -m evals.run_sql_eval         # execution accuracy del text-to-SQL
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

## Decisiones de arquitectura
- [ADR-001: Capa LLM agnóstica de proveedor](docs/adr/001-provider-agnostic-llm.md)
- [ADR-002: Tool text-to-SQL evaluada por execution accuracy](docs/adr/002-text-to-sql-execution-accuracy.md)

## Datos
Todos los datos son **sintéticos**. Nunca envíes datos reales a proveedores gratuitos.

## Licencia
MIT
