import pandas as pd
import streamlit as st

from agent import store
from agent.pipeline import lead_files, read_leads, score_all, signal_rows
from agent.score_lead import _parse_headcount, load_icp

VERDICTS = ["qualify", "review", "disqualify"]
VERDICT_COLORS = {"qualify": "green", "review": "orange", "disqualify": "red"}
UNKNOWN = "Unknown"

icp = load_icp()

st.title("Leads")
st.caption("Every company researched and scored against your ideal customer, best first. "
           "Location is a filter, never part of the score.")

filters_box = st.sidebar.container()
st.sidebar.divider()

with st.sidebar:
    st.subheader("Score new leads")
    files = lead_files()
    file = st.selectbox("Lead list", files, index=files.index("real_leads.csv") if "real_leads.csv" in files else 0)
    skip_scored = st.checkbox("Skip companies already scored", value=True)
    run = st.button("Research and score", icon=":material/bolt:", type="primary", width="stretch")

if run:
    done = {lead.name.lower() for lead in store.load_all()} if skip_scored else set()
    rows = [r for r in read_leads(file) if r["name"].strip().lower() not in done]
    with st.status(f"Researching and scoring {len(rows)} companies", expanded=True) as status:
        score_all(rows, icp, signal_rows(), report=st.write)
        status.update(label=f"Scored {len(rows)} companies", state="complete", expanded=False)

leads = store.load_all()
if not leads:
    st.info("No leads scored yet. Pick a lead list in the sidebar and click **Research and score**.",
            icon=":material/info:")
    st.stop()

by_name = {lead.name: lead for lead in leads}
df = pd.DataFrame([{
    "Company": lead.name,
    "Verdict": lead.score.verdict,
    "Fit": lead.score.fit,
    "Stage": lead.stage,
    "Country": lead.score.location.country or UNKNOWN,
    "In ICP region": lead.score.location.in_region,
    "Headcount": _parse_headcount(lead.company.size),
    "Sales roles open": len(lead.score.sales_roles),
    "Summary": lead.score.summary,
} for lead in leads])

with filters_box:
    st.subheader("Filters")
    known = sorted(set(df["Country"]) - {UNKNOWN})
    region_only = st.toggle("Only my ICP region",
                            help="Countries set on the Ideal customer page. Unknown locations stay visible.")
    picked = st.multiselect("Country", known + [UNKNOWN] if UNKNOWN in set(df["Country"]) else known,
                            placeholder="All countries")
    verdicts = st.pills("Verdict", VERDICTS, selection_mode="multi", default=VERDICTS,
                        format_func=str.capitalize)
    min_fit = st.slider("Minimum fit", 0, 100, 0, step=5)
    hiring_only = st.toggle("Only companies hiring for sales")

location_ok = pd.Series(True, index=df.index)
if picked:
    location_ok &= df["Country"].isin(picked)
if region_only:
    location_ok &= df["In ICP region"] != "no"
other_ok = df["Verdict"].isin(verdicts or VERDICTS) & (df["Fit"] >= min_fit)
if hiring_only:
    other_ok &= df["Sales roles open"] > 0
shown = df[location_ok & other_ok].sort_values("Fit", ascending=False).reset_index(drop=True)
hidden_by_location = df[~location_ok & other_ok]

with st.container(horizontal=True):
    st.metric("Leads shown", f"{len(shown)} of {len(df)}", border=True)
    for verdict in VERDICTS:
        st.metric(verdict.capitalize(), int((shown["Verdict"] == verdict).sum()), border=True)

if len(hidden_by_location):
    with st.expander(f"{len(hidden_by_location)} hidden by your location filter"):
        for _, r in hidden_by_location.sort_values("Fit", ascending=False).iterrows():
            st.markdown(f"**{r['Company']}** ({r['Country']}): {r['Verdict']} {r['Fit']}")

event = st.dataframe(
    shown,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="leads_table",
    column_config={
        "Fit": st.column_config.ProgressColumn("Fit", min_value=0, max_value=100, format="%d"),
        "Headcount": st.column_config.NumberColumn("Headcount", format="%d"),
        "Summary": st.column_config.TextColumn("Summary", width="large"),
    },
)

selected = event.selection.rows
if not selected or selected[0] >= len(shown):
    st.caption("Select a row to see why it scored that way.")
    st.stop()

lead = by_name[shown.iloc[selected[0]]["Company"]]
s = lead.score

with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(lead.name)
        st.badge(s.verdict.capitalize(), color=VERDICT_COLORS[s.verdict])
        st.badge(f"Fit {s.fit}/100", color="gray")
    st.write(s.summary)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Why this score**")
        for c in s.criteria:
            st.markdown(f"{c.criterion.capitalize()}: **{c.score}/5**")
            st.caption(c.evidence)
        if s.disqualifier_hit:
            st.error(f"Disqualified: {s.disqualifier_reason}", icon=":material/block:")
        for flag in s.review_flags:
            st.warning(f"Held at review: {flag}", icon=":material/flag:")
        if so := s.second_opinion:
            st.info(f"Second opinion from {so.model}: {so.verdict} {so.fit}", icon=":material/balance:")
            with st.expander("Second model's reasons"):
                for c in so.criteria:
                    st.markdown(f"{c.criterion.capitalize()}: **{c.score}/5**")
                    st.caption(c.evidence)

    with right:
        st.markdown("**Location** (filter only, not scored)")
        st.caption(s.location.evidence)
        st.markdown("**Open sales roles**")
        if s.sales_roles:
            for role in s.sales_roles:
                st.markdown(f"- [{role.title}]({role.source}) ({role.match} match)")
        else:
            st.caption("None found on a job board.")
        st.markdown("**Source**")
        text = "website text" if lead.text_source == "fetched" else lead.text_source
        st.caption(f"{lead.url or 'no url'} · scored from {text} · {lead.scored_at} · model {s.model or 'unknown'}")
        with st.expander("Facts the model found"):
            st.dataframe(pd.DataFrame(list(s.facts.items()), columns=["Fact", "Value"]), hide_index=True)
        if s.rejected_quotes:
            with st.expander(f"Quotes rejected by the quote check ({len(s.rejected_quotes)})"):
                for r in s.rejected_quotes:
                    st.caption(r)
