"""Paper-selection rules (offline): each rule on a made-up paper, and PubMed XML parsing."""

import pytest

from drug_safety.ingest import pubmed_select as ps
from drug_safety.rag.tagging import default_tagger


@pytest.fixture(scope="module")
def tagger():
    return default_tagger()


def paper(
    title,
    abstract="",
    found_by=("pgx:clopidogrel:CYP2C19",),
    pub_types=("Journal Article",),
    mesh=("Humans",),
    pmcid="PMC1",
):
    if abstract:  # real abstracts are long; only test_no_abstract_excluded uses an empty one
        abstract += " A clinical study of patients, with outcomes measured over one year."
    return ps.Paper(
        pmid="1",
        pmcid=pmcid,
        doi=None,
        title=title,
        abstract=abstract,
        journal="J",
        pub_year=2024,
        pub_types=list(pub_types),
        mesh=list(mesh),
        found_by=list(found_by),
    )


GOOD = "CYP2C19 loss-of-function and clopidogrel outcomes after PCI"


def test_good_pgx_paper_selected_as_tier3(tagger):
    p = ps.classify(
        paper(GOOD, "clopidogrel CYP2C19 cohort study of 800 patients after PCI"), tagger
    )
    assert p.selected and p.tier == 3 and p.study_type == "clinical_study"


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"pmcid": None}, "no PMCID"),
        ({"title": "Outcomes after PCI", "abstract": "warfarin only"}, "relevance"),
        ({"mesh": ()}, "not a human study"),
        ({"pub_types": ("Review",)}, "narrative review"),
        ({"pub_types": ("Comment",)}, "comment, letter"),
    ],
)
def test_exclusions(tagger, kwargs, reason):
    p = ps.classify(
        paper(kwargs.pop("title", GOOD), kwargs.pop("abstract", "clopidogrel CYP2C19"), **kwargs),
        tagger,
    )
    assert not p.selected and reason in p.exclusion


@pytest.mark.parametrize(
    "title,reason",
    [
        ("Effect of icosapent ethyl on omeprazole plasma pharmacokinetics", "victim/probe"),
        ("Lack of effect of imrecoxib on pharmacokinetics of warfarin", "victim/probe"),
        ("A CYP2C19 probe cocktail study", "probe, cocktail"),
        ("CYP2C19 inhibition in vitro by clopidogrel metabolites", "lab or computer"),
        ("Quantum mechanics modeling of CYP2C19 and clopidogrel", "lab or computer"),
        ("CYP2C19 and clopidogrel in transgenic mice", "animal study"),
        ("A PCR method for CYP2C19 genotyping before clopidogrel", "laboratory method"),
        ("Impact of STAT6 variants in eosinophilic esophagitis", "title names neither"),
    ],
)
def test_title_rules(tagger, title, reason):
    p = ps.classify(paper(title, "clopidogrel omeprazole warfarin CYP2C19"), tagger)
    assert not p.selected and reason in p.exclusion, (title, p.exclusion)


def test_title_context_words_rescue_relevant_titles(tagger):
    p = ps.classify(
        paper("Genetic variants and platelet reactivity after PCI", "clopidogrel CYP2C19"), tagger
    )
    assert p.selected


def test_tiers(tagger):
    assert (
        ps.classify(
            paper(GOOD, "clopidogrel CYP2C19", pub_types=("Practice Guideline",)), tagger
        ).tier
        == 1
    )
    assert (
        ps.classify(paper(GOOD, "clopidogrel CYP2C19", pub_types=("Meta-Analysis",)), tagger).tier
        == 2
    )


def test_case_report_only_where_pair_lacks_better_evidence(tagger):
    strong = [ps.classify(paper(GOOD, "clopidogrel CYP2C19"), tagger) for _ in range(3)]
    case = ps.classify(paper(GOOD, "clopidogrel CYP2C19", pub_types=("Case Reports",)), tagger)
    lonely = ps.classify(
        paper(
            "Codeine CYP2D6 toxicity",
            "codeine CYP2D6",
            found_by=("pgx:codeine:CYP2D6",),
            pub_types=("Case Reports",),
        ),
        tagger,
    )
    ps.apply_tier4_rule(strong + [case, lonely])
    assert not case.selected and "better evidence" in case.exclusion
    assert lonely.selected and lonely.tier == 4


def test_ddi_needs_two_v1_drugs(tagger):
    one = ps.classify(
        paper("Warfarin interactions", "warfarin and digoxin", found_by=("ddi:warfarin",)), tagger
    )
    two = ps.classify(
        paper("Warfarin and amlodipine", "warfarin with amlodipine", found_by=("ddi:warfarin",)),
        tagger,
    )
    assert not one.selected and two.selected


XML = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue><Title>J Test</Title>
</Journal><ArticleTitle>CYP2C19 and <i>clopidogrel</i></ArticleTitle><Abstract>
<AbstractText Label="A">First.</AbstractText><AbstractText>Second.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList>
</Article><MeshHeadingList><MeshHeading><DescriptorName>Humans</DescriptorName></MeshHeading>
</MeshHeadingList></MedlineCitation><PubmedData><ArticleIdList>
<ArticleId IdType="pmc">PMC999</ArticleId><ArticleId IdType="doi">10.1/x</ArticleId>
</ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""


def test_parse_pubmed():
    [r] = ps.parse_pubmed(XML)
    assert r == {
        "pmid": "123",
        "pmcid": "PMC999",
        "doi": "10.1/x",
        "title": "CYP2C19 and clopidogrel",
        "abstract": "First. Second.",
        "journal": "J Test",
        "pub_year": 2024,
        "pub_types": ["Clinical Trial"],
        "mesh": ["Humans"],
    }


def test_no_abstract_excluded(tagger):
    p = ps.classify(paper(GOOD, ""), tagger)
    assert not p.selected and "no abstract" in p.exclusion
