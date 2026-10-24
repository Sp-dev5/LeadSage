import streamlit as st

st.set_page_config(page_title="LeadSage", page_icon=":material/radar:", layout="wide")

page = st.navigation(
    [
        st.Page("app_pages/leads.py", title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page("app_pages/outreach.py", title="Outreach", icon=":material/mail:"),
        st.Page("app_pages/icp.py", title="Ideal customer", icon=":material/person_search:"),
    ],
    position="top",
)
page.run()
