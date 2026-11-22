import csv
import io
from datetime import date

import streamlit as st

from expense_tracker.analytics import prepare_transactions
from expense_tracker.database import (
    DatabaseError,
    add_transaction,
    bdt_to_paisa,
    delete_transaction,
    get_transaction,
    list_transactions_filtered,
    paisa_to_bdt,
    summarize_transactions_filtered,
    set_transaction_tags,
    list_transaction_tags,
    update_transaction,
)
from expense_tracker.ui.common import (
    clear_transaction_caches,
    format_paisa,
    initialize_database_once,
    load_categories,
    load_transactions,
)


st.title("Transactions")

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

income_paisa = 0
expense_paisa = 0
history_rows = []
history_rows_by_id = {}

try:
    for transaction in dashboard_transactions:
        amount_paisa = transaction["amount"]
        amount_bdt = paisa_to_bdt(amount_paisa)
        transaction_type = transaction["transaction_type"]

        if transaction_type == "income":
            income_paisa += amount_paisa
        elif transaction_type == "expense":
            expense_paisa += amount_paisa
        else:
            raise ValueError(
                "Transaction {} has an unsupported type.".format(
                    transaction.get("id", "with unknown id")
                )
            )

        history_row = {
            "Transaction Type": transaction_type.title(),
            "Amount (BDT)": "৳{:,.2f}".format(amount_bdt),
            "Category": transaction["category"],
            "Date": transaction["transaction_date"],
            "Note": transaction["note"],
        }
        history_rows.append(history_row)
        history_rows_by_id[transaction["id"]] = history_row

    current_balance_paisa = income_paisa - expense_paisa
    income_display = format_paisa(income_paisa)
    expense_display = format_paisa(expense_paisa)
    balance_display = format_paisa(current_balance_paisa)
except (KeyError, TypeError, ValueError) as error:
    st.error("Could not display transaction data: {}".format(error))
    st.stop()

add_success_message = st.session_state.pop("add_transaction_success", None)
if add_success_message:
    st.session_state.pop("add_submission_lock", None)
    st.success(add_success_message)

edit_success_message = st.session_state.pop("edit_transaction_success", None)
if edit_success_message:
    st.success(edit_success_message)

delete_success_message = st.session_state.pop("delete_transaction_success", None)
if delete_success_message:
    st.success(delete_success_message)

st.subheader("Add a Transaction")
with st.form("transaction_form"):
    transaction_type_label = st.selectbox(
        "Transaction Type",
        ["Income", "Expense"],
    )
    amount_input = st.text_input(
        "Amount (BDT)",
        placeholder="For example, 125.50",
    )
    category = st.text_input("Category")
    transaction_date = st.date_input("Transaction Date", value=date.today())
    note = st.text_area("Optional Note")
    tags_input = st.text_input(
        "Tags (comma separated)",
        placeholder="travel, groceries",
    )
    submitted = st.form_submit_button("Save Transaction")

if submitted:
    if not amount_input.strip():
        st.error("Enter an amount in BDT.")
    elif not category.strip():
        st.error("Enter a category.")
    else:
        submission_saved = False
        try:
            amount_paisa = bdt_to_paisa(amount_input.strip())
            transaction_type = transaction_type_label.lower()
            submission_payload = (
                transaction_type,
                amount_paisa,
                category.strip(),
                transaction_date.isoformat(),
                note.strip(),
            )
            if st.session_state.get("add_submission_lock") == submission_payload:
                st.warning(
                    "This transaction appears to have just been submitted. "
                    "Wait for the form to refresh before submitting it again."
                )
            else:
                st.session_state["add_submission_lock"] = submission_payload
                saved_transaction_id = add_transaction(
                    transaction_type=transaction_type,
                    amount_paisa=amount_paisa,
                    category=category.strip(),
                    transaction_date=transaction_date.isoformat(),
                    note=note.strip(),
                )
                tags = [
                    tag.strip()
                    for tag in tags_input.split(",")
                    if tag.strip()
                ]
                if tags:
                    set_transaction_tags(
                        saved_transaction_id,
                        tags,
                    )
                submission_saved = True
        except DatabaseError as error:
            st.session_state.pop("add_submission_lock", None)
            st.error("Could not save the transaction: {}".format(error))
        except OSError as error:
            st.session_state.pop("add_submission_lock", None)
            st.error("Could not access the finance database: {}".format(error))
        except (TypeError, ValueError) as error:
            st.session_state.pop("add_submission_lock", None)
            st.error("Check the transaction details: {}".format(error))
        else:
            if submission_saved:
                clear_transaction_caches()
                st.session_state["add_transaction_success"] = (
                    "{} of {} added.".format(
                        transaction_type.title(),
                        format_paisa(amount_paisa),
                    )
                )
                st.rerun()

st.subheader("Edit a Transaction")
if not dashboard_transactions:
    st.info("There are no valid transactions to edit.")
else:
    transactions_by_id = {
        transaction["id"]: transaction for transaction in dashboard_transactions
    }
    transaction_labels = {
        transaction_id: "#{} | {} | {} | {} | {}".format(
            transaction_id,
            transaction["transaction_type"].title(),
            transaction["category"],
            transaction["transaction_date"],
            format_paisa(transaction["amount"]),
        )
        for transaction_id, transaction in transactions_by_id.items()
    }
    selected_transaction_id = st.selectbox(
        "Select a transaction to edit",
        options=[transaction["id"] for transaction in dashboard_transactions],
        format_func=lambda transaction_id: transaction_labels[transaction_id],
    )

    try:
        selected_transaction = get_transaction(selected_transaction_id)
    except DatabaseError as error:
        st.error("Could not load the selected transaction: {}".format(error))
        st.stop()
    except OSError as error:
        st.error("Could not access the finance database: {}".format(error))
        st.stop()
    except (TypeError, ValueError) as error:
        st.error("Could not load the selected transaction: {}".format(error))
        st.stop()

    if selected_transaction is None:
        st.warning("That transaction could not be found. Refresh the page and try again.")
    else:
        try:
            current_transaction_type = selected_transaction["transaction_type"]
            if current_transaction_type not in ("income", "expense"):
                raise ValueError("The selected transaction has an invalid type.")

            current_amount_bdt = paisa_to_bdt(selected_transaction["amount"])
            current_transaction_date = date.fromisoformat(
                selected_transaction["transaction_date"]
            )
            current_category = selected_transaction["category"]
            current_note = selected_transaction["note"]
            current_tags = [
                tag_row["tag"]
                for tag_row in list_transaction_tags(
                    selected_transaction_id
                )
            ]
        except (KeyError, TypeError, ValueError) as error:
            st.error("Could not prepare the selected transaction for editing: {}".format(error))
        else:
            transaction_type_options = ["Income", "Expense"]
            with st.form(
                "edit_transaction_form_{}".format(selected_transaction_id)
            ):
                edited_transaction_type = st.selectbox(
                    "Transaction Type",
                    transaction_type_options,
                    index=transaction_type_options.index(
                        current_transaction_type.title()
                    ),
                )
                edited_amount = st.text_input(
                    "Amount (BDT)",
                    value="{:.2f}".format(current_amount_bdt),
                )
                edited_category = st.text_input(
                    "Category",
                    value=current_category,
                )
                edited_date = st.date_input(
                    "Transaction Date",
                    value=current_transaction_date,
                )
                edited_note = st.text_area(
                    "Optional Note",
                    value=current_note,
                )
                edited_tags = st.text_input(
                    "Tags (comma separated)",
                    value=", ".join(current_tags),
                )
                edit_submitted = st.form_submit_button("Save Changes")

            if edit_submitted:
                if not edited_amount.strip():
                    st.error("Enter an amount in BDT.")
                elif not edited_category.strip():
                    st.error("Enter a category.")
                else:
                    try:
                        edited_amount_paisa = bdt_to_paisa(edited_amount.strip())
                        was_updated = update_transaction(
                            transaction_id=selected_transaction_id,
                            transaction_type=edited_transaction_type.lower(),
                            amount_paisa=edited_amount_paisa,
                            category=edited_category.strip(),
                            transaction_date=edited_date.isoformat(),
                            note=edited_note.strip(),
                        )
                    except DatabaseError as error:
                        st.error("Could not update the transaction: {}".format(error))
                    except OSError as error:
                        st.error("Could not access the finance database: {}".format(error))
                    except (TypeError, ValueError) as error:
                        st.error("Check the transaction details: {}".format(error))
                    else:
                        if not was_updated:
                            st.warning(
                                "The selected transaction no longer exists. "
                                "Refresh the page and try again."
                            )
                        else:
                            clear_transaction_caches()
                            set_transaction_tags(
                                selected_transaction_id,
                                [
                                    tag.strip()
                                    for tag in edited_tags.split(",")
                                    if tag.strip()
                                ],
                            )
                            st.session_state["edit_transaction_success"] = (
                                "Transaction #{} updated.".format(
                                    selected_transaction_id
                                )
                            )
                            st.rerun()

st.subheader("Delete a Transaction")
if not dashboard_transactions:
    st.info("There are no valid transactions to delete.")
else:
    delete_labels = {
        transaction["id"]: "#{} | {} | {} | {} | {}".format(
            transaction["id"],
            transaction["transaction_type"].title(),
            transaction["category"],
            transaction["transaction_date"],
            format_paisa(transaction["amount"]),
        )
        for transaction in dashboard_transactions
    }
    selected_delete_id = st.selectbox(
        "Select a transaction to delete",
        options=[transaction["id"] for transaction in dashboard_transactions],
        format_func=lambda transaction_id: delete_labels[transaction_id],
    )

    try:
        selected_delete_transaction = get_transaction(selected_delete_id)
    except DatabaseError as error:
        st.error("Could not load the selected transaction: {}".format(error))
        st.stop()
    except OSError as error:
        st.error("Could not access the finance database: {}".format(error))
        st.stop()
    except (TypeError, ValueError) as error:
        st.error("Could not load the selected transaction: {}".format(error))
        st.stop()

    if selected_delete_transaction is None:
        st.warning("That transaction could not be found. Refresh the page and try again.")
    else:
        try:
            delete_amount = format_paisa(selected_delete_transaction["amount"])
            delete_type = selected_delete_transaction["transaction_type"].title()
            delete_category = selected_delete_transaction["category"]
            delete_date = selected_delete_transaction["transaction_date"]
            delete_note = selected_delete_transaction["note"] or "(none)"
        except (KeyError, TypeError, ValueError) as error:
            st.error("Could not display the selected transaction: {}".format(error))
        else:
            st.write("**Transaction Type:** {}".format(delete_type))
            st.write("**Amount:** {}".format(delete_amount))
            st.write("**Category:** {}".format(delete_category))
            st.write("**Date:** {}".format(delete_date))
            st.write("**Note:** {}".format(delete_note))

            with st.form(
                "delete_transaction_form_{}".format(selected_delete_id)
            ):
                confirm_delete = st.checkbox(
                    "I understand this will permanently delete the selected transaction."
                )
                delete_submitted = st.form_submit_button("Confirm Delete")

            if delete_submitted:
                if not confirm_delete:
                    st.warning("Check the confirmation box before deleting.")
                else:
                    try:
                        was_deleted = delete_transaction(
                            transaction_id=selected_delete_id
                        )
                    except DatabaseError as error:
                        st.error("Could not delete the transaction: {}".format(error))
                    except OSError as error:
                        st.error("Could not access the finance database: {}".format(error))
                    except (TypeError, ValueError) as error:
                        st.error("Could not delete the transaction: {}".format(error))
                    else:
                        if not was_deleted:
                            st.warning(
                                "The selected transaction no longer exists. "
                                "Refresh the page and try again."
                            )
                        else:
                            clear_transaction_caches()
                            st.session_state["delete_transaction_success"] = (
                                "Transaction #{} deleted.".format(selected_delete_id)
                            )
                            st.rerun()

st.subheader("Search & Filter")
search_column, type_column, category_column = st.columns(3)
with search_column:
    search_text = st.text_input("Search category or note").strip().casefold()
with type_column:
    transaction_type_filter = st.selectbox(
        "Transaction Type",
        ["All", "Income", "Expense"],
    )

try:
    category_options = load_categories()
except (DatabaseError, OSError) as error:
    st.error("Could not load categories: {}".format(error))
    category_options = sorted(
        {transaction["category"] for transaction in dashboard_transactions}
    )
with category_column:
    category_filter = st.selectbox(
        "Category",
        ["All Categories"] + category_options,
    )

transaction_dates_by_id = {}
date_data_is_valid = False
for transaction in dashboard_transactions:
    try:
        transaction_dates_by_id[transaction["id"]] = date.fromisoformat(
            transaction["transaction_date"]
        )
    except (KeyError, TypeError, ValueError):
        continue

date_data_is_valid = bool(transaction_dates_by_id)

if dashboard_transactions and date_data_is_valid:
    earliest_date = min(transaction_dates_by_id.values())
    latest_date = max(transaction_dates_by_id.values())
else:
    earliest_date = date.today()
    latest_date = date.today()

start_date_column, end_date_column = st.columns(2)
with start_date_column:
    start_date = st.date_input(
        "Start Date",
        value=earliest_date,
        disabled=not dashboard_transactions or not date_data_is_valid,
    )
with end_date_column:
    end_date = st.date_input(
        "End Date",
        value=latest_date,
        disabled=not dashboard_transactions or not date_data_is_valid,
    )

filter_transaction_type = (
    None if transaction_type_filter == "All"
    else transaction_type_filter.lower()
)
filter_category = (
    None if category_filter == "All Categories"
    else category_filter
)

filtered_summary = {
    "transaction_count": 0,
    "income_paisa": 0,
    "expense_paisa": 0,
    "net_paisa": 0,
}
filtered_page = []

if date_data_is_valid:
    if start_date > end_date:
        st.warning("Start Date must be on or before End Date.")
    else:
        try:
            filtered_summary = summarize_transactions_filtered(
                search=search_text,
                transaction_type=filter_transaction_type,
                category=filter_category,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            history_filter_key = (
                search_text,
                filter_transaction_type,
                filter_category,
                start_date.isoformat(),
                end_date.isoformat(),
            )
            if st.session_state.get("history_filter_key") != history_filter_key:
                st.session_state["history_filter_key"] = history_filter_key
                st.session_state["history_offset"] = 0

            history_page_size = 100
            history_offset = int(st.session_state.get("history_offset", 0))
            filtered_page = list_transactions_filtered(
                search=search_text,
                transaction_type=filter_transaction_type,
                category=filter_category,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                limit=history_page_size,
                offset=history_offset,
            )
        except (DatabaseError, OSError, TypeError, ValueError) as error:
            st.error("Could not filter transactions: {}".format(error))

st.subheader("Filtered Summary")
summary_count_column, summary_income_column, summary_expense_column, summary_balance_column = st.columns(4)
summary_count_column.metric(
    "Matching Transactions",
    filtered_summary["transaction_count"],
)
summary_income_column.metric(
    "Matching Income",
    format_paisa(filtered_summary["income_paisa"]),
)
summary_expense_column.metric(
    "Matching Expenses",
    format_paisa(filtered_summary["expense_paisa"]),
)
summary_balance_column.metric(
    "Filtered Balance",
    format_paisa(filtered_summary["net_paisa"]),
)

st.subheader("Transaction History")
if not dashboard_transactions:
    if transactions:
        st.info("No valid transactions are available to display.")
    else:
        st.info("No transactions have been added yet.")
elif not filtered_summary["transaction_count"]:
    st.info("No transactions match the current search and filters.")
else:
    filtered_history_rows = []
    for transaction in filtered_page:
        if transaction["id"] in history_rows_by_id:
            filtered_history_rows.append(history_rows_by_id[transaction["id"]])

    st.dataframe(
        filtered_history_rows,
        use_container_width=True,
        hide_index=True,
    )

    history_offset = int(st.session_state.get("history_offset", 0))
    history_page_size = 100
    previous_column, next_column = st.columns(2)
    with previous_column:
        if st.button(
            "Previous 100",
            disabled=history_offset == 0,
        ):
            st.session_state["history_offset"] = max(
                0,
                history_offset - history_page_size,
            )
            st.rerun()
    with next_column:
        if st.button(
            "Next 100",
            disabled=history_offset + history_page_size
            >= filtered_summary["transaction_count"],
        ):
            st.session_state["history_offset"] = (
                history_offset + history_page_size
            )
            st.rerun()

    try:
        csv_transactions = list_transactions_filtered(
            search=search_text,
            transaction_type=filter_transaction_type,
            category=filter_category,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        csv_buffer = io.StringIO(newline="")
        csv_writer = csv.writer(csv_buffer)
        csv_writer.writerow(
            [
                "ID",
                "Transaction Type",
                "Amount (Paisa)",
                "Amount (BDT)",
                "Category",
                "Date",
                "Note",
                "Created At",
            ]
        )
        for transaction in csv_transactions:
            csv_writer.writerow(
                [
                    transaction["id"],
                    transaction["transaction_type"],
                    transaction["amount"],
                    format(paisa_to_bdt(transaction["amount"]), ".2f"),
                    transaction["category"],
                    transaction["transaction_date"],
                    transaction["note"],
                    transaction["created_at"],
                ]
            )
        csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
        st.download_button(
            "Export Filtered Transactions to CSV",
            data=csv_bytes,
            file_name="personal_finance_filtered_transactions.csv",
            mime="text/csv",
        )
    except (DatabaseError, OSError, TypeError, ValueError) as error:
        st.error("Could not prepare CSV export: {}".format(error))
