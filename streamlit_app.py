from pathlib import Path

import streamlit as st

HERE = Path(__file__).parent

st.set_page_config(page_title="LeadSage", page_icon=str(HERE / "static" / "logo-icon.svg"), layout="wide")
st.html(HERE / "app.css")

pages = [
    st.Page("app_pages/home.py", title="Home", default=True),
    st.Page("app_pages/leads.py", title="Leads"),
    st.Page("app_pages/outreach.py", title="Outreach"),
    st.Page("app_pages/icp.py", title="Ideal customer"),
]
page = st.navigation(pages, position="hidden")

with st.container(horizontal=True, vertical_alignment="center", key="topbar"):
    st.image(str(HERE / "static" / "logo.svg"), width=150)
    st.space("stretch")
    with st.container(horizontal=True, vertical_alignment="center", gap="xxsmall", width="content", key="navpills"):
        for p in pages:
            if p.url_path == page.url_path:
                with st.container(width="content", key="navactive"):
                    st.page_link(p, label=p.title)
            else:
                st.page_link(p, label=p.title)
    st.space("stretch")
    if st.button("Review drafts", type="primary", icon=":material/arrow_forward:", key="topbar_review"):
        st.session_state.outreach_queue = "Ready to review"
        st.switch_page(pages[2])

page.run()
