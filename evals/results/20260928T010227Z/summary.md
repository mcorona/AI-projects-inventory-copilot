# Resultados de evaluacion · corrida 20260928T010227Z

Split **test** · 3 repeticiones por proveedor · media (min–max) · juez: `lmstudio:qwen/qwen3.6-35b-a3b`

> Recalificado el 2026-09-28 desde las respuestas guardadas (se corrigio el evaluador, no las respuestas de los modelos).

| Metrica | lmstudio · `qwen/qwen3.6-35b-a3b` | omniroute · `kr/minimax-m2.1` |
|---|---|---|
| SQL: execution accuracy | 95.6% (93.3%–96.7%) | 92.2% (86.7%–96.7%) |
| SQL: latencia p50 (s) | 16.8 (16.4–17.6) | 2.3 (2.3–2.5) |
| SQL: costo equivalente en Bedrock por consulta (USD) | 0.00130 (0.00120–0.00130) | 0.00150 (0.00140–0.00150) |
| Agente: seleccion de tools | 100.0% | 100.0% |
| Agente: exactitud de respuestas (hechos) | 100.0% | 100.0% |
| Agente: faithfulness (juez) | 100.0% | 100.0% |
| Agente: cifras respaldadas (determinista) | 90.0% | 80.0% |
| Agente: latencia p50 (s) | 6.6 (6.1–7.3) | 4.0 (3.9–4.1) |
| Agente: latencia p95 (s) | 16.4 (12.7–22.7) | 8.8 (7.3–11.4) |
| Agente: tokens por consulta | 4,052 (3,872–4,397) | 13,538 (13,412–13,619) |
| Agente: costo equivalente en Bedrock por consulta (USD) | 0.00090 (0.00090–0.00100) | 0.00410 (0.00410–0.00420) |
| Aciertos de cache del gateway (debe ser 0) | 0.0% | 0.0% |
| Inyeccion indirecta: exito del ataque (none) | 29% | 14% |
| Inyeccion indirecta: exito del ataque (prompt) | 14% | 14% |
| Inyeccion indirecta: exito del ataque (full) | 29% | 29% |
| Inyeccion indirecta: OC no pedidas ejecutadas | 0 | 0 |

- **RAG** (8 preguntas, bge-m3): hit@1 87.5% · MRR 0.938
- **Guardrails (heuristicas)**: deteccion 80.0% de 30 ataques · falsos positivos 0.0% de 109 preguntas legitimas
- **Calibracion del juez** (16 casos etiquetados): acuerdo 100.0% · detecta infieles 100.0%

Huellas: `sql=1c44b4c18ff66a8d`, `agent=e977593c23fdd042`, `rag=0bdd2d457d1d0aff`, `guardrails=8308f02fb5743885`, `judge=4ee3a39b96595155`
