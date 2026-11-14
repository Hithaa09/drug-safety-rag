"""Tagging: which v1 drugs and genes a text mentions."""

import pytest

from drug_safety.rag.tagging import default_tagger
from drug_safety.scope import load_scope


@pytest.fixture(scope="module")
def tagger():
    return default_tagger()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Avoid Plavix with omeprazole.", ["clopidogrel", "omeprazole"]),  # brand -> ingredient
        ("Esomeprazole reduces activity.", []),  # not "omeprazole"
        ("escitalopram and nortriptyline", []),  # not citalopram / amitriptyline
        ("ACETAMINOPHEN 500 mg", ["paracetamol"]),  # US name, any case
        ("acetylsalicylic\nacid", ["aspirin"]),  # multi-word name across a line break
        ("fosphenytoin injection", []),  # not phenytoin
        ("S-warfarin is cleared by CYP2C9", ["warfarin"]),  # hyphen is a word boundary
    ],
)
def test_drugs(tagger, text, expected):
    assert tagger.drugs(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CYP2C19 poor metabolizers", ["CYP2C19"]),
        ("CYP2C19 and CYP2C9", ["CYP2C19", "CYP2C9"]),  # 2C9 not found inside 2C19
        ("CYP2C19 only", ["CYP2C19"]),
        ("cytochrome P450 2D6 (P450 isoenzyme 2D6)", ["CYP2D6"]),  # older spellings
        ("OATP1B1 transporter", ["SLCO1B1"]),  # protein name -> gene
        ("VKORC1 -1639G>A", ["VKORC1"]),
        ("CYP3A4 and CYP1A2", []),  # not v1 genes
    ],
)
def test_genes(tagger, text, expected):
    assert tagger.genes(text) == expected


def test_dictionary_covers_exactly_the_scope():
    import yaml

    raw = yaml.safe_load(open("scope/drug_aliases.yaml"))
    scope = load_scope()
    assert set(raw["aliases"]) == set(scope.drugs)
    assert set(raw["genes"]) == set(scope.genes)
    for drug, names in raw["aliases"].items():
        assert drug in [n.lower() for n in names] or drug == "paracetamol", drug


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Avoid NSAIDs and other SSRIs.", ["nsaid", "ssri"]),
        ("nonsteroidal anti-inflammatory drugs", ["nsaid"]),
        ("statins and HMG-CoA reductase inhibitors", ["statin"]),
        ("diminished antiplatelet activity of Plavix", []),  # an effect, not a drug class
        ("other antiplatelet agents", ["antiplatelet"]),
        ("Opioids", ["opioid"]),
    ],
)
def test_classes(tagger, text, expected):
    assert tagger.classes(text) == expected
