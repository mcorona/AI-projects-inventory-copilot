# ADR-004: RAG sobre pgvector con chunks por sección e índice etiquetado por modelo

**Estado:** aceptada · **Fecha:** 2026-09-27

## Contexto
El agente debe responder sobre políticas internas (aprobaciones, reorden, devoluciones) citando
la fuente. El proyecto ya usa PostgreSQL + pgvector (equivalente local de Aurora PostgreSQL con
pgvector, uno de los vector stores de Bedrock Knowledge Bases).

## Decisión
- **Corpus:** 9 políticas sintéticas en `data/docs/*.md` con reglas verificables y consistentes
  con los datos (CEDIS, categorías, lead times).
- **Chunking por sección `##`:** en políticas, la sección es la unidad semántica. Secciones de más
  de ~400 tokens se parten por párrafos con traslape. Cada chunk lleva el encabezado de contexto
  `Documento: <título> > <sección>` para que fragmentos cortos ("Plazo: 30 días") conserven su
  origen. Las notas de control (`> ...`) no se indexan.
- **Embeddings:** bge-m3 (1024 dim, multilingüe) en LM Studio; Titan v2 (1024) en Bedrock.
  `EMBED_PROVIDER` es independiente del proveedor de chat (OmniRoute no tiene embeddings).
- **Índice etiquetado por modelo:** `metadata.embed_model` guarda el modelo y la búsqueda filtra
  por él. Vectores de modelos distintos no son comparables aunque tengan la misma dimensión.
- **Búsqueda:** similitud coseno (`<=>`, índice HNSW) con una consulta fija ejecutada por
  `copilot_ro`. El guard valida esa consulta con `allowed_tables={"doc_chunks"}`, pero se ejecuta
  la constante original: **sqlglot reescribe `<=>` como `IS NOT DISTINCT FROM`** (semántica de
  MySQL), lo que rompería la búsqueda sin error. Una prueba fija este comportamiento.
  `doc_chunks` sigue prohibida para el SQL que genera el LLM.

## Resultados
`evals/run_rag_eval.py` sobre 15 preguntas: hit@1 = 100%, hit@4 = 100%, MRR = 1.0 con bge-m3.
Es un techo esperable con 9 documentos temáticamente separados; no es evidencia de que el
diseño escale. Una pregunta (r15) tiene dos fuentes válidas porque el hecho aparece en dos
documentos; el golden set admite `expected_sources` múltiples.

## Consecuencias
- (+) Reindexar es idempotente por documento y modelo; cambiar a Titan no mezcla índices.
- (−) Sin búsqueda híbrida (BM25 + vector) ni re-ranking; se justifican cuando el corpus crezca
  y el eval muestre fallos de recuperación.
- (−) Faithfulness de la respuesta final (¿el agente dice lo que dice la fuente?) se mide en la Semana 5.
