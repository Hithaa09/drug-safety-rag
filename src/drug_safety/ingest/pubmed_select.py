"""Select research papers for the document store (spec: "Selecting papers"). Metadata only.

Usage:
    python -m drug_safety.ingest.pubmed_select run      # network: search + PubMed metadata
    python -m drug_safety.ingest.pubmed_select verify   # offline checks on the selection

Only NCBI E-utilities (an approved service), at most 3 requests per second without an API key
(10 with NCBI_API_KEY). Every response is saved under data/raw/pubmed/.

Candidates (decided 2026-11-11 after measuring; see docs/progress.md step 6a):
  - drug-gene: each CPIC v1 pair (plus acenocoumarol via DPWG), open access, humans, gene named
    in title/abstract;
  - drug-drug: a v1 drug as major topic with another v1 drug, MeSH "Drug Interactions", open access.
Lifestyle literature is not searched: measured precision was too low; labels cover it.

Rules, applied to every candidate, each exclusion recorded with its reason:
  1. needs a PMCID (open-access full text);
  2. relevance: title/abstract names the drug AND the gene (drug-gene) or two v1 drugs (drug-drug);
  3. humans only; no probe/cocktail/phenotyping studies; no "effect of <non-v1 drug> on the
     pharmacokinetics of <v1 drug>" studies;
  4. narrative reviews dropped (not an evidence tier in the spec);
  5. tier 1 guideline, 2 systematic review/meta-analysis, 3 human clinical study, 4 case report;
     tier 4 kept only for drug-gene pairs with fewer than 3 papers in tiers 1-3.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from lxml import etree
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT
from drug_safety.rag.tagging import default_tagger
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "drug-safety-rag"
OA = ' AND "pubmed pmc open access"[filter]'
HUMANS = ' AND "Humans"[mh]'
PGX_TERMS = "(pharmacogenetic* OR pharmacogenomic* OR genotype OR polymorphism OR metabolizer)"
US_NAME = {"paracetamol": "acetaminophen"}
# CPIC pairs for the v1 genes (live CPIC check, docs/sources.md) + acenocoumarol (DPWG).
PAIRS = [
    ("clopidogrel", "CYP2C19"),
    ("omeprazole", "CYP2C19"),
    ("pantoprazole", "CYP2C19"),
    ("warfarin", "CYP2C9"),
    ("warfarin", "VKORC1"),
    ("acenocoumarol", "CYP2C9"),
    ("acenocoumarol", "VKORC1"),
    ("aspirin", "CYP2C9"),
    ("ibuprofen", "CYP2C9"),
    ("diclofenac", "CYP2C9"),
    ("celecoxib", "CYP2C9"),
    ("simvastatin", "SLCO1B1"),
    ("atorvastatin", "SLCO1B1"),
    ("rosuvastatin", "SLCO1B1"),
    ("phenytoin", "CYP2C9"),
    ("codeine", "CYP2D6"),
    ("tramadol", "CYP2D6"),
    ("amitriptyline", "CYP2C19"),
    ("amitriptyline", "CYP2D6"),
    ("sertraline", "CYP2C19"),
    ("sertraline", "CYP2D6"),
    ("citalopram", "CYP2C19"),
    ("citalopram", "CYP2D6"),
    ("clozapine", "CYP2D6"),
    ("methotrexate", "SLCO1B1"),
]
TIER4_ONLY_IF_FEWER_THAN = 3
PROBE_TITLE = re.compile(r"\b(probe|cocktail|phenotyping)\b", re.I)
EFFECT_OF = re.compile(
    r"^(?:the\s+)?(?:(?:lack|absence)\s+of\s+(?:an?\s+)?|no\s+)?(?:effects?|influence|impact)"
    r"\s+of\s+(.+?)\s+on\s+(?:the\s+)?(?:[\w-]+\s+){0,3}?"
    r"(?:pharmacokinetics|exposure|plasma|bioavailability|disposition)",
    re.I,
)
# Title words that still mean a v1 drug or its use (besides drug names, genes and the classes
# in scope/drug_classes.yaml). Added after reading 30 papers the title rule had wrongly dropped.
TITLE_CONTEXT = re.compile(
    r"\b(MTX|antidepressants?|antipsychotics?|anticoagula\w+|platelet (?:reactivity|function|"
    r"aggregation|inhibition)|dual antiplatelet)\b",
    re.I,
)
NOT_STUDIES = {"Comment", "Letter", "Editorial", "Published Erratum", "Retraction of Publication"}
ANIMAL_TITLE = re.compile(r"\b(mice|mouse|murine|rats?|transgenic|canine|porcine)\b", re.I)
LAB_METHOD_TITLE = re.compile(
    r"\b(polymerase chain reaction|PCR|assay|genotyping method|detection method)\b", re.I
)
# Lab and computer studies: human cells or enzymes, not patients.
LAB_MESH = {
    "In Vitro Techniques",
    "Microsomes, Liver",
    "Cell Line",
    "Cell Line, Tumor",
    "Hep G2 Cells",
    "Hepatocytes",
    "Models, Molecular",
    "Molecular Docking Simulation",
    "Molecular Dynamics Simulation",
    "Recombinant Proteins",
    "Cells, Cultured",
}
LAB_TITLE = re.compile(
    r"\b(in vitro|cell lines?|cells\b|microsom\w*|molecular (?:dynamics|docking|mechanics)|"
    r"quantum mechanics|in silico)",
    re.I,
)


class Paper(BaseModel):
    pmid: str
    pmcid: str | None
    doi: str | None
    title: str
    abstract: str
    journal: str
    pub_year: int | None
    pub_types: list[str]
    mesh: list[str]
    found_by: list[str]  # query ids, e.g. "pgx:clopidogrel:CYP2C19"
    drugs: list[str] = []
    genes: list[str] = []
    tier: int | None = None
    study_type: str | None = None
    selected: bool = False
    exclusion: str | None = None


# ---------------------------------------------------------------- NCBI


class EUtils:
    def __init__(self, settings: Settings):
        self.key = settings.ncbi_api_key
        self.delay = 0.11 if self.key else 0.35  # 10/s with a key, 3/s without
        self.http = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
        self.raw = settings.raw_dir / "pubmed"

    def _get(self, path: str, params: dict) -> httpx.Response:
        params = params | {"tool": TOOL} | ({"api_key": self.key} if self.key else {})
        for attempt in range(5):
            time.sleep(self.delay)
            try:
                r = self.http.post(f"{EUTILS}/{path}", data=params)
            except httpx.TransportError as e:
                log.warning("network error %s", e)
            else:
                if r.status_code == 200:
                    return r
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
            time.sleep(2**attempt)
        raise RuntimeError(f"E-utilities failed: {path}")

    def search(self, term: str) -> list[str]:
        r = self._get(
            "esearch.fcgi", {"db": "pubmed", "term": term, "retmax": 5000, "retmode": "json"}
        )
        return r.json()["esearchresult"]["idlist"]

    def fetch(self, pmids: list[str]) -> list[bytes]:
        out = []
        self.raw.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(pmids), 200):
            batch = pmids[i : i + 200]
            xml = self._get(
                "efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}
            ).content
            (self.raw / f"efetch_{i // 200:03d}.xml").write_bytes(xml)
            out.append(xml)
        return out


def queries(drugs: list[str]) -> dict[str, str]:
    q = {}
    for d, g in PAIRS:
        q[f"pgx:{d}:{g}"] = f"{US_NAME.get(d, d)} AND {g} AND {PGX_TERMS} AND {g}[tiab]{OA}{HUMANS}"
    for d in drugs:
        others = " OR ".join(f"{US_NAME.get(o, o)}[mh]" for o in drugs if o != d)
        q[f"ddi:{d}"] = f'{US_NAME.get(d, d)}[majr] AND ({others}) AND "Drug Interactions"[mh]{OA}'
    return q


def parse_pubmed(xml: bytes) -> list[dict]:
    rows = []
    for art in etree.fromstring(xml).iterfind(".//PubmedArticle"):

        def text(path, art=art):
            el = art.find(path)
            return " ".join("".join(el.itertext()).split()) if el is not None else ""

        ids = {
            i.get("IdType"): (i.text or "").strip()
            for i in art.iterfind(".//PubmedData/ArticleIdList/ArticleId")
        }
        year = text(".//JournalIssue/PubDate/Year") or text(".//ArticleDate/Year")
        rows.append(
            {
                "pmid": text(".//MedlineCitation/PMID"),
                "pmcid": ids.get("pmc") or None,
                "doi": ids.get("doi") or None,
                "title": text(".//ArticleTitle"),
                "abstract": " ".join(
                    " ".join("".join(a.itertext()).split())
                    for a in art.iterfind(".//Abstract/AbstractText")
                ),
                "journal": text(".//Journal/Title"),
                "pub_year": int(year) if year.isdigit() else None,
                "pub_types": [p.text for p in art.iterfind(".//PublicationType") if p.text],
                "mesh": [m.text for m in art.iterfind(".//MeshHeading/DescriptorName") if m.text],
            }
        )
    return rows


# ---------------------------------------------------------------- rules


def classify(p: Paper, tagger) -> Paper:
    text = f"{p.title} {p.abstract}"
    p.drugs, p.genes = tagger.drugs(text), tagger.genes(text)
    pt = set(p.pub_types)
    if not p.pmcid:
        p.exclusion = "no PMCID (no open-access full text)"
        return p
    pgx = [q for q in p.found_by if q.startswith("pgx:")]
    ok_pgx = any(q.split(":")[1] in p.drugs and q.split(":")[2] in p.genes for q in pgx)
    ok_ddi = any(q.startswith("ddi:") for q in p.found_by) and len(p.drugs) >= 2
    if not (ok_pgx or ok_ddi):
        p.exclusion = "relevance: title/abstract doesn't name the drug and gene, or two v1 drugs"
        return p
    # The title must be about the pair: the drug or the gene named there, not only in the abstract.
    title_hit = (
        tagger.drugs(p.title)
        or tagger.genes(p.title)
        or tagger.classes(p.title)
        or TITLE_CONTEXT.search(p.title)
    )
    if not title_hit:
        p.exclusion = "title names neither a v1 drug nor a v1 gene"
        return p
    if "Humans" not in p.mesh:
        p.exclusion = "not a human study (no Humans MeSH)"
        return p
    if pt & NOT_STUDIES:
        p.exclusion = "comment, letter, editorial or erratum (not a study)"
        return p
    if len(p.abstract) < 50:  # e.g. a letter to the editor tagged only "Journal Article"
        p.exclusion = "no abstract (letter or commentary, not a study)"
        return p
    if ANIMAL_TITLE.search(p.title):
        p.exclusion = "animal study (named in the title)"
        return p
    if LAB_METHOD_TITLE.search(p.title):
        p.exclusion = "laboratory method paper"
        return p
    if set(p.mesh) & LAB_MESH or LAB_TITLE.search(p.title):
        p.exclusion = "lab or computer study (cells, enzymes or models, not patients)"
        return p
    if PROBE_TITLE.search(p.title):
        p.exclusion = "probe, cocktail or phenotyping study"
        return p
    m = EFFECT_OF.match(p.title)
    if m and not tagger.drugs(m.group(1)):
        p.exclusion = "another drug's interaction study with a v1 drug as the victim/probe"
        return p
    if pt & {"Practice Guideline", "Guideline"}:
        p.tier, p.study_type = 1, "guideline"
    elif pt & {"Systematic Review", "Meta-Analysis"}:
        p.tier, p.study_type = 2, "systematic_review"
    elif "Review" in pt:
        p.exclusion = "narrative review (not an evidence tier in the spec)"
        return p
    elif "Case Reports" in pt:
        p.tier, p.study_type = 4, "case_report"
    else:
        trial = pt & {"Randomized Controlled Trial", "Clinical Trial"}
        p.tier, p.study_type = 3, "trial" if trial else "clinical_study"
    p.selected = True
    return p


def apply_tier4_rule(papers: list[Paper]) -> None:
    strong = Counter(
        q for p in papers if p.selected and p.tier <= 3 for q in p.found_by if q.startswith("pgx:")
    )
    for p in papers:
        if p.selected and p.tier == 4:
            pairs = [q for q in p.found_by if q.startswith("pgx:")]
            if not pairs or all(strong[q] >= TIER4_ONLY_IF_FEWER_THAN for q in pairs):
                p.selected, p.exclusion = False, "case report, and its pair has better evidence"


# ---------------------------------------------------------------- run / verify


def out_dir(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "papers"


def run(settings: Settings) -> dict:
    eu = EUtils(settings)
    found: dict[str, list[str]] = defaultdict(list)
    q = queries(load_scope().drugs)
    for qid, term in q.items():
        for pmid in eu.search(term):
            found[pmid].append(qid)
    meta = [r for xml in eu.fetch(sorted(found)) for r in parse_pubmed(xml)]
    tagger = default_tagger()
    papers = [classify(Paper(**r, found_by=found[r["pmid"]]), tagger) for r in meta]
    apply_tier4_rule(papers)
    return _save(settings, papers, q)


def reclassify(settings: Settings) -> dict:
    """Re-apply the rules to the saved metadata (no network), after a rule change."""
    tagger = default_tagger()
    fields = {"drugs", "genes", "tier", "study_type", "selected", "exclusion"}
    papers = [classify(Paper(**p.model_dump(exclude=fields)), tagger) for p in read(settings)]
    apply_tier4_rule(papers)
    q = json.loads((out_dir(settings) / "queries.json").read_text())
    return _save(settings, papers, q)


def _save(settings: Settings, papers: list[Paper], q: dict) -> dict:
    od = out_dir(settings)
    od.mkdir(parents=True, exist_ok=True)
    with open(od / "candidates.jsonl", "w", encoding="utf-8") as f:
        for p in papers:
            f.write(p.model_dump_json() + "\n")
    (od / "queries.json").write_text(json.dumps(q, indent=1))
    summary = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "candidates": len(papers),
        "selected": sum(p.selected for p in papers),
        "by_tier": dict(sorted(Counter(p.tier for p in papers if p.selected).items())),
        "excluded_by_reason": dict(Counter(p.exclusion for p in papers if not p.selected)),
    }
    (od / "selection_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def read(settings: Settings) -> list[Paper]:
    with open(out_dir(settings) / "candidates.jsonl", encoding="utf-8") as f:
        return [Paper.model_validate_json(line) for line in f]


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


def verify(settings: Settings) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=bool(ok), detail="" if ok else str(detail)))

    papers = read(settings)
    sel = [p for p in papers if p.selected]
    tagger = default_tagger()
    add(
        "every paper is selected or has an exclusion reason",
        all(p.selected != bool(p.exclusion) for p in papers),
        "inconsistent rows",
    )
    add("no duplicate PMIDs", len({p.pmid for p in papers}) == len(papers), "duplicates")
    dois = [p.doi.lower() for p in sel if p.doi]
    add("no duplicate DOIs among selected", len(set(dois)) == len(dois), "duplicates")
    add(
        "every selected paper has a PMCID",
        all(p.pmcid for p in sel),
        [p.pmid for p in sel if not p.pmcid][:5],
    )
    redo = [
        p.pmid
        for p in sel
        if classify(
            Paper(
                **p.model_dump(
                    exclude={"drugs", "genes", "tier", "study_type", "selected", "exclusion"}
                )
            ),
            tagger,
        ).exclusion
    ]
    add("re-applying the rules keeps every selected paper", not redo, redo[:5])
    add("tiers 1-4 only", all(p.tier in (1, 2, 3, 4) for p in sel), "bad tier")
    raw = list((settings.raw_dir / "pubmed").glob("efetch_*.xml"))
    add("raw PubMed responses kept", bool(raw), "none")
    covered = {q for p in sel for q in p.found_by if q.startswith("pgx:")}
    missing = [f"pgx:{d}:{g}" for d, g in PAIRS if f"pgx:{d}:{g}" not in covered]
    add("report: drug-gene pairs with no selected paper (not a failure)", True)
    checks[-1].detail = ", ".join(missing) or "none"
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level="WARNING", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.pubmed_select")
    p.add_argument("cmd", choices=["run", "reclassify", "verify"])
    args = p.parse_args(argv)
    if args.cmd in ("run", "reclassify"):
        print(json.dumps((run if args.cmd == "run" else reclassify)(settings), indent=2))
        return 0
    checks = verify(settings)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
