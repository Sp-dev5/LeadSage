from collections import Counter

import pandas as pd
import streamlit as st

import app_ui as ui
from agent import store
from agent.outreach import load_outreach
from agent.pipeline import lead_files, read_leads, score_all, signal_rows
from agent.score_lead import _parse_headcount, load_icp

icp, cfg = load_icp(), load_outreach()

st.title("Dashboard", icon=":material/dashboard:")
st.caption("Every company researched and scored against your ideal customer, and how close each one is to a sent email.")

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
verdict = df["Verdict"].str[0]

with filters_box:
    st.subheader("Filters")
    known = sorted(set(df["Country"]) - {ui.UNKNOWN})
    region_only = st.toggle("Only my ideal-customer region",
                            help="Countries set on the Ideal customer page. Unknown locations stay visible.")
    picked = st.multiselect("Country", known + [ui.UNKNOWN] if ui.UNKNOWN in set(df["Country"]) else known,
                            placeholder="All countries")
    verdicts = st.pills("Verdict", ui.VERDICTS, selection_mode="multi", default=ui.VERDICTS,
                        format_func=str.capitalize)
    min_fit = st.slider("Minimum fit", 0, 100, 0, step=5)
    hiring_only = st.toggle("Only companies hiring for sales")

location_ok = pd.Series(True, index=df.index)
if picked:
    location_ok &= df["Country"].isin(picked)
if region_only:
    location_ok &= df["In ICP region"] != "no"
other_ok = verdict.isin(verdicts or ui.VERDICTS) & (df["Fit"] >= min_fit)
if hiring_only:
    other_ok &= df["Sales roles"] > 0
shown_df = df[location_ok & other_ok].sort_values("Fit", ascending=False, kind="stable")
rows = [all_rows[i] for i in shown_df.index]
hidden_by_location = df[~location_ok & other_ok]

if not rows:
    st.warning("No leads match these filters. Loosen them in the sidebar.", icon=":material/filter_alt_off:")
    st.stop()

counts = Counter(r.lead.score.verdict for r in rows)
ready = [r.ready for r in rows]
worth = sum(r.worth_contacting for r in ready)
drafted = sum(r.drafted for r in ready)
with_hook = sum(r.drafted and bool(r.hook) for r in ready)
ready_now = sum(r.ready for r in ready)
needs_contact = sum(r.drafted and r.status == "draft" and not r.contact for r in ready)
sent = sum(r.status == "sent" for r in ready)

replied = sum(r.lead.stage in ("replied", "meeting") for r in rows)
kpis = st.columns(4)
kpis[0].metric("Leads scored", len(rows), border=True, icon=":material/database:",
               delta=f"{len(df) - len(rows)} hidden by filters" if len(df) > len(rows) else f"{counts['disqualify']} disqualified",
               delta_color="off", delta_arrow="off")
kpis[1].metric("Worth contacting", worth, border=True, icon=":material/person_search:",
               delta=f"{drafted} emails drafted, {with_hook} with a hook", delta_color="off", delta_arrow="off",
               help="Leads whose verdict gets an email drafted (set in data/outreach.json).")
kpis[2].metric("Ready to review", ready_now, border=True, icon=":material/task_alt:",
               delta=f"{needs_contact} still need a contact", delta_color="off", delta_arrow="off",
               help="A draft that opens with a hook and has a named person to write to.")
kpis[3].metric("Sent", sent, border=True, icon=":material/send:",
               delta=f"{replied} replied", delta_color="off", delta_arrow="off")

left, right = st.columns([2, 3])
with left.container(border=True):
    st.markdown("**Verdicts**")
    st.altair_chart(ui.verdict_chart(rows))
with right.container(border=True):
    st.markdown("**Outreach readiness**")
    st.altair_chart(ui.readiness_chart(rows))

if len(hidden_by_location):
    with st.expander(f"{len(hidden_by_location)} hidden by your location filter", icon=":material/visibility_off:"):
        for _, r in hidden_by_location.sort_values("Fit", ascending=False).iterrows():
            st.markdown(f"**{r['Company']}** ({r['Country']}): {r['Verdict'][0].capitalize()}, fit {r['Fit']}")

table = shown_df.reset_index(drop=True)
with st.container(border=True):
    st.markdown(f"**Leads** ({len(table)})")
    st.caption("Best first. Click a column to sort, or select a row to see why it scored that way.")
    event = st.dataframe(
        table,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="leads_table",
        height=min(40 + 35 * len(table), 525),
        column_order=["Company", "Verdict", "Fit", "Hook", "Contact", "Stage", "Country", "Headcount", "Sales roles"],
        column_config=ui.lead_columns(),
    )

if event.selection.rows and event.selection.rows[0] < len(table):
    st.session_state.dashboard_lead = table.iloc[event.selection.rows[0]]["Company"]
names = [r.lead.name for r in rows]
if st.session_state.get("dashboard_lead") not in names:
    st.stop()

current = rows[names.index(st.session_state.dashboard_lead)]
lead, s, r = current.lead, current.lead.score, current.ready
headcount = _parse_headcount(lead.company.size)

with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(lead.name)
        ui.verdict_badge(s.verdict)
        st.badge(f"Fit {s.fit}/100", icon=":material/speed:", color="gray")
        st.badge(lead.stage.capitalize(), icon=":material/flag:", color="blue")
        if lead.name in store.suppressed():
            st.badge("Do not contact", icon=":material/shield:", color="red")
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
                st.badge(f"Hook: {r.hook}" if r.hook else "No hook", icon=":material/bolt:",
                         color="green" if r.hook else "gray")
                st.badge(r.contact or "No contact", icon=":material/person:", color="green" if r.contact else "gray")
                st.badge(f"Email: {r.status}", icon=":material/mail:", color="blue")
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
