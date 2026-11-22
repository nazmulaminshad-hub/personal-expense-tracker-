from datetime import date

import streamlit as st

from expense_tracker.analytics import prepare_transactions
from expense_tracker.database import (
    DatabaseError,
    add_recurring_template,
    bdt_to_paisa,
    delete_recurring_template,
    execute_due_recurring_templates,
    list_recurring_templates,
    set_recurring_template_active,
)
from expense_tracker.ui.common import (
    clear_transaction_caches,
    format_paisa,
    initialize_database_once,
    load_transactions,
)


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
except (TypeError, ValueError) as error:
    st.error("Could not prepare analytics data: {}".format(error))
    st.stop()

exclusion_report = analytics_transactions.attrs.get("exclusion_report", {})
excluded_row_numbers = {
    row["row_number"]
    for row in exclusion_report.get("excluded_rows", [])
}
dashboard_transactions = [
    transaction
    for row_number, transaction in enumerate(transactions)
    if row_number not in excluded_row_numbers
]

st.title("Recurring & Calendar")

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

st.subheader("Recurring Transactions")
with st.form("recurring_template_form"):
    recurring_type = st.selectbox("Recurring Transaction Type", ["Income", "Expense"])
    recurring_amount = st.text_input("Recurring Amount (BDT)")
    recurring_category = st.text_input("Recurring Category")
    recurring_note = st.text_area("Recurring Note")
    recurring_schedule = st.selectbox("Schedule", ["Weekly", "Monthly"])
    recurring_start = st.date_input("Start Date", value=date.today())
    recurring_end = st.date_input("End Date", value=date.today())
    recurring_end_enabled = st.checkbox("Set an end date")
    recurring_submitted = st.form_submit_button("Create Recurring Template")

if recurring_submitted:
    try:
        add_recurring_template(
            recurring_type.lower(),
            bdt_to_paisa(recurring_amount.strip()),
            recurring_category.strip(),
            recurring_note.strip(),
            recurring_schedule.lower(),
            recurring_start.isoformat(),
            recurring_end.isoformat() if recurring_end_enabled else None,
        )
    except (DatabaseError, OSError, TypeError, ValueError) as error:
        st.error("Could not create recurring template: {}".format(error))
    else:
        clear_transaction_caches()
        st.success("Recurring template created. No transaction was created automatically.")
        st.rerun()

try:
    recurring_templates = list_recurring_templates()
except (DatabaseError, OSError) as error:
    st.error("Could not load recurring transaction templates: {}".format(error))
    st.stop()

if not recurring_templates:
    st.info("No recurring transaction templates exist.")
else:
    for template in recurring_templates:
        st.write(
            "#{} | {} | {} | {} | Next: {}".format(
                template["id"],
                template["schedule_type"].title(),
                template["transaction_type"].title(),
                format_paisa(template["amount"]),
                template["next_run_date"],
            )
        )
        active_value = st.checkbox(
            "Active",
            value=bool(template["active"]),
            key="recurring_active_{}".format(template["id"]),
        )
        if active_value != bool(template["active"]):
            try:
                set_recurring_template_active(template["id"], active_value)
            except (DatabaseError, OSError, TypeError, ValueError) as error:
                st.error("Could not update recurring template: {}".format(error))
            else:
                clear_transaction_caches()
                st.rerun()
        if st.button(
            "Delete",
            key="delete_recurring_{}".format(template["id"]),
        ):
            try:
                delete_recurring_template(template["id"])
            except (DatabaseError, OSError, TypeError, ValueError) as error:
                st.error("Could not delete recurring template: {}".format(error))
            else:
                clear_transaction_caches()
                st.success("Recurring template deleted.")
                st.rerun()

if st.button("Process Due Recurring Transactions"):
    try:
        created_ids = execute_due_recurring_templates(date.today())
    except (DatabaseError, OSError, TypeError, ValueError) as error:
        st.error("Could not process recurring transactions: {}".format(error))
    else:
        clear_transaction_caches()
        if created_ids:
            st.success(
                "{} recurring transaction(s) created.".format(
                    len(created_ids)
                )
            )
        else:
            st.info("No recurring transactions were due.")
        st.rerun()

st.subheader("Transaction Calendar")
calendar_month = st.date_input(
    "Calendar Month",
    value=date.today().replace(day=1),
    key="transaction_calendar_month",
)
calendar_month_text = calendar_month.strftime("%Y-%m")
calendar_rows = {}
for transaction in dashboard_transactions:
    transaction_date = str(transaction.get("transaction_date") or "")
    if transaction_date.startswith(calendar_month_text + "-"):
        calendar_rows[transaction_date] = calendar_rows.get(transaction_date, 0) + 1
calendar_data = [
    {
        "Date": transaction_date,
        "Transaction Count": count,
    }
    for transaction_date, count in sorted(calendar_rows.items())
]
if calendar_data:
    st.dataframe(
        calendar_data,
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No transactions exist in this calendar month.")
