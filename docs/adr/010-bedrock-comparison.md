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

### Nueva medición (v1.1.2, corrida `20261001T225947Z`)
Con el acceso a Bedrock restablecido (2026-10-01), los cinco proveedores se midieron con el mismo
código: prompt SQL con nombres de almacenes, reintento ante respuesta vacía y vista previa de OC que
calcula la alternativa sobre el tope.

| Métrica | Qwen3.6 local | minimax OmniRoute | Haiku 4.5 | MiniMax M2.1 | Qwen3 32B |
|---|---|---|---|---|---|
| SQL execution accuracy | 100% | 93.3% | 86.7% | 93.3% | 86.7% |
| Agente: tools · exactitud · faithfulness | 100 · 100 · 100% | 97 · 96 · 97% | 90 · 100 · 100% | 100 · 100 · 100% | 70 · 78 · 80% |
| Agente: latencia p50 | 6.4 s | 4.1 s | 2.6 s | 2.9 s | 1.5 s |
| Agente: costo por consulta (Bedrock) | $0.0009 | $0.0037 | $0.0059 | $0.0013 | $0.0005 |

- La recomendación no cambia: **MiniMax M2.1** sigue en 100% en el agente, con el segundo costo más
  bajo. Haiku y Qwen3 subieron en SQL (83.3% → 86.7%).
- El caso de la OC sobre el tope (at08) ahora da el monto correcto en los 5 proveedores.
- Bedrock sigue con 1 repetición: las diferencias de un caso (3.3 puntos en SQL, 10 en tools) están
  dentro del ruido.
- Claude Opus 4.8 aparece en el catálogo de la cuenta, pero Converse responde *"not available for this
  account"*; no se incluyó.

### Techo de calidad: Claude Sonnet 4.6 y Opus 4.6 (v1.1.3, corrida `20261002T042745Z`)
Se agregaron los Claude más capaces que la cuenta puede usar. Opus 4.7, 4.8 y 5 aparecen en el
catálogo, pero responden *"not available for this account"*.
- Precios: AWS Price List (`AmazonBedrockFoundationModels`, publicada el 2026-09-30), regional para
  perfiles `us.*`.
  - Sonnet 4.6: $3.30 / $16.50 por 1M de tokens.
  - Opus 4.6: $5.50 / $27.50 por 1M de tokens.

| Métrica | MiniMax M2.1 | Sonnet 4.6 | Opus 4.6 |
|---|---|---|---|
| SQL execution accuracy | 93.3% | 96.7% | **100%** |
| Agente: tools · exactitud · faithfulness | 100 · 100 · 100% | 100 · 100 · 90% | 100 · 100 · 100% |
| Agente: latencia p50 | 2.9 s | 4.9 s | 6.5 s |
| Agente: costo por consulta | **$0.0013** | $0.0210 | $0.0347 |
| Inyección con defensas completas | 14% | 14% | 14% |

- **La recomendación se mantiene.** Opus 4.6 solo supera a MiniMax M2.1 en SQL: 2 preguntas de 30,
  con 1 repetición. En el agente empatan, y Opus cuesta 27 veces más por consulta y es más del doble
  de lento.
- **Si la exactitud de SQL fuera crítica,** la opción sería el router en cascada (ADR-005): MiniMax
  primero y escalar a un Claude solo cuando el verificador rechaza. Así se paga el modelo caro solo
  en los casos difíciles.
- **Opus 4.6 tampoco resiste la desinformación en los datos** (el único ataque que sobrevive a las
  defensas). Confirma que esa mitigación tiene que estar en los datos y en los guardrails, no en el
  modelo.
- **Costo real de esta corrida:** ~US$2.10 (Sonnet ~$0.80, Opus ~$1.30), cubiertos por créditos.

## Resultados de Bedrock Guardrails (en vivo)
Entrada: 30 ataques y 110 preguntas legítimas. Grounding: 16 casos etiquetados.

| Configuración | Detección | Falsos positivos | Latencia p50 |
|---|---|---|---|
| Heurísticas locales | 80.0% | 0% | ~0 ms |
| Bedrock CLASSIC + temas denegados (v1) | 36.7% | 5.5% | 320 ms |
| Bedrock STANDARD + temas denegados (v2) | 76.7% | 13.6% | 596 ms |
| **Bedrock STANDARD sin temas (v3, la recomendada)** | 70.0% | 0.9% | 593 ms |
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
- **Aplicado en `infra/stacks/app_stack.py`:** `BEDROCK_CHAT_MODEL=minimax.minimax-m2.1`. El IAM
  queda acotado a dos ARNs de `foundation-model` en la región del stack; ya no incluye el perfil
  `us.` ni sus 3 regiones. Una prueba de infraestructura lo fija. El modelo ya estaba evaluado en
  Bedrock (tabla de arriba, 1 repetición); el despliegue de la Lambda sigue sin probarse en vivo.

## Limitaciones
- Los modelos de Bedrock se corrieron con **1 repetición**. Con 30 preguntas, una sola mueve
  3.3 puntos, y la variación observada en local es de ±3–7 puntos.
- La evaluación de inyección es de 1 corrida por modelo con 7 escenarios: un escenario = 14 puntos.
- El juez es Qwen local y podría favorecer respuestas de estilo similar. Está validado solo con
  16 casos de errores claros.
