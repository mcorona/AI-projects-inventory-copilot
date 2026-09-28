# ADR-008: Estrategia de evaluación, costo y gate de CI

**Estado:** aceptada · **Fecha:** 2026-09-28

## Contexto
Hasta la Semana 4 las evaluaciones tenían tres debilidades:
1. el prompt de SQL se ajustó mirando los fallos del mismo golden set que se reportaba;
2. cada número salía de una sola corrida, aunque una pregunta mueve 3–10 puntos entre corridas;
3. se medía si el agente elegía la tool correcta, pero no si su respuesta era correcta y fiel a
   lo que devolvieron las tools.

Además, OmniRoute cachea respuestas idénticas, y los costos no contaban los tokens del LLM
interno de `query_inventory`.

## Decisión

**Sets dev y test** (`evals/datasets/{suite}_{split}.jsonl`). Los sets anteriores pasan a *dev* y
se escriben sets *test* nuevos: 30 SQL, 10 agente y 8 RAG. Los test se escribieron y verificaron
contra la DB antes de correr ningún modelo sobre ellos, y nunca se usan para ajustar. Una prueba
unitaria verifica que dev y test no compartan preguntas.

**Calidad de la respuesta del agente, en tres niveles:**
- **Exactitud:** `expected_facts` son grupos de alternativas. Los números se comparan por valor
  (19,820 = 19820.00, pero 2 ≠ 2026 y 98 ≠ 98.5).
- **Faithfulness:** un juez LLM (Qwen local, fijo para todos los modelos) extrae afirmaciones y
  clasifica cada una como respaldada, no respaldada o contradicha por las salidas de las tools.
  **El juez se valida antes de usarlo** con `judge_calibration.jsonl`: 16 casos etiquetados con
  errores plantados (cifra, fecha, nivel de aprobación, extrapolación, contradicción) y casos
  fieles (aritmética derivada, abstención).
- **Cifras respaldadas (determinista):** cada número de la respuesta debe estar en el contexto o
  en la pregunta. Es barato y estricto; se usa como señal, no como veredicto.

**Repeticiones:** `evals/run_all.py` corre 3 repeticiones por proveedor y reporta media (mín–máx).
Cada repetición agrega un sufijo de sistema único para esquivar la caché de OmniRoute. El
adaptador lee el encabezado `x-omniroute-cache` y los reportes incluyen `cache_hit_rate`, que
debe ser 0.

**Costo y latencia:**
- `src/telemetry.py` registra por turno cada llamada al LLM (del agente y la interna de
  text-to-SQL), la latencia por etapa (LLM, tools, guardrails) y el costo.
- El costo tiene dos cifras: el real (LM Studio y OmniRoute gratuitos, $0) y el **equivalente en
  Bedrock**. Los precios están en `config/pricing.json`, tomados de la AWS Price List pública con
  fuente y fecha.
- MiniMax M2.1 existe en Bedrock (equivalente exacto). Qwen3.6-35B no, así que se estima con
  Qwen3 32B y se marca como aproximado.

**Router con verificador:** `SQLVerifier` le pide al nivel barato que juzgue si el SQL y una
muestra del resultado responden la pregunta. Si no, la cascada escala al nivel capaz. Su costo se
suma al de la consulta.

**Inyección indirecta con línea base honesta.** Tres configuraciones: `none` (sin regla en el
prompt ni pipeline), `prompt` y `full`. Siete escenarios, cinco diseñados para no activar las
heurísticas, entre ellos desinformación sin instrucciones.

**Gate de CI** (`evals/gate.py`), sin LLM ni DB:
- **Huellas:** hash de prompts, descripciones de tools, módulos de guardrails, corpus y datasets.
  Si el último `evals/results/latest.json` no coincide con el código actual, el CI falla: cambiar
  un prompt sin reevaluar rompe el build.
- **Umbrales** (`evals/gate.json`): pisos de regresión sobre la media (línea base menos un
  margen), no metas aspiracionales.
- **Pruebas de integración** con un contenedor pgvector en CI: la matriz de roles y triggers de
  ADR-006, las consultas de referencia de dev y test, y que `<=>` ordene por distancia coseno en
  Postgres real.

## Resultados (split test, 3 repeticiones, corrida `20260928T010227Z`)

| Métrica | Qwen3.6-35B (LM Studio) | minimax-m2.1 (OmniRoute) |
|---|---|---|
| SQL: execution accuracy | 95.6% (93.3–96.7) | 92.2% (86.7–96.7) |
| SQL: latencia p50 | 16.8 s | 2.3 s |
| Agente: selección de tools · exactitud · faithfulness | 100% · 100% · 100% | 100% · 100% · 100% |
| Agente: cifras respaldadas (determinista) | 90% | 80% |
| Agente: latencia p50 / p95 | 6.6 s / 16.4 s | 4.0 s / 8.8 s |
| Agente: tokens por consulta | 4,052 | 13,538 |
| Agente: costo equivalente en Bedrock por consulta | $0.0009 | $0.0041 |
| Aciertos de caché | 0% | 0% |

**Router con verificador** (1 corrida): 96.7% con 10% de escaladas y p50 de 6.6 s. Es más preciso
que minimax solo (92.2%) y más rápido que Qwen solo (16.8 s). El verificador detectó 2 errores
semánticos que la cascada original dejaba pasar: un periodo equivocado y una fórmula invertida.
También dio 1 falsa alarma, porque no recibe el esquema y no sabe que `sales_daily` es densa.

**Otros resultados:**
- **RAG test:** hit@1 87.5% (7/8), frente a 100% en dev.
- **Juez:** acuerdo de 100% con las 16 etiquetas.
- **Guardrails:** 80% de detección y 0 falsos positivos en 109 preguntas legítimas.

**Hallazgos:**
- **El sobreajuste a dev existe pero es pequeño:** Qwen SQL da 100% en dev y 95.6% en test. Su
  único fallo recurrente (t08) viene de que el prompt no lista los nombres de los almacenes. No se
  corrigió mirando test.
- **La variación entre corridas es real:** minimax SQL va de 86.7% a 96.7% con el mismo prompt.
  Una sola corrida no basta para comparar.
- **Los tokens de minimax están inflados por OmniRoute,** que agrega su propio contexto (~4.5k
  tokens por llamada). Su costo equivalente en Bedrock está **sobreestimado**: llamando a
  MiniMax M2.1 directo en Bedrock, el costo sería menor.
- **Un error del evaluador, no del modelo:** Qwen escribió "3.222 unidades" (punto como separador
  de miles) y el comparador lo marcó como fallo. Se corrigió la forma de calificar y se recalificó
  desde las respuestas guardadas (`run_all --rescore`), sin volver a llamar a los modelos.
- **Inyección indirecta:** ver la actualización de ADR-007. Hubo exfiltración del canario en las
  tres configuraciones.

## Consecuencias
- (+) El número que se reporta viene de datos que no se usaron para ajustar, con su variación
  entre corridas.
- (+) Un cambio de prompt no puede llegar a `main` sin evaluación: el gate lo detecta por la huella.
- (+) El costo por consulta es comparable entre proveedores y trasladable a Bedrock.
- (−) El gate depende de que alguien corra `run_all` en local (unos 60–90 min con Qwen): las
  evaluaciones con LLM no corren en GitHub Actions.
- (−) Los sets test son pequeños (10–30). Sirven para detectar regresiones, no para comparar
  modelos con precisión fina.
- (−) El juez está validado con 16 casos de errores claros; su desempeño en errores sutiles no
  está medido.
