# ADR-005: Router de modelos en cascada con escalamiento por señales verificables

**Estado:** aceptada · **Fecha:** 2026-09-27

## Contexto
En el golden set SQL, minimax-m2.1 (OmniRoute) responde en ~2 s con 93–97% de execution
accuracy, y Qwen 3.6 35B (LM Studio) en ~15 s con 97–100%. La hipótesis: usar el modelo rápido
por defecto y escalar al capaz solo cuando haga falta.

## Decisión
`CascadeRouter` (`src/llm/router.py`) implementa `LLMProvider` sobre una lista de niveles
(`ROUTER_TIERS=omniroute:kr/minimax-m2.1,lmstudio:qwen/qwen3.6-35b-a3b`). Escala en dos puntos:
- **`chat()`**: excepción del proveedor (disponibilidad), respuesta vacía o `<think>` truncado.
- **`run(fn, accept)`**: la tool define qué es aceptable. Para text-to-SQL:
  sin error (guard, DB, truncado) **y** con filas.

Tokens y latencia se suman sobre todos los intentos (es el costo real). El resultado registra
`escalations` y cada intento.

## Resultados (golden set SQL, 30 preguntas)
El router obtuvo la misma accuracy que minimax solo (93.3%) con **0 escaladas**: los dos
fallos de minimax (q15, q23) son SQL válido con filas pero semánticamente incorrecto, que
ninguna señal barata detecta.

## Lecciones
- **Una cascada solo corrige fallos detectables.** Su valor aquí es la disponibilidad (si
  OmniRoute cae, responde Qwen) y los errores explícitos, no la precisión semántica.
- Para escalar por calidad se necesita una señal de verificación: autoconsistencia entre dos
  modelos, un juez LLM o validaciones de dominio (p. ej. "pediste un almacén y devolví 200
  filas"). Se evalúa en la Semana 5, donde se puede medir su costo.
- **OmniRoute cachea respuestas:** al repetir prompts idénticos devolvió los mismos tokens con
  latencia de ~30 ms. Las latencias de OmniRoute en evals repetidas no son confiables; deben
  correrse con caché desactivada o con prompts nuevos.

## Actualización (Semana 5): verificador
`SQLVerifier` le pide al nivel barato que juzgue si el SQL y una muestra del resultado responden la
pregunta, y la cascada escala si no. En el split test (1 corrida):
- **96.7% de execution accuracy con 10% de escaladas y p50 de 6.6 s.** Minimax solo da 92.2%
  (media de 3 corridas) y Qwen solo 95.6% con 16.8 s de p50.
- El verificador detectó 2 errores semánticos que antes pasaban sin escalar, y dio 1 falsa alarma
  por no conocer el esquema.
- Su costo (una llamada extra por consulta) se suma al de cada consulta.

## Consecuencias
- (+) Tolerancia a fallas del proveedor gratuito sin cambiar el agente.
- (+) Métrica de escalamiento lista para las evals de costo/latencia.
- (−) El criterio "con filas" puede escalar respuestas vacías correctas (costo: latencia extra).
