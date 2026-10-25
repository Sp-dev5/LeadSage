import json

import pandas as pd
import pycountry
import streamlit as st
from pydantic import ValidationError

import app_ui as ui
from agent.score_lead import ICP, ICP_PATH

ui.page_header("Setup", "person_search", "Ideal customer", "",
               "Who you sell to. Every lead is scored against this profile, saved in data/icp.json. "
               "After a change, rescore the leads to apply it.")

raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
country_names = sorted({c.name for c in pycountry.countries} | set(raw["geography_countries"]))

with st.form("icp"):
    buyer = st.text_area("Buyer persona", raw["buyer_persona"])
    pain = st.text_area("Problem you solve", raw["pain_solved"])
    notes = st.text_area("What you sell and buying signals", raw.get("notes", ""), height=140)
    with st.container(horizontal=True):
        size_min = st.number_input("Smallest company (employees)", min_value=1, value=raw["company_size_min"])
        size_max = st.number_input("Largest company (employees)", min_value=1, value=raw["company_size_max"])
    countries = st.multiselect("Target countries", country_names, default=raw["geography_countries"],
                               help="Sets the 'Only my ICP region' filter on the Leads page. Never scored.")
    saved = st.form_submit_button("Save", type="primary", icon=":material/save:")

if saved:
    new = raw | {"buyer_persona": buyer.strip(), "pain_solved": pain.strip(), "notes": notes.strip(),
                 "company_size_min": int(size_min), "company_size_max": int(size_max),
                 "geography_countries": countries}
    if size_min > size_max:
        st.error("The smallest company size can't be bigger than the largest.", icon=":material/error:")
    else:
        try:
            ICP.model_validate(new)
        except ValidationError as e:
            st.error(f"Not saved: {e.errors()[0]['msg']}", icon=":material/error:")
        else:
            ICP_PATH.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            st.success("Saved. Rescore the leads to apply it.", icon=":material/check_circle:")

with st.container(border=True):
    st.markdown("**Rules**")
    st.dataframe(pd.DataFrame([{"If": f"{r['fact']} = {r['value']}", "Then": r["action"], "Why": r["label"]}
                               for r in raw["fact_rules"]]), hide_index=True)
    st.markdown("**Scoring settings**")
    st.caption("Weights and cut-offs used by the fit score. Edit them in data/icp.json.")
    st.json(raw["settings"], expanded=False)
