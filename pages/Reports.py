import calendar
from datetime import date

import streamlit as st

from expense_tracker.analytics import (
    expense_category_totals,
    filter_transactions_by_date,
    prepare_transactions,
    summarize_transactions,
)
from expense_tracker.database import (
    DatabaseError,
    calculate_budget_usage,
)
from expense_tracker.intelligence import generate_recommendations
from expense_tracker.reports import generate_monthly_report_pdf
from expense_tracker.ui.common import (
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

st.title("Reports")

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

st.subheader("Monthly Reports")
report_year = st.number_input(
    "Report Year",
    min_value=2000,
    max_value=2100,
    value=date.today().year,
)
report_month = st.number_input(
    "Report Month",
    min_value=1,
    max_value=12,
    value=date.today().month,
)
try:
    report_start = date(int(report_year), int(report_month), 1)
    report_end = report_start.replace(
        day=calendar.monthrange(report_start.year, report_start.month)[1]
    )
except ValueError as error:
    st.error("Invalid report period: {}".format(error))
else:
    report_period = filter_transactions_by_date(
        analytics_transactions,
        report_start,
        report_end,
    )
    report_summary = summarize_transactions(report_period)
    report_savings = report_summary["net_paisa"]
    report_savings_rate = (
        report_savings * 100.0 / report_summary["income_paisa"]
        if report_summary["income_paisa"] > 0
        else 0.0
    )
    report_category_rows = expense_category_totals(report_period)
    try:
        report_budget_usage = calculate_budget_usage(
            report_start.strftime("%Y-%m"),
            transactions=dashboard_transactions,
        )
    except (DatabaseError, OSError, TypeError, ValueError) as error:
        st.error("Could not calculate report budget usage: {}".format(error))
        st.stop()

    report_insights = generate_recommendations(
        analytics_transactions,
        report_start,
        report_end,
    )["recommendations"]
    try:
        pdf_bytes = generate_monthly_report_pdf(
            int(report_year),
            int(report_month),
            {
                "income_paisa": report_summary["income_paisa"],
                "expense_paisa": report_summary["expense_paisa"],
                "net_savings_paisa": report_savings,
                "savings_rate_percent": report_savings_rate,
            },
            [
                {
                    "category": row.category,
                    "amount_paisa": int(row.amount_paisa),
                }
                for row in report_category_rows.itertuples(index=False)
            ],
            report_budget_usage,
            report_insights,
        )
    except (KeyError, OSError, TypeError, ValueError) as error:
        st.error("Could not generate the monthly PDF report: {}".format(error))
        st.stop()

    st.download_button(
        "Download Monthly PDF Report",
        data=pdf_bytes,
        file_name="personal_finance_{}-{:02d}.pdf".format(
            int(report_year),
            int(report_month),
        ),
        mime="application/pdf",
    )
