# Resultados de evaluacion · corrida 20261001T053044Z

Split **test** · 3 repeticiones por proveedor · media (min–max) · juez: `lmstudio:qwen/qwen3.6-35b-a3b`

| Metrica | lmstudio · `qwen/qwen3.6-35b-a3b` (3x) | omniroute · `kr/minimax-m2.1` (3x) |
|---|---|---|
| SQL: execution accuracy | 98.9% (96.7%–100.0%) | 94.4% (93.3%–96.7%) |
| SQL: latencia p50 (s) | 14.8 (13.1–15.9) | 2.3 (2.0–2.5) |
| SQL: costo equivalente en Bedrock por consulta (USD) | 0.00120 (0.00120–0.00140) | 0.00150 |
| Agente: seleccion de tools | 100.0% | 93.3% (90.0%–100.0%) |
| Agente: exactitud de respuestas (hechos) | 100.0% | 100.0% |
| Agente: faithfulness (juez) | 96.7% (90.0%–100.0%) | 93.3% (90.0%–100.0%) |
| Agente: cifras respaldadas (determinista) | 86.7% (80.0%–90.0%) | 73.3% (70.0%–80.0%) |
| Agente: latencia p50 (s) | 6.5 (5.7–7.0) | 4.3 (3.6–5.4) |
| Agente: latencia p95 (s) | 13.7 (12.0–16.5) | 7.4 (6.3–9.3) |
| Agente: tokens por consulta | 3,919 (3,904–3,946) | 12,562 (11,836–13,422) |
| Agente: costo equivalente en Bedrock por consulta (USD) | 0.00090 | 0.00380 (0.00360–0.00410) |
| Aciertos de cache del gateway (debe ser 0) | 0.0% | 0.0% |
| Inyeccion indirecta: exito del ataque (none) | 29% | 14% |
| Inyeccion indirecta: exito del ataque (prompt) | 29% | 29% |
| Inyeccion indirecta: exito del ataque (full) | 14% | 14% |
| Inyeccion indirecta: OC no pedidas ejecutadas | 0 | 0 |

- **RAG** (8 preguntas, bge-m3): hit@1 87.5% · MRR 0.938
- **Guardrails (heuristicas)**: deteccion 80.0% de 30 ataques · falsos positivos 0.0% de 113 preguntas legitimas
- **Calibracion del juez** (29 casos etiquetados): acuerdo 96.5% · detecta infieles 93.8%
  - obvious (16): acuerdo 100.0% · detecta infieles 100.0% · precision 100.0%
  - subtle (13): acuerdo 92.3% · detecta infieles 87.5% · precision 100.0%

Huellas: `sql=afa8fac8a1d3a172`, `agent=9a25db7f710b896a`, `rag=01ebd76b050ac37f`, `guardrails=9fc40842e4075c8d`, `judge=0ffe2314ea41eb30`
