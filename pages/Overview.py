import streamlit as st

from expense_tracker.analytics import prepare_transactions, summarize_transactions
from expense_tracker.database import DatabaseError
from expense_tracker.ui.common import (
    format_paisa,
    initialize_database_once,
    load_transactions,
)


st.title("Personal Finance Overview")
st.caption("Income and expenses are stored and calculated in integer paisa.")

try:
    initialize_database_once()
    transactions = load_transactions()
except DatabaseError as error:
    st.error("Could not load the finance database: {}".format(error))
    st.stop()
except OSError as error:
    st.error("Could not access the finance database: {}".format(error))
    st.stop()

try:
    analytics_transactions = prepare_transactions(transactions)
    summary = summarize_transactions(analytics_transactions)
except (KeyError, TypeError, ValueError) as error:
    st.error("Could not prepare the dashboard summary: {}".format(error))
    st.stop()

metric_columns = st.columns(3)
with metric_columns[0]:
    with st.container(border=True):
        st.metric("Total Income", format_paisa(summary["income_paisa"]))
with metric_columns[1]:
    with st.container(border=True):
        st.metric("Total Expense", format_paisa(summary["expense_paisa"]))
with metric_columns[2]:
    with st.container(border=True):
        st.metric("Lifetime Net", format_paisa(summary["net_paisa"]))

st.caption("Lifetime income minus expenses; this is not a bank or account balance.")

exclusion_report = analytics_transactions.attrs.get("exclusion_report", {})
excluded_count = exclusion_report.get("excluded_count", 0)
if excluded_count:
    reason_summary = ", ".join(
        "{} ({})".format(reason, count)
        for reason, count in exclusion_report.get("reason_counts", {}).items()
    )
    st.warning(
        "{} transaction(s) excluded from Dashboard totals and analytics: {}."
        .format(excluded_count, reason_summary)
    )

if not transactions:
    st.info("No transactions yet. Use Transactions to add one.")
