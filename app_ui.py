"""
Shared pieces of the app's pages: lead rows with their readiness, the lead table, the charts.

The verdict colors are the same values as the theme in .streamlit/config.toml, so charts, badges
and table labels agree. Counting and wording rules live in agent/readiness.py.
"""

from __future__ import annotations

from collections import Counter
from typing import NamedTuple

import altair as alt
import pandas as pd
import streamlit as st

from agent import store
from agent.outreach import OutreachConfig, saved_contact
from agent.readiness import Readiness, funnel, readiness
from agent.score_lead import _parse_headcount

VERDICTS = ["qualify", "review", "disqualify"]
VERDICT_HEX = {"qualify": "#2FA36B", "review": "#C26A00", "disqualify": "#E5484D"}
VERDICT_BADGE = {"qualify": "green", "review": "orange", "disqualify": "red"}
VERDICT_ICON = {"qualify": ":material/check_circle:", "review": ":material/visibility:",
                "disqualify": ":material/block:"}
HOOKS = ["Hiring", "News"]
HOOK_HEX = ["#2FA36B", "#3B82F6"]
UNKNOWN = "Unknown"
NO_CONTACT = "Not found yet"


class LeadRow(NamedTuple):
    lead: store.StoredLead
    emails: list[store.Email]
    ready: Readiness


def lead_rows(leads: list[store.StoredLead], cfg: OutreachConfig) -> list[LeadRow]:
    rows = []
    for lead in leads:
        emails = store.load_emails(lead.name)
        contact = saved_contact(lead, cfg)
        rows.append(LeadRow(lead, emails, readiness(lead, emails, contact.name if contact else "", cfg)))
    return rows


def lead_frame(rows: list[LeadRow]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Company": r.lead.name,
        "Verdict": [r.lead.score.verdict],
        "Fit": r.lead.score.fit,
        "Hook": [r.ready.hook] if r.ready.hook else [],
        "Contact": r.ready.contact or (NO_CONTACT if r.ready.worth_contacting else ""),
        "Stage": r.lead.stage.capitalize(),
        "Country": r.lead.score.location.country or UNKNOWN,
        "In ICP region": r.lead.score.location.in_region,
        "Headcount": _parse_headcount(r.lead.company.size),
        "Sales roles": len(r.lead.score.sales_roles),
    } for r in rows])


def lead_columns() -> dict:
    return {
        "Company": st.column_config.TextColumn("Company", pinned=True),
        "Verdict": st.column_config.MultiselectColumn(
            "Verdict", options=VERDICTS, color=[VERDICT_HEX[v] for v in VERDICTS], format_func=str.capitalize,
            help="Qualify: a strong match. Review: worth a look. Disqualify: not a fit."),
        "Fit": st.column_config.ProgressColumn("Fit", min_value=0, max_value=100, format="%d",
                                                help="0 to 100, from the four criteria on each lead's card."),
        "Hook": st.column_config.MultiselectColumn(
            "Hook", options=HOOKS, color=HOOK_HEX,
            help="The timely reason the first email opens with: hiring for sales, or recent news."),
        "Contact": st.column_config.TextColumn("Contact", help="The person the email is written to."),
        "Headcount": st.column_config.NumberColumn("Headcount", format="%d"),
        "Sales roles": st.column_config.NumberColumn("Sales roles", format="%d", help="Open sales jobs found."),
    }


def verdict_chart(rows: list[LeadRow]) -> alt.Chart:
    counts = Counter(r.lead.score.verdict for r in rows)
    data = pd.DataFrame({"Verdict": [f"{v.capitalize()} ({counts[v]})" for v in VERDICTS],
                         "Leads": [counts[v] for v in VERDICTS]})
    names = list(data["Verdict"])
    return alt.Chart(data).mark_arc(innerRadius=62, outerRadius=100).encode(
        theta=alt.Theta("Leads:Q"),
        color=alt.Color("Verdict:N", sort=names,
                        scale=alt.Scale(domain=names, range=[VERDICT_HEX[v] for v in VERDICTS]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["Verdict", "Leads"],
    ).properties(height=250)


def readiness_chart(rows: list[LeadRow]) -> alt.Chart:
    data = pd.DataFrame(funnel([r.ready for r in rows]), columns=["Step", "Leads"])
    data["Label"] = [f"{step}  ({n})" for step, n in zip(data["Step"], data["Leads"])]
    return alt.Chart(data).mark_bar(cornerRadiusEnd=5, size=18).encode(
        y=alt.Y("Label:N", sort=None, title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False)),
        x=alt.X("Leads:Q", title=None, axis=None, scale=alt.Scale(domain=[0, max(1, int(data["Leads"].max()))])),
        tooltip=["Step", "Leads"],
    ).properties(height=250)


def verdict_badge(verdict: str) -> None:
    st.badge(verdict.capitalize(), icon=VERDICT_ICON[verdict], color=VERDICT_BADGE[verdict])
