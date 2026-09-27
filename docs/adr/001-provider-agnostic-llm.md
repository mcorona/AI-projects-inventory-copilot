# ADR-001: Capa LLM agnóstica de proveedor

**Estado:** aceptada · **Fecha:** 2026-09-26

## Contexto
El proyecto debe correr a $0 en una MacBook M5 Pro (35 GB) y, a la vez, demostrar
integración con Amazon Bedrock para roles de GenAI Developer en AWS.

## Decisión
Una interfaz única (`LLMProvider`: `chat`, `embed`) con tres adaptadores
seleccionados por `LLM_PROVIDER`:

| Proveedor | Uso | Costo |
|---|---|---|
| `lmstudio` | Desarrollo y evaluaciones reproducibles | $0 |
| `omniroute` | Fallback a modelos gratuitos (solo datos sintéticos) | $0 |
| `bedrock` | Corrida comparativa y demostración AWS (Converse API) | créditos |

LM Studio y OmniRoute comparten un adaptador porque ambos exponen la API de OpenAI.

## Consecuencias
- (+) Cambiar de proveedor no toca el código del agente; permite comparar costo/calidad.
- (+) Evaluaciones publicadas se generan con LM Studio (reproducible por cualquiera).
- (−) Funciones exclusivas de un proveedor (Bedrock Guardrails, prompt caching) se
  implementan detrás de la misma interfaz o como capacidades opcionales.
