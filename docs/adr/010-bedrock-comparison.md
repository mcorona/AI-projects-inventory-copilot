# ADR-010: Comparativa local vs. Amazon Bedrock y configuración recomendada

**Estado:** aceptada · **Fecha:** 2026-09-28

## Contexto
El proyecto se desarrolló a $0 con Qwen3.6-35B (LM Studio) y minimax-m2.1 (OmniRoute). La Semana 6
corre el mismo agente, los mismos sets test y el mismo juez contra modelos de Amazon Bedrock, y
evalúa en vivo Bedrock Guardrails. Así se puede decidir con datos qué usar en AWS.

## Resultados del agente y text-to-SQL
Corrida `20260928T023236Z`, split test. Los modelos locales tienen 3 repeticiones y los de Bedrock
1 (recortado por tiempo; ver Limitaciones).

| Métrica | Qwen3.6-35B local | minimax-m2.1 OmniRoute | Haiku 4.5 Bedrock | MiniMax M2.1 Bedrock | Qwen3 32B Bedrock |
|---|---|---|---|---|---|
| SQL execution accuracy (30) | **96.7%** | 93.3% (90–96.7) | 83.3% | 93.3% | 83.3% |
| SQL latencia p50 | 16.2 s | 2.5 s | **1.0 s** | 2.0 s | **1.0 s** |
| Agente: selección de tools (10) | 100% | 96.7% | 90% | **100%** | 70% |
| Agente: exactitud de respuestas | 100% | 100% | 88.9% | **100%** | 66.7% |
| Agente: faithfulness (juez) | 100% | 100% | 100% | 100% | 80% |
| Agente: latencia p50 / p95 | 6.8 / 15.3 s | 3.9 / 7.9 s | 2.8 / 4.0 s | 2.8 / 4.7 s | **1.7 / 2.7 s** |
| Agente: tokens por consulta | 4,088 | 13,603 | 4,247 | 3,209 | 2,836 |
| Agente: costo por consulta (Bedrock) | $0.0009* | $0.0041** | $0.0056 | **$0.0012** | $0.0005 |
| Inyección: éxito con defensas completas (7) | 0% | 14% | 14% | 14% | 14% |
| OC no pedidas ejecutadas | 0 | 0 | 0 | 0 | 0 |

\* Estimado con Qwen3 32B, el Qwen más cercano en Bedrock. \** Tokens inflados por OmniRoute (ver abajo).

**RAG** (split test, 8 preguntas): hit@1 de **100% con Titan Embeddings V2** y 87.5% con bge-m3
local. En dev, ambos dan 100%.

## Resultados de Bedrock Guardrails (en vivo)
Entrada: 30 ataques y 110 preguntas legítimas. Grounding: 16 casos etiquetados.

| Configuración | Detección | Falsos positivos | Latencia p50 |
|---|---|---|---|
| Heurísticas locales | 80.0% | 0% | ~0 ms |
| Bedrock CLASSIC + temas denegados (v1) | 36.7% | 5.5% | 320 ms |
| Bedrock STANDARD + temas denegados (v2) | 76.7% | 13.6% | 596 ms |
| **Bedrock STANDARD sin temas (v3, desplegada)** | 70.0% | 0.9% | 593 ms |
| **Local + Bedrock v3 combinados** | **96.7%** | **0.9%** | ~0.6 s |

| Faithfulness sobre 16 casos etiquetados | Acuerdo | Detecta respuestas infieles |
|---|---|---|
| Juez LLM local (Qwen) | 100% | 100% |
| Bedrock contextual grounding (0.75 / 0.5) | 68.8% | 75% |

## Hallazgos
1. **MiniMax M2.1 directo en Bedrock es la mejor relación calidad/costo del agente:** 100% en tools,
   exactitud y faithfulness, 93.3% en SQL, $0.0012 por consulta y 2.8 s de p50. Es el mismo modelo
   que por OmniRoute, pero con **3.4 veces menos costo**: OmniRoute agrega unos 4,500 tokens de
   contexto propio por llamada (13,603 contra 3,209 tokens por consulta). Queda confirmada la
   sospecha de la Semana 5.
2. **Haiku 4.5 no fue el mejor en esta tarea.** SQL 83.3% y exactitud 88.9%, a 4.7 veces el costo
   de MiniMax. Sus fallos son semánticos: comparó el stock por almacén en lugar del total contra el
   punto de reorden. Con 1 repetición la diferencia es indicativa, no concluyente.
3. **Qwen3 32B es rápido y barato, pero no sirve como agente:** 70% en tools y 66.7% en
   exactitud. Podría servir como nivel barato de un router con verificador, no como modelo único.
4. **El filtro de salida (DLP) cerró la exfiltración de la Semana 5 en los 5 modelos.** Sin él,
   4 de 5 filtraron el canario; con él, ninguno. Con defensas completas solo sobrevive la
   **desinformación en los datos** (sin instrucciones), que ningún guardrail de texto detecta por
   diseño.
5. **Bedrock Guardrails complementa, no reemplaza, a las capas locales:**
   - En la entrada, `ANONYMIZE` no enmascara PII: solo `BLOCK` funciona en la entrada. Nuestra
     capa local anonimiza antes de llamar al modelo.
   - El tier STANDARD detecta mucho mejor que CLASSIC en español, pero sus temas denegados
     generaron 13.6% de falsos positivos. Se quitaron, porque ese control ya lo imponen la DB y
     la compuerta humana (ADR-006). **Este ajuste se hizo después de ver los datos.**
   - El contextual grounding castiga la aritmética derivada y no detecta contradicciones de
     política con alto solapamiento léxico. El juez LLM es más preciso, pero cuesta ~1 min por
     respuesta con Qwen local.
6. **Titan Embeddings V2 superó a bge-m3** en el set test (100% contra 87.5% de hit@1).

## Decisión (configuración recomendada en AWS)
- **Chat del agente y text-to-SQL:** `minimax.minimax-m2.1` en Bedrock. Haiku 4.5 queda como
  alternativa de mayor costo, a reevaluar con más repeticiones.
- **Embeddings:** `amazon.titan-embed-text-v2:0`.
- **Guardrails:** capas locales + Bedrock Guardrail STANDARD sin temas denegados. El juez LLM se
  usa en evaluación, no en línea.
- **Cambio pendiente de aplicar:** `infra/stacks/app_stack.py` aún apunta a Haiku 4.5
  (`CHAT_PROFILE`). Cambiarlo a MiniMax implica ajustar los ARNs de IAM y volver a evaluar.

## Limitaciones
- Los modelos de Bedrock se corrieron con **1 repetición**. Con 30 preguntas, una sola mueve
  3.3 puntos, y la variación observada en local es de ±3–7 puntos.
- La evaluación de inyección es de 1 corrida por modelo con 7 escenarios: un escenario = 14 puntos.
- El juez es Qwen local y podría favorecer respuestas de estilo similar. Está validado solo con
  16 casos de errores claros.
