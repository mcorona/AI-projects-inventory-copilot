"""Lambda de arranque (se invoca una vez tras desplegar): esquema, roles, datos e indice RAG.

    aws lambda invoke --function-name <BootstrapFunction> out.json
"""
from __future__ import annotations

import json
import os


def handler(event=None, context=None) -> dict:
    import psycopg
    from psycopg import sql

    from src.api.aws_runtime import _secret, load_runtime_env

    load_runtime_env(admin=True, signing_key=False)
    admin = os.environ["PG_ADMIN_DSN"]
    steps = []
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('public.products') IS NOT NULL").fetchone()[0]
    from scripts.migrate import migration_files
    with psycopg.connect(admin) as conn:
        for path in migration_files(include_base=not exists):
            conn.execute(path.read_text(encoding="utf-8"))
            steps.append(f"migracion {path.name}")
        # las contrasenas locales de 01/02 se reemplazan por las de Secrets Manager (rotadas)
        import boto3
        client = boto3.client("secretsmanager")
        for role, arn in json.loads(os.environ["DB_ROLE_SECRETS"]).items():
            s = _secret(client, arn)
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(s["password"])))
        steps.append("contrasenas de roles desde Secrets Manager")
    if not exists or (event or {}).get("reload_data"):
        from scripts.generate_data import build_dataset, load
        load(admin, build_dataset())
        steps.append("datos sinteticos (seed 42)")
    from scripts.ingest_docs import load_chunks
    from src.llm import embed_model_id, get_embedder
    from src.rag.store import index_chunks
    chunks = load_chunks()
    embedder = get_embedder()
    vectors = embedder.embed([c.content for c in chunks])
    with psycopg.connect(admin) as conn:
        index_chunks(conn, chunks, vectors, embed_model_id(embedder))
    steps.append(f"{len(chunks)} chunks indexados con {embed_model_id(embedder)}")
    return {"ok": True, "steps": steps}
