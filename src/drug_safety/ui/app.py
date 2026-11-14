"""Streamlit screen (spec p17). Talks only to the API at API_URL.

    streamlit run src/drug_safety/ui/app.py        # or: make ui

Layout logic lives in view.py and form handling in patient_input.py (both unit-tested); this
file only places widgets. Everything that comes from the API is escaped before display.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from drug_safety.config import get_settings
from drug_safety.contracts import AssessResponse
from drug_safety.ui import view
from drug_safety.ui.api_client import ApiClient, ApiError
from drug_safety.ui.patient_input import (
    ALCOHOL,
    CONDITIONS,
    DIET,
    SMOKING,
    STATUS,
    Form,
    InputError,
    lab_tests,
    load_examples,
    parse_upload,
    to_form,
    to_patient,
)

COLUMNS = {
    "prescriptions": ["name", "dose", "frequency", "status"],
    "labs": ["test", "value", "unit", "taken_at"],
    "genotypes": ["gene", "diplotype"],
}

st.set_page_config(page_title="Drug Safety Check", page_icon="💊", layout="wide")
settings = get_settings()
client = ApiClient(settings.api_url, settings.api_timeout_s)
state = st.session_state
state.setdefault("form", Form())
state.setdefault("form_version", 0)
state.setdefault("response", None)


def banner(text: str) -> None:
    """Spec: a fixed banner. Sticky under Streamlit's top bar, so it stays on screen."""
    st.markdown(
        # Streamlit wraps each container in a stLayoutWrapper; the wrapper is what must stick.
        "<style>[data-testid='stLayoutWrapper']:has(> .st-key-disclaimer)"
        "{position:sticky;top:3.75rem;z-index:999;background:var(--background-color,#fff);}"
        "</style>",
        unsafe_allow_html=True,
    )
    with st.container(key="disclaimer"):
        st.warning(f"**{view.md(text)}**", icon="⚕️")


def load_form(form: Form) -> None:
    state.form = form
    state.form_version += 1  # new widget keys, so the widgets show the loaded values
    state.response = None


def bullets(lines: list[str]) -> str:
    return "\n".join(f"- {view.md(x)}" for x in lines)


def maybe(x: str | None) -> str:
    return x or "not given"


def table(name: str, rows: list[dict]) -> pd.DataFrame:
    cols = COLUMNS[name]
    df = pd.DataFrame([{c: "" if r.get(c) is None else str(r[c]) for c in cols} for r in rows])
    return df.reindex(columns=cols).astype("string")


# ---------------------------------------------------------------- sidebar


with st.sidebar:
    st.header("Patient")
    examples = load_examples()
    pick = st.selectbox(
        "Load example (synthetic)",
        [None, *range(len(examples))],
        format_func=lambda i: "—" if i is None else examples[i].label,
        key="example_pick",
    )
    if st.button("Load example", disabled=pick is None):
        load_form(to_form(examples[pick].patient))
        st.rerun()
    upload = st.file_uploader("Or upload a patient JSON", type=["json"])
    if upload is not None and st.button("Load file"):
        try:
            load_form(to_form(parse_upload(upload.getvalue())))
            st.rerun()
        except InputError as e:
            st.error("The file is not a valid patient:\n\n" + bullets(e.problems))
    if st.button("Clear form"):
        load_form(Form())
        st.rerun()

    st.header("API")
    st.caption(f"`{client.base_url.replace('`', '')}`")  # code span: shown as is, no escaping
    if st.button("Check API health") or "health" not in state:
        state.health = client.health()
    ok, body = state.health
    if isinstance(body, dict):
        (st.success if ok and body.get("status") == "ok" else st.warning)(
            f"Status: {view.md(str(body.get('status', 'unknown')))}"
        )
        for name, c in (body.get("checks") or {}).items():
            if isinstance(c, dict):
                mark = "✅" if c.get("ok") else "❌"
                st.caption(f"{mark} {view.md(name)}: {view.md(str(c.get('detail', '')))}")
            else:
                st.caption(f"{view.md(name)}: {view.md(str(c))}")
        if body.get("stub"):
            st.info("Connected to the stand-in API: only the example patients can be assessed.")
    else:
        st.error(view.md(str(body)))


# ---------------------------------------------------------------- main


current = state.response
banner(current.disclaimer if current else view.DISCLAIMER)
st.title("Drug safety check")

f, v = state.form, state.form_version
# Folded away once there is a result, so the result is near the top (a new label = new widget,
# so `expanded` applies again).
details = "Patient details" if current is None else "Patient details (click to edit)"
with st.expander(details, expanded=current is None), st.form(f"patient_{v}"):
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    pid = c1.text_input("Patient ID", f.patient_id)
    age = c2.number_input("Age (years)", 0, 120, value=f.age, step=1)
    sex = c3.selectbox("Sex", [None, "female", "male"], index=[None, "female", "male"].index(f.sex))
    weight = c4.number_input(
        "Weight (kg, optional)", 0.0, 400.0, value=f.weight_kg, step=0.5,
        help="Lets kidney rules use creatinine clearance, as many labels do",
    )  # fmt: skip

    st.subheader("Prescriptions")
    rx = st.data_editor(
        table("prescriptions", f.prescriptions),
        num_rows="dynamic",
        width="stretch",
        key=f"rx_{v}",
        column_config={
            "name": st.column_config.TextColumn("Drug (brand, generic or NDC)", required=True),
            "dose": "Dose",
            "frequency": "Frequency",
            "status": st.column_config.SelectboxColumn(
                "New or ongoing (optional)",
                options=list(STATUS),
                help="Starting it now, or already taking it (clozapine's rules differ)",
            ),  # fmt: skip
        },
    )
    st.subheader("Lab values (LFT, CBC, RFT)")
    labs = st.data_editor(
        table("labs", f.labs),
        num_rows="dynamic",
        width="stretch",
        key=f"labs_{v}",
        column_config={"taken_at": st.column_config.TextColumn("Taken on (YYYY-MM-DD)")},
    )
    known = lab_tests()
    if known:
        st.caption(
            "Tests the rules use: "
            + ", ".join(f"{n} ({u}, {p})" for n, u, p in known)
            + ". eGFR is calculated from creatinine, age and sex."
        )
    st.subheader("Genotypes")
    genes = st.data_editor(
        table("genotypes", f.genotypes),
        num_rows="dynamic",
        width="stretch",
        key=f"genes_{v}",
        column_config={
            "diplotype": st.column_config.TextColumn(
                "Diplotype (e.g. *2/*2; HLA-B: *58:01 positive)"
            )
        },
    )
    st.subheader("Lifestyle")
    l1, l2, l3 = st.columns(3)
    smoking = l1.selectbox("Smoking", SMOKING, SMOKING.index(f.smoking), format_func=maybe)
    alcohol = l2.selectbox("Alcohol", ALCOHOL, ALCOHOL.index(f.alcohol), format_func=maybe)
    diet = l3.selectbox(
        "Diet type",
        DIET,
        index=DIET.index(f.diet_type),
        format_func=lambda x: {"veg": "vegetarian", "non_veg": "non-vegetarian"}.get(x, maybe(x)),
    )
    meals = st.text_area("Meals (one per line)", f.meals)
    conditions = st.multiselect(
        "Conditions that change a rule (optional)", list(CONDITIONS), default=f.conditions,
        format_func=CONDITIONS.get,
    )  # fmt: skip
    use_llm = st.checkbox("Write an AI summary (Qwen)", value=True)
    submitted = st.form_submit_button("Assess", type="primary")

if submitted:
    form = Form(
        patient_id=pid,
        age=None if age is None else int(age),
        sex=sex,
        prescriptions=rx.to_dict("records"),
        labs=labs.to_dict("records"),
        genotypes=genes.to_dict("records"),
        smoking=smoking,
        alcohol=alcohol,
        diet_type=diet,
        meals=meals,
        weight_kg=None if not weight else float(weight),
        conditions=conditions,
    )
    state.form = form  # keep what was typed if the request fails
    try:
        patient = to_patient(form)
        with st.spinner("Assessing… the AI summary can take a minute."):
            state.response = client.assess(patient, use_llm=use_llm)
        st.rerun()
    except InputError as e:
        st.error("Please fix:\n\n" + bullets(e.problems))
    except ApiError as e:
        st.error(view.md(str(e)))


def evidence(c: view.Cited) -> None:
    with st.expander(f"[{c.number}] {view.source_line(c.chunk)}"):
        st.markdown(view.md(c.chunk.text.strip()).replace("\n", "  \n"))
        url = view.source_url(c.chunk)
        link = f"[Open the source]({url}) · " if url else ""
        st.caption(f"{link}{view.md(c.chunk.chunk_id)} · licence: {view.md(c.chunk.license)}")


def refs(numbers: list[int]) -> str:
    return " " + " ".join(f"\\[{n}\\]" for n in numbers) if numbers else ""


def results(r: AssessResponse) -> None:
    st.divider()
    st.header(f"Result for {view.md(r.patient_id)}")
    if r.notice:
        st.warning(view.md(r.notice))
    rows = view.flag_table(r)
    st.subheader(f"Flags ({len(rows)})")
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.success("No flags were raised for these prescriptions.")
    for g in r.data_gaps:
        st.info(f"Note ({g.kind}): {view.md(g.message)}")

    views = view.flag_views(r)
    st.caption(view.REPORT_SOURCE[r.report_source])
    if r.report:
        st.subheader("Summary")
        st.markdown(view.md(r.report.summary))
        st.subheader("Suggestions for clinician review")
        any_s = False
        for fv in views:
            for text, nums in fv.suggestions:
                any_s = True
                st.markdown(f"- {view.md(text)} *({view.md(fv.flag.drug)}{refs(nums)})*")
        if not any_s:
            st.caption("None.")
        if r.report.uncertainties:
            st.subheader("Uncertainties")
            for u in r.report.uncertainties:
                st.markdown(f"- {view.md(u)}")

    st.subheader("Each flag and its evidence")
    for fv in views:
        fl = fv.flag
        st.markdown(
            f"#### {view.SEVERITY_ICON[fl.severity]} {view.md(fl.drug)} · "
            f"{view.TYPE_LABEL[fl.flag_type]} · {fl.severity}"
        )
        st.caption(
            view.md(f"{view.trigger(fl)} · {fl.reason} · rule source: {fl.source} · {fl.flag_id}")
        )
        if fv.explanation:
            st.markdown(view.md(fv.explanation) + refs([c.number for c in fv.cited]))
        elif r.report:
            st.warning("The report has no explanation for this flag.")
        if fv.missing_ids:
            missing = view.md(", ".join(fv.missing_ids))
            st.error(f"Cited evidence not supplied by the API: {missing}")
        for c in fv.cited:
            evidence(c)
        if fv.other:
            st.caption("Also retrieved, not cited:" if fv.cited else "Evidence retrieved:")
            for c in fv.other:
                evidence(c)
        if not fv.cited and not fv.other:
            st.caption("No evidence was found for this flag.")
    st.download_button(
        "Download the full response (JSON)",
        json.dumps(r.model_dump(mode="json"), indent=1),
        file_name=f"assessment_{r.patient_id}.json",
        mime="application/json",
    )


if current is not None:
    results(current)
