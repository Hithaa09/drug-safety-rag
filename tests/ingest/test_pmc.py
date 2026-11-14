"""PMC BioC parsing and licence policy (offline, made-up responses)."""

import pytest

from drug_safety.ingest import pmc


@pytest.mark.parametrize(
    "raw,norm,allowed",
    [
        ("CC BY", "CC BY", True),
        ("cc by 4.0", "CC BY", True),
        ("CC-BY-NC 3.0", "CC BY-NC", True),
        ("CC BY-NC-SA", "CC BY-NC-SA", True),
        ("CC0", "CC0", True),
        ("CC BY-ND", "CC BY-ND", False),  # no derivatives: chunking is a derivative
        ("CC BY-NC-ND 4.0", "CC BY-NC-ND", False),
        ("NO-CC CODE", "NO-CC CODE", False),  # open access without a CC licence
        (None, "UNKNOWN", False),
    ],
)
def test_licence_policy(raw, norm, allowed):
    assert pmc.normalise_license(raw) == norm
    assert pmc.full_text_allowed(norm) is allowed


def bioc(license_, passages):
    return [
        {
            "documents": [
                {
                    "infons": {"license": license_},
                    "passages": [
                        {"infons": {"section_type": s, "type": t}, "text": x}
                        for s, t, x in passages
                    ],
                }
            ]
        }
    ]


PASSAGES = [
    ("TITLE", "front", "CYP2C19 and clopidogrel"),
    ("ABSTRACT", "abstract_title_1", "Background"),
    ("ABSTRACT", "abstract", "Clopidogrel   needs CYP2C19."),
    ("INTRO", "title_1", "Introduction"),
    ("INTRO", "paragraph", "Background text."),
    ("METHODS", "paragraph", "We genotyped."),
    ("RESULTS", "title_1", "Results"),
    ("RESULTS", "paragraph", "Poor metabolizers had more events."),
    ("TABLE", "table_caption", "Table 1. Outcomes"),
    ("TABLE", "table", "Genotype Events *2/*2 12"),
    ("FIG", "fig_caption", "Figure 1."),
    ("DISCUSS", "paragraph", "This confirms CPIC."),
    ("REF", "ref", "Smith 2010."),
    ("ACK_FUND", "paragraph", "Funded by X."),
]


def test_kept_and_dropped_sections():
    lic, sections = pmc.parse_bioc(bioc("CC BY", PASSAGES))
    assert lic == "CC BY"
    assert [(s.section, s.text) for s in sections] == [
        ("ABSTRACT", "Clopidogrel needs CYP2C19."),  # whitespace normalised
        ("RESULTS", "Poor metabolizers had more events."),
        ("TABLE", "Table 1. Outcomes"),
        ("TABLE", "Genotype Events *2/*2 12"),
        ("DISCUSS", "This confirms CPIC."),
    ]
    assert sections[0].heading == "Background" and sections[1].heading == "Results"
    text = " ".join(s.text for s in sections)
    for dropped in (
        "Background text.",
        "We genotyped.",
        "Smith 2010.",
        "Funded by X.",
        "Figure 1.",
    ):
        assert dropped not in text


def test_bioc_error_page_is_not_accepted():
    assert not pmc.is_bioc(b"[Error] : No result can be found. <BR><HR><B> - https://...")
    assert not pmc.is_bioc(b"[]")
    assert pmc.is_bioc(b'[{"documents": [{"passages": [{"text": "x"}]}]}]')
