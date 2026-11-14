"""Setup checks: Person B modules import, settings come from the environment, scope matches spec."""

import importlib

import pytest

from drug_safety.config import Settings
from drug_safety.scope import Scope, load_scope


@pytest.mark.parametrize("name", ["ingest", "rag", "llm", "ui"])
def test_person_b_modules_import(name):
    importlib.import_module(f"drug_safety.{name}")


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("MODEL_URL", "http://workbench:9000/v1")
    monkeypatch.setenv("DATA_DIR", "/tmp/ds")
    s = Settings(_env_file=None)
    assert s.model_url == "http://workbench:9000/v1"
    assert str(s.raw_dir) == "/tmp/ds/raw"


def test_settings_defaults_match_spec():
    s = Settings(_env_file=None)
    assert s.embedding_model == "BAAI/bge-base-en-v1.5"
    assert s.model_name == "Qwen/Qwen2.5-7B-Instruct"


def test_v1_scope_matches_spec():
    scope = load_scope()
    assert len(scope.drugs) == 25
    spec_genes = ["CYP2C19", "CYP2D6", "CYP2C9", "SLCO1B1", "VKORC1"]
    assert scope.genes == [*spec_genes, "HLA-B"]  # HLA-B added 2026-11-14 (Person A, E3)
    assert scope.lab_panels == ["LFT", "CBC", "RFT"]


def test_scope_rejects_bad_names():
    base = dict(
        version="v1", status="candidate", genes=["CYP2C19"], lab_panels=[], lifestyle_inputs=[]
    )
    with pytest.raises(ValueError):
        Scope(drugs=["Clopidogrel"], **base)
    with pytest.raises(ValueError):
        Scope(drugs=["warfarin", "warfarin"], **base)
