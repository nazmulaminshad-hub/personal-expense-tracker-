"""Explainable, local-only financial insights."""

from datetime import date
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List

import pandas as pd

from expense_tracker.analytics import expense_category_totals, summarize_transactions


def frequent_expense_categories(
    transactions: pd.DataFrame,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Rank expense categories by transaction count."""
    expenses = transactions.loc[
        transactions["transaction_type"] == "expense"
    ].copy()
    if expenses.empty:
        return []
    counts = (
        expenses.groupby("normalized_category")
        .size()
        .sort_values(ascending=False)
    )
    return [
        {
            "category": str(category),
            "transaction_count": int(count),
        }
        for category, count in counts.head(limit).items()
    ]


def detect_category_trends(
    transactions: pd.DataFrame,
    current_month: date,
) -> List[Dict[str, Any]]:
    """Compare category expenses with the previous calendar month."""
    current_start = current_month.replace(day=1)
    if current_start.month == 1:
        previous_start = current_start.replace(year=current_start.year - 1, month=12)
    else:
        previous_start = current_start.replace(month=current_start.month - 1)
    previous_end = current_start - pd.Timedelta(days=1)
    current_end = previous_end + pd.Timedelta(days=1)
    current_end = date(
        current_start.year,
        current_start.month,
        pd.Timestamp(current_start).days_in_month,
    )

    previous = transactions.loc[
        (transactions["parsed_transaction_date"].notna())
        & (transactions["parsed_transaction_date"] >= pd.Timestamp(previous_start))
        & (transactions["parsed_transaction_date"] <= pd.Timestamp(previous_end))
        & (transactions["transaction_type"] == "expense")
    ].copy()
    current = transactions.loc[
        (transactions["parsed_transaction_date"].notna())
        & (transactions["parsed_transaction_date"] >= pd.Timestamp(current_start))
        & (transactions["parsed_transaction_date"] <= pd.Timestamp(current_end))
        & (transactions["transaction_type"] == "expense")
    ].copy()

    def totals(frame: pd.DataFrame) -> Dict[str, int]:
        if frame.empty:
            return {}
        result = {}
        for row in frame.itertuples(index=False):
            category = str(row.normalized_category)
            result[category] = result.get(category, 0) + int(row.amount_paisa)
        return result

    previous_totals = totals(previous)
    current_totals = totals(current)
    trends = []
    for category in sorted(set(previous_totals) | set(current_totals)):
        before = previous_totals.get(category, 0)
        after = current_totals.get(category, 0)
        change = after - before
        change_percent = (
            change * 100.0 / before
            if before > 0
            else None
        )
        if change > 0:
            trends.append(
                {
                    "category": category,
                    "previous_paisa": before,
                    "current_paisa": after,
                    "change_paisa": change,
                    "change_percent": change_percent,
                    "percentage_available": before > 0,
                }
            )
    return sorted(trends, key=lambda item: item["change_paisa"], reverse=True)


def detect_unusual_expenses(
    transactions: pd.DataFrame,
    minimum_history_count: int = 5,
) -> List[Dict[str, Any]]:
    """Flag expenses beyond category mean plus two population deviations."""
    if transactions.empty:
        return []
    expenses = transactions.loc[
        transactions["transaction_type"] == "expense"
    ].copy()
    if expenses.empty:
        return []

    alerts = []
    for category, category_rows in expenses.groupby("normalized_category"):
        category_rows = category_rows.sort_values("parsed_transaction_date")
        amount_values = [
            int(amount) for amount in category_rows["amount_paisa"].tolist()
        ]
        if len(amount_values) < minimum_history_count:
            continue
        category_mean = mean(amount_values)
        category_deviation = pstdev(amount_values)
        if category_deviation == 0:
            continue
        threshold = category_mean + (2 * category_deviation)
        for row in category_rows.itertuples(index=False):
            amount = int(row.amount_paisa)
            if amount > threshold:
                alerts.append(
                    {
                        "transaction_id": row.id,
                        "category": str(category),
                        "amount_paisa": amount,
                        "threshold_paisa": int(threshold),
                        "history_count": len(amount_values),
                        "message": (
                            "Unusual expense: amount is above the category "
                            "mean plus two standard deviations."
                        ),
                    }
                )
    return sorted(alerts, key=lambda item: item["amount_paisa"], reverse=True)


def generate_recommendations(
    transactions: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    """Generate conservative recommendations supported by observed totals."""
    period = transactions.loc[
        (transactions["parsed_transaction_date"].notna())
        & (transactions["parsed_transaction_date"] >= pd.Timestamp(start_date))
        & (transactions["parsed_transaction_date"] <= pd.Timestamp(end_date))
    ].copy()
    summary = summarize_transactions(period)
    category_totals = expense_category_totals(period)

    def format_paisa_label(amount_paisa: int) -> str:
        sign = "-" if amount_paisa < 0 else ""
        whole, remainder = divmod(abs(int(amount_paisa)), 100)
        return "{}৳{:,.0f}.{:02d}".format(sign, whole, remainder)

    recommendations = []
    if summary["income_paisa"] == 0:
        recommendations.append(
            "No confirmed income was found in this period; savings-rate recommendations are unavailable."
        )
    else:
        rate = summary["net_paisa"] * 100.0 / summary["income_paisa"]
        if rate < 10:
            recommendations.append(
                "Confirmed savings rate is below 10%; review the highest expense categories before increasing spending."
            )

    if not category_totals.empty:
        highest = category_totals.iloc[0]
        recommendations.append(
            "{} is the highest confirmed expense category at {}.".format(
                highest["category"],
                format_paisa_label(int(highest["amount_paisa"])),
            )
        )

    trends = detect_category_trends(
        transactions,
        end_date,
    )
    for trend in trends[:3]:
        if trend["percentage_available"]:
            percentage_text = " ({:+.1f}%)".format(trend["change_percent"])
        else:
            percentage_text = " (percentage unavailable: previous spending was ৳0.00)"
        recommendations.append(
            "{} increased by {} compared with the previous month{}.".format(
                trend["category"],
                format_paisa_label(trend["change_paisa"]),
                percentage_text,
            )
        )

    return {
        "summary": summary,
        "savings_rate_percent": (
            summary["net_paisa"] * 100.0 / summary["income_paisa"]
            if summary["income_paisa"] > 0
            else 0.0
        ),
        "expense_to_income_percent": (
            summary["expense_paisa"] * 100.0 / summary["income_paisa"]
            if summary["income_paisa"] > 0
            else 0.0
        ),
        "recommendations": recommendations,
        "confirmed_facts": len(recommendations) > 0,
    }
