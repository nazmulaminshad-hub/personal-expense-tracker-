"""In-memory analytics helpers for personal finance transactions."""

import calendar
from datetime import date, timedelta
from numbers import Integral
from typing import Any, Dict, Iterable, Mapping, Optional

import pandas as pd


SQLITE_MAX_INTEGER = 2**63 - 1
TRANSACTION_COLUMNS = (
    "id",
    "transaction_type",
    "amount",
    "amount_paisa",
    "category",
    "normalized_category",
    "transaction_date",
    "parsed_transaction_date",
    "note",
    "created_at",
)
EXCLUSION_REPORT_ATTR = "exclusion_report"
MONTHLY_COLUMNS = ("month", "income_paisa", "expense_paisa")
CATEGORY_COLUMNS = ("category", "amount_paisa")


def normalize_category(category: Any) -> str:
    """Return a trimmed category name, using Uncategorized when it is missing."""
    if not isinstance(category, str) or not category.strip():
        return "Uncategorized"
    return category.strip()


def parse_transaction_dates(values: Iterable[Any]) -> pd.Series:
    """Parse ISO dates, representing invalid or missing values as NaT."""
    date_values = pd.Series(list(values), dtype="object")
    return pd.to_datetime(
        date_values,
        format="%Y-%m-%d",
        errors="coerce",
    )


def prepare_transactions(
    transactions: Iterable[Any],
) -> pd.DataFrame:
    """Prepare valid rows and attach a report for rows excluded from analytics.

    Original transaction fields are retained. Parsed dates, integer-paisa
    amounts, and normalized categories are added as separate analytics columns.
    """
    prepared_rows = []
    excluded_rows = []
    reason_counts = {}

    for row_number, transaction in enumerate(transactions):
        if not isinstance(transaction, Mapping):
            reasons = ["transaction row is not a mapping"]
        else:
            reasons = []
            transaction_type = transaction.get("transaction_type")
            amount = transaction.get("amount")

            if (
                not isinstance(transaction_type, str)
                or transaction_type not in ("income", "expense")
            ):
                reasons.append("invalid transaction type")
            if (
                isinstance(amount, bool)
                or not isinstance(amount, Integral)
                or amount <= 0
            ):
                reasons.append("amount must be a positive integer-paisa value")
            elif amount > SQLITE_MAX_INTEGER:
                reasons.append("amount exceeds SQLite's integer range")

        if reasons:
            excluded_rows.append(
                {
                    "row_number": row_number,
                    "reasons": tuple(reasons),
                }
            )
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue

        amount_paisa = int(amount)
        prepared_row = dict(transaction)
        prepared_row["amount_paisa"] = amount_paisa
        prepared_row["normalized_category"] = normalize_category(
            transaction.get("category")
        )
        prepared_row["parsed_transaction_date"] = transaction.get(
            "transaction_date"
        )
        prepared_rows.append(prepared_row)

    frame = pd.DataFrame.from_records(prepared_rows)
    for column in TRANSACTION_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")

    frame["parsed_transaction_date"] = parse_transaction_dates(
        frame["parsed_transaction_date"].tolist()
    )
    frame["amount_paisa"] = pd.Series(
        [int(amount) for amount in frame["amount_paisa"].tolist()],
        index=frame.index,
        dtype=object,
    )
    frame.attrs[EXCLUSION_REPORT_ATTR] = {
        "excluded_count": len(excluded_rows),
        "reason_counts": reason_counts,
        "excluded_rows": excluded_rows,
    }
    return frame


def filter_transactions_by_date(
    transactions: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Return a copy containing valid dates within the inclusive date range."""
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    start_timestamp = pd.Timestamp(start_date)
    end_timestamp = pd.Timestamp(end_date)
    date_column = transactions["parsed_transaction_date"]
    in_range = date_column.notna() & date_column.between(
        start_timestamp,
        end_timestamp,
        inclusive="both",
    )
    return transactions.loc[in_range].copy()


def _sum_integer_paisa(amounts: Iterable[Any]) -> int:
    """Sum paisa with Python integers to avoid fixed-width overflow."""
    return sum(int(amount) for amount in amounts)


def summarize_transactions(transactions: pd.DataFrame) -> Dict[str, int]:
    """Summarize a prepared transaction set using integer paisa."""
    income_paisa = _sum_integer_paisa(
        transactions.loc[
            transactions["transaction_type"] == "income",
            "amount_paisa",
        ].tolist()
    )
    expense_paisa = _sum_integer_paisa(
        transactions.loc[
            transactions["transaction_type"] == "expense",
            "amount_paisa",
        ].tolist()
    )
    return {
        "transaction_count": int(len(transactions)),
        "income_paisa": income_paisa,
        "expense_paisa": expense_paisa,
        "net_paisa": income_paisa - expense_paisa,
    }


def _monthly_result(
    months: Iterable[pd.Period],
    totals: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build monthly totals with object integers, including missing months."""
    records = []
    for month in months:
        if totals is None:
            income_paisa = 0
            expense_paisa = 0
        else:
            income_paisa = int(totals.at[month, "income"])
            expense_paisa = int(totals.at[month, "expense"])
        records.append(
            {
                "month": month,
                "income_paisa": income_paisa,
                "expense_paisa": expense_paisa,
            }
        )

    result = pd.DataFrame.from_records(records, columns=MONTHLY_COLUMNS)
    for column in ("income_paisa", "expense_paisa"):
        result[column] = pd.Series(
            [int(amount) for amount in result[column].tolist()],
            index=result.index,
            dtype=object,
        )
    return result


def monthly_income_expenses(
    transactions: pd.DataFrame,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> pd.DataFrame:
    """Aggregate monthly income and expenses, filling months without entries.

    When a date range is supplied, all months intersecting that range appear,
    even when the period contains no transactions.
    """
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    valid_transactions = transactions.loc[
        transactions["parsed_transaction_date"].notna()
    ].copy()

    if valid_transactions.empty:
        if start_date is None or end_date is None:
            return _monthly_result([])
        months = pd.period_range(
            pd.Period(start_date, freq="M"),
            pd.Period(end_date, freq="M"),
            freq="M",
        )
        return _monthly_result(months)

    valid_transactions["month"] = (
        valid_transactions["parsed_transaction_date"].dt.to_period("M")
    )
    grouped = (
        valid_transactions.groupby(
            ["month", "transaction_type"],
            sort=True,
        )["amount_paisa"]
        .agg(_sum_integer_paisa)
        .unstack(fill_value=0)
    )

    for transaction_type in ("income", "expense"):
        if transaction_type not in grouped.columns:
            grouped[transaction_type] = 0
    grouped = grouped[["income", "expense"]]

    if start_date is not None and end_date is not None:
        months = pd.period_range(
            pd.Period(start_date, freq="M"),
            pd.Period(end_date, freq="M"),
            freq="M",
        )
    else:
        months = pd.period_range(
            grouped.index.min(),
            grouped.index.max(),
            freq="M",
        )

    grouped = grouped.reindex(months, fill_value=0)
    return _monthly_result(months, grouped)


def expense_category_totals(transactions: pd.DataFrame) -> pd.DataFrame:
    """Return expense totals by normalized category, in descending order."""
    expenses = transactions.loc[
        transactions["transaction_type"] == "expense"
    ]
    if expenses.empty:
        return pd.DataFrame(columns=CATEGORY_COLUMNS)

    grouped = (
        expenses.groupby("normalized_category", sort=True)["amount_paisa"]
        .agg(_sum_integer_paisa)
        .reset_index()
        .rename(columns={"normalized_category": "category"})
    )
    records = [
        {
            "category": category,
            "amount_paisa": int(amount_paisa),
        }
        for category, amount_paisa in grouped.itertuples(index=False, name=None)
    ]
    records.sort(
        key=lambda record: (-record["amount_paisa"], record["category"].casefold())
    )
    result = pd.DataFrame.from_records(records, columns=CATEGORY_COLUMNS)
    result["amount_paisa"] = pd.Series(
        [int(amount) for amount in result["amount_paisa"].tolist()],
        index=result.index,
        dtype=object,
    )
    return result


def highest_spending_categories(
    transactions: pd.DataFrame,
    limit: int = 5,
) -> pd.DataFrame:
    """Return up to ``limit`` expense categories, highest total first."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return expense_category_totals(transactions).head(limit).reset_index(drop=True)


def financial_summary(
    transactions: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    """Return an exact integer-paisa summary for an inclusive date range."""
    period = filter_transactions_by_date(transactions, start_date, end_date)
    summary = summarize_transactions(period)
    income = summary["income_paisa"]
    savings = summary["net_paisa"]
    savings_rate = (savings * 100.0 / income) if income > 0 else 0.0
    expense_days = (
        period.loc[
            period["transaction_type"] == "expense",
            "parsed_transaction_date",
        ].dropna().nunique()
    )
    day_count = max((end_date - start_date).days + 1, 1)
    average_daily_expense = (
        summary["expense_paisa"] / day_count
        if summary["expense_paisa"]
        else 0
    )
    return {
        "transaction_count": summary["transaction_count"],
        "income_paisa": income,
        "expense_paisa": summary["expense_paisa"],
        "net_savings_paisa": savings,
        "savings_rate_percent": savings_rate,
        "average_daily_expense_paisa": average_daily_expense,
        "expense_transaction_days": int(expense_days),
    }


def weekly_financial_summary(
    transactions: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Aggregate valid transactions into ISO weeks for the selected range."""
    period = filter_transactions_by_date(transactions, start_date, end_date)
    if period.empty:
        return pd.DataFrame(
            columns=["week", "income_paisa", "expense_paisa", "net_savings_paisa"]
        )
    period = period.copy()
    period["week"] = period["parsed_transaction_date"].dt.to_period("W-SUN")
    grouped = (
        period.groupby(["week", "transaction_type"])["amount_paisa"]
        .agg(_sum_integer_paisa)
        .unstack(fill_value=0)
    )
    for transaction_type in ("income", "expense"):
        if transaction_type not in grouped.columns:
            grouped[transaction_type] = 0
    records = []
    for week in grouped.index:
        income = int(grouped.at[week, "income"])
        expense = int(grouped.at[week, "expense"])
        records.append(
            {
                "week": str(week),
                "income_paisa": income,
                "expense_paisa": expense,
                "net_savings_paisa": income - expense,
            }
        )
    return pd.DataFrame(records)


def monthly_savings_series(
    transactions: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Return monthly income, expense, and net savings in integer paisa."""
    period = filter_transactions_by_date(transactions, start_date, end_date)
    monthly = monthly_income_expenses(period, start_date, end_date)
    monthly = monthly.copy()
    monthly["net_savings_paisa"] = pd.Series(
        [
            int(row.income_paisa) - int(row.expense_paisa)
            for row in monthly.itertuples(index=False)
        ],
        index=monthly.index,
        dtype=object,
    )
    return monthly


def month_over_month_spending(
    transactions: pd.DataFrame,
    month: date,
) -> Dict[str, Any]:
    """Compare expense totals for a month and its previous month."""
    current_start = month.replace(day=1)
    if current_start.month == 1:
        previous_start = current_start.replace(year=current_start.year - 1, month=12)
    else:
        previous_start = current_start.replace(month=current_start.month - 1)

    current_end = current_start.replace(
        day=calendar.monthrange(current_start.year, current_start.month)[1]
    )
    previous_end = current_start - timedelta(days=1)

    current = filter_transactions_by_date(transactions, current_start, current_end)
    previous = filter_transactions_by_date(transactions, previous_start, previous_end)
    current_expense = summarize_transactions(current)["expense_paisa"]
    previous_expense = summarize_transactions(previous)["expense_paisa"]
    change_paisa = current_expense - previous_expense
    change_percent = (
        change_paisa * 100.0 / previous_expense
        if previous_expense > 0
        else 0.0
    )
    return {
        "previous_expense_paisa": previous_expense,
        "current_expense_paisa": current_expense,
        "change_paisa": change_paisa,
        "change_percent": change_percent,
    }


def category_spending_rankings(
    transactions: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    """Return the highest and lowest nonzero expense categories."""
    period = filter_transactions_by_date(transactions, start_date, end_date)
    totals = expense_category_totals(period)
    if totals.empty:
        return {
            "highest": None,
            "lowest": None,
            "categories": totals,
        }
    highest = totals.iloc[0].to_dict()
    lowest = totals.iloc[-1].to_dict()
    return {
        "highest": highest,
        "lowest": lowest,
        "categories": totals,
    }


def yearly_financial_summary(
    transactions: pd.DataFrame,
    year: int,
) -> Dict[str, Any]:
    """Summarize one calendar year using exact integer-paisa totals."""
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError("year must be an integer")
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    period = filter_transactions_by_date(transactions, start, end)
    return summarize_transactions(period)
