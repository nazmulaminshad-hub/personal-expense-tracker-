import streamlit as st

from expense_tracker.database import initialize_database, list_categories, list_transactions


@st.cache_resource
def initialize_database_once() -> None:
    """Initialize the database once per Streamlit process."""
    initialize_database()


@st.cache_data(ttl=30)
def load_transactions():
    """Load transactions for the dashboard and existing feature views."""
    return list_transactions()


@st.cache_data(ttl=30)
def load_categories():
    """Load distinct categories for transaction filters."""
    return list_categories()


def clear_transaction_caches() -> None:
    """Invalidate cached transaction and category data after a write."""
    st.cache_data.clear()


def format_paisa(amount_paisa: int) -> str:
    """Format a signed integer-paisa amount as BDT."""
    if amount_paisa == 0:
        return "৳0.00"

    sign = "-" if amount_paisa < 0 else ""
    whole_bdt, remaining_paisa = divmod(abs(amount_paisa), 100)
    return "{}৳{:,}.{:02d}".format(sign, whole_bdt, remaining_paisa)


def format_taka_label(amount_paisa: int) -> str:
    """Format integer paisa using a readable Bangladeshi Taka label."""
    sign = "-" if amount_paisa < 0 else ""
    whole, remainder = divmod(abs(int(amount_paisa)), 100)
    return "{}৳{:,.0f}.{:02d}".format(sign, whole, remainder)
