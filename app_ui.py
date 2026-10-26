"""
Shared pieces of the app's pages: lead rows with their readiness, the lead table, the charts.

The verdict colors are the same values as the theme in .streamlit/config.toml, so charts, badges
and table labels agree. Counting and wording rules live in agent/readiness.py.
"""

from __future__ import annotations

import base64
import html
from typing import NamedTuple

import altair as alt
import pandas as pd
import streamlit as st

from agent import logos, store
from agent.outreach import OutreachConfig, with_fallbacks
from agent.readiness import Readiness, funnel, held_back_by, readiness
from agent.score_lead import _parse_headcount

VERDICTS = ["qualify", "review", "disqualify"]
VERDICT_HEX = {"qualify": "#124236", "review": "#4B3D22", "disqualify": "#2A3230"}
VERDICT_BADGE = {"qualify": "green", "review": "orange", "disqualify": "gray"}
VERDICT_ICON = {"qualify": ":material/check:", "review": ":material/visibility:",
                "disqualify": ":material/remove:"}
HOOKS = ["Hiring", "News"]
HOOK_HEX = ["#1E3B40", "#24353B"]
BAR_HEX = "#4BEDBD"
UNKNOWN = "Unknown"
NO_CONTACT = "Not found yet"


class LeadRow(NamedTuple):
    lead: store.StoredLead
    emails: list[store.Email]
    ready: Readiness


def lead_rows(leads: list[store.StoredLead], cfg: OutreachConfig) -> list[LeadRow]:
    all_emails, searches = store.load_all_emails(), store.load_all_contacts()
    rows = []
    for lead in leads:
        emails = all_emails.get(lead.name, [])
        search = searches.get(lead.name)
        contact = with_fallbacks(search, lead, cfg).contact if search else None
        rows.append(LeadRow(lead, emails, readiness(lead, emails, contact.name if contact else "", cfg)))
    return rows


def monogram(name: str) -> str:
    """A plain initial in a circle, for companies whose site gave no logo."""
    letter = html.escape((name.strip()[:1] or "?").upper())
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
           f'<circle cx="32" cy="32" r="31" fill="#0D2E25" stroke="#1E6B55" stroke-width="2"/>'
           f'<text x="32" y="42" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" font-size="28" '
           f'font-weight="600" fill="#7DF5D8">{letter}</text></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def logo(name: str) -> str:
    return logos.data_uri(name) or monogram(name)


def lead_frame(rows: list[LeadRow]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Logo": logo(r.lead.name),
        "Company": r.lead.name,
        "Website": r.lead.url or None,
        "Verdict": [r.lead.score.verdict],
        "Fit": r.lead.score.fit,
        "Hook": [r.ready.hook] if r.ready.hook else [],
        "Contact": r.ready.contact or (NO_CONTACT if r.ready.worth_contacting else ""),
        "Stage": r.lead.stage.capitalize(),
        "Country": r.lead.score.location.country or UNKNOWN,
        "In ICP region": r.lead.score.location.in_region,
        "Headcount": _parse_headcount(r.lead.company.size),
        "Sales roles": len(r.lead.score.sales_roles),
        "Held back by": held_back_by(r.lead.score),
    } for r in rows])


def lead_columns() -> dict:
    return {
        "Logo": st.column_config.ImageColumn("", width=40),
        "Company": st.column_config.TextColumn("Company"),
        "Website": st.column_config.LinkColumn("Website", display_text=r"https?://(?:www\.)?([^/]+)",
                                               help="The company's own site. Opens in a new tab."),
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
        "Held back by": st.column_config.TextColumn(
            "Held back by", width="medium", help="The rule that kept this lead from qualify."),
    }


def stat_grid(items: list[tuple[str, int, str, str]]) -> None:
    """Numbers in one bordered panel, split into cells: (label, value, note, help)."""
    with st.container(border=True, key="stats"):
        for col, (label, value, note, help_text) in zip(st.columns(len(items), gap="medium"), items):
            col.metric(label, value, delta=note or None, delta_color="off", delta_arrow="off",
                       help=help_text or None)


def eyebrow(text: str, icon: str, key: str) -> None:
    """A small uppercase label above a heading."""
    with st.container(key=f"eyebrow_{key}"):
        st.markdown(f":material/{icon}: {text}")


def page_header(label: str, icon: str, title: str, highlight: str, subtitle: str) -> None:
    """Label, a large title with its key words in the accent colour, and one line under it."""
    with st.container(key="hero"):
        eyebrow(label, icon, "hero")
        st.markdown(f"# {title} :green[{highlight}]" if highlight else f"# {title}")
        st.caption(subtitle)


def readiness_chart(rows: list[LeadRow]) -> alt.Chart:
    data = pd.DataFrame(funnel([r.ready for r in rows]), columns=["Step", "Leads"])
    data["Label"] = [f"{step}   {n}" for step, n in zip(data["Step"], data["Leads"])]
    top = max(1, int(data["Leads"].max()))
    track = alt.Chart(data).mark_bar(size=10, cornerRadius=5, opacity=0.12, color="#4BEDBD").encode(
        y=alt.Y("Label:N", sort=None, title=None,
                axis=alt.Axis(labelLimit=260, ticks=False, domain=False, labelPadding=10)),
        x=alt.X("max_leads:Q", title=None, axis=None, scale=alt.Scale(domain=[0, top])),
    ).transform_calculate(max_leads=str(top))
    bars = alt.Chart(data).mark_bar(size=10, cornerRadius=5, color=BAR_HEX).encode(
        y=alt.Y("Label:N", sort=None), x=alt.X("Leads:Q"), tooltip=["Step", "Leads"])
    return (track + bars).properties(height=340, background="transparent").configure_view(stroke=None)


def verdict_badge(verdict: str) -> None:
    st.badge(verdict.capitalize(), icon=VERDICT_ICON[verdict], color=VERDICT_BADGE[verdict])
