"""CPIC guideline chunks: which rows are kept, how a chunk reads, and the retriever's rule that a
recommendation is evidence only for a gene flag with the same phenotype (offline)."""

import numpy as np

from drug_safety.contracts import Flag
from drug_safety.ingest.cpic import CpicDocument, CpicRecommendation, actionable
from drug_safety.rag import retriever as rt
from drug_safety.rag.chunk_cpic import chunk_doc, chunk_text
from drug_safety.rag.chunking import Chunk

GENES = {"CYP2C19", "CYP2D6", "CYP2C9", "SLCO1B1", "VKORC1"}


def rec(drug="codeine", **phen):
    return CpicRecommendation(
        ids=[1], drug=drug, phenotypes=phen or {"CYP2D6": "Ultrarapid Metabolizer"},
        implications={"CYP2D6": "Increased formation of morphine"},
        recommendation="Avoid codeine use because of potential for serious toxicity.",
        classification="Strong", population="general",
    )  # fmt: skip


def doc(*recs):
    return CpicDocument(
        doc_id="cpic:PA166251454", external_id="PA166251454",
        title="CPIC guideline: CYP2D6, OPRM1, COMT, and Opioids",
        url="https://www.clinpgx.org/guideline/PA166251454", drugs=["codeine"],
        genes=["CYP2D6"], raw_path="r", source_version="CPIC guideline version 73",
        retrieved_at="2026-11-11", load_id="l", recommendations=list(recs),
    )  # fmt: skip


def test_kept_rows_are_actionable_for_a_v1_gene():
    assert actionable({"CYP2D6": "Ultrarapid Metabolizer"}, GENES)
    assert actionable({"CYP2D6": "Poor Metabolizer", "CYP2C19": "Normal Metabolizer"}, GENES)
    assert not actionable({"CYP2D6": "Normal Metabolizer"}, GENES)
    assert not actionable({"CYP2D6": "Indeterminate"}, GENES)
    assert not actionable({"HLA-B": "HLA-B*15:02 positive"}, GENES)  # gene outside v1


def test_chunk_reads_like_cpic():
    text = chunk_text(doc(rec()), rec())
    assert text.splitlines()[0] == (
        "guideline | codeine · CYP2D6 | CPIC guideline: CYP2D6, OPRM1, COMT, and Opioids | "
        "CYP2D6 Ultrarapid Metabolizer"
    )
    for line in ("CYP2D6 phenotype: Ultrarapid Metabolizer", "Population: general",
                 "Implication (CYP2D6): Increased formation of morphine",
                 "Recommendation: Avoid codeine use because of potential for serious toxicity.",
                 "Strength of recommendation: Strong"):  # fmt: skip
        assert line in text.splitlines()


def test_chunks_tag_only_their_own_drug():
    r = rec()
    r.comments = "Unlike tramadol, codeine ..."  # another v1 drug named in a comment
    (c,) = chunk_doc(doc(r), lambda t: len(t.split()), "l")
    assert c.drugs == ["codeine"] and c.genes == ["CYP2D6"]
    assert c.chunk_id == "cpic:PA166251454:codeine:001" and c.tier == 1


GENE_FLAG = dict(flag_id="gene-001", flag_type="gene_drug", drug="codeine", severity="major",
                 rule_id="gene_drug:CYP2D6:ultrarapid_metabolizer:codeine", reason="r",
                 source="CPIC", gene="CYP2D6", phenotype="ultrarapid metabolizer")  # fmt: skip


def flag(**kw):
    return Flag.model_validate({**GENE_FLAG, **kw})


def text(**phen):
    return chunk_text(doc(), rec(**phen))


def test_guideline_fits_only_the_flags_phenotype():
    um = flag()
    assert rt.guideline_fits(um, text(CYP2D6="Ultrarapid Metabolizer"))
    assert rt.guideline_fits(um, text(CYP2D6="Likely Ultrarapid Metabolizer"))  # CPIC: same
    assert not rt.guideline_fits(um, text(CYP2D6="Poor Metabolizer"))
    pm = flag(phenotype="poor metabolizer", rule_id="gene_drug:CYP2D6:poor_metabolizer:codeine")
    assert rt.guideline_fits(pm, text(CYP2D6="Poor Metabolizer", CYP2C19="Normal Metabolizer"))
    assert not rt.guideline_fits(pm, text(CYP2D6="Poor Metabolizer", CYP2C19="Poor Metabolizer"))
    assert not rt.guideline_fits(None, text(CYP2D6="Ultrarapid Metabolizer"))


def test_non_gene_flags_never_get_guideline_chunks():
    ddi = Flag.model_validate(dict(flag_id="ddi-001", flag_type="drug_drug", drug="codeine",
                                   severity="major", rule_id="drug_drug:codeine:x", reason="r",
                                   source="DDInter", interacting_drug="sertraline"))  # fmt: skip
    assert not rt.guideline_fits(ddi, text(CYP2D6="Ultrarapid Metabolizer"))
    # even if an interaction flag names a gene (the contract allows it), the type decides
    ddi_gene = ddi.model_copy(update={"gene": "CYP2D6", "phenotype": "ultrarapid metabolizer"})
    assert not rt.guideline_fits(ddi_gene, text(CYP2D6="Ultrarapid Metabolizer"))


def _chunk(cid, source, txt):
    return Chunk(chunk_id=cid, doc_id=cid.rsplit(":", 2)[0], source=source, tier=1, section="s",
                 headings=[["H"]], text=txt, drugs=["codeine"], genes=["CYP2D6"], token_count=1,
                 source_version="1", license="L", load_id="l")  # fmt: skip


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class Emb:
    def embed_query(self, t):
        return _unit([1, 0])


def test_retriever_skips_wrong_phenotype_and_keeps_one_guideline_and_the_label():
    chunks = [
        _chunk("cpic:PA1:codeine:001", "cpic", text(CYP2D6="Poor Metabolizer")),  # closest
        _chunk("cpic:PA1:codeine:002", "cpic", text(CYP2D6="Ultrarapid Metabolizer")),
        _chunk("cpic:PA1:codeine:003", "cpic", text(CYP2D6="Likely Ultrarapid Metabolizer")),
        _chunk("dailymed:x:warnings:001", "dailymed", "label text"),
    ]
    vecs = np.stack([_unit(v) for v in ([1, 0.01], [1, 0.02], [1, 0.03], [1, 0.5])])
    got = [r.evidence.chunk_id for r in rt.retrieve(flag(), rt.InMemoryRepository(chunks, vecs),
                                                       Emb())]  # fmt: skip
    assert got == ["cpic:PA1:codeine:002", "dailymed:x:warnings:001"]


def test_hla_b_allele_results():
    """HLA-B (added 2026-11-14): CPIC gives allele results, not phenotypes."""
    genes = GENES | {"HLA-B"}
    assert actionable({"HLA-B": "HLA-B*58:01 positive"}, genes)
    assert not actionable({"HLA-B": "HLA-B*58:01 negative"}, genes)
    assert not actionable({"HLA-B": "No HLA-B Result"}, genes)
    assert not actionable({"CYP2C9": "No Result"}, genes)
    hla = flag(drug="phenytoin", gene="HLA-B", phenotype="HLA-B*15:02 positive",
               rule_id="gene_drug:HLA-B:15:02_positive:phenytoin")  # fmt: skip
    assert rt.guideline_fits(hla, text(**{"HLA-B": "HLA-B*15:02 positive",
                                          "CYP2C9": "Normal Metabolizer"}))  # fmt: skip
    assert not rt.guideline_fits(hla, text(**{"HLA-B": "HLA-B*15:02 negative",
                                              "CYP2C9": "Normal Metabolizer"}))  # fmt: skip
    # a CYP2C9 flag still matches phenytoin rows whose HLA-B result is negative or missing
    pm = flag(drug="phenytoin", gene="CYP2C9", phenotype="poor metabolizer",
              rule_id="gene_drug:CYP2C9:poor_metabolizer:phenytoin")  # fmt: skip
    for other in ("HLA-B*15:02 negative", "No HLA-B Result"):
        assert rt.guideline_fits(pm, text(**{"CYP2C9": "Poor Metabolizer", "HLA-B": other}))
    assert not rt.guideline_fits(pm, text(**{"CYP2C9": "Poor Metabolizer",
                                             "HLA-B": "HLA-B*15:02 positive"}))  # fmt: skip
