# Resultados de evaluacion · corrida 20261002T182158Z

Split **test** · 3 repeticiones por proveedor · media (min–max) · juez: `lmstudio:qwen/qwen3.6-35b-a3b`

| Metrica | lmstudio · `qwen/qwen3.6-35b-a3b` (3x) | omniroute · `kr/minimax-m2.1` (3x) |
|---|---|---|
| SQL: execution accuracy | 97.8% (96.7%–100.0%) | 91.1% (86.7%–100.0%) |
| SQL: latencia p50 (s) | 14.7 (13.8–15.3) | 2.3 (2.0–2.5) |
| SQL: costo equivalente en Bedrock por consulta (USD) | 0.00120 (0.00120–0.00130) | 0.00150 |
| Agente: seleccion de tools | 100.0% | 100.0% |
| Agente: exactitud de respuestas (hechos) | 100.0% | 100.0% |
| Agente: faithfulness (juez) | 100.0% | 100.0% |
| Agente: cifras respaldadas (determinista) | 90.0% | 83.3% (80.0%–90.0%) |
| Agente: latencia p50 (s) | 6.8 (5.4–7.7) | 4.4 (4.2–4.6) |
| Agente: latencia p95 (s) | 11.6 (11.0–12.4) | 11.2 (8.4–12.9) |
| Agente: tokens por consulta | 4,583 (4,220–4,861) | 13,725 (13,460–14,160) |
| Agente: costo equivalente en Bedrock por consulta (USD) | 0.00100 (0.00090–0.00100) | 0.00420 (0.00410–0.00430) |
| Aciertos de cache del gateway (debe ser 0) | 0.0% | 0.0% |
| Inyeccion indirecta: exito del ataque (none) | 29% | 14% |
| Inyeccion indirecta: exito del ataque (prompt) | 29% | 14% |
| Inyeccion indirecta: exito del ataque (full) | 14% | 14% |
| Inyeccion indirecta: OC no pedidas ejecutadas | 0 | 0 |

- **RAG** (8 preguntas, bge-m3): hit@1 87.5% · MRR 0.938
- **Guardrails (heuristicas)**: deteccion 80.0% de 30 ataques · falsos positivos 0.0% de 116 preguntas legitimas
- **Calibracion del juez** (29 casos etiquetados): acuerdo 96.5% · detecta infieles 93.8%
  - obvious (16): acuerdo 100.0% · detecta infieles 100.0% · precision 100.0%
  - subtle (13): acuerdo 92.3% · detecta infieles 87.5% · precision 100.0%

Huellas: `sql=afa8fac8a1d3a172`, `agent=b6048051bc0bfd1e`, `rag=01ebd76b050ac37f`, `guardrails=be7cc37068ffdf61`, `judge=0ffe2314ea41eb30`
