import pytest

from src.guardrails.pii import BLOCK, apply_pii_policy, clabe_ok, find_pii, luhn_ok


@pytest.mark.parametrize("text,kind", [
    ("escribe a juan.perez@empresa.com.mx", "EMAIL"),
    ("mi cel es 55 1234 5678", "PHONE"),
    ("llama al +52 (81) 1234-5678", "PHONE"),
    ("RFC PEPJ800101AB1", "RFC"),
    ("RFC de empresa ABC010203XY9", "RFC"),
    ("CURP PEPJ800101HDFRRN09", "CURP"),
    ("tarjeta 4111 1111 1111 1111", "CARD"),
    ("tarjeta 5555-5555-5555-4444", "CARD"),
    ("CLABE 002010077777777771", "CLABE"),
])
def test_detects_mexican_pii(text, kind):
    assert [m.kind for m in find_pii(text)] == [kind]


@pytest.mark.parametrize("text", [
    "¿Cuántas unidades se vendieron del SKU-0009 el 2026-09-26?",
    "Propón 10000 unidades por $250,000 MXN",
    "tarjeta con Luhn invalido 4111 1111 1111 1112",
    "RFC con mes invalido PEPJ801301AB1",
    "El pedido 123456789012345678 no es CLABE valida",   # digito de control incorrecto
    "Monto 1757010.00 y stock 49382",
])
def test_no_false_positives_on_inventory_text(text):
    assert find_pii(text) == []


def test_validators():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert clabe_ok("002010077777777771") and not clabe_ok("002010077777777770")


def test_anonymize_numbers_by_type_and_reuses_label():
    r = apply_pii_policy("a@x.com, b@y.com y otra vez a@x.com; tel 5512345678")
    assert r.text == "[EMAIL_1], [EMAIL_2] y otra vez [EMAIL_1]; tel [PHONE_1]"
    assert not r.blocked


def test_financial_data_blocks_by_default_and_policy_is_configurable():
    r = apply_pii_policy("paga con 4111 1111 1111 1111")
    assert r.blocked_kinds == ["CARD"] and "[CARD_1]" in r.text
    assert not apply_pii_policy("paga con 4111 1111 1111 1111", {"CARD": "ANONYMIZE"}).blocked
    assert apply_pii_policy("a@x.com", {"EMAIL": BLOCK}).blocked_kinds == ["EMAIL"]


def test_overlaps_resolved_by_priority():
    # la CURP contiene un prefijo con forma de RFC: debe detectarse una sola entidad
    assert [m.kind for m in find_pii("PEPJ800101HDFRRN09")] == ["CURP"]
