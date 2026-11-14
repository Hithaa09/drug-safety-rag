"""The retriever: a Flag in, cited evidence chunks out (spec RAG layer, steps 1-5).

    from drug_safety.rag.retriever import PgRepository, retrieve
    evidence = retrieve(flag, PgRepository(conn), embedder)       # list[EvidenceChunk]

Steps, for one flag:
  1. query = flag.query_text()   (drug, gene or lab, and reason; from the contract)
  2. filter: chunks whose `drugs` overlap flag.drug_ids()   (both drugs for an interaction)
  3. rank by cosine distance, separately for tier 1 (labels, guidelines) and lower tiers (papers)
  4. select tier-balanced: the best AUTHORITY_K tier-1 chunks + the best SUPPORT_K paper chunk
     if it is within SUPPORT_MAX_DISTANCE (not for lab flags: no lab literature was collected);
     fill from the other side if one is short
  5. if the drug filter finds nothing, retry once on `class_drugs` (e.g. a label passage about
     "NSAIDs" for an ibuprofen flag) and mark those chunks via_class=True
Guideline recommendations (CPIC: one chunk per drug x phenotype) are used only for a gene flag,
only for the flag's own phenotype (`guideline_fits`), and at most one per flag (GUIDELINE_K).
Embeddings barely tell "poor" from "ultrarapid" metabolizer: without this rule a CYP2D6
ultrarapid codeine flag got CPIC's advice for poor metabolizers (measured, docs/progress.md
step 6d).
Then `build_context` assembles the evidence for all flags within a token budget, dropping the
lowest-tier chunks first (spec step 5).

The rule in step 4 replaced the spec's "closest 10, re-sort by tier" after measurement
(docs/progress.md step 6c): once papers were loaded, that rule let papers crowd label evidence out.
Repositories hide where chunks live: PgRepository (PostgreSQL) and InMemoryRepository (tests).
"""

from __future__ import annotations

import re
from typing import Protocol

import numpy as np
from pydantic import BaseModel

from drug_safety.contracts import EvidenceChunk, Flag, FlagType

K = 3
AUTHORITY_K, SUPPORT_K = 2, 1
# A paper fills the support slot only if it is this close to the query (cosine distance).
# Set 2026-11-11 from the 25 gold cases: every paper judged relevant was <= 0.222, every
# irrelevant one >= 0.249. Chosen on the same cases it was judged on: recheck on real flags.
SUPPORT_MAX_DISTANCE = 0.235


# Sources whose chunks are one recommendation per phenotype (chunk_id starts with "<source>:").
GUIDELINE_SOURCES = {"cpic", "dpwg"}
CANDIDATES = 30  # tier-1 chunks looked at before guideline chunks are filtered
# At most one guideline chunk per flag, so the other tier-1 slot keeps the label passage: the
# spec's worked example cites "CPIC clopidogrel guideline chunk, label pharmacogenomics section".
GUIDELINE_K = 1
_PHENOTYPE_LINE = re.compile(r"^(\S+) phenotype: (.+)$", re.M)
# Another gene in a recommendation that doesn't change it: normal, unknown, or a negative /
# missing allele result (phenytoin rows pair CYP2C9 with "HLA-B*15:02 negative").
_NO_EFFECT = re.compile(r"^(?:normal|indeterminate|n/a|no result)\b|\bnegative$|^no\b.*\bresult$")


# Words a passage must contain to be about a lifestyle flag's factor. Embeddings weigh the drug
# and "levels" over the factor: for clozapine + smoking, a CYP2D6 passage ("higher plasma
# concentrations of clozapine") ranked above the one naming tobacco smoke (2026-11-14).
LIFESTYLE_TERMS = {
    "smoking": r"smok|tobacco|cigarette|nicotine",
    "alcohol": r"alcohol|ethanol|drinks?\b",
    "grapefruit": r"grapefruit",
    # about diet, not vitamin K as warfarin's antidote (10.2 Treatment matched "vitamin k")
    "high_vitamin_k": r"vitamin k (?:intake|in (?:the )?food|content)|\bdiet|leafy|vegetables",
}


def mentions_trigger(flag: Flag | None, text: str) -> bool:
    """True unless the flag is a lifestyle flag and the passage never names its factor."""
    if flag is None or flag.flag_type != FlagType.lifestyle_drug or not flag.lifestyle:
        return True
    f = flag.lifestyle
    key = f.factor_type if f.factor_type in LIFESTYLE_TERMS else f.factor_value
    pattern = LIFESTYLE_TERMS.get(key, re.escape(f.factor_value.replace("_", " ")))
    return re.search(pattern, text, re.I) is not None


def _phenotype(p: str) -> str:
    """'Likely Poor Metabolizer' -> 'poor metabolizer' (CPIC: same recommendation)."""
    return re.sub(r"^likely\s+", "", p.strip().lower())


def guideline_fits(flag: Flag | None, text: str) -> bool:
    """A guideline recommendation is evidence for this flag: a gene flag, the recommendation's
    phenotype for the flag's gene is the flag's phenotype, and any other gene it names is normal
    (the flag says nothing about other genes, so assume the usual case)."""
    if flag is None or flag.flag_type != FlagType.gene_drug or not flag.gene:
        return False
    phen = dict(_PHENOTYPE_LINE.findall(text))
    if _phenotype(phen.get(flag.gene, "")) != _phenotype(flag.phenotype or ""):
        return False
    return all(g == flag.gene or _NO_EFFECT.search(_phenotype(v)) for g, v in phen.items())


def _owner(text: str) -> str:
    """The drug whose label a chunk comes from (its context line), lower case."""
    return text.split(" | ", 1)[0].strip().lower()


def is_guideline(chunk_id: str) -> bool:
    return chunk_id.split(":", 1)[0] in GUIDELINE_SOURCES


class Hit(BaseModel):
    chunk_id: str
    distance: float


class RetrievedChunk(BaseModel):
    """An evidence chunk plus why it was chosen (kept out of the shared contract)."""

    evidence: EvidenceChunk
    distance: float
    via_class: bool = False


class EvidenceRepository(Protocol):
    def search(
        self, qvec: np.ndarray, drugs: list[str], authority: bool, n: int, column: str = "drugs"
    ) -> list[Hit]: ...

    def get(self, chunk_ids: list[str]) -> dict[str, EvidenceChunk]: ...


def assemble(authority: list, support: list, k: int = K) -> list:
    """Both lists ranked by distance. Take AUTHORITY_K from tier 1 and SUPPORT_K from lower
    tiers; if one side is short, fill from the other so up to k items are returned."""
    top = authority[:AUTHORITY_K] + support[:SUPPORT_K]
    rest = authority[AUTHORITY_K:] + support[SUPPORT_K:]
    return (top + rest)[:k]


def select(
    repo: EvidenceRepository,
    qvec: np.ndarray,
    drugs: list[str],
    k: int = K,
    column: str = "drugs",
    allow_support: bool = True,
    flag: Flag | None = None,
) -> list[Hit]:
    authority = repo.search(qvec, drugs, True, CANDIDATES, column)
    text = {cid: c.text for cid, c in repo.get([h.chunk_id for h in authority]).items()}
    kept, guidelines = [], 0
    for h in authority:
        if is_guideline(h.chunk_id):
            if guidelines >= GUIDELINE_K or not guideline_fits(flag, text[h.chunk_id]):
                continue
            guidelines += 1
        kept.append(h)
    # The flag drug's own label before another drug's label that only mentions it (a warfarin
    # flag got a Celebrex passage on "CYP2C9 substrates such as warfarin" ahead of the warfarin
    # label). A label chunk's first line names its drug: "warfarin | JANTOVEN ... | 12 ...".
    own = {d.lower() for d in drugs}

    def secondary(h: Hit) -> bool:
        return not is_guideline(h.chunk_id) and _owner(text[h.chunk_id]) not in own

    # Only for the AUTHORITY_K main slots. A fill-in slot (when there is no paper) goes back to
    # plain distance: for methotrexate + pantoprazole, own-first put an antiretroviral passage
    # from the pantoprazole label ahead of the omeprazole label's methotrexate passage.
    # For a lifestyle flag, passages that name the factor come first (LIFESTYLE_TERMS).
    head = sorted(kept, key=lambda h: (not mentions_trigger(flag, text[h.chunk_id]),
                                       secondary(h), h.distance))[:AUTHORITY_K]  # fmt: skip
    authority = (head + [h for h in kept if h not in head])[:k]
    support = (
        [
            h
            for h in repo.search(qvec, drugs, False, k, column)
            if h.distance <= SUPPORT_MAX_DISTANCE
        ]
        if allow_support
        else []
    )
    return assemble(authority, support, k)


# Flag types the paper collection covers. Papers were selected for drug-gene and drug-drug
# evidence only (docs/progress.md step 6a), so a "supporting paper" for a lab flag can only be
# noise: with Flag-built queries one slipped under the distance cut-off (atorvastatin + ALT).
SUPPORT_FLAG_TYPES = {"gene_drug", "drug_drug", "lifestyle_drug"}


def retrieve(flag: Flag, repo: EvidenceRepository, embedder, k: int = K) -> list[RetrievedChunk]:
    qvec = embedder.embed_query(flag.query_text())
    support = flag.flag_type in SUPPORT_FLAG_TYPES
    hits = select(repo, qvec, flag.drug_ids(), k, allow_support=support, flag=flag)
    via_class = False
    if not hits:  # no chunk names the drug: fall back to passages about its drug class
        hits = select(
            repo, qvec, flag.drug_ids(), k, column="class_drugs", allow_support=support, flag=flag
        )
        via_class = True
    found = repo.get([h.chunk_id for h in hits])
    return [
        RetrievedChunk(evidence=found[h.chunk_id], distance=h.distance, via_class=via_class)
        for h in hits
    ]


# ---------------------------------------------------------------- context (spec step 5)


def build_context(
    evidence: dict[str, list[RetrievedChunk]], max_tokens: int, count
) -> dict[str, list[RetrievedChunk]]:
    """Keep every flag, and as much evidence as fits in max_tokens. When over budget, drop
    chunks from the lowest tier first (papers before labels), farthest first within a tier, and
    never a flag's last chunk while another flag still has more than one."""
    kept = {fid: list(chunks) for fid, chunks in evidence.items()}

    def total() -> int:
        return sum(count(c.evidence.text) for chunks in kept.values() for c in chunks)

    while total() > max_tokens:
        candidates = [
            (c.evidence.tier, c.distance, fid, c)
            for fid, chunks in kept.items()
            for c in chunks
            if len(chunks) > 1
        ]
        if not candidates:
            break  # one chunk per flag already: the caller must raise the budget
        _, _, fid, worst = max(candidates, key=lambda x: (x[0], x[1]))
        kept[fid].remove(worst)
    return kept


# ---------------------------------------------------------------- repositories


class PgRepository:
    """kb.evidence_chunks in PostgreSQL (pgvector cosine distance)."""

    _SQL = """
        SELECT chunk_id, embedding <=> %(q)s AS distance
        FROM kb.evidence_chunks
        WHERE {column} && %(drugs)s AND (tier = 1) = %(authority)s
        ORDER BY distance, chunk_id
        LIMIT %(n)s"""

    def __init__(self, conn):
        self.conn = conn

    def search(self, qvec, drugs, authority, n, column="drugs"):
        if column not in ("drugs", "class_drugs"):
            raise ValueError(column)
        rows = self.conn.execute(
            self._SQL.format(column=column),
            {"q": qvec, "drugs": drugs, "authority": authority, "n": n},
        )
        return [Hit(chunk_id=r[0], distance=float(r[1])) for r in rows.fetchall()]

    def get(self, chunk_ids):
        rows = self.conn.execute(
            """SELECT c.chunk_id, c.doc_id, c.source, c.tier, d.title, c.headings, c.text,
                      c.license
               FROM kb.evidence_chunks c JOIN kb.documents d USING (doc_id)
               WHERE c.chunk_id = ANY(%s)""",
            [chunk_ids],
        ).fetchall()
        return {
            r[0]: EvidenceChunk(
                chunk_id=r[0],
                doc_id=r[1],
                source=r[2],
                tier=r[3],
                title=r[4],
                heading=" > ".join(r[5][0]) if r[5] else None,
                text=r[6],
                license=r[7],
            )
            for r in rows
        }


class InMemoryRepository:
    """Chunks and their normalised vectors in memory: for tests and offline evaluation."""

    def __init__(self, chunks, vectors: np.ndarray, titles: dict[str, str] | None = None):
        self.chunks, self.vectors, self.titles = chunks, vectors, titles or {}

    def search(self, qvec, drugs, authority, n, column="drugs"):
        pool = [
            i
            for i, c in enumerate(self.chunks)
            if set(getattr(c, column)) & set(drugs) and (c.tier == 1) == authority
        ]
        if not pool:
            return []
        dist = 1.0 - self.vectors[pool] @ qvec
        order = sorted(
            range(len(pool)), key=lambda j: (float(dist[j]), self.chunks[pool[j]].chunk_id)
        )
        return [
            Hit(chunk_id=self.chunks[pool[j]].chunk_id, distance=float(dist[j])) for j in order[:n]
        ]

    def get(self, chunk_ids):
        by_id = {c.chunk_id: c for c in self.chunks if c.chunk_id in set(chunk_ids)}
        return {
            cid: EvidenceChunk(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                source=c.source,
                tier=c.tier,
                title=self.titles.get(c.doc_id),
                heading=" > ".join(c.headings[0]) if c.headings else None,
                text=c.text,
                license=c.license,
            )
            for cid, c in by_id.items()
        }


# ---------------------------------------------------------------- LangChain wrapper


def langchain_retriever(repo: EvidenceRepository, embedder, drugs: list[str], k: int = K):
    """A LangChain BaseRetriever over the same rule, for chains that need one (spec). The drug
    filter is fixed per retriever, because a plain query string carries no drugs."""
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    class _FlagEvidenceRetriever(BaseRetriever):
        def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
            hits = select(repo, embedder.embed_query(query), drugs, k)
            found = repo.get([h.chunk_id for h in hits])
            return [
                Document(
                    page_content=found[h.chunk_id].text,
                    metadata={
                        "chunk_id": h.chunk_id,
                        "distance": h.distance,
                        "tier": found[h.chunk_id].tier,
                        "source": found[h.chunk_id].source,
                    },
                )
                for h in hits
            ]

    return _FlagEvidenceRetriever()
