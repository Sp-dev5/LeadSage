from collections import Counter

import streamlit as st

import app_ui as ui
from agent import store
from agent.outreach import load_outreach
from agent.readiness import group, held_back_by
from agent.score_lead import load_icp

icp, cfg = load_icp(), load_outreach()
leads = store.load_all()

if not leads:
    ui.page_header("Start here", "rocket_launch", "No leads", "scored yet",
                   "Pick a lead list on the Leads page and click Research and score.")
    if st.button("Go to Leads", type="primary", icon=":material/arrow_forward:"):
        st.switch_page("app_pages/leads.py")
    st.stop()

rows = ui.lead_rows(leads, cfg)
ready = [r.ready for r in rows]
worth = sum(r.worth_contacting for r in ready)
drafted = sum(r.drafted for r in ready)
with_hook = sum(r.drafted and bool(r.hook) for r in ready)
ready_now = sum(r.ready for r in ready)
needs_contact = sum(r.drafted and r.status == "draft" and not r.contact for r in ready)
sent = sum(r.status == "sent" for r in ready)
replied = sum(r.lead.stage in ("replied", "meeting") for r in rows)
groups = Counter(group(r.lead.score, icp.settings.qualify_at) for r in rows)

ui.page_header(
    "Today", "today", f"{ready_now} drafts are" if ready_now != 1 else "1 draft is", "ready to review",
    f"{len(rows)} companies scored, {worth} worth contacting, {sent} sent. "
    "Every line about a company in an email shows where it was found.")

if st.button("See all leads", icon=":material/arrow_forward:"):
    st.switch_page("app_pages/leads.py")

st.space("small")
ui.stat_grid([
    ("Leads scored", len(rows), f"{groups['Disqualify']} disqualified", ""),
    ("Worth contacting", worth, f"{drafted} drafted, {with_hook} with a hook",
     "Leads whose verdict gets an email drafted (set in data/outreach.json)."),
    ("Ready to review", ready_now, f"{needs_contact} still need a contact",
     "A draft that opens with a hook and has a named person to write to."),
    ("Sent", sent, f"{replied} replied", ""),
])

st.space("small")
ui.eyebrow("Verdicts", "fact_check", "verdicts")
tile_text = {
    "Qualify": "Strong match, nothing in the way",
    "Strong fit": f"Fit {icp.settings.qualify_at}+, held back by one rule",
    "Review": "Worth a look",
    "Disqualify": "Not a fit, reason given",
}
with st.container(border=True, key="tiles"):
    for col, name in zip(st.columns(4, gap="medium"), tile_text):
        with col:
            st.markdown(f"## {groups[name]}")
            if st.button(name, key=f"tile_{name}", type="tertiary", icon=":material/arrow_outward:"):
                st.session_state.dashboard_group = name
                st.switch_page("app_pages/leads.py")
            st.caption(tile_text[name])

st.space("small")
left, right = st.columns([3, 2], gap="medium")
with left.container(border=True, key="panel_1", height="stretch"):
    ui.eyebrow("Pipeline", "conversion_path", "pipeline")
    st.markdown("**From scored to sent**")
    st.altair_chart(ui.readiness_chart(rows))
with right.container(border=True, key="panel_2", height="stretch"):
    ui.eyebrow("Up next", "task_alt", "next")
    st.markdown("**Ready to review now**")
    up_next = sorted((r for r in rows if r.ready.ready), key=lambda r: -r.lead.score.fit)
    if not up_next:
        st.caption("Nothing yet. Find contacts and hooks on the Outreach page.")
    for r in up_next[:7]:
        with st.container(horizontal=True, vertical_alignment="center"):
            if st.button(r.lead.name, key=f"next_{r.lead.name}", type="tertiary"):
                st.session_state.outreach_lead = r.lead.name
                st.switch_page("app_pages/outreach.py")
            st.caption(f"{r.ready.hook} · {r.ready.contact} · fit {r.lead.score.fit}",
                       width="stretch", text_alignment="right")

strong = sorted((r for r in rows if group(r.lead.score, icp.settings.qualify_at) == "Strong fit"),
                key=lambda r: -r.lead.score.fit)
if strong:
    st.space("small")
    with st.container(border=True, key="panel_3"):
        ui.eyebrow("Held back", "flag", "held")
        st.markdown("**Strong fits that need one check**")
        st.caption("High fit, kept at review by a single rule. Clear the rule and they qualify.")
        for r in strong:
            with st.container(horizontal=True, vertical_alignment="center"):
                if st.button(r.lead.name, key=f"strong_{r.lead.name}", type="tertiary"):
                    st.session_state.dashboard_group = "Strong fit"
                    st.session_state.dashboard_lead = r.lead.name
                    st.switch_page("app_pages/leads.py")
                st.caption(f"fit {r.lead.score.fit} · {held_back_by(r.lead.score)}",
                           width="stretch", text_alignment="right")
