"""Revision humana de ordenes de compra (segunda compuerta del HITL).

Uso:
    python -m scripts.po_review list [--status PENDING_APPROVAL]
    python -m scripts.po_review approve 12 --as gerente --by "Luis Perez" [--note "..."]
    python -m scripts.po_review reject 12 --as comprador --by "Ana Ruiz" --note "no urgente"

La DB rechaza aprobaciones sin autoridad suficiente (comprador < gerente < director) y
cualquier cambio a una orden ya decidida. Cada decision queda en audit_log.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.audit import get_audit_sink
from src.tools.purchase_orders import LEVELS, decide_purchase_order, list_purchase_orders


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="po_review")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status", default=None)
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("po_id", type=int)
        d.add_argument("--as", dest="level", required=True, choices=LEVELS)
        d.add_argument("--by", required=True, help="nombre de quien decide")
        d.add_argument("--note", default="")
    args = p.parse_args(argv)

    if args.cmd == "list":
        orders = list_purchase_orders(args.status)
        if not orders:
            print("sin ordenes")
        for o in orders:
            print(f"#{o['po_id']:<4} {o['status']:<17} {o['sku']}  qty={o['qty']:<6} "
                  f"${float(o['amount']):>13,.2f}  requiere={o['required_level']:<9} "
                  f"propuso={o['requested_by']} confirmo={o['confirmed_by']}"
                  + (f"  decidio={o['decided_by']} ({o['decided_level']})" if o["decided_by"] else ""))
        return 0

    approve = args.cmd == "approve"
    try:
        r = decide_purchase_order(args.po_id, approve, args.by, args.level, args.note)
    except Exception as e:
        error = str(e).splitlines()[0]
        get_audit_sink().log(args.by, "po_decision_denied",
                             {"po_id": args.po_id, "approve": approve, "level": args.level, "error": error})
        print(f"No se pudo {'aprobar' if approve else 'rechazar'}: {error}", file=sys.stderr)
        return 1
    get_audit_sink().log(args.by, "po_decided", {**r, "note": args.note})
    print(f"Orden #{r['po_id']} {r['status']} por {r['decided_by']} ({r['decided_level']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
