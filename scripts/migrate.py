"""Aplica las migraciones idempotentes de db/init/ (excepto 01, que crea el esquema base).

Uso:
    python -m scripts.migrate                  # usa PG_ADMIN_DSN
    python -m scripts.migrate --include-base   # DB vacia (p. ej. CI): aplica tambien 01_schema.sql
"""
from __future__ import annotations

import os
from pathlib import Path

INIT_DIR = Path(__file__).resolve().parent.parent / "db" / "init"


def migration_files(init_dir: Path = INIT_DIR, include_base: bool = False) -> list[Path]:
    return [p for p in sorted(init_dir.glob("*.sql")) if include_base or not p.name.startswith("01_")]


def main() -> None:
    import argparse

    import psycopg
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--include-base", action="store_true")
    args = p.parse_args()
    with psycopg.connect(os.environ["PG_ADMIN_DSN"]) as conn:
        for path in migration_files(include_base=args.include_base):
            conn.execute(path.read_text(encoding="utf-8"))
            print(f"aplicada: {path.name}")


if __name__ == "__main__":
    main()
