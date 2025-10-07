"""Streamlit entrypoint for deployment environments."""

import streamlit as st

st.set_page_config(page_title="SF311 Priority Labeler — Human-in-the-Loop", layout="wide")

from scripts import labeler_app

labeler_app.main()
