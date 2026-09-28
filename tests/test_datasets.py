import pytest

from evals.datasets import dataset_path, load_dataset
from evals.run_agent_eval import facts_match
from src.guardrails.sql_guard import validate_sql
from src.tools.sql_tool import SCHEMA_PROMPT


@pytest.mark.parametrize("suite,split,n", [("sql", "dev", 30), ("sql", "test", 30), ("agent", "dev", 16),
                                          ("agent", "test", 10), ("rag", "dev", 15), ("rag", "test", 8)])
def test_dataset_sizes_and_unique_ids(suite, split, n):
    rows = load_dataset(suite, split)
    assert len(rows) == n and len({r["id"] for r in rows}) == n


@pytest.mark.parametrize("suite", ["sql", "agent", "rag"])
def test_dev_and_test_do_not_overlap(suite):
    dev = {r["question"].lower() for r in load_dataset(suite, "dev")}
    test = {r["question"].lower() for r in load_dataset(suite, "test")}
    assert dev.isdisjoint(test)
    assert {r["id"] for r in load_dataset(suite, "dev")}.isdisjoint({r["id"] for r in load_dataset(suite, "test")})


@pytest.mark.parametrize("row", load_dataset("sql", "test"), ids=lambda r: r["id"])
def test_sql_test_reference_passes_guard(row):
    validate_sql(row["sql"])
    assert "now()" not in row["sql"].lower() and "current_date" not in row["sql"].lower()


def test_test_questions_not_in_prompt_examples():
    for row in load_dataset("sql", "test"):
        assert row["question"] not in SCHEMA_PROMPT


def test_agent_test_facts_are_groups_of_alternatives():
    for row in load_dataset("agent", "test"):
        assert all(isinstance(g, list) and g for g in row["expected_facts"])


def test_invalid_split():
    with pytest.raises(ValueError):
        dataset_path("sql", "train")


@pytest.mark.parametrize("answer,groups,ok", [
    ("Tiene **2,326** unidades, la mayoría en CEDIS Norte.", [["2326"], ["norte"]], True),
    ("Hay 2 proveedores en China.", [["2", "dos"]], True),
    ("Datos al 2026-09-26.", [["2", "dos"]], False),          # 2 no coincide dentro de 2026
    ("Costaría $19,820.00 MXN; aprueba el Comprador.", [["19820"], ["comprador"]], True),
    ("Recibe de 7:00 a 15:00.", [["7:00"], ["15:00"]], True),
    ("La meta es 98.5%", [["98"]], False),                     # 98 no coincide dentro de 98.5
    ("Se vendieron 3.222 unidades", [["3222"]], True),         # punto como separador de miles
    ("Promedio 3.222 por dia", [["3.222"]], True),
    ("Se vendieron 13.222 unidades", [["3222"]], False),
    ("Aplica el nivel 1 de escalamiento", [["nivel 1", "comprador"]], True),
    ("", [], True),
])
def test_facts_match(answer, groups, ok):
    assert facts_match(answer, groups)[0] is ok
