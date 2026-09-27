"""Ingesta de documentos para RAG: data/docs/*.md -> chunks -> embeddings -> doc_chunks.

Uso:
    python -m scripts.ingest_docs                # EMBED_PROVIDER del .env (default lmstudio)
    python -m scripts.ingest_docs --dry-run      # solo muestra los chunks
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from src.rag.chunking import Chunk, chunk_markdown

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
BATCH = 16


def load_chunks(docs_dir: Path = DOCS_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        chunks += chunk_markdown(path.read_text(encoding="utf-8"), source=path.name)
    return chunks


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default=None, help="proveedor de embeddings")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    chunks = load_chunks()
    print(f"{len(chunks)} chunks de {len({c.source for c in chunks})} documentos")
    if args.dry_run:
        for c in chunks:
            print(f"  {c.source:<28} #{c.index:<2} {len(c.content):>5} chars  {c.section}")
        return

    import psycopg

    from src.llm import embed_model_id, get_embedder
    from src.rag.store import index_chunks

    embedder = get_embedder(args.provider)
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), BATCH):
        vectors += embedder.embed([c.content for c in chunks[i:i + BATCH]])
    model_id = embed_model_id(embedder)
    with psycopg.connect(os.environ["PG_ADMIN_DSN"]) as conn:
        n = index_chunks(conn, chunks, vectors, model_id)
    print(f"Indexados {n} chunks con {model_id} (dim={len(vectors[0])})")


if __name__ == "__main__":
    main()
