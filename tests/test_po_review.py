import scripts.po_review as cli


def test_list_and_decide(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(cli, "get_audit_sink", lambda: type("S", (), {"log": lambda s, *a: events.append(a)})())
    monkeypatch.setattr(cli, "list_purchase_orders", lambda status: [
        {"po_id": 1, "status": "PENDING_APPROVAL", "sku": "SKU-0009", "qty": 1500, "amount": 11655,
         "required_level": "comprador", "requested_by": "copilot:Ana", "confirmed_by": "Ana",
         "decided_by": None, "decided_level": None}])
    assert cli.main(["list"]) == 0 and "SKU-0009" in capsys.readouterr().out

    monkeypatch.setattr(cli, "decide_purchase_order", lambda *a: {
        "po_id": 1, "status": "APPROVED", "decided_by": "Luis", "decided_level": "gerente"})
    assert cli.main(["approve", "1", "--as", "gerente", "--by", "Luis"]) == 0
    assert events[-1][1] == "po_decided"

    def deny(*a):
        raise RuntimeError("Nivel insuficiente: la orden 1 requiere director\nCONTEXT: ...")
    monkeypatch.setattr(cli, "decide_purchase_order", deny)
    assert cli.main(["approve", "1", "--as", "gerente", "--by", "Luis"]) == 1
    assert events[-1][1] == "po_decision_denied" and events[-1][2]["error"].endswith("requiere director")
    assert "Nivel insuficiente" in capsys.readouterr().err
