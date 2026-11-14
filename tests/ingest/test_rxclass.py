"""RxClass drug classes (offline)."""

from drug_safety.ingest import rxclass


def test_assign_classes_by_epc_and_moa():
    defs = rxclass.class_definitions()
    assert rxclass.assign_classes(
        ["Nonsteroidal Anti-inflammatory Drug", "Platelet Aggregation Inhibitor"], [], defs
    ) == ["nsaid", "antiplatelet"]
    assert rxclass.assign_classes([], ["Full Opioid Agonists"], defs) == ["opioid"]  # codeine
    assert rxclass.assign_classes(["Biguanide"], [], defs) == []  # metformin: no class rule


def test_manual_members_have_reasons_and_known_classes():
    defs = rxclass.class_definitions()
    for drug, m in defs["manual_members"].items():
        assert m["reason"], drug
        assert set(m["classes"]) <= set(defs["classes"]), drug


def test_every_class_has_text_phrases_and_a_source_class():
    for key, c in rxclass.class_definitions()["classes"].items():
        assert c["text"], key
        assert c.get("epc") or c.get("moa"), key
