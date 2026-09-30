# ADR-003: Tool calling neutro, loop de agente propio y MCP sobre el mismo registro

**Estado:** aceptada · **Fecha:** 2026-09-27

## Contexto
El agente debe elegir entre varias herramientas (datos, ficha de SKU, políticas) y correr
igual en LM Studio, OmniRoute y Bedrock. Además, las mismas capacidades deben quedar
disponibles para clientes externos (Claude Desktop, Claude Code, IDEs) vía MCP.

## Decisión
**Tool calling nativo con formato neutro.** `LLMProvider.chat(..., tools=[...])` recibe tools
como `{name, description, parameters: JSON Schema}` y devuelve `ChatResult.tool_calls`. Los
adaptadores traducen a OpenAI (`tools` / `tool_calls` / `role: tool`) y a Bedrock Converse
(`toolConfig` / `toolUse` / `toolResult`, fusionando resultados consecutivos en un solo turno
`user`, como exige Converse). Probado: Qwen 3.6 en LM Studio y minimax-m2.1 en OmniRoute emiten
tool calls nativos.

**Loop propio, sin framework.** `src/agent/core.py`: LLM → tool calls → resultados → LLM, con
máximo de pasos. Los errores de tools (desconocida, JSON inválido, falta de argumentos,
excepción) se devuelven al modelo como datos para que pueda corregir, y quedan en la traza
(`Step`: tool, argumentos, latencia, error). Equivalencias: tools = action groups de Bedrock
Agents; traza = trace de Bedrock Agents / AgentCore Observability.

**Tres tools, un registro** (`src/tools/registry.py`), compartido con el MCP server:
- `get_sku_status`: consultas fijas parametrizadas; la pregunta más frecuente no depende del
  LLM para escribir SQL (más rápida, determinista y sin tokens extra).
- `query_inventory`: text-to-SQL (ADR-002) para preguntas analíticas abiertas.
- `search_documents`: RAG (ADR-004).

**MCP server** (`src/mcp_server/`, SDK oficial `mcp` 2.x, `MCPServer`, stdio): expone las tres
tools con `ToolAnnotations(read_only_hint=True)` y el resource `inventory://schema`.

**Defensas contra prompt injection indirecta (mínimas; la Semana 4 las amplía):** el prompt
de sistema declara que la salida de las tools son datos, no instrucciones; las salidas se
truncan (50 filas, 8,000 caracteres) antes de volver al modelo.

## Actualización (v1.1.1): respuesta vacía
En la corrida `20260930T004357Z`, Qwen terminó una pregunta de test con solo un bloque `<think>` en 2 de
3 repeticiones, y el agente devolvió una respuesta vacía con `stop_reason=answer`.
- Si la respuesta queda vacía después de quitar `<think>`, el loop le pide una vez la respuesta
  (`EMPTY_ANSWER_NUDGE`, que forma parte de la huella del agente).
- Si vuelve a salir vacía, el agente termina con `stop_reason=empty_answer` y un mensaje explícito,
  nunca con texto en blanco.
- El eval reporta `empty_answer_retry_rate`. En dev (16 preguntas, 2 corridas) hubo una respuesta vacía
  (a12, 1 de 16 en la segunda corrida): el reintento la recuperó con la respuesta correcta y su fuente.

## Alternativas consideradas
- **Strands Agents / LangGraph:** menos código propio, pero ocultan el loop que este
  portafolio quiere mostrar y agregan dependencias. Un adaptador a Strands puede sumarse en la
  Semana 6 junto con Bedrock.
- **ReAct por texto (sin tool calling nativo):** frágil de parsear; innecesario porque los tres
  proveedores soportan tools nativas.

## Consecuencias
- (+) Cambiar de proveedor no cambia el agente; la traza alimenta las evals y las métricas de costo.
- (+) MCP y agente no divergen: una sola implementación por tool.
- (−) Sin memoria de conversación persistente ni streaming; se agregan cuando haya API/UI.
- (−) En Bedrock, el historial con `toolUse` exige enviar `toolConfig` en cada llamada: el loop
  nunca hace una llamada final sin tools (al agotar pasos devuelve `max_steps`).
