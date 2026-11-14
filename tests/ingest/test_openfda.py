"""openFDA loader: who made a label, and the holder check (offline)."""

import pytest

from drug_safety.ingest import openfda as of


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("Glenmark Pharmaceuticals Inc., USA", "GLENMARK PHARMS LTD", True),
        ("Aurobindo Pharma Limited", "AUROBINDO PHARMA LTD", True),
        ("sanofi-aventis U.S. LLC", "SANOFI AVENTIS US", True),
        ("Proficient Rx LP", "AUROBINDO PHARMA LTD", False),
        ("Viatris Specialty LLC", "UPJOHN", False),  # same group, different name: not matched
        (None, "X", False),
    ],
)
def test_same_company(a, b, same):
    assert of.same_company(a, b) is same


@pytest.mark.parametrize(
    "maker,holder,ops,role",
    [
        ("Sanofi-Aventis U.S. LLC", "SANOFI AVENTIS US", [], "holder_or_manufacturer"),
        ("Hikma", "WEST-WARD", ["MANUFACTURE"], "holder_or_manufacturer"),  # makes it itself
        ("Proficient Rx LP", "AUROBINDO", ["RELABEL", "REPACK"], "repackager"),
        ("REMEDYREPACK INC.", "RISING", [], "repackager"),  # says so in its name
        ("Viatris Specialty LLC", "UPJOHN", [], "unclear"),  # left for a person to judge
    ],
)
def test_labeler_role(maker, holder, ops, role):
    assert of.labeler_role(maker, holder, ops) == role


def test_labeler_operations_from_xml():
    xml = b"""<document xmlns="urn:hl7-org:v3"><author><assignedEntity><representedOrganization>
      <assignedEntity><assignedOrganization><assignedEntity><assignedOrganization>
      <performance><actDefinition><code displayName="repack"/></actDefinition></performance>
      <performance><actDefinition><code displayName="RELABEL"/></actDefinition></performance>
      </assignedOrganization></assignedEntity></assignedOrganization></assignedEntity>
      </representedOrganization></assignedEntity></author></document>"""
    assert of.labeler_operations(xml) == ["RELABEL", "REPACK"]
