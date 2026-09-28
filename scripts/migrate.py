"""Aplica las migraciones idempotentes de db/init/ (excepto 01, que crea el esquema base).

Uso:
    python -m scripts.migrate          # usa PG_ADMIN_DSN
"""
from __future__ import annotations

import os
from pathlib import Path

INIT_DIR = Path(__file__).resolve().parent.parent / "db" / "init"


def migration_files(init_dir: Path = INIT_DIR) -> list[Path]:
    return [p for p in sorted(init_dir.glob("*.sql")) if not p.name.startswith("01_")]


def main() -> None:
    import psycopg
    from dotenv import load_dotenv

    load_dotenv()
    with psycopg.connect(os.environ["PG_ADMIN_DSN"]) as conn:
        for path in migration_files():
            conn.execute(path.read_text(encoding="utf-8"))
            print(f"aplicada: {path.name}")


if __name__ == "__main__":
    main()
