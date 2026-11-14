import re

import pytest

from drug_safety.contracts import DataGap, EvidenceChunk
from drug_safety.ui import view

SETID = "de8b0b67-eb25-4684-83b5-7ad785314227"


def ev(cid, text, doc_id, source="dailymed", tier=1):
    return EvidenceChunk(chunk_id=cid, doc_id=doc_id, source=source, tier=tier, text=text,
                         title="PLAVIX (CLOPIDOGREL)", heading="12.5 Pharmacogenomics",
                         license="DailyMed (NLM) SPL")  # fmt: skip


def test_flag_table_spec_columns_sorted(response):
    rows = view.flag_table(response())
    assert list(rows[0]) == ["Severity", "Drug", "Type", "Reason", "Triggered by", "Rule source",
                             "Flag"]  # fmt: skip
    assert [r["Severity"] for r in rows] == ["🟥 contraindicated", "🟧 major", "🟧 major"]
    assert [r["Type"] for r in rows] == ["Lab-drug", "Gene-drug", "Drug-drug"]
    assert rows[2]["Triggered by"] == "with omeprazole"
    assert rows[1]["Triggered by"] == "CYP2C19 poor metabolizer"
    assert rows[0]["Triggered by"].startswith("egfr 28 ")


def test_explanation_linked_to_its_evidence(response):
    views = {v.flag.flag_id: v for v in view.flag_views(response())}
    gene = views["gene-001"]
    assert gene.explanation.startswith("Clopidogrel must be activated")
    assert [c.chunk.chunk_id for c in gene.cited] == ["cpic-clopidogrel-01"]
    assert [c.chunk.chunk_id for c in gene.other] == ["label-clop-pgx"]  # retrieved, not cited
    assert gene.suggestions == [(gene.suggestions[0][0], [gene.cited[0].number])]
    assert views["ddi-001"].suggestions == []


def test_numbers_run_in_page_order_without_gaps(response):
    nums = [c.number for v in view.flag_views(response()) for c in v.cited + v.other]
    assert nums == list(range(1, 5))


def test_cited_but_not_supplied_is_shown_not_hidden(response, good):
    good["flags_explained"][0]["evidence_ids"] = ["cpic-clopidogrel-01", "made-up-id"]
    gene = next(v for v in view.flag_views(response(good)) if v.flag.flag_id == "gene-001")
    assert gene.missing_ids == ["made-up-id"]


def test_without_report_all_evidence_still_shown(response):
    views = view.flag_views(response(report=None))
    assert all(v.explanation is None and not v.cited and not v.suggestions for v in views)
    assert sum(len(v.other) for v in views) == 4


def test_flag_without_evidence(response, evidence):
    del evidence["ddi-001"]
    ddi = next(v for v in view.flag_views(response(ev=evidence)) if v.flag.flag_id == "ddi-001")
    assert ddi.cited == [] and ddi.other == []


def test_data_gaps_pass_through(response):
    r = response(data_gaps=[DataGap(kind="lab", message="No creatinine value")])
    assert r.data_gaps[0].message == "No creatinine value"


@pytest.mark.parametrize("doc_id, url", [
    (f"dailymed:{SETID}", f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={SETID}"),
    ("pmc:PMC3159941", "https://pmc.ncbi.nlm.nih.gov/articles/PMC3159941/"),
    ("pubmed:12345678", "https://pubmed.ncbi.nlm.nih.gov/12345678/"),
    ("cpic:PA166251443", "https://www.clinpgx.org/guideline/PA166251443"),
    ("dpwg:PA166182842", "https://www.clinpgx.org/guidelineAnnotation/PA166182842"),
    ("bdpm:61510352", "https://base-donnees-publique.medicaments.gouv.fr/medicament/61510352/extrait"),
    ("curated:rule-7", None),
    ("dailymed:../../evil", None),
])  # fmt: skip
def test_source_url(doc_id, url):
    assert view.source_url(ev("c", "t", doc_id=doc_id)) == url


def test_source_line():
    c = ev("c", "t", doc_id="pmc:PMC1", source="pmc", tier=3)
    assert view.source_line(c) == (
        "PubMed Central paper · PLAVIX (CLOPIDOGREL) · 12.5 Pharmacogenomics · tier 3"
    )


@pytest.mark.parametrize("text", [
    "[click here](http://evil.example)",
    "![x](http://evil.example/p.png)",
    "<img src=x onerror=alert(1)>",
    "**bold** `code` # heading",
])  # fmt: skip
def test_md_escapes_markup(text):
    out = view.md(text)
    # every markup character is preceded by a backslash
    assert not re.search(r"(?<!\\)[\[\]()<>*`#!]", out)
    assert out.replace("\\", "") == text
