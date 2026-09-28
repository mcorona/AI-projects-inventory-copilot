# Resultados de evaluacion · corrida 20260928T023236Z

Split **test** · 3 repeticiones por proveedor · media (min–max) · juez: `lmstudio:qwen/qwen3.6-35b-a3b`

| Metrica | lmstudio · `qwen/qwen3.6-35b-a3b` (3x) | omniroute · `kr/minimax-m2.1` (3x) | bedrock-haiku · `us.anthropic.claude-haiku-4-5-20251001-v1:0` (1x) | bedrock-minimax · `minimax.minimax-m2.1` (1x) | bedrock-qwen3 · `qwen.qwen3-32b-v1:0` (1x) |
|---|---|---|---|---|---|
| SQL: execution accuracy | 96.7% | 93.3% (90.0%–96.7%) | 83.3% | 93.3% | 83.3% |
| SQL: latencia p50 (s) | 16.2 (15.4–16.6) | 2.5 (2.1–3.0) | 1.0 | 2.0 | 1.0 |
| SQL: costo equivalente en Bedrock por consulta (USD) | 0.00120 | 0.00140 | 0.00130 | 0.00050 | 0.00010 |
| Agente: seleccion de tools | 100.0% | 96.7% (90.0%–100.0%) | 90.0% | 100.0% | 70.0% |
| Agente: exactitud de respuestas (hechos) | 100.0% | 100.0% | 88.9% | 100.0% | 66.7% |
| Agente: faithfulness (juez) | 100.0% | 100.0% | 100.0% | 100.0% | 80.0% |
| Agente: cifras respaldadas (determinista) | 90.0% | 76.7% (70.0%–80.0%) | 70.0% | 70.0% | 90.0% |
| Agente: latencia p50 (s) | 6.8 (6.5–7.2) | 3.9 (3.9–4.0) | 2.8 | 2.8 | 1.7 |
| Agente: latencia p95 (s) | 15.3 (12.3–18.9) | 7.9 (6.6–10.1) | 4.0 | 4.7 | 2.7 |
| Agente: tokens por consulta | 4,088 (4,049–4,124) | 13,603 (13,408–13,929) | 4,247 | 3,209 | 2,836 |
| Agente: costo equivalente en Bedrock por consulta (USD) | 0.00090 | 0.00410 (0.00410–0.00430) | 0.00560 | 0.00120 | 0.00050 |
| Aciertos de cache del gateway (debe ser 0) | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Inyeccion indirecta: exito del ataque (none) | 14% | 29% | 14% | 29% | 43% |
| Inyeccion indirecta: exito del ataque (prompt) | 14% | 29% | 14% | 29% | 29% |
| Inyeccion indirecta: exito del ataque (full) | 0% | 14% | 14% | 14% | 14% |
| Inyeccion indirecta: OC no pedidas ejecutadas | 0 | 0 | 0 | 0 | 0 |

- **RAG** (8 preguntas, bge-m3): hit@1 87.5% · MRR 0.938
- **Guardrails (heuristicas)**: deteccion 80.0% de 30 ataques · falsos positivos 0.0% de 110 preguntas legitimas
- **Calibracion del juez** (16 casos etiquetados): acuerdo 100.0% · detecta infieles 100.0%

Huellas: `sql=6b3eb904e7c70804`, `agent=e977593c23fdd042`, `rag=0bdd2d457d1d0aff`, `guardrails=1309f5d3301c8244`, `judge=4ee3a39b96595155`
