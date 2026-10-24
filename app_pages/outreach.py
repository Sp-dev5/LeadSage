from datetime import datetime

import pandas as pd
import streamlit as st

from agent import store
from agent.contacts import typed_contact
from agent.outreach import contact_for, draft_step, due_dates, load_outreach, next_step, save_outreach, \
    saved_contact, OutreachConfig
from agent.score_lead import load_icp

STATUS_COLORS = {"draft": "orange", "approved": "blue", "sent": "green", "stopped": "gray"}
STOP_STAGES = {"replied": "Replied", "meeting": "Meeting booked", "lost": "Lost"}

icp, cfg = load_icp(), load_outreach()
now = datetime.now().astimezone()

st.title("Outreach")
st.caption("The person to write to, a first email where every line about them is cited, and follow-ups. "
           "Nothing is sent from here: approve an email, send it from your own mailbox, then mark it sent.")


def act(fn, *args) -> None:
    """Run a store change from a button; show why if the change isn't allowed."""
    try:
        fn(*args)
    except ValueError as e:
        st.session_state.outreach_error = str(e)


leads = store.load_all()
emails = {lead.name: store.load_emails(lead.name) for lead in leads}
in_play = [lead for lead in leads if lead.score.verdict in cfg.draft_verdicts or emails[lead.name]]

with st.sidebar:
    st.subheader("Draft first emails")
    todo = [lead for lead in in_play if lead.score.verdict in cfg.draft_verdicts and not emails[lead.name]
            and lead.stage not in store.STOPPED]
    st.caption(f"{len(todo)} leads ({', '.join(cfg.draft_verdicts)}) have no email yet. "
               "Each takes the model about half a minute.")
    if st.button("Find contacts and draft", icon=":material/edit_note:", type="primary", width="stretch",
                 disabled=not todo):
        with st.status(f"Drafting {len(todo)} first emails", expanded=True) as status:
            for lead in todo:
                try:
                    email = draft_step(lead, 0, icp, cfg)
                    st.write(f"{lead.name}: drafted, {len(email.citations)} cited lines")
                except Exception as e:
                    st.write(f"{lead.name}: failed ({str(e)[:90]})")
            status.update(label="Drafts ready", state="complete", expanded=False)
        st.rerun()

    st.divider()
    with st.expander("Sender and follow-ups"):
        with st.form("outreach_settings"):
            name = st.text_input("Sign emails as", cfg.sender_name)
            company = st.text_input("Company", cfg.sender_company)
            verdicts = st.multiselect("Draft emails for", ["qualify", "review", "disqualify"], cfg.draft_verdicts)
            days = st.text_input("Follow-ups, days after the first email", ", ".join(map(str, cfg.follow_up_days)))
            words = st.number_input("Longest email, in words", 40, 300, cfg.max_words, step=10)
            startup = st.number_input("Startup: write to the chief executive at or below this headcount", 1, 10000,
                                      cfg.startup_max_headcount, step=10,
                                      help="Used only when no one matching your buyer is named on the website.")
            if st.form_submit_button("Save", icon=":material/save:"):
                try:
                    new = OutreachConfig(**(cfg.model_dump() | {
                        "sender_name": name, "sender_company": company, "draft_verdicts": verdicts,
                        "follow_up_days": [int(d) for d in days.split(",") if d.strip()],
                        "max_words": words, "startup_max_headcount": startup}))
                    save_outreach(new)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

if error := st.session_state.pop("outreach_error", None):
    st.error(error, icon=":material/error:")

if not in_play:
    st.info("No leads to write to yet. Score leads on the Leads page first.", icon=":material/info:")
    st.stop()

with st.container(horizontal=True):
    for stage in store.STAGES:
        st.metric(stage.capitalize(), sum(lead.stage == stage for lead in in_play), border=True)

rows = []
for lead in sorted(in_play, key=lambda x: -x.score.fit):
    contact = saved_contact(lead, cfg)
    rows.append({
        "Company": lead.name,
        "Verdict": lead.score.verdict,
        "Fit": lead.score.fit,
        "Stage": lead.stage,
        "Contact": f"{contact.name}, {contact.title}" if contact else "",
        "Next step": next_step(lead.stage, emails[lead.name], cfg, now).text,
    })
table = pd.DataFrame(rows)
event = st.dataframe(
    table, hide_index=True, on_select="rerun", selection_mode="single-row", key="outreach_table",
    column_config={"Fit": st.column_config.ProgressColumn("Fit", min_value=0, max_value=100, format="%d"),
                   "Next step": st.column_config.TextColumn("Next step", width="large")},
)

if event.selection.rows:
    st.session_state.outreach_lead = table.iloc[event.selection.rows[0]]["Company"]
if st.session_state.get("outreach_lead") not in set(table["Company"]):
    st.caption("Select a company to see its contact and emails.")
    st.stop()

lead = store.load(st.session_state.outreach_lead)
lead_emails = {e.step: e for e in store.load_emails(lead.name)}
stopped = lead.stage in store.STOPPED

with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(lead.name)
        st.badge(lead.stage.capitalize(), color="gray" if stopped else "blue")
        st.badge(f"{lead.score.verdict.capitalize()} {lead.score.fit}", color="gray")
    st.write(lead.score.summary)

    with st.container(horizontal=True):
        for stage, label in STOP_STAGES.items():
            st.button(label, key=f"stop_{lead.name}_{stage}", disabled=lead.stage == stage,
                      on_click=act, args=(store.set_stage, lead.name, stage))
        picked = st.selectbox("Set stage", store.STAGES, index=store.STAGES.index(lead.stage),
                              key=f"stage_{lead.name}_{lead.stage}", label_visibility="collapsed")
        if picked != lead.stage:
            act(store.set_stage, lead.name, picked)
            st.rerun()

    st.markdown("**Who to write to**")
    search = store.load_contacts(lead.name)
    if search is None:
        st.caption("Not searched yet. Drafting the first email searches the website first.")
        if st.button("Search the website for people", icon=":material/person_search:"):
            with st.spinner("Reading the about and team pages"):
                contact_for(lead, icp, cfg)
            st.rerun()
    else:
        search = contact_for(lead, icp, cfg)
        if c := search.contact:
            where = "typed by you" if c.matches_buyer == "typed" else f"[source]({c.source}), {c.fetched_at[:10]}"
            why = {"buyer": "matches your buyer", "rep": "chosen by you",
                   "startup_ceo": f"startup ({lead.company.size}) and no buyer named, so the chief executive"}
            st.success(f"{c.name}, {c.title} · {why.get(search.chosen_by, 'chosen')} ({where})",
                       icon=":material/person:")
        else:
            st.warning("No one matching your buyer was named on the website.", icon=":material/person_off:")
        others = [p for p in search.people if p != search.contact]
        if others:
            choice = st.selectbox("Other people named on the site", [None] + others, key=f"pick_{lead.name}_{search.contact}",
                                  format_func=lambda p: "Pick one to write to them instead" if p is None
                                  else f"{p.name}, {p.title}")
            if choice is not None:
                store.save_contacts(lead.name, search.model_copy(update={"contact": choice, "chosen_by": "rep"}))
                st.rerun()
        with st.expander("Add a contact yourself, or see what was searched"):
            with st.form(f"typed_{lead.name}", border=False):
                typed_name = st.text_input("Name", key=f"typed_name_{lead.name}")
                typed_title = st.text_input("Title", key=f"typed_title_{lead.name}")
                if st.form_submit_button("Use this contact") and typed_name.strip():
                    store.save_contacts(lead.name, search.model_copy(
                        update={"contact": typed_contact(typed_name, typed_title), "chosen_by": "rep"}))
                    st.rerun()
            for page in search.pages_tried:
                st.caption(f"Read: {page}")
            for r in search.rejected:
                st.caption(f"Rejected (not on the page): {r}")
            if st.button("Search the website again", icon=":material/refresh:"):
                with st.spinner("Reading the about and team pages"):
                    contact_for(lead, icp, cfg, refresh=True)
                st.rerun()

    due = due_dates(list(lead_emails.values()), cfg)
    nxt = next_step(lead.stage, list(lead_emails.values()), cfg, now)
    st.markdown(f"**Emails** Â· next: {nxt.text}")

    for step in range(len(cfg.follow_up_days) + 1):
        email = lead_emails.get(step)
        name = "First email" if step == 0 else f"Follow-up {step}"
        when = f" Â· due {due[step]:%d %b %Y}" if step in due else (
            f" Â· {cfg.follow_up_days[step - 1]} days after the first email" if step else "")
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{name}**{when}")
                if email:
                    st.badge(email.status.capitalize(), color=STATUS_COLORS[email.status])
                    if email.edited:
                        st.badge("Edited by you", color="gray")

            previous_sent = step == 0 or lead_emails.get(step - 1, None) is not None \
                and lead_emails[step - 1].status == "sent"
            can_draft = not stopped and (email is None or email.status != "sent") and previous_sent
            if email is None:
                if st.button("Draft it", key=f"draft_{lead.name}_{step}", icon=":material/edit_note:", disabled=not can_draft,
                             help=None if can_draft else "Send the email before it first"):
                    with st.spinner("Drafting"):
                        act(draft_step, lead, step, icp, cfg)
                    st.rerun()
                continue

            if email.hook:
                st.caption(f"Hook: {email.hook}")
            for flag in email.flags:
                st.warning(flag, icon=":material/flag:")
            editable = email.status == "draft"
            subject = st.text_input("Subject", email.subject, key=f"subject_{lead.name}_{step}_{email.drafted_at}_{email.edited}", disabled=not editable)
            body = st.text_area("Body", email.body, key=f"body_{lead.name}_{step}_{email.drafted_at}_{email.edited}", height=260, disabled=not editable)

            with st.container(horizontal=True):
                if editable:
                    if st.button("Save my edits", key=f"save_{lead.name}_{step}", icon=":material/save:",
                                 disabled=(subject, body) == (email.subject, email.body)):
                        act(store.edit_email, lead.name, step, subject, body)
                        st.rerun()
                    st.button("Approve", key=f"approve_{lead.name}_{step}", icon=":material/check:", type="primary",
                              disabled=(subject, body) != (email.subject, email.body),
                              help="Save your edits first" if (subject, body) != (email.subject, email.body) else None,
                              on_click=act, args=(store.approve_email, lead.name, step))
                if email.status == "approved":
                    st.button("I sent it", key=f"sent_{lead.name}_{step}", icon=":material/send:", type="primary",
                              on_click=act, args=(store.mark_sent, lead.name, step))
                if can_draft and email.status != "sent":
                    if st.button("Redraft", key=f"redraft_{lead.name}_{step}", icon=":material/refresh:"):
                        with st.spinner("Drafting"):
                            act(draft_step, lead, step, icp, cfg)
                        st.rerun()

            if email.citations:
                st.markdown("**Why each line about them is true**")
                for c in email.citations:
                    st.markdown(f"- {c.sentence}")
                    st.caption(f'"{c.quote}" Â· [source]({c.source}) Â· {c.fetched_at[:10]}'
                               if c.source.startswith("http") and " " not in c.source
                               else f'"{c.quote}" Â· {c.source} Â· {c.fetched_at[:10]}')
            if email.rejected:
                with st.expander(f"Lines cut by the quote check ({len(email.rejected)})"):
                    for r in email.rejected:
                        st.caption(r)
            st.caption(f"Drafted {email.drafted_at[:16].replace('T', ' ')} by {email.model or 'unknown'}"
                       + (f" Â· sent {email.sent_at[:10]}" if email.sent_at else ""))
