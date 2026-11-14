"""Drug name finder: whole words, longest name first, v1 aliases map to one key."""

import pytest

from drug_safety.llm.drug_names import DrugNameFinder


def test_whole_words_and_aliases(finder):
    assert finder.find("esomeprazole, not omeprazole") == {"esomeprazole", "omeprazole"}
    assert finder.find("PLAVIX and Tylenol") == {"clopidogrel", "paracetamol"}
    assert finder.find("clopidogrel-induced bleeding") == {"clopidogrel"}
    assert finder.find("Theo 24 and Theo-24") == {"theophylline"}
    assert finder.find("isomeprazoles") == set()


def test_longest_name_wins():
    f = DrugNameFinder({"sodium chloride": "sodium chloride", "chloride": "chloride"})
    assert f.mentions("Sodium chloride 0.9%") == [("Sodium chloride", "sodium chloride")]


def test_real_list_when_fetched():
    from drug_safety.config import get_settings

    try:
        f = DrugNameFinder.load(get_settings())
    except FileNotFoundError:
        pytest.skip("run `make fetch-drug-names`")
    assert {"prasugrel", "effient"} <= f.find("Prasugrel (Effient) is an option")
    assert f.find("water, iron, glucose, creatinine, vitamin K, protein C, IBU") == set()
