from collections import Counter

import pandas as pd
import streamlit as st

import app_ui as ui
from agent import store
from agent.outreach import load_outreach
from agent.pipeline import lead_files, read_leads, score_all, signal_rows
from agent.readiness import GROUPS, group
from agent.score_lead import _parse_headcount, load_icp

icp, cfg = load_icp(), load_outreach()

ui.page_header("Pipeline", "table_rows", "Leads", "",
               "Every company scored against your ideal customer. Select one to see why it scored that way.")

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
    todo = [r for r in read_leads(file) if r["name"].strip().lower() not in done]
    with st.status(f"Researching and scoring {len(todo)} companies", expanded=True) as status:
        score_all(todo, icp, signal_rows(), report=st.write)
        status.update(label=f"Scored {len(todo)} companies", state="complete", expanded=False)

leads = store.load_all()
if not leads:
    st.info("No leads scored yet. Pick a lead list in the sidebar and click **Research and score**.",
            icon=":material/info:")
    st.stop()

all_rows = ui.lead_rows(leads, cfg)
df = ui.lead_frame(all_rows)
df["Group"] = [group(r.lead.score, icp.settings.qualify_at) for r in all_rows]

with filters_box:
    st.subheader("Filters")
    known = sorted(set(df["Country"]) - {ui.UNKNOWN})
    region_only = st.toggle("Only my ideal-customer region",
                            help="Countries set on the Ideal customer page. Unknown locations stay visible.")
    picked = st.multiselect("Country", known + [ui.UNKNOWN] if ui.UNKNOWN in set(df["Country"]) else known,
                            placeholder="All countries")
    min_fit = st.slider("Minimum fit", 0, 100, 0, step=5)
    hiring_only = st.toggle("Only companies hiring for sales")

location_ok = pd.Series(True, index=df.index)
if picked:
    location_ok &= df["Country"].isin(picked)
if region_only:
    location_ok &= df["In ICP region"] != "no"
other_ok = df["Fit"] >= min_fit
if hiring_only:
    other_ok &= df["Sales roles"] > 0
filtered = df[location_ok & other_ok]
rows = [all_rows[i] for i in filtered.index]
hidden_by_location = df[~location_ok & other_ok]

if not rows:
    st.warning("No leads match these filters. Loosen them in the sidebar.", icon=":material/filter_alt_off:")
    st.stop()

if len(df) > len(rows):
    st.caption(f"{len(df) - len(rows)} of {len(df)} leads hidden by the filters in the sidebar.")

if len(hidden_by_location):
    with st.expander(f"{len(hidden_by_location)} hidden by your location filter", icon=":material/visibility_off:"):
        for _, h in hidden_by_location.sort_values("Fit", ascending=False).iterrows():
            st.markdown(f"**{h['Company']}** ({h['Country']}): {h['Verdict'][0].capitalize()}, fit {h['Fit']}")

group_counts = Counter(filtered["Group"])
group_counts["All"] = len(filtered)
st.session_state.setdefault("dashboard_group", "Qualify")
with st.container(border=True, key="rise_1"):
    picked_group = st.segmented_control(
        "Show", GROUPS, required=True, key="dashboard_group", label_visibility="collapsed",
        format_func=lambda g: f"{g}  {group_counts[g]}")
    table = filtered if picked_group == "All" else filtered[filtered["Group"] == picked_group]
    table = table.sort_values("Fit", ascending=False, kind="stable").reset_index(drop=True)
    columns = ["Logo", "Company", "Website", "Verdict", "Fit", "Hook", "Contact", "Stage", "Country",
               "Headcount", "Sales roles"]
    if picked_group != "Qualify":
        columns.append("Held back by")
    with st.container(key=f"swap_{GROUPS.index(picked_group)}"):
        st.caption({
            "Qualify": "Strong matches with nothing holding them back.",
            "Strong fit": f"Fit {icp.settings.qualify_at} or more, held at review by one rule. "
                          "The reason is in the last column.",
            "Review": "Worth a look. Each one says what held it back.",
            "Disqualify": "Not a fit, with the reason.",
            "All": "Every lead, best fit first.",
        }[picked_group])
        if table.empty:
            st.caption("No leads here with these filters.")
            event = None
        else:
            event = st.dataframe(
                table,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="leads_table",
                height=min(40 + 35 * len(table), 525),
                column_order=columns,
                column_config=ui.lead_columns(),
            )

if event and event.selection.rows and event.selection.rows[0] < len(table):
    st.session_state.dashboard_lead = table.iloc[event.selection.rows[0]]["Company"]
names = [r.lead.name for r in rows]
if st.session_state.get("dashboard_lead") not in names:
    st.caption("Select a row to see why it scored that way.")
    st.stop()

index = names.index(st.session_state.dashboard_lead)
current = rows[index]
lead, s, r = current.lead, current.lead.score, current.ready
headcount = _parse_headcount(lead.company.size)

with st.container(border=True, key=f"panel_card_{index}"):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.image(ui.logo(lead.name), width=40)
        st.subheader(lead.name)
        ui.verdict_badge(s.verdict)
        st.badge(f"Fit {s.fit}/100", color="gray")
        st.badge(lead.stage.capitalize(), color="gray")
        if lead.name in store.suppressed():
            st.badge("Do not contact", icon=":material/shield:", color="red")
        st.space("stretch")
        if lead.url:
            st.link_button("Website", lead.url, icon=":material/open_in_new:")
    st.write(s.summary)
    with st.container(horizontal=True):
        st.metric("Headcount", f"{headcount:,}" if headcount else "Unknown")
        st.metric("Country", s.location.country or "Unknown")
        st.metric("Open sales roles", len(s.sales_roles))

    why, outreach = st.columns(2, gap="large")
    with why:
        st.markdown("**Why this score**")
        for c in s.criteria:
            st.progress(c.score / 5, text=f"{c.criterion.capitalize()}: {c.score} of 5")
            st.caption(c.evidence)
        if s.disqualifier_hit:
            st.error(f"Disqualified: {s.disqualifier_reason}", icon=":material/block:")
        for flag in s.review_flags:
            st.warning(f"Held at review: {flag}", icon=":material/flag:")
        if so := s.second_opinion:
            st.info(f"Second opinion from {so.model}: {so.verdict} {so.fit}", icon=":material/balance:")
            with st.expander("Second model's reasons"):
                for c in so.criteria:
                    st.progress(c.score / 5, text=f"{c.criterion.capitalize()}: {c.score} of 5")
                    st.caption(c.evidence)

    with outreach:
        st.markdown("**Outreach**")
        if not r.worth_contacting:
            st.caption("This verdict does not get an email.")
        elif not r.drafted:
            st.caption("No email drafted yet.")
        else:
            with st.container(horizontal=True):
                st.badge(f"Hook: {r.hook}" if r.hook else "No hook", color="green" if r.hook else "gray")
                st.badge(r.contact or "No contact", color="green" if r.contact else "gray")
                st.badge(f"Email: {r.status}", color="gray")
            if st.button("Open in Outreach", icon=":material/arrow_forward:"):
                st.session_state.outreach_lead = lead.name
                st.switch_page("app_pages/outreach.py")
        st.markdown("**Open sales roles**")
        if s.sales_roles:
            for role in s.sales_roles:
                st.markdown(f"- [{role.title}]({role.source}) ({role.match} match)")
        else:
            st.caption("None found on a job board.")
        st.markdown("**Where they are**")
        st.caption(s.location.evidence or "No location given.")
        st.markdown("**Source**")
        text = "website text" if lead.text_source == "fetched" else lead.text_source
        st.caption(f"{lead.url or 'No url'} · scored from {text} · {lead.scored_at[:10]} · model {s.model or 'unknown'}")
        with st.expander("Facts the model found"):
            st.dataframe(pd.DataFrame(list(s.facts.items()), columns=["Fact", "Value"]), hide_index=True)
        if s.rejected_quotes:
            with st.expander(f"Quotes rejected by the quote check ({len(s.rejected_quotes)})"):
                for q in s.rejected_quotes:
                    st.caption(q)
