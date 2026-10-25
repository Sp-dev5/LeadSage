from pathlib import Path

import streamlit as st

st.set_page_config(page_title="LeadSage", page_icon=":material/radar:", layout="wide")
st.html(Path(__file__).parent / "app.css")
if st.context.theme.type == "dark":
    st.html("<style>[data-testid='stButtonGroup'] button[aria-checked='true'], "
            "[data-testid='stSliderThumbValue'] { color: #34D399 !important; }</style>")

page = st.navigation(
    [
        st.Page("app_pages/leads.py", title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page("app_pages/outreach.py", title="Outreach", icon=":material/mail:"),
        st.Page("app_pages/icp.py", title="Ideal customer", icon=":material/person_search:"),
    ],
    position="top",
)
page.run()
