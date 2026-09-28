"""Datasets de evaluacion: {suite}_{split}.jsonl.

- dev: se usa para desarrollar y ajustar prompts. Sus metricas son optimistas.
- test: NUNCA se usa para ajustar prompts ni heuristicas; es la medida que se reporta.
"""
from __future__ import annotations

import json
from pathlib import Path

DATASETS_DIR = Path(__file__).parent
SPLITS = ("dev", "test")


SPLIT_SUITES = ("sql", "agent", "rag")   # las demas (guardrails_attacks, judge_calibration) son sets unicos


def dataset_path(suite: str, split: str = "dev") -> Path:
    if suite in SPLIT_SUITES and split not in SPLITS:
        raise ValueError(f"split invalido: {split}")
    return DATASETS_DIR / f"{suite}_{split}.jsonl"


def load_dataset(suite: str, split: str = "dev") -> list[dict]:
    with open(dataset_path(suite, split), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
