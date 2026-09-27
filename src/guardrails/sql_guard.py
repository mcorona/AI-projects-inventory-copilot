"""SQL guard: solo permite un SELECT sobre tablas autorizadas, con LIMIT forzado."""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"suppliers", "products", "warehouses", "stock", "sales_daily"}
FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
                   exp.Alter, exp.Command, exp.Merge, exp.TruncateTable, exp.Grant)
MAX_ROWS = 200


class SQLRejected(ValueError):
    pass


def validate_sql(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Devuelve el SQL normalizado y seguro, o lanza SQLRejected con el motivo."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as e:
        raise SQLRejected(f"SQL invalido: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLRejected("Se permite exactamente una sentencia")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union)):
        raise SQLRejected(f"Solo se permite SELECT (recibido: {type(tree).__name__})")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLRejected(f"Operacion prohibida: {type(node).__name__}")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        if table.db and table.db.lower() not in ("public",):
            raise SQLRejected(f"Esquema no permitido: {table.db}")
        if name not in ALLOWED_TABLES:
            raise SQLRejected(f"Tabla no permitida: {name}")

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
    else:
        try:
            n = int(limit.expression.name)
            if n > max_rows:
                tree = tree.limit(max_rows)
        except (AttributeError, ValueError):
            tree = tree.limit(max_rows)

    return tree.sql(dialect="postgres")
