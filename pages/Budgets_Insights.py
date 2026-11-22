from datetime import date

import streamlit as st

from expense_tracker.analytics import prepare_transactions
from expense_tracker.database import (
    DatabaseError,
    bdt_to_paisa,
    calculate_budget_usage,
    delete_category_budget,
    delete_overall_budget,
    list_category_budgets,
    paisa_to_bdt,
    set_category_budget,
    set_overall_budget,
)
from expense_tracker.intelligence import (
    detect_category_trends,
    detect_unusual_expenses,
    generate_recommendations,
)
from expense_tracker.ui.common import (
    clear_transaction_caches,
    format_paisa,
    format_taka_label,
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

st.title("Budgets & Insights")

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

st.subheader("Smart Budget Management")
budget_month = st.date_input(
    "Budget Month",
    value=date.today().replace(day=1),
    key="budget_month",
)
budget_month_text = budget_month.strftime("%Y-%m")

try:
    budget_usage = calculate_budget_usage(
        budget_month_text,
        transactions=dashboard_transactions,
    )
except (DatabaseError, OSError, TypeError, ValueError) as error:
    st.error("Could not load budget usage: {}".format(error))
    st.stop()

overall_budget_column, category_budget_column = st.columns(2)
with overall_budget_column:
    with st.form("overall_budget_form"):
        overall_budget_input = st.text_input(
            "Overall Monthly Budget (BDT)",
            value=(
                "{:.2f}".format(
                    paisa_to_bdt(budget_usage["overall_budget_paisa"])
                )
                if budget_usage["overall_budget_paisa"] > 0
                else ""
            ),
        )
        overall_budget_submitted = st.form_submit_button(
            "Save Overall Budget"
        )
    if overall_budget_submitted:
        try:
            set_overall_budget(
                budget_month_text,
                bdt_to_paisa(overall_budget_input.strip()),
            )
        except (DatabaseError, OSError, TypeError, ValueError) as error:
            st.error("Could not save overall budget: {}".format(error))
        else:
            clear_transaction_caches()
            st.success("Overall budget saved.")
            st.rerun()
    if budget_usage["overall_budget_paisa"] > 0:
        if st.button("Delete Overall Budget"):
            try:
                deleted = delete_overall_budget(budget_month_text)
            except (DatabaseError, OSError, TypeError, ValueError) as error:
                st.error("Could not delete overall budget: {}".format(error))
            else:
                if deleted:
                    clear_transaction_caches()
                    st.success("Overall budget deleted.")
                    st.rerun()

with category_budget_column:
    with st.form("category_budget_form"):
        category_budget_category = st.text_input("Category")
        category_budget_input = st.text_input("Category Budget (BDT)")
        category_budget_submitted = st.form_submit_button(
            "Save Category Budget"
        )
    if category_budget_submitted:
        try:
            set_category_budget(
                budget_month_text,
                category_budget_category.strip(),
                bdt_to_paisa(category_budget_input.strip()),
            )
        except (DatabaseError, OSError, TypeError, ValueError) as error:
            st.error("Could not save category budget: {}".format(error))
        else:
            clear_transaction_caches()
            st.success("Category budget saved.")
            st.rerun()

try:
    budget_usage = calculate_budget_usage(
        budget_month_text,
        transactions=dashboard_transactions,
    )
except (DatabaseError, OSError, TypeError, ValueError) as error:
    st.error("Could not refresh budget usage: {}".format(error))
    st.stop()
if budget_usage["overall_budget_paisa"] > 0:
    overall_usage = budget_usage["usage_percent"]
    st.progress(
        min(max(overall_usage / 100.0, 0.0), 1.0),
        text="Overall budget used: {:.1f}%".format(overall_usage),
    )
    if overall_usage >= 100:
        st.error(
            "Budget exceeded by {}".format(
                format_paisa(
                    budget_usage["total_expense_paisa"]
                    - budget_usage["overall_budget_paisa"]
                )
            )
        )
    elif overall_usage >= 80:
        st.warning("Spending caution: overall budget usage is at or above 80%.")
    elif overall_usage >= 50:
        st.info("Overall budget usage is at or above 50%.")

try:
    category_budget_rows = list_category_budgets(budget_month_text)
except (DatabaseError, OSError, TypeError, ValueError) as error:
    st.error("Could not load category budgets: {}".format(error))
    st.stop()
for category_usage in budget_usage["category_usage"]:
    usage_percent = category_usage["usage_percent"]
    st.write(
        "**{}:** {} of {}".format(
            category_usage["category"],
            format_paisa(category_usage["spent_paisa"]),
            format_paisa(category_usage["budget_paisa"]),
        )
    )
    st.progress(
        min(max(usage_percent / 100.0, 0.0), 1.0),
        text="{:.1f}% used".format(usage_percent),
    )
    if usage_percent >= 100:
        st.error("Exceeded by {}".format(format_paisa(-category_usage["remaining_paisa"])))
    elif usage_percent >= 80:
        st.warning("Spending caution.")
    elif usage_percent >= 50:
        st.info("Budget usage has reached 50%.")

with st.expander("Edit or Delete Category Budgets"):
    if not category_budget_rows:
        st.info("No category budgets exist for this month.")
    else:
        category_budget_names = [
            row["category"] for row in category_budget_rows
        ]
        selected_category_budget = st.selectbox(
            "Category Budget",
            category_budget_names,
        )
        selected_row = next(
            row
            for row in category_budget_rows
            if row["category"] == selected_category_budget
        )
        with st.form("edit_category_budget_form"):
            edited_category_budget = st.text_input(
                "Category Budget (BDT)",
                value="{:.2f}".format(paisa_to_bdt(selected_row["amount"])),
            )
            edit_category_budget_submitted = st.form_submit_button("Update")
        if edit_category_budget_submitted:
            try:
                set_category_budget(
                    budget_month_text,
                    selected_category_budget,
                    bdt_to_paisa(edited_category_budget.strip()),
                )
            except (DatabaseError, OSError, TypeError, ValueError) as error:
                st.error("Could not update category budget: {}".format(error))
            else:
                clear_transaction_caches()
                st.success("Category budget updated.")
                st.rerun()
        if st.button("Delete Category Budget"):
            try:
                deleted = delete_category_budget(
                    budget_month_text,
                    selected_category_budget,
                )
            except (DatabaseError, OSError, TypeError, ValueError) as error:
                st.error("Could not delete category budget: {}".format(error))
            else:
                if deleted:
                    clear_transaction_caches()
                    st.success("Category budget deleted.")
                    st.rerun()

st.subheader("Financial Intelligence")
intelligence_valid_dates = analytics_transactions["parsed_transaction_date"].dropna()
intelligence_start = (
    intelligence_valid_dates.min().date()
    if intelligence_valid_dates.size
    else date.today()
)
intelligence_end = (
    intelligence_valid_dates.max().date()
    if intelligence_valid_dates.size
    else date.today()
)
intelligence_summary = generate_recommendations(
    analytics_transactions,
    intelligence_start,
    intelligence_end,
)
intelligence_columns = st.columns(3)
intelligence_columns[0].metric(
    "Savings Rate",
    "{:.1f}%".format(intelligence_summary["savings_rate_percent"]),
)
intelligence_columns[1].metric(
    "Expense-to-Income Ratio",
    "{:.1f}%".format(intelligence_summary["expense_to_income_percent"]),
)
intelligence_columns[2].metric(
    "Budget Adherence",
    (
        "{:.1f}%".format(
            100.0 - max(budget_usage["usage_percent"] - 100.0, 0.0)
        )
        if budget_usage["overall_budget_paisa"] > 0
        else "No budget"
    ),
)
for recommendation in intelligence_summary["recommendations"]:
    st.info(recommendation)

trends = detect_category_trends(
    analytics_transactions,
    intelligence_end,
)
if trends:
    st.write("**Increasing expense categories:**")
    for trend in trends[:5]:
        if trend["percentage_available"]:
            trend_text = "{}: {} to {} ({:+.1f}%)".format(
                trend["category"],
                format_taka_label(trend["previous_paisa"]),
                format_taka_label(trend["current_paisa"]),
                trend["change_percent"],
            )
        else:
            trend_text = "{}: {} to {} (percentage unavailable: previous spending was ৳0.00)".format(
                trend["category"],
                format_taka_label(trend["previous_paisa"]),
                format_taka_label(trend["current_paisa"]),
            )
        st.write("- " + trend_text)

anomaly_rows = detect_unusual_expenses(analytics_transactions)
if anomaly_rows:
    st.warning("Potential unusual expenses:")
    for anomaly in anomaly_rows[:5]:
        st.write(
            "- Transaction #{} in {}: {} paisa (historical threshold {}).".format(
                anomaly["transaction_id"],
                anomaly["category"],
                anomaly["amount_paisa"],
                anomaly["threshold_paisa"],
            )
        )
else:
    st.caption(
        "No unusual-expense alert was generated. Historical data may be insufficient."
    )
