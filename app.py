from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from expense_tracker.auth import require_authentication


st.set_page_config(
    page_title="Personal Expense Tracker",
    page_icon="💰",
    layout="wide",
)

PROJECT_ROOT = Path(__file__).resolve().parent
try:
    streamlit_secrets = st.secrets.to_dict()
except StreamlitSecretNotFoundError:
    streamlit_secrets = {}

if not require_authentication(PROJECT_ROOT, streamlit_secrets):
    st.stop()

pages = [
    st.Page(
        str(PROJECT_ROOT / "pages" / "Overview.py"),
        title="Overview",
        icon="🏠",
        default=True,
    ),
    st.Page(
        str(PROJECT_ROOT / "pages" / "Transactions.py"),
        title="Transactions",
        icon="💳",
    ),
    st.Page(
        str(PROJECT_ROOT / "pages" / "Budgets_Insights.py"),
        title="Budgets & Insights",
        icon="📊",
    ),
    st.Page(
        str(PROJECT_ROOT / "pages" / "Analytics.py"),
        title="Analytics",
        icon="📈",
    ),
    st.Page(
        str(PROJECT_ROOT / "pages" / "Recurring_Calendar.py"),
        title="Recurring & Calendar",
        icon="🔁",
    ),
    st.Page(
        str(PROJECT_ROOT / "pages" / "Reports.py"),
        title="Reports",
        icon="📄",
    ),
]

navigation = st.navigation(pages, position="sidebar")
navigation.run()
