"""DPWG parsing (offline: a small made-up ClinPGx zip in a temporary folder)."""

import json
import zipfile

from drug_safety.config import Settings
from drug_safety.ingest import dpwg
from drug_safety.rag.chunking import Chunk

HEADER = "<tr><th>Allele/Genotype/Phenotype</th><th>Drug</th><th>Description</th><th>Recommendation</th></tr>"  # noqa: E501


def table(*rows):
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<p>text</p><table>{HEADER}{body}</table>"


def annotation(pid, drug, gene, html):
    return {"guideline": {
        "id": pid, "name": f"Annotation of DPWG Clinical Guideline for {drug} and {gene}",
        "source": "DPWG", "version": 3, "relatedChemicals": [{"name": drug}],
        "relatedGenes": [{"symbol": gene}], "textMarkdown": {"html": html},
    }}  # fmt: skip


def test_phenotype_vocabulary():
    assert dpwg.phenotype("CYP2C9 PM other a", "CYP2C9") == "Poor Metabolizer"
    assert dpwg.phenotype("CYP2C9 IM", "CYP2C9") == "Intermediate Metabolizer"
    assert dpwg.phenotype("CYP2C9*1/*3", "CYP2C9") is None  # repeats its phenotype row
    # the same VKORC1 genotype written two ways by ClinPGx
    assert dpwg.phenotype("VKORC1 rs9923231 AA", "VKORC1") == "-1639 AA"
    assert dpwg.phenotype("VKORC1 -1639 AA", "VKORC1") == "-1639 AA"
    assert dpwg.phenotype("VKORC1 something else", "VKORC1") is None


def test_only_the_current_guideline_table_is_read():
    old = "<table><tr><th>Genotype</th><th>Therapeutic Dose Recommendation</th></tr>" \
          "<tr><td>VKORC1 AA</td><td>Check INR</td></tr></table>"  # fmt: skip
    html = old + table(["VKORC1 -1639 AA", "warfarin", "d", "use 60%"])
    assert dpwg.guideline_rows(html) == [["VKORC1 -1639 AA", "warfarin", "d", "use 60%"]]


def test_parse_keeps_actionable_rows_for_pairs_cpic_lacks(tmp_path):
    s = Settings(data_dir=tmp_path, _env_file=None)
    zp = dpwg.raw_path(s)
    zp.parent.mkdir(parents=True)
    (zp.parent / "fetched_at.txt").write_text("2026-11-14T10:00:00+00:00")
    files = {
        "PA1.json": annotation("PA1", "warfarin", "CYP2C9", table(
            ["CYP2C9 PM other a", "warfarin", "d", "1. use 20% of the standard initial dose."],
            ["CYP2C9*3/*3", "warfarin", "d", "1. use 20% of the standard initial dose."],
            ["CYP2C9*1/*2", "warfarin", "d", "NO action is required for this interaction."],
        )),
        "PA2.json": annotation("PA2", "clopidogrel", "CYP2C19", table(  # CPIC covers it
            ["CYP2C19 PM", "clopidogrel", "d", "choose an alternative"])),
        "PA3.json": annotation("PA3", "clozapine", "CYP2D6", table(
            ["CYP2D6 PM", "clozapine", "d", "NO action is needed for this gene-drug interaction"])),
    }  # fmt: skip
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CREATED_2026-11-05.txt", "")
        z.writestr("LICENSE.txt", "CC BY-SA 4.0")
        for name, body in files.items():
            z.writestr(name, json.dumps(body))
    cpic = tmp_path / "processed" / "chunks" / "cpic.jsonl"
    cpic.parent.mkdir(parents=True)
    cpic.write_text(Chunk(chunk_id="cpic:x:clopidogrel:001", doc_id="cpic:x", source="cpic",
                          tier=1, section="clopidogrel", headings=[["h"]], text="t",
                          drugs=["clopidogrel"], genes=["CYP2C19"], token_count=1,
                          source_version="1", license="CC0", load_id="l")
                    .model_dump_json() + "\n")  # fmt: skip

    docs, report = dpwg.parse(s)
    assert report == {"warfarin x CYP2C9": 1, "clozapine x CYP2D6": 0}  # clopidogrel: CPIC
    (doc,) = docs
    assert doc.doc_id == "dpwg:PA1" and doc.url.endswith("/guidelineAnnotation/PA1")
    (r,) = doc.recommendations
    assert (r.phenotype, r.label) == ("Poor Metabolizer", "CYP2C9 PM other a")
    assert doc.license == dpwg.LICENSE and doc.title.startswith("DPWG guideline")
