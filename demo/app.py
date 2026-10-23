"""Demo platform. Run: streamlit run demo/app.py  (needs DEV_API_KEY, and INDEX_API_URL if not on :8000)."""

import streamlit as st

st.set_page_config(page_title="File library demo", layout="wide")
st.title("File library demo platform")
st.write(
    "This app plays the platform's role: it knows the agents and which libraries each has attached. "
    "The index only stores libraries and searches them."
)
st.page_link("pages/1_Admin.py", label="Admin: libraries and files")
st.page_link("pages/2_Agents.py", label="Agents: attach libraries")
st.page_link("pages/3_Chat.py", label="Chat")
