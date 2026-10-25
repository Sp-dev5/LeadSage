from datetime import datetime

import pandas as pd
import streamlit as st

import app_ui as ui
from agent import store
from agent.contacts import typed_contact
from agent.outreach import (OutreachConfig, contact_for, draft_step, due_dates, forget_lead, load_outreach, next_step,
                            save_outreach, sending_problems)
from agent.readiness import QUEUES, in_queue, todo
from agent.score_lead import load_icp

STATUS_COLORS = {"draft": "orange", "approved": "blue", "sent": "green", "stopped": "gray"}
STATUS_ICONS = {"draft": ":material/edit_note:", "approved": ":material/check_circle:", "sent": ":material/send:",
                "stopped": ":material/block:"}
STOP_STAGES = {"replied": "Replied", "meeting": "Meeting booked", "lost": "Lost"}

icp, cfg = load_icp(), load_outreach()
now = datetime.now().astimezone()

st.title("Outreach")
st.caption("Read each draft, check where every line about the company comes from, then send it from your own mailbox "
           "and mark it sent. Nothing is sent from this app.")


def act(fn, *args) -> None:
    """Run a store change from a button; show why if the change isn't allowed."""
    try:
        fn(*args)
    except ValueError as e:
        st.session_state.outreach_error = str(e)


leads = store.load_all()
all_rows = ui.lead_rows(leads, cfg)
in_play = [r for r in all_rows if r.ready.worth_contacting or r.ready.drafted]

with st.sidebar:
    st.subheader("Draft first emails")
    undrafted = [r.lead for r in in_play if r.ready.worth_contacting and not r.ready.drafted
                 and r.lead.stage not in store.STOPPED]
    st.caption(f"{len(undrafted)} leads ({', '.join(cfg.draft_verdicts)}) have no email yet. Finding the contact and "
               "reading their news takes about a minute each.")
    if st.button("Find contacts and draft", icon=":material/edit_note:", type="primary", width="stretch",
                 disabled=not undrafted):
        with st.status(f"Drafting {len(undrafted)} first emails", expanded=True) as status:
            for lead in undrafted:
                try:
                    email = draft_step(lead, 0, icp, cfg)
                    st.write(f"{lead.name}: drafted, {len(email.citations)} cited lines")
                except Exception as e:
                    st.write(f"{lead.name}: failed ({str(e)[:90]})")
            status.update(label="Drafts ready", state="complete", expanded=False)
        st.rerun()

    st.divider()
    with st.expander("Sender and follow-ups", icon=":material/tune:"):
        with st.form("outreach_settings", border=False):
            name = st.text_input("Sign emails as", cfg.sender_name)
            company = st.text_input("Company", cfg.sender_company)
            address = st.text_input("Business address", cfg.sender_address,
                                    help="Shown under your signature. Most countries require one in marketing email.")
            opt_out = st.text_area("Opt-out line", cfg.opt_out_line, height=80,
                                   help="Ends every email. An email cannot be approved without it.")
            verdicts = st.multiselect("Draft emails for", ["qualify", "review", "disqualify"], cfg.draft_verdicts)
            days = st.text_input("Follow-ups, days after the first email", ", ".join(map(str, cfg.follow_up_days)))
            words = st.number_input("Longest email, in words", 40, 300, cfg.max_words, step=10)
            startup = st.number_input("Startup: write to the chief executive at or below this headcount", 1, 10000,
                                      cfg.startup_max_headcount, step=10,
                                      help="Used only when no one matching your buyer is named on the website.")
            if st.form_submit_button("Save settings", icon=":material/save:"):
                try:
                    new = OutreachConfig(**(cfg.model_dump() | {
                        "sender_name": name, "sender_company": company, "sender_address": address.strip(),
                        "opt_out_line": opt_out.strip(), "draft_verdicts": verdicts,
                        "follow_up_days": [int(d) for d in days.split(",") if d.strip()],
                        "max_words": words, "startup_max_headcount": startup}))
                    save_outreach(new)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    on_list = store.suppressed()
    with st.expander(f"Do-not-contact list ({len(on_list)})", icon=":material/shield:"):
        if not on_list:
            st.caption("Empty. Add a company from its Privacy menu when it asks not to be contacted.")
        for company_name, (reason, added) in on_list.items():
            st.markdown(f"**{company_name}**")
            st.caption(f"{reason or 'No reason given'} · added {added[:10]}")
            if st.button("Remove from the list", key=f"unsuppress_{company_name}", icon=":material/undo:"):
                store.unsuppress(company_name)
                st.rerun()

if error := st.session_state.pop("outreach_error", None):
    st.error(error, icon=":material/error:")

setup = sending_problems(cfg.opt_out_line, cfg)
if setup:
    st.warning("Before you can approve an email: " + "; ".join(setup) + ".", icon=":material/lock:")

if not in_play:
    st.info("No leads to write to yet. Score leads on the Dashboard first.", icon=":material/info:")
    st.stop()

ready = [r.ready for r in in_play]
ui.kpi_row([
    ("Ready to review", sum(r.ready for r in ready), "a hook and a named contact", ""),
    ("Need a contact", sum(in_queue(r, "Needs a contact") for r in ready), "find or type one in", ""),
    ("Need a hook", sum(in_queue(r, "Needs a hook") for r in ready), "add a reason to write now", ""),
    ("Approved or sent", sum(r.status in ("approved", "sent") for r in ready),
     f"{sum(r.lead.stage in ('replied', 'meeting') for r in in_play)} replied", ""),
])

queue = st.segmented_control("Queue", QUEUES, default="All", required=True, key="outreach_queue",
                             format_func=lambda q: f"{q}  {sum(in_queue(r.ready, q) for r in in_play)}")
queue_rows = sorted((r for r in in_play if in_queue(r.ready, queue)), key=lambda r: -r.lead.score.fit)

table = pd.DataFrame([{
    "Company": r.lead.name,
    "Verdict": [r.lead.score.verdict],
    "Fit": r.lead.score.fit,
    "Hook": [r.ready.hook] if r.ready.hook else [],
    "Contact": r.ready.contact or ui.NO_CONTACT,
    "Email": (r.ready.status or "none").capitalize(),
    "To do": next_step(r.lead.stage, r.emails, cfg, now).text if r.ready.status in ("sent", "stopped")
    or r.lead.stage in store.STOPPED else todo(r.ready),
} for r in queue_rows])

if table.empty:
    st.info(f"Nothing in \"{queue}\" right now.", icon=":material/inbox:")
else:
    with st.container(border=True, key="rise_1"):
        st.caption("Best fit first. Select a company to read and approve its emails.")
        event = st.dataframe(
            table, hide_index=True, on_select="rerun", selection_mode="single-row", key="outreach_table",
            height=min(40 + 35 * len(table), 420),
            column_config={**ui.lead_columns(), "To do": st.column_config.TextColumn("To do", width="medium")},
        )
    if event.selection.rows and event.selection.rows[0] < len(table):
        st.session_state.outreach_lead = table.iloc[event.selection.rows[0]]["Company"]

names = {r.lead.name for r in in_play}
if st.session_state.get("outreach_lead") not in names:
    st.caption("Select a company to see its contact and emails.")
    st.stop()

lead = store.load(st.session_state.outreach_lead)
current = next(r for r in in_play if r.lead.name == lead.name)
lead_emails = {e.step: e for e in store.load_emails(lead.name)}
stopped = lead.stage in store.STOPPED
steps = range(len(cfg.follow_up_days) + 1)
due = due_dates(list(lead_emails.values()), cfg)
nxt = next_step(lead.stage, list(lead_emails.values()), cfg, now)

with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(lead.name)
        ui.verdict_badge(lead.score.verdict)
        st.badge(f"Fit {lead.score.fit}/100", icon=":material/speed:", color="gray")
        st.badge(lead.stage.capitalize(), icon=":material/flag:", color="gray" if stopped else "blue")
        if lead.name in store.suppressed():
            st.badge("Do not contact", icon=":material/shield:", color="red")
        with st.popover("Privacy", icon=":material/shield:"):
            st.caption("Do not contact stops every email and keeps this company from being drafted for again, "
                       "even if it is scored again.")
            reason = st.text_input("Reason (optional)", key=f"dnc_reason_{lead.name}")
            if st.button("Add to the do-not-contact list", key=f"dnc_{lead.name}", width="stretch",
                         disabled=lead.name in store.suppressed()):
                act(store.suppress, lead.name, reason)
                st.rerun()
            st.caption("Delete removes the score, contacts, news, emails and the website pages saved for this "
                       "company. The do-not-contact list is kept.")
            sure = st.checkbox("I understand this cannot be undone", key=f"delete_sure_{lead.name}")
            if st.button("Delete this lead and its data", key=f"delete_{lead.name}", icon=":material/delete:",
                         width="stretch", disabled=not sure):
                removed = forget_lead(lead, cfg)
                st.session_state.pop("outreach_lead", None)
                st.toast(f"Deleted {lead.name} and {removed} saved pages.", icon=":material/delete:")
                st.rerun()
        with st.popover("Update stage", icon=":material/flag:"):
            st.caption("A reply, a meeting or a loss stops the follow-ups.")
            for stage, label in STOP_STAGES.items():
                st.button(label, key=f"stop_{lead.name}_{stage}", disabled=lead.stage == stage, width="stretch",
                          on_click=act, args=(store.set_stage, lead.name, stage))
            picked = st.selectbox("Or set any stage", store.STAGES, index=store.STAGES.index(lead.stage),
                                  key=f"stage_{lead.name}_{lead.stage}")
            if picked != lead.stage:
                act(store.set_stage, lead.name, picked)
                st.rerun()
    st.caption(f"{lead.company.size or 'Size unknown'} · {lead.score.location.country or 'Country unknown'} · "
               f"next step: {nxt.text}")

    main, side = st.columns([3, 2], gap="large")

    with side:
        with st.container(border=True):
            st.markdown("**Who to write to**")
            search = store.load_contacts(lead.name)
            if search is None:
                st.caption("Not searched yet. Drafting the first email searches the website first.")
                if st.button("Search the website for people", icon=":material/person_search:"):
                    with st.spinner("Reading the about and team pages…"):
                        contact_for(lead, icp, cfg)
                    st.rerun()
            else:
                search = contact_for(lead, icp, cfg)
                if c := search.contact:
                    st.markdown(f"**{c.name}**, {c.title}")
                    why = {"buyer": "Matches your buyer", "rep": "Chosen by you",
                           "startup_ceo": f"Startup of {lead.company.size}: no buyer named, so the chief executive",
                           "closest": "Closest match on their site, not a sales leader: check it fits"}
                    st.caption(f"{why.get(search.chosen_by, 'Chosen')} · "
                               + ("typed by you" if c.matches_buyer == "typed"
                                  else f"[source]({c.source}), {c.fetched_at[:10]}"))
                else:
                    st.warning("No one matching your buyer is named on their website. Pick someone below or type "
                               "a contact in.", icon=":material/person_off:")
                others = [p for p in search.people if p != search.contact]
                if others:
                    choice = st.selectbox("Other people named on their site", [None] + others,
                                          key=f"pick_{lead.name}_{search.contact}",
                                          format_func=lambda p: "Pick one to write to them instead" if p is None
                                          else f"{p.name}, {p.title}")
                    if choice is not None:
                        store.save_contacts(lead.name, search.model_copy(update={"contact": choice, "chosen_by": "rep"}))
                        st.rerun()
                with st.expander("Type a contact in, or see what was searched"):
                    with st.form(f"typed_{lead.name}", border=False):
                        typed_name = st.text_input("Name", key=f"typed_name_{lead.name}")
                        typed_title = st.text_input("Title", key=f"typed_title_{lead.name}")
                        if st.form_submit_button("Use this contact") and typed_name.strip():
                            store.save_contacts(lead.name, search.model_copy(
                                update={"contact": typed_contact(typed_name, typed_title), "chosen_by": "rep"}))
                            st.rerun()
                    for page in search.pages_tried:
                        st.caption(f"Read: {page}")
                    for rejected in search.rejected:
                        st.caption(f"Rejected (not on the page): {rejected}")
                    if st.button("Search the website again", icon=":material/refresh:"):
                        with st.spinner("Reading the about and team pages…"):
                            contact_for(lead, icp, cfg, refresh=True)
                        st.rerun()

        with st.container(border=True):
            st.markdown("**Hook**")
            first = lead_emails.get(0)
            if first and first.hook:
                st.badge(current.ready.hook, icon=":material/bolt:", color="green")
                st.caption(first.hook)
            elif first:
                st.badge("No hook yet", icon=":material/bolt:", color="gray")
                st.caption("Nothing timely on their site or job boards. Add a reason to write now (news, funding, a "
                           "new hire) when you edit the email.")
            else:
                st.caption("Drafted with the first email.")
            if found := store.load_news(lead.name):
                with st.expander(f"Recent news on their own site ({len(found.items)})"):
                    for item in sorted(found.items, key=lambda i: i.date, reverse=True):
                        st.markdown(f"**{item.date_text}** ({item.kind}): [{item.headline}]({item.source})")
                    if not found.items:
                        st.caption(f"Nothing dated in the last {cfg.news_max_age_days} days. "
                                   f"Pages read: {len(found.pages_tried)}.")
                    for dropped in found.rejected:
                        st.caption(f"Dropped: {dropped}")

    with main:
        labels = []
        for step in steps:
            email = lead_emails.get(step)
            icon = STATUS_ICONS[email.status] if email else ":material/schedule:"
            labels.append(f"{icon} {'First email' if step == 0 else f'Follow-up {step}'}")
        for tab, step in zip(st.tabs(labels), steps):
            email = lead_emails.get(step)
            with tab:
                when = (f"Due {due[step]:%d %b %Y}" if step in due else
                        f"{cfg.follow_up_days[step - 1]} days after the first email" if step else "")
                previous_sent = step == 0 or (lead_emails.get(step - 1) is not None
                                              and lead_emails[step - 1].status == "sent")
                can_draft = not stopped and (email is None or email.status != "sent") and previous_sent
                if email is None:
                    st.caption(when or "Not drafted yet.")
                    if st.button("Draft it", key=f"draft_{lead.name}_{step}", icon=":material/edit_note:",
                                 disabled=not can_draft, type="primary",
                                 help=None if can_draft else "Send the email before it first"):
                        with st.spinner("Drafting…"):
                            act(draft_step, lead, step, icp, cfg)
                        st.rerun()
                    continue

                with st.container(horizontal=True, vertical_alignment="center"):
                    st.badge(email.status.capitalize(), icon=STATUS_ICONS[email.status], color=STATUS_COLORS[email.status])
                    if email.edited:
                        st.badge("Edited by you", icon=":material/edit:", color="gray")
                    if when:
                        st.caption(when)
                for flag in email.flags:
                    st.warning(flag, icon=":material/flag:")
                editable = email.status == "draft"
                key = f"{lead.name}_{step}_{email.drafted_at}_{email.edited}"
                subject = st.text_input("Subject", email.subject, key=f"subject_{key}", disabled=not editable)
                body = st.text_area("Body", email.body, key=f"body_{key}", height=300, disabled=not editable)
                unsaved = (subject, body) != (email.subject, email.body)
                problems = sending_problems(email.body, cfg)

                with st.container(horizontal=True):
                    if editable:
                        if st.button("Save my edits", key=f"save_{lead.name}_{step}", icon=":material/save:",
                                     disabled=not unsaved):
                            act(store.edit_email, lead.name, step, subject, body)
                            st.rerun()
                        st.button("Approve", key=f"approve_{lead.name}_{step}", icon=":material/check:", type="primary",
                                  disabled=unsaved or bool(problems),
                                  help="Save your edits first" if unsaved else None,
                                  on_click=act, args=(store.approve_email, lead.name, step))
                    if email.status == "approved":
                        st.button("I sent it", key=f"sent_{lead.name}_{step}", icon=":material/send:", type="primary",
                                  on_click=act, args=(store.mark_sent, lead.name, step))
                    if can_draft and email.status != "sent":
                        if st.button("Write it again", key=f"redraft_{lead.name}_{step}", icon=":material/refresh:"):
                            with st.spinner("Drafting…"):
                                act(draft_step, lead, step, icp, cfg)
                            st.rerun()

                if editable and problems and not unsaved:
                    st.caption(":material/lock: Can't approve yet: " + "; ".join(problems) + ".")
                if email.citations:
                    with st.expander(f"Where each line about them comes from ({len(email.citations)})",
                                     expanded=step == 0, icon=":material/fact_check:"):
                        for c in email.citations:
                            st.markdown(f"- {c.sentence}")
                            where = f"[source]({c.source})" if c.source.startswith("http") and " " not in c.source \
                                else c.source
                            st.caption(f'"{c.quote}" · {where} · {c.fetched_at[:10]}')
                if email.rejected:
                    with st.expander(f"Lines cut by the quote check ({len(email.rejected)})",
                                     icon=":material/content_cut:"):
                        for cut in email.rejected:
                            st.caption(cut)
                st.caption(f"Drafted {email.drafted_at[:16].replace('T', ' ')} by {email.model or 'unknown'}"
                           + (f" · sent {email.sent_at[:10]}" if email.sent_at else ""))
