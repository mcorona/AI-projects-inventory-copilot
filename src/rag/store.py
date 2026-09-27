"""Indice vectorial en pgvector (tabla doc_chunks): escritura con rol admin, lectura con copilot_ro."""
from __future__ import annotations

import json
import os
from typing import Callable

from src.guardrails.sql_guard import validate_sql
from src.llm import embed_model_id, get_embedder
from src.rag.chunking import Chunk

# Consulta FIJA (no la escribe el LLM). Se filtra por modelo de embeddings: vectores de
# modelos distintos (bge-m3 vs Titan) no son comparables aunque ambos midan 1024.
SEARCH_SQL = """
SELECT source, chunk_index, metadata->>'section' AS section, content,
       1 - (embedding <=> %(q)s::vector) AS score
FROM doc_chunks
WHERE metadata->>'embed_model' = %(model)s
ORDER BY embedding <=> %(q)s::vector
LIMIT {k}"""

MAX_K = 10
ParamExecutor = Callable[[str, dict], tuple[list[str], list[tuple]]]


def to_vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def search_sql(k: int) -> str:
    """SQL de busqueda con k acotado. Se valida con el guard (politica de tablas), pero se
    ejecuta la constante: sqlglot reescribe el operador de pgvector `<=>` como
    `IS NOT DISTINCT FROM` (semantica de MySQL), lo que romperia la busqueda sin error."""
    k = max(1, min(int(k), MAX_K))
    sql = SEARCH_SQL.format(k=k)
    validate_sql(sql, allowed_tables={"doc_chunks"})
    return sql


def search(query: str, k: int = 4, embedder=None, executor: ParamExecutor | None = None) -> list[dict]:
    embedder = embedder or get_embedder()
    if executor is None:
        from src.tools.sql_tool import execute_readonly
        dsn = os.environ["PG_DSN"]
        executor = lambda sql, params: execute_readonly(sql, dsn, params)  # noqa: E731
    vec = embedder.embed([query])[0]
    cols, rows = executor(search_sql(k), {"q": to_vector_literal(vec), "model": embed_model_id(embedder)})
    return [{**dict(zip(cols, r)), "score": round(float(r[cols.index("score")]), 4)} for r in rows]


def index_chunks(conn, chunks: list[Chunk], vectors: list[list[float]], model_id: str) -> int:
    """Reemplaza los chunks de los documentos dados para ese modelo (idempotente)."""
    sources = sorted({c.source for c in chunks})
    with conn.cursor() as cur:
        cur.execute("DELETE FROM doc_chunks WHERE metadata->>'embed_model' = %s AND source = ANY(%s)",
                    (model_id, sources))
        cur.executemany(
            "INSERT INTO doc_chunks (source, chunk_index, content, metadata, embedding) "
            "VALUES (%s, %s, %s, %s, %s::vector)",
            [(c.source, c.index, c.content,
              json.dumps({**c.metadata, "title": c.title, "section": c.section, "embed_model": model_id},
                         ensure_ascii=False),
              to_vector_literal(v)) for c, v in zip(chunks, vectors)])
    return len(chunks)
