import json

import pandas as pd
import pycountry
import streamlit as st
from pydantic import ValidationError

import app_ui as ui
from agent.files import write_text_atomic
from agent.icp_assist import suggest
from agent.score_lead import ICP, ICP_PATH

ui.page_header("Setup", "person_search", "Ideal customer", "",
               "Who you sell to. Every lead is scored against this profile, saved in data/icp.json. "
               "After a change, rescore the leads to apply it.")

raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
country_names = sorted({c.name for c in pycountry.countries} | set(raw["geography_countries"]))

TEXT_FIELDS = {
    "buyer_persona": ("Buyer persona", 110),
    "pain_solved": ("Problem you solve", 110),
    "notes": ("What you sell and buying signals", 140),
}
for field in TEXT_FIELDS:
    st.session_state.setdefault(f"icp_{field}", raw.get(field, ""))


def use_suggestion(field: str) -> None:
    st.session_state[f"icp_{field}"] = st.session_state.pop(f"suggestion_{field}")


def dismiss(field: str) -> None:
    st.session_state.pop(f"suggestion_{field}", None)


with st.container(border=True, key="panel_1"):
    st.caption("Type a few words and use Suggest for a fuller version. A fuller description helps the scorer "
               "and the search. A suggestion only replaces your text when you click Use this.")
    for field, (label, height) in TEXT_FIELDS.items():
        st.text_area(label, key=f"icp_{field}", height=height)
        if st.button("Suggest a fuller description", key=f"suggest_{field}", icon=":material/auto_awesome:",
                     type="tertiary"):
            context = {f: st.session_state[f"icp_{f}"] for f in TEXT_FIELDS}
            with st.spinner("Writing a suggestion with the local model..."):
                try:
                    st.session_state[f"suggestion_{field}"] = suggest(field, context[field], context)
                except Exception as e:
                    st.error(f"No suggestion: the local model did not answer ({str(e)[:120]}). "
                             "Is `ollama serve` running?", icon=":material/error:")
        if suggestion := st.session_state.get(f"suggestion_{field}"):
            with st.container(border=True, key=f"rise_suggestion_{field}"):
                st.markdown(f"**Suggestion**  \n{suggestion}")
                with st.container(horizontal=True):
                    st.button("Use this", key=f"use_{field}", type="primary", icon=":material/check:",
                              on_click=use_suggestion, args=(field,))
                    st.button("Dismiss", key=f"dismiss_{field}", on_click=dismiss, args=(field,))

    with st.container(horizontal=True):
        size_min = st.number_input("Smallest company (employees)", min_value=1, value=raw["company_size_min"])
        size_max = st.number_input("Largest company (employees)", min_value=1, value=raw["company_size_max"])
    countries = st.multiselect("Target countries", country_names, default=raw["geography_countries"],
                               help="Sets the 'Only my ICP region' filter on the Leads page. Never scored.")
    saved = st.button("Save", type="primary", icon=":material/save:")

if saved:
    new = raw | {f: st.session_state[f"icp_{f}"].strip() for f in TEXT_FIELDS} | {
        "company_size_min": int(size_min), "company_size_max": int(size_max), "geography_countries": countries}
    if size_min > size_max:
        st.error("The smallest company size can't be bigger than the largest.", icon=":material/error:")
    else:
        try:
            ICP.model_validate(new)
        except ValidationError as e:
            st.error(f"Not saved: {e.errors()[0]['msg']}", icon=":material/error:")
        else:
            write_text_atomic(ICP_PATH, json.dumps(new, indent=2, ensure_ascii=False) + "\n")
            st.success("Saved. Rescore the leads to apply it.", icon=":material/check_circle:")

s = raw["settings"]
with st.container(border=True, key="panel_2"):
    st.markdown("**Rules**")
    st.dataframe(pd.DataFrame([{"If": f"{r['fact']} = {r['value']}", "Then": r["action"], "Why": r["label"]}
                               for r in raw["fact_rules"]]), hide_index=True)

with st.container(border=True, key="panel_3"):
    st.markdown("**Scoring settings**")
    st.caption("How the fit score is worked out. These are plain numbers in data/icp.json, not model settings: "
               "the model never sees them.")
    weights, cutoffs = st.columns(2, gap="large")
    with weights:
        st.markdown("How much each criterion counts")
        total = sum(s["weights"].values()) or 1
        for name, w in s["weights"].items():
            st.progress(w / max(s["weights"].values()), text=f"{name.capitalize()}: {round(100 * w / total)}%")
    with cutoffs:
        st.markdown("Cut-offs")
        st.dataframe(pd.DataFrame([
            ("Qualify at fit", s["qualify_at"]),
            ("Disqualify below fit", s["disqualify_below"]),
            ("A failed gate caps fit at", s["fail_cap"]),
            ("Gate fails at a score of", s["gate_fail_max"]),
            ("Minimum headcount", s["min_headcount"]),
            ("Hard fail above this many times the largest size", s["max_size_factor"]),
            ("Score when unknown", s["unknown_score"]),
            ("Models disagree at a fit gap of", s["disagree_gap"]),
            ("A quote needs at least this many words", s["min_quote_words"]),
            ("A qualify needs a quote for", s["qualify_needs_evidence"]),
        ], columns=["Setting", "Value"]).astype(str), hide_index=True)
