# Resultados de evaluacion · corrida 20260930T004357Z

Split **test** · 3 repeticiones por proveedor · media (min–max) · juez: `lmstudio:qwen/qwen3.6-35b-a3b`

| Metrica | lmstudio · `qwen/qwen3.6-35b-a3b` (3x) | omniroute · `kr/minimax-m2.1` (3x) |
|---|---|---|
| SQL: execution accuracy | 100.0% | 94.4% (93.3%–96.7%) |
| SQL: latencia p50 (s) | 15.7 (14.5–16.5) | 2.4 (2.2–2.6) |
| SQL: costo equivalente en Bedrock por consulta (USD) | 0.00120 | 0.00150 |
| Agente: seleccion de tools | 100.0% | 93.3% (90.0%–100.0%) |
| Agente: exactitud de respuestas (hechos) | 92.6% (88.9%–100.0%) | 96.3% (88.9%–100.0%) |
| Agente: faithfulness (juez) | 96.3% (88.9%–100.0%) | 100.0% |
| Agente: cifras respaldadas (determinista) | 85.6% (77.8%–90.0%) | 76.7% (70.0%–80.0%) |
| Agente: latencia p50 (s) | 7.6 (7.2–7.8) | 3.8 (3.7–3.9) |
| Agente: latencia p95 (s) | 16.3 (12.4–21.9) | 6.6 (6.3–7.0) |
| Agente: tokens por consulta | 4,263 (4,076–4,617) | 12,731 (11,755–13,481) |
| Agente: costo equivalente en Bedrock por consulta (USD) | 0.00100 (0.00090–0.00110) | 0.00390 (0.00360–0.00410) |
| Aciertos de cache del gateway (debe ser 0) | 0.0% | 0.0% |
| Inyeccion indirecta: exito del ataque (none) | 14% | 29% |
| Inyeccion indirecta: exito del ataque (prompt) | 14% | 14% |
| Inyeccion indirecta: exito del ataque (full) | 0% | 14% |
| Inyeccion indirecta: OC no pedidas ejecutadas | 0 | 0 |

- **RAG** (8 preguntas, bge-m3): hit@1 87.5% · MRR 0.938
- **Guardrails (heuristicas)**: deteccion 80.0% de 30 ataques · falsos positivos 0.0% de 113 preguntas legitimas
- **Calibracion del juez** (29 casos etiquetados): acuerdo 96.5% · detecta infieles 93.8%
  - obvious (16): acuerdo 100.0% · detecta infieles 100.0% · precision 100.0%
  - subtle (13): acuerdo 92.3% · detecta infieles 87.5% · precision 100.0%

Huellas: `sql=21d9edafc4bbf66b`, `agent=f97a76f69fab5e4e`, `rag=0bdd2d457d1d0aff`, `guardrails=1309f5d3301c8244`, `judge=0ffe2314ea41eb30`
