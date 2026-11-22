import tempfile
import unittest
from datetime import date
from pathlib import Path
from contextlib import closing
import sqlite3

from expense_tracker.analytics import (
    financial_summary,
    month_over_month_spending,
    monthly_savings_series,
    prepare_transactions,
    weekly_financial_summary,
)
from expense_tracker.database import (
    add_recurring_template,
    add_transaction,
    calculate_budget_usage,
    delete_category_budget,
    execute_due_recurring_templates,
    initialize_database,
    list_category_budgets,
    list_recurring_templates,
    list_transactions,
    set_category_budget,
    set_overall_budget,
)
from expense_tracker.intelligence import (
    detect_category_trends,
    detect_unusual_expenses,
    generate_recommendations,
)
from expense_tracker.reports import generate_monthly_report_pdf


def transaction_row(
    transaction_id,
    transaction_type,
    amount,
    category,
    transaction_date,
):
    return {
        "id": transaction_id,
        "transaction_type": transaction_type,
        "amount": amount,
        "category": category,
        "transaction_date": transaction_date,
        "note": "",
        "created_at": "2026-11-22 00:00:00",
    }


class Phase3Tests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "finance.db"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_financial_summary_handles_zero_income_and_empty_data(self):
        prepared = prepare_transactions([])
        summary = financial_summary(
            prepared,
            date(2026, 11, 1),
            date(2026, 11, 30),
        )
        self.assertEqual(summary["income_paisa"], 0)
        self.assertEqual(summary["net_savings_paisa"], 0)
        self.assertEqual(summary["savings_rate_percent"], 0.0)

    def test_advanced_date_range_and_month_over_month_calculations(self):
        prepared = prepare_transactions(
            [
                transaction_row(1, "income", 10000, "Salary", "2026-10-10"),
                transaction_row(2, "expense", 2000, "Food", "2026-10-15"),
                transaction_row(3, "income", 12000, "Salary", "2026-11-10"),
                transaction_row(4, "expense", 3000, "Food", "2026-11-15"),
            ]
        )
        november = financial_summary(
            prepared,
            date(2026, 11, 1),
            date(2026, 11, 30),
        )
        self.assertEqual(november["income_paisa"], 12000)
        self.assertEqual(november["expense_paisa"], 3000)
        self.assertEqual(november["net_savings_paisa"], 9000)
        self.assertEqual(november["savings_rate_percent"], 75.0)

        comparison = month_over_month_spending(
            prepared,
            date(2026, 11, 1),
        )
        self.assertEqual(comparison["previous_expense_paisa"], 2000)
        self.assertEqual(comparison["current_expense_paisa"], 3000)
        self.assertEqual(comparison["change_paisa"], 1000)

        monthly = monthly_savings_series(
            prepared,
            date(2026, 10, 1),
            date(2026, 11, 30),
        )
        self.assertEqual(
            [int(value) for value in monthly["net_savings_paisa"]],
            [8000, 9000],
        )

        weekly = weekly_financial_summary(
            prepared,
            date(2026, 10, 1),
            date(2026, 11, 30),
        )
        self.assertFalse(weekly.empty)

    def test_overall_and_category_budget_duplicate_prevention(self):
        set_overall_budget("2026-11", 50000, self.database_path)
        set_overall_budget("2026-11", 60000, self.database_path)
        set_category_budget("2026-11", "Food", 10000, self.database_path)
        set_category_budget("2026-11", "Food", 12000, self.database_path)

        usage = calculate_budget_usage(
            "2026-11",
            transactions=[],
            database_path=self.database_path,
        )
        self.assertEqual(usage["overall_budget_paisa"], 60000)
        self.assertEqual(usage["category_usage"][0]["budget_paisa"], 12000)
        self.assertTrue(
            delete_category_budget(
                "2026-11",
                "Food",
                self.database_path,
            )
        )
        self.assertEqual(
            list_category_budgets("2026-11", self.database_path),
            [],
        )

    def test_recurring_execution_is_idempotent(self):
        template_id = add_recurring_template(
            "expense",
            500,
            "Rent",
            "Monthly rent",
            "monthly",
            "2026-11-01",
            "2026-12-31",
            self.database_path,
        )
        first = execute_due_recurring_templates(
            date(2026, 12, 31),
            self.database_path,
        )
        second = execute_due_recurring_templates(
            date(2026, 12, 31),
            self.database_path,
        )

        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        rows = list_transactions(database_path=self.database_path)
        rent_rows = [
            row for row in rows
            if row["category"] == "Rent"
        ]
        self.assertEqual(len(rent_rows), 2)
        templates = list_recurring_templates(self.database_path)
        self.assertEqual(templates[0]["id"], template_id)

    def test_intelligence_requires_sufficient_history_for_anomaly(self):
        prepared = prepare_transactions(
            [
                transaction_row(index, "expense", amount, "Food", "2026-11-{:02d}".format(index))
                for index, amount in enumerate([100, 110, 105, 95, 100, 500], start=1)
            ]
        )
        alerts = detect_unusual_expenses(prepared)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["amount_paisa"], 500)

        insufficient = detect_unusual_expenses(
            prepare_transactions(
                [
                    transaction_row(1, "expense", 100, "Food", "2026-11-01"),
                    transaction_row(2, "expense", 500, "Food", "2026-11-02"),
                ]
            )
        )
        self.assertEqual(insufficient, [])

    def test_recommendations_use_confirmed_totals(self):
        prepared = prepare_transactions(
            [
                transaction_row(1, "income", 10000, "Salary", "2026-11-10"),
                transaction_row(2, "expense", 9500, "Rent", "2026-11-11"),
            ]
        )
        result = generate_recommendations(
            prepared,
            date(2026, 11, 1),
            date(2026, 11, 30),
        )
        self.assertEqual(result["summary"]["income_paisa"], 10000)
        self.assertEqual(result["summary"]["expense_paisa"], 9500)
        self.assertLess(result["savings_rate_percent"], 10)
        self.assertTrue(result["recommendations"])

    def test_zero_baseline_trend_has_no_misleading_percentage(self):
        prepared = prepare_transactions(
            [
                transaction_row(
                    1,
                    "expense",
                    2500,
                    "Transport",
                    "2026-11-10",
                ),
            ]
        )
        trends = detect_category_trends(prepared, date(2026, 11, 1))
        transport_trend = next(
            trend
            for trend in trends
            if trend["category"] == "Transport"
        )
        self.assertFalse(transport_trend["percentage_available"])
        self.assertIsNone(transport_trend["change_percent"])

        result = generate_recommendations(
            prepared,
            date(2026, 11, 1),
            date(2026, 11, 30),
        )
        combined_text = " ".join(result["recommendations"])
        self.assertIn("৳25.00", combined_text)
        self.assertIn("percentage unavailable", combined_text)

    def test_pdf_report_generation_returns_pdf_bytes(self):
        pdf_bytes = generate_monthly_report_pdf(
            2026,
            11,
            {
                "income_paisa": 10000,
                "expense_paisa": 3000,
                "net_savings_paisa": 7000,
                "savings_rate_percent": 70.0,
            },
            [{"category": "Food", "amount_paisa": 3000}],
            {
                "overall_budget_paisa": 5000,
                "total_expense_paisa": 3000,
                "remaining_paisa": 2000,
                "usage_percent": 60.0,
                "category_usage": [],
            },
            ["Confirmed insight: Net savings are positive."],
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_phase3_migration_preserves_original_transactions(self):
        transaction_id = add_transaction(
            "income",
            10000,
            "Salary",
            "2026-11-22",
            "Preserved",
            self.database_path,
        )
        initialize_database(self.database_path)
        with closing(sqlite3.connect(str(self.database_path))) as connection:
            migrations = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            table_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' "
                    "AND name IN ('overall_budgets','category_budgets','transaction_tags','recurring_templates','recurring_executions')"
                )
            ]

        self.assertIn(4, migrations)
        self.assertEqual(len(table_names), 5)
        rows = list_transactions(database_path=self.database_path)
        self.assertTrue(any(row["id"] == transaction_id for row in rows))


if __name__ == "__main__":
    unittest.main()
