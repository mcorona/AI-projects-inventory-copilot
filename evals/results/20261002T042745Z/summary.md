# Resultados de evaluacion · corrida 20261002T042745Z

Split **test** · 3 repeticiones por proveedor · media (min–max) · juez: `lmstudio:qwen/qwen3.6-35b-a3b`

| Metrica | lmstudio · `qwen/qwen3.6-35b-a3b` (3x) | omniroute · `kr/minimax-m2.1` (3x) | bedrock-sonnet46 · `us.anthropic.claude-sonnet-4-6` (1x) | bedrock-opus46 · `us.anthropic.claude-opus-4-6-v1` (1x) |
|---|---|---|---|---|
| SQL: execution accuracy | 98.9% (96.7%–100.0%) | 92.2% (90.0%–96.7%) | 96.7% | 100.0% |
| SQL: latencia p50 (s) | 15.3 (14.2–16.5) | 2.4 (2.3–2.5) | 1.6 | 2.0 |
| SQL: costo equivalente en Bedrock por consulta (USD) | 0.00130 (0.00120–0.00140) | 0.00150 | 0.00450 | 0.00730 |
| Agente: seleccion de tools | 100.0% | 90.0% | 100.0% | 100.0% |
| Agente: exactitud de respuestas (hechos) | 100.0% | 96.3% (88.9%–100.0%) | 100.0% | 100.0% |
| Agente: faithfulness (juez) | 96.7% (90.0%–100.0%) | 96.7% (90.0%–100.0%) | 90.0% | 100.0% |
| Agente: cifras respaldadas (determinista) | 90.0% | 73.3% (70.0%–80.0%) | 80.0% | 80.0% |
| Agente: latencia p50 (s) | 7.1 (6.8–7.4) | 4.3 (4.0–4.7) | 4.9 | 6.5 |
| Agente: latencia p95 (s) | 13.1 (12.4–13.5) | 10.6 (7.9–14.4) | 9.2 | 12.6 |
| Agente: tokens por consulta | 4,568 (4,478–4,638) | 13,291 (12,654–13,630) | 5,098 | 5,088 |
| Agente: costo equivalente en Bedrock por consulta (USD) | 0.00100 | 0.00410 (0.00390–0.00420) | 0.02100 | 0.03470 |
| Aciertos de cache del gateway (debe ser 0) | 0.0% | 0.0% | 0.0% | 0.0% |
| Inyeccion indirecta: exito del ataque (none) | 29% | 29% | 14% | 14% |
| Inyeccion indirecta: exito del ataque (prompt) | 14% | 29% | 14% | 14% |
| Inyeccion indirecta: exito del ataque (full) | 14% | 14% | 14% | 14% |
| Inyeccion indirecta: OC no pedidas ejecutadas | 0 | 0 | 0 | 0 |

- **RAG** (8 preguntas, bge-m3): hit@1 87.5% · MRR 0.938
- **Guardrails (heuristicas)**: deteccion 80.0% de 30 ataques · falsos positivos 0.0% de 115 preguntas legitimas
- **Calibracion del juez** (29 casos etiquetados): acuerdo 96.5% · detecta infieles 93.8%
  - obvious (16): acuerdo 100.0% · detecta infieles 100.0% · precision 100.0%
  - subtle (13): acuerdo 92.3% · detecta infieles 87.5% · precision 100.0%

Huellas: `sql=afa8fac8a1d3a172`, `agent=074a9fd7f397a87e`, `rag=01ebd76b050ac37f`, `guardrails=d058b92d69c2701c`, `judge=0ffe2314ea41eb30`
