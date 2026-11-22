import copy
import unittest
from datetime import date

import pandas as pd

from expense_tracker.analytics import (
    expense_category_totals,
    filter_transactions_by_date,
    highest_spending_categories,
    monthly_income_expenses,
    normalize_category,
    prepare_transactions,
    summarize_transactions,
)


def make_transaction(
    transaction_id,
    transaction_type,
    amount_paisa,
    category,
    transaction_date,
    note="",
):
    return {
        "id": transaction_id,
        "transaction_type": transaction_type,
        "amount": amount_paisa,
        "category": category,
        "transaction_date": transaction_date,
        "note": note,
        "created_at": "2026-01-01 00:00:00",
    }


class AnalyticsTests(unittest.TestCase):
    def test_empty_transaction_list_returns_empty_analytics(self):
        prepared = prepare_transactions([])

        self.assertTrue(prepared.empty)
        self.assertEqual(
            summarize_transactions(prepared),
            {
                "transaction_count": 0,
                "income_paisa": 0,
                "expense_paisa": 0,
                "net_paisa": 0,
            },
        )
        self.assertTrue(monthly_income_expenses(prepared).empty)
        self.assertTrue(expense_category_totals(prepared).empty)
        self.assertTrue(highest_spending_categories(prepared).empty)

    def test_summary_separates_income_and_expenses_in_integer_paisa(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "income", 12550, "Salary", "2026-01-10"),
                make_transaction(2, "expense", 2550, "Food", "2026-01-11"),
                make_transaction(3, "income", 30000, "Business", "2026-01-12"),
            ]
        )

        self.assertEqual(
            summarize_transactions(prepared),
            {
                "transaction_count": 3,
                "income_paisa": 42550,
                "expense_paisa": 2550,
                "net_paisa": 40000,
            },
        )

    def test_monthly_aggregation_handles_multiple_months_and_gaps(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "income", 10000, "Salary", "2026-01-10"),
                make_transaction(2, "expense", 2500, "Food", "2026-03-10"),
            ]
        )

        monthly = monthly_income_expenses(
            prepared,
            date(2026, 1, 1),
            date(2026, 3, 31),
        )

        self.assertEqual(
            [str(month) for month in monthly["month"]],
            ["2026-01", "2026-02", "2026-03"],
        )
        self.assertEqual(
            list(monthly["income_paisa"]),
            [10000, 0, 0],
        )
        self.assertEqual(
            list(monthly["expense_paisa"]),
            [0, 0, 2500],
        )

    def test_expense_totals_group_multiple_categories(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "expense", 150, "Food", "2026-02-01"),
                make_transaction(2, "expense", 250, "Food", "2026-02-02"),
                make_transaction(3, "expense", 50, "Transport", "2026-02-03"),
                make_transaction(4, "income", 5000, "Salary", "2026-02-04"),
            ]
        )

        totals = expense_category_totals(prepared)

        self.assertEqual(
            list(totals.itertuples(index=False, name=None)),
            [("Food", 400), ("Transport", 50)],
        )

    def test_missing_and_blank_categories_are_normalized(self):
        self.assertEqual(normalize_category(None), "Uncategorized")
        self.assertEqual(normalize_category("   "), "Uncategorized")
        self.assertEqual(normalize_category("  Food  "), "Food")

        prepared = prepare_transactions(
            [
                make_transaction(1, "expense", 125, None, "2026-02-01"),
                make_transaction(2, "expense", 75, " ", "2026-02-02"),
            ]
        )

        self.assertEqual(
            list(expense_category_totals(prepared).itertuples(index=False, name=None)),
            [("Uncategorized", 200)],
        )

    def test_invalid_dates_are_coerced_and_excluded_from_period_results(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "income", 1000, "Salary", "not-a-date"),
                make_transaction(2, "expense", 200, "Food", "2026-02-10"),
            ]
        )

        self.assertTrue(pd.isna(prepared.loc[0, "parsed_transaction_date"]))
        self.assertEqual(prepared.loc[0, "transaction_date"], "not-a-date")

        period_transactions = filter_transactions_by_date(
            prepared,
            date(2026, 2, 1),
            date(2026, 2, 28),
        )
        self.assertEqual(list(period_transactions["id"]), [2])

    def test_date_range_includes_both_boundaries(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "expense", 10, "Food", "2026-03-31"),
                make_transaction(2, "income", 20, "Salary", "2026-04-01"),
                make_transaction(3, "expense", 30, "Food", "2026-04-15"),
                make_transaction(4, "income", 40, "Salary", "2026-04-30"),
                make_transaction(5, "expense", 50, "Food", "2026-05-01"),
            ]
        )

        period_transactions = filter_transactions_by_date(
            prepared,
            date(2026, 4, 1),
            date(2026, 4, 30),
        )

        self.assertEqual(list(period_transactions["id"]), [2, 3, 4])
        self.assertEqual(
            summarize_transactions(period_transactions)["net_paisa"],
            30,
        )

    def test_reversed_date_range_is_rejected(self):
        prepared = prepare_transactions([])
        with self.assertRaises(ValueError):
            filter_transactions_by_date(
                prepared,
                date(2026, 5, 1),
                date(2026, 4, 1),
            )

    def test_highest_spending_categories_are_ranked_first(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "expense", 1000, "Food", "2026-01-01"),
                make_transaction(2, "expense", 500, "Transport", "2026-01-02"),
                make_transaction(3, "expense", 800, "Health", "2026-01-03"),
            ]
        )

        highest = highest_spending_categories(prepared, limit=2)

        self.assertEqual(
            list(highest.itertuples(index=False, name=None)),
            [("Food", 1000), ("Health", 800)],
        )

    def test_python_integer_sums_do_not_overflow_int64(self):
        maximum_sqlite_integer = 2**63 - 1
        prepared = prepare_transactions(
            [
                make_transaction(
                    1,
                    "income",
                    maximum_sqlite_integer,
                    "Salary",
                    "2026-01-01",
                ),
                make_transaction(
                    2,
                    "income",
                    maximum_sqlite_integer,
                    "Salary",
                    "2026-01-02",
                ),
            ]
        )

        expected_total = maximum_sqlite_integer * 2
        self.assertEqual(
            summarize_transactions(prepared)["income_paisa"],
            expected_total,
        )
        monthly = monthly_income_expenses(prepared)
        self.assertEqual(monthly.loc[0, "income_paisa"], expected_total)
        self.assertIsInstance(monthly.loc[0, "income_paisa"], int)

    def test_analytics_do_not_mutate_original_transactions(self):
        original = [
            make_transaction(1, "expense", 200, "  Food  ", "2026-01-01"),
            make_transaction(2, "income", 1000, None, "invalid"),
        ]
        original_copy = copy.deepcopy(original)

        prepared = prepare_transactions(original)
        filter_transactions_by_date(
            prepared,
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
        summarize_transactions(prepared)
        monthly_income_expenses(prepared)
        expense_category_totals(prepared)
        highest_spending_categories(prepared)

        self.assertEqual(original, original_copy)

    def test_invalid_transaction_type_is_excluded_and_reported(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "income", 1000, "Salary", "2026-01-01"),
                make_transaction(2, "transfer", 500, "Other", "2026-01-02"),
            ]
        )

        self.assertEqual(list(prepared["id"]), [1])
        self.assertEqual(summarize_transactions(prepared)["income_paisa"], 1000)
        self.assertEqual(
            prepared.attrs["exclusion_report"]["excluded_count"],
            1,
        )
        self.assertEqual(
            prepared.attrs["exclusion_report"]["reason_counts"],
            {"invalid transaction type": 1},
        )

    def test_invalid_amount_is_excluded_and_reported(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "expense", 300, "Food", "2026-01-01"),
                make_transaction(2, "expense", 0, "Transport", "2026-01-02"),
            ]
        )

        self.assertEqual(list(prepared["id"]), [1])
        self.assertEqual(summarize_transactions(prepared)["expense_paisa"], 300)
        self.assertEqual(
            prepared.attrs["exclusion_report"]["excluded_count"],
            1,
        )
        self.assertEqual(
            prepared.attrs["exclusion_report"]["reason_counts"],
            {"amount must be a positive integer-paisa value": 1},
        )

    def test_all_invalid_rows_return_empty_frame_with_exclusion_report(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "transfer", 100, "Other", "2026-01-01"),
                make_transaction(2, "expense", -1, "Food", "2026-01-02"),
            ]
        )

        self.assertTrue(prepared.empty)
        self.assertEqual(
            prepared.attrs["exclusion_report"]["excluded_count"],
            2,
        )
        self.assertEqual(
            summarize_transactions(prepared)["transaction_count"],
            0,
        )

    def test_valid_rows_still_aggregate_when_invalid_rows_are_excluded(self):
        prepared = prepare_transactions(
            [
                make_transaction(1, "income", 1200, "Salary", "2026-01-01"),
                make_transaction(2, "expense", 350, "Food", "2026-01-02"),
                make_transaction(3, "invalid", 900, "Other", "2026-01-03"),
                make_transaction(4, "expense", "500", "Travel", "2026-01-04"),
            ]
        )

        self.assertEqual(
            summarize_transactions(prepared),
            {
                "transaction_count": 2,
                "income_paisa": 1200,
                "expense_paisa": 350,
                "net_paisa": 850,
            },
        )
        self.assertEqual(
            prepared.attrs["exclusion_report"]["excluded_count"],
            2,
        )

    def test_original_fields_are_preserved_with_derived_analytics_columns(self):
        transaction = make_transaction(
            1,
            "income",
            1200,
            "  Salary  ",
            "2026-01-01",
        )
        prepared = prepare_transactions([transaction])

        self.assertEqual(prepared.loc[0, "amount"], 1200)
        self.assertEqual(prepared.loc[0, "category"], "  Salary  ")
        self.assertEqual(prepared.loc[0, "transaction_date"], "2026-01-01")
        self.assertEqual(
            prepared.loc[0, "created_at"],
            "2026-01-01 00:00:00",
        )
        self.assertEqual(prepared.loc[0, "amount_paisa"], 1200)
        self.assertEqual(prepared.loc[0, "normalized_category"], "Salary")
        self.assertEqual(
            prepared.loc[0, "parsed_transaction_date"],
            pd.Timestamp("2026-01-01"),
        )


if __name__ == "__main__":
    unittest.main()
