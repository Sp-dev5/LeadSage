from pathlib import Path

import streamlit as st

HERE = Path(__file__).parent

st.set_page_config(page_title="LeadSage", page_icon=str(HERE / "static" / "logo-icon.svg"), layout="wide")
st.html(HERE / "app.css")
st.logo(str(HERE / "static" / "logo.svg"), size="large", icon_image=str(HERE / "static" / "logo-icon.svg"))

page = st.navigation(
    {
        "": [st.Page("app_pages/home.py", title="Home", icon=":material/home:", default=True)],
        "Pipeline": [
            st.Page("app_pages/leads.py", title="Leads", icon=":material/table_rows:"),
            st.Page("app_pages/outreach.py", title="Outreach", icon=":material/mail:"),
        ],
        "Setup": [st.Page("app_pages/icp.py", title="Ideal customer", icon=":material/person_search:")],
    },
    position="sidebar",
)
page.run()
