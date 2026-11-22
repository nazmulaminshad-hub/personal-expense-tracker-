from decimal import Decimal, localcontext

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from expense_tracker.analytics import (
    expense_category_totals,
    filter_transactions_by_date,
    highest_spending_categories,
    monthly_income_expenses,
    month_over_month_spending,
    prepare_transactions,
    summarize_transactions,
    weekly_financial_summary,
    yearly_financial_summary,
)
from expense_tracker.database import DatabaseError
from expense_tracker.ui.common import (
    format_paisa,
    format_taka_label,
    initialize_database_once,
    load_transactions,
)

PRIMARY_BLUE = "#2563EB"
INCOME_GREEN = "#15803D"
EXPENSE_RED = "#DC2626"
CHART_BACKGROUND = "#F8FAFC"
CHART_TEXT = "#172B4D"
CHART_GRID = "#DCE4EF"
CATEGORY_CHART_COLORS = [
    "#2563EB",
    "#0F766E",
    "#475569",
    "#0891B2",
    "#4F46E5",
    "#64748B",
]


def style_financial_chart(figure: go.Figure) -> go.Figure:
    """Apply the shared light-theme styling to Plotly charts."""
    figure.update_layout(
        template="plotly_white",
        font={"family": "Arial, sans-serif", "color": CHART_TEXT},
        paper_bgcolor=CHART_BACKGROUND,
        plot_bgcolor=CHART_BACKGROUND,
        margin={"l": 24, "r": 24, "t": 28, "b": 24},
        legend_title_text="",
    )
    figure.update_xaxes(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID)
    figure.update_yaxes(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID)
    return figure


def paisa_to_chart_bdt(amount_paisa: int) -> float:
    """Convert integer paisa to a numeric BDT value for Plotly only."""
    if amount_paisa == 0:
        return 0.0
    with localcontext() as context:
        context.prec = max(context.prec, len(str(abs(amount_paisa))) + 2)
        amount_bdt = Decimal(amount_paisa).scaleb(-2)
    return float(amount_bdt)


def render_analytics(analytics_transactions, excluded_count: int = 0) -> None:
    """Render analytics from the prepared in-memory transaction data."""
    st.subheader("Analytics")

    invalid_date_count = int(
        analytics_transactions["parsed_transaction_date"].isna().sum()
    )
    if invalid_date_count:
        st.warning(
            "{} transaction(s) have invalid dates and are excluded from analytics."
            .format(invalid_date_count)
        )

    if analytics_transactions.empty:
        if excluded_count:
            st.info("No valid transactions remain for analytics.")
        else:
            st.info("No transactions are available to analyze yet.")
        return

    valid_dates = analytics_transactions["parsed_transaction_date"].dropna()
    if valid_dates.empty:
        st.info("Analytics requires at least one transaction with a valid date.")
        return

    earliest_date = valid_dates.min().date()
    latest_date = valid_dates.max().date()
    start_column, end_column = st.columns(2)
    with start_column:
        analytics_start_date = st.date_input(
            "Analytics Start Date",
            value=earliest_date,
            key="analytics_start_date",
        )
    with end_column:
        analytics_end_date = st.date_input(
            "Analytics End Date",
            value=latest_date,
            key="analytics_end_date",
        )

    if analytics_start_date > analytics_end_date:
        st.warning("Analytics start date must be on or before the end date.")
        return

    try:
        period_transactions = filter_transactions_by_date(
            analytics_transactions,
            analytics_start_date,
            analytics_end_date,
        )
        period_summary = summarize_transactions(period_transactions)
        monthly_totals = monthly_income_expenses(
            period_transactions,
            analytics_start_date,
            analytics_end_date,
        )
        category_totals = expense_category_totals(period_transactions)
        highest_categories = highest_spending_categories(
            period_transactions,
            limit=5,
        )
        income_display = format_paisa(period_summary["income_paisa"])
        expense_display = format_paisa(period_summary["expense_paisa"])
        net_savings_display = format_paisa(period_summary["net_paisa"])
    except (KeyError, TypeError, ValueError) as error:
        st.error("Could not calculate analytics: {}".format(error))
        return

    st.subheader("Selected-Period Financial Summary")
    summary_columns = st.columns(4)
    summary_columns[0].metric(
        "Total Income",
        income_display,
    )
    summary_columns[1].metric(
        "Total Expense",
        expense_display,
    )
    summary_columns[2].metric(
        "Net Savings (Income minus Expense)",
        net_savings_display,
    )
    summary_columns[3].metric(
        "Transaction Count",
        period_summary["transaction_count"],
    )
    st.caption("Net savings is not a bank or account balance.")

    selected_month_comparison = month_over_month_spending(
        analytics_transactions,
        analytics_start_date,
    )
    st.caption(
        "Month-over-month spending: {} to {}{}".format(
            format_taka_label(selected_month_comparison["previous_expense_paisa"]),
            format_taka_label(selected_month_comparison["current_expense_paisa"]),
            (
                " ({:+.1f}%)".format(selected_month_comparison["change_percent"])
                if selected_month_comparison["previous_expense_paisa"] > 0
                else " (percentage unavailable: previous spending was ৳0.00)"
            ),
        )
    )
    weekly_summary = weekly_financial_summary(
        analytics_transactions,
        analytics_start_date,
        analytics_end_date,
    )
    if not weekly_summary.empty:
        st.caption("Weekly financial trend")
        st.dataframe(
            weekly_summary,
            use_container_width=True,
            hide_index=True,
        )
    yearly_summary = yearly_financial_summary(
        analytics_transactions,
        analytics_end_date.year,
    )
    st.caption(
        "Yearly confirmed totals for {}: income {}, expenses {}, net savings {}.".format(
            analytics_end_date.year,
            format_paisa(yearly_summary["income_paisa"]),
            format_paisa(yearly_summary["expense_paisa"]),
            format_paisa(yearly_summary["net_paisa"]),
        )
    )

    if period_transactions.empty:
        st.info("No transactions exist in the selected analytics period.")
        return

    monthly_chart_data = monthly_totals.copy()
    monthly_chart_data["Month"] = monthly_chart_data["month"].astype(str)
    monthly_chart_data["Income (BDT)"] = monthly_chart_data[
        "income_paisa"
    ].map(paisa_to_chart_bdt)
    monthly_chart_data["Expense (BDT)"] = monthly_chart_data[
        "expense_paisa"
    ].map(paisa_to_chart_bdt)

    st.subheader("Monthly Income vs Expense")
    monthly_bar = px.bar(
        monthly_chart_data,
        x="Month",
        y=["Income (BDT)", "Expense (BDT)"],
        barmode="group",
        labels={"value": "Amount (BDT)", "variable": "Transaction Type"},
        color_discrete_map={
            "Income (BDT)": INCOME_GREEN,
            "Expense (BDT)": EXPENSE_RED,
        },
    )
    style_financial_chart(monthly_bar)
    monthly_bar.update_yaxes(tickprefix="৳", separatethousands=True)
    st.plotly_chart(monthly_bar, use_container_width=True, theme=None)

    st.subheader("Expense Category Breakdown")
    if category_totals.empty:
        st.info("No expenses exist in the selected analytics period.")
    else:
        category_chart_data = category_totals.copy()
        category_chart_data["Amount (BDT)"] = category_chart_data[
            "amount_paisa"
        ].map(paisa_to_chart_bdt)
        category_donut = px.pie(
            category_chart_data,
            names="category",
            values="Amount (BDT)",
            hole=0.5,
            color_discrete_sequence=CATEGORY_CHART_COLORS,
        )
        style_financial_chart(category_donut)
        category_donut.update_traces(textinfo="label+percent")
        st.plotly_chart(category_donut, use_container_width=True, theme=None)

    st.subheader("Monthly Financial Trends")
    monthly_trend = px.line(
        monthly_chart_data,
        x="Month",
        y=["Income (BDT)", "Expense (BDT)"],
        markers=True,
        labels={"value": "Amount (BDT)", "variable": "Transaction Type"},
        color_discrete_map={
            "Income (BDT)": INCOME_GREEN,
            "Expense (BDT)": EXPENSE_RED,
        },
    )
    style_financial_chart(monthly_trend)
    monthly_trend.update_yaxes(tickprefix="৳", separatethousands=True)
    st.plotly_chart(monthly_trend, use_container_width=True, theme=None)

    st.subheader("Highest Spending Categories")
    if highest_categories.empty:
        st.info("No expense categories are available for ranking.")
    else:
        highest_chart_data = highest_categories.copy()
        highest_chart_data["Amount (BDT)"] = highest_chart_data[
            "amount_paisa"
        ].map(paisa_to_chart_bdt)
        highest_chart = px.bar(
            highest_chart_data,
            x="Amount (BDT)",
            y="category",
            orientation="h",
            labels={"category": "Category"},
        )
        style_financial_chart(highest_chart)
        highest_chart.update_traces(marker_color=PRIMARY_BLUE)
        highest_chart.update_yaxes(autorange="reversed")
        highest_chart.update_xaxes(tickprefix="৳", separatethousands=True)
        st.plotly_chart(highest_chart, use_container_width=True, theme=None)


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

render_analytics(analytics_transactions, excluded_count)
