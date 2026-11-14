"""What Qwen sees: the spec's prompt rules, the flags, and only their evidence (spec p16).

    messages = build_messages(patient_summary(patient), flags, evidence)
    messages = add_errors(messages, raw_answer, errors)       # for the one retry

`evidence` maps flag_id -> the EvidenceChunks supplied for that flag (already trimmed to the
context budget by rag.retriever.build_context). Messages are plain dicts
({"role": ..., "content": ...}) so any OpenAI-compatible client can send them.

Evidence is shown to Qwen under short labels (E1, E2, ...), not its chunk IDs
("dailymed:<36-character setid>:clinical_pharmacology:006"): in the first real run copying
those IDs was about a third of every answer. `resolve_labels` turns the labels in an answer
back into chunk IDs before validation, so the Report and the validator only see real IDs.
"""

from __future__ import annotations

import json

from drug_safety.contracts import EvidenceChunk, Flag, Patient

PROMPT_VERSION = "explain-v3"  # v2: E-labels, flag list, wording rules (first Colab run)
# v3: verbatim quotes per explanation; explanation says only what its quotes say; the
# evidence's instructions go in quotes, not in the model's own words (second Colab run)

SYSTEM = """You write a short drug-safety report for a pharmacist or clinician.
A rule engine has already decided which findings are unsafe ("flags"). You do not decide what is
unsafe and you do not add findings. You explain each flag in plain language, using the evidence
passages supplied for it, and you list points the clinician may want to review.

Rules:
1. Use only the supplied flags and evidence passages.
2. Never add a drug, interaction, dose or number that is not in them. Evidence passages may name
   other drugs (alternatives, other members of a class): do not repeat those names; write "an
   alternative antiplatelet", "another NSAID" and so on. Name only the patient's drugs.
3. If a flag has no evidence, say so under "uncertainties" and leave its evidence_ids empty.
4. Cite evidence by its label (E1, E2, ...) on every explanation and suggestion. Cite only
   labels listed under that same flag.
5. For every flag with evidence, copy one to three sentences from its evidence into "quotes",
   word for word: the sentences your explanation rests on. Do not shorten or reword them.
6. The plain_explanation says only what its quotes say, in one or two plain sentences. Do not
   add effects, causes, mechanisms or risks that the quotes do not state. Keep their limits
   ("at high dose", "more than 1.2 liters daily", "in poor metabolizers").
7. Start every suggestion with "Consider reviewing", "Consider monitoring" or "Consider
   checking". In your own words (summary, explanations, suggestions), never tell anyone to stop,
   start, switch or avoid a drug, and never say a dose should, must or needs to change. When a
   passage gives such an instruction, put that sentence in "quotes" instead of restating it.
8. Return only JSON that matches the schema below. No markdown, no text before or after it.

Schema:
{
  "summary": "one or two sentences for the whole patient",
  "flags_explained": [
    {"flag_id": "<a flag_id below>", "plain_explanation": "...", "evidence_ids": ["E1"],
     "quotes": [{"evidence_id": "E1", "text": "<a sentence copied exactly from E1>"}]}
  ],
  "suggestions_for_clinician_review": [
    {"text": "Consider reviewing ...", "flag_id": "<a flag_id below>", "evidence_ids": ["E1"]}
  ],
  "uncertainties": ["..."]
}
Give exactly one entry in "flags_explained" for every flag."""


# Patient fields left out of the summary on purpose. Every other field must appear
# (tests/llm/test_prompts.py walks the contract, so a field added later cannot be dropped).
SUMMARY_OMITS = {"patient_id"}  # an identifier, not clinical content


def patient_summary(patient: Patient) -> str:
    """Short, de-identified summary (no patient_id): what Qwen needs to word the report."""
    lines = [f"{patient.age}-year-old {patient.sex}."]
    lines.append("Prescriptions: " + "; ".join(_rx(p) for p in patient.prescriptions) + ".")
    if patient.labs:
        lines.append("Labs: " + "; ".join(_lab(x) for x in patient.labs))
    if patient.genotypes:
        lines.append(
            "Genotypes: " + "; ".join(f"{g.gene} {g.diplotype}" for g in patient.genotypes)
        )
    if patient.weight_kg is not None:
        lines.append(f"Weight: {patient.weight_kg:g} kg")
    if patient.conditions:
        lines.append("Conditions: " + "; ".join(c.replace("_", " ") for c in patient.conditions))
    life = patient.lifestyle
    habits = [f"smoking: {life.smoking}" if life.smoking else "",
              f"alcohol: {life.alcohol}" if life.alcohol else "",
              f"diet: {life.diet.type}" if life.diet.type else ""]  # fmt: skip
    if any(habits):
        lines.append("Lifestyle: " + ", ".join(h for h in habits if h) + ".")
    # The meals are what diet rules fire on (grapefruit, vitamin K): without them the model sees
    # a flag with no reason in the patient (second Colab run, p02 and p07).
    if life.diet.meals:
        lines.append("Meals: " + "; ".join(life.diet.meals) + ".")
    return "\n".join(lines)


def _lab(x) -> str:
    when = f" ({x.taken_at})" if x.taken_at else ""
    return f"{x.test} {x.value:g} {x.unit}{when}"


def _rx(p) -> str:
    status = f"({p.status})" if p.status else ""  # new or ongoing, when given
    return " ".join(x for x in (p.name, p.dose, p.frequency, status) if x)


def trigger(flag: Flag) -> str:
    if flag.interacting_drug:
        return f"taken with {flag.interacting_drug}"
    if flag.gene:
        return f"{flag.gene} {flag.phenotype}"
    if flag.lab:
        x = flag.lab
        return f"{x.measure} {x.value:g} {x.unit} (rule: {x.operator} {x.threshold:g})"
    if flag.lifestyle:
        return f"{flag.lifestyle.factor_type}: {flag.lifestyle.factor_value}"
    return ""


def evidence_labels(flags: list[Flag], evidence: dict[str, list[EvidenceChunk]]) -> dict[str, str]:
    """chunk_id -> "E1", "E2", ... in prompt order; a chunk under two flags keeps one label."""
    labels: dict[str, str] = {}
    for f in flags:
        for c in evidence.get(f.flag_id, []):
            labels.setdefault(c.chunk_id, f"E{len(labels) + 1}")
    return labels


def _evidence_block(chunk: EvidenceChunk, label: str) -> str:
    where = ", ".join(x for x in (chunk.source, chunk.title, chunk.heading) if x)
    return f"[{label}] ({where})\n{chunk.text.strip()}"


def flags_block(flags: list[Flag], evidence: dict[str, list[EvidenceChunk]]) -> str:
    labels = evidence_labels(flags, evidence)
    parts = []
    for f in flags:
        head = (
            f"FLAG {f.flag_id} | {f.flag_type} | severity: {f.severity} | drug: {f.drug} | "
            f"trigger: {trigger(f)} | reason: {f.reason} | rule source: {f.source}"
        )
        chunks = evidence.get(f.flag_id, [])
        body = (
            "\n\n".join(_evidence_block(c, labels[c.chunk_id]) for c in chunks)
            if chunks
            else "No evidence was found for this flag."
        )
        ids = ", ".join(labels[c.chunk_id] for c in chunks) or "none"
        parts.append(f"{head}\nEvidence for {f.flag_id}: {ids}\n\n{body}")
    return "\n\n-----\n\n".join(parts)


def build_messages(
    summary: str, flags: list[Flag], evidence: dict[str, list[EvidenceChunk]]
) -> list[dict]:
    ids = ", ".join(f.flag_id for f in flags)
    user = (
        f"PATIENT\n{summary}\n\n"
        f"FLAGS AND THEIR EVIDENCE ({len(flags)} flags)\n\n{flags_block(flags, evidence)}\n\n"
        f'"flags_explained" must have exactly {len(flags)} entries, one for each of: {ids}.\n'
        "Return the JSON report now."
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def resolve_labels(answer: str, labels: dict[str, str]) -> str:
    """The answer with every cited label (E1) replaced by its chunk ID. Unknown labels and
    unparseable answers are left as they are, for the validator to report."""
    from drug_safety.llm.validator import parse

    data, issues = parse(answer)
    if issues:
        return answer
    to_id = {v: k for k, v in labels.items()}
    for key in ("flags_explained", "suggestions_for_clinician_review"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("evidence_ids"), list):
                item["evidence_ids"] = [to_id.get(x, x) if isinstance(x, str) else x
                                        for x in item["evidence_ids"]]  # fmt: skip
            for q in (item.get("quotes") or []) if isinstance(item, dict) else []:
                if isinstance(q, dict) and isinstance(q.get("evidence_id"), str):
                    q["evidence_id"] = to_id.get(q["evidence_id"], q["evidence_id"])
    return json.dumps(data, ensure_ascii=False)


def to_labels(message: str, labels: dict[str, str]) -> str:
    """A validator message as Qwen should read it: chunk IDs shown as their labels."""
    for cid in sorted(labels, key=len, reverse=True):
        message = message.replace(cid, labels[cid])
    return message


def add_errors(messages: list[dict], answer: str, errors: list[str]) -> list[dict]:
    """Spec retry: the same conversation plus the failed answer and the error list."""
    listed = "\n".join(f"- {e}" for e in errors)
    return [
        *messages,
        {"role": "assistant", "content": answer},
        {
            "role": "user",
            "content": "Your answer failed these checks:\n"
            f"{listed}\n\nReturn the corrected JSON report only, following every rule.",
        },
    ]


def render(messages: list[dict]) -> str:
    """Readable dump for logs and the manual review."""
    return json.dumps(messages, indent=1, ensure_ascii=False)
