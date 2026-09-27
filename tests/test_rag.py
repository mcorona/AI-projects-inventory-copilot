import pytest

from scripts.ingest_docs import load_chunks
from src.guardrails.sql_guard import SQLRejected, validate_sql
from src.rag.chunking import chunk_markdown
from src.rag.store import MAX_K, index_chunks, search, search_sql, to_vector_literal

DOC = """# Politica X

> Documento sintetico. Version 1.

## Alcance
Aplica a todo.

## Reglas
Regla uno.

Regla dos.
"""


def test_chunks_by_section_with_context_header():
    chunks = chunk_markdown(DOC, "x.md")
    assert [c.section for c in chunks] == ["Alcance", "Reglas"]  # la nota > no se indexa
    assert chunks[1].content.startswith("Documento: Politica X > Reglas\n\n")
    assert [c.index for c in chunks] == [0, 1] and chunks[0].title == "Politica X"


def test_preamble_with_real_content_is_kept():
    chunks = chunk_markdown("# T\n\nContexto importante.\n\n## A\nTexto.", "t.md")
    assert chunks[0].section == "Introduccion" and "Contexto importante" in chunks[0].content


def test_long_section_is_split_with_overlap():
    body = "\n\n".join(f"Parrafo {i} " + "x" * 90 for i in range(10))
    chunks = chunk_markdown(f"# T\n\n## Larga\n{body}", "t.md", max_chars=300, overlap=50)
    assert len(chunks) > 1
    assert all(c.section == "Larga" for c in chunks)
    # el traslape repite el final del chunk anterior al inicio del siguiente
    prev_tail = chunks[0].content[-30:]
    assert prev_tail in chunks[1].content


def test_corpus_loads_every_document():
    chunks = load_chunks()
    sources = {c.source for c in chunks}
    assert len(sources) == 9 and all(c.content.strip() for c in chunks)
    assert all("Documento sintético" not in c.content for c in chunks)


def test_search_sql_keeps_pgvector_operator_and_clamps_k():
    sql = search_sql(99)
    assert "<=>" in sql and f"LIMIT {MAX_K}" in sql
    assert "LIMIT 1" in search_sql(0)


def test_sqlglot_rewrites_pgvector_operator():
    """Documenta por que search() ejecuta la constante y no el SQL normalizado del guard."""
    normalized = validate_sql(search_sql(4), allowed_tables={"doc_chunks"})
    assert "<=>" not in normalized and "IS NOT DISTINCT FROM" in normalized


def test_doc_chunks_stays_blocked_for_llm_sql():
    with pytest.raises(SQLRejected):
        validate_sql("SELECT content FROM doc_chunks")


class FakeEmbedder:
    name, embed_model = "lmstudio", "bge"

    def embed(self, texts):
        return [[0.5, -0.25] for _ in texts]


def test_search_passes_vector_and_model_filter():
    seen = {}

    def executor(sql, params):
        seen.update(sql=sql, params=params)
        return (["source", "chunk_index", "section", "content", "score"],
                [("a.md", 0, "S", "texto", 0.912345)])

    hits = search("pregunta", k=3, embedder=FakeEmbedder(), executor=executor)
    assert seen["params"] == {"q": "[0.5,-0.25]", "model": "lmstudio:bge"}
    assert "<=>" in seen["sql"] and "LIMIT 3" in seen["sql"]
    assert hits == [{"source": "a.md", "chunk_index": 0, "section": "S", "content": "texto", "score": 0.9123}]


def test_index_chunks_replaces_by_model_and_source():
    class Cur:
        def __init__(self):
            self.ops = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def execute(self, sql, params):
            self.ops.append(("execute", sql, params))

        def executemany(self, sql, rows):
            self.ops.append(("many", sql, rows))

    cur = Cur()
    conn = type("Conn", (), {"cursor": lambda self: cur})()
    chunks = chunk_markdown(DOC, "x.md")
    n = index_chunks(conn, chunks, [[0.1], [0.2]], "lmstudio:bge")
    assert n == 2
    assert cur.ops[0][0] == "execute" and cur.ops[0][2] == ("lmstudio:bge", ["x.md"])
    rows = cur.ops[1][2]
    assert rows[0][4] == "[0.1]" and '"embed_model": "lmstudio:bge"' in rows[0][3]


def test_vector_literal():
    assert to_vector_literal([1.0, 0.123456789]) == "[1,0.1234568]"
