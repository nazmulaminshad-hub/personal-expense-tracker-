"""Local PDF report generation for personal finance summaries."""

from datetime import date
from io import BytesIO
from typing import Any, Dict, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def paisa_label(amount_paisa: int) -> str:
    """Format integer paisa as a BDT label."""
    sign = "-" if amount_paisa < 0 else ""
    whole, remainder = divmod(abs(int(amount_paisa)), 100)
    return "{}৳{:,.0f}.{:02d}".format(sign, whole, remainder)


def generate_monthly_report_pdf(
    year: int,
    month: int,
    summary: Dict[str, Any],
    category_rows: Iterable[Dict[str, Any]],
    budget_usage: Dict[str, Any],
    insights: Iterable[str],
) -> bytes:
    """Generate a monthly PDF report in memory."""
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError("year must be an integer")
    if isinstance(month, bool) or not isinstance(month, int):
        raise ValueError("month must be an integer")
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        title="Personal Finance Monthly Report",
    )
    styles = getSampleStyleSheet()
    content = []

    content.append(
        Paragraph(
            "Personal Finance Report - {}-{:02d}".format(year, month),
            styles["Title"],
        )
    )
    content.append(Spacer(1, 12))

    summary_table = Table(
        [
            ["Metric", "Confirmed Value"],
            ["Total Income", paisa_label(summary["income_paisa"])],
            ["Total Expenses", paisa_label(summary["expense_paisa"])],
            ["Net Savings", paisa_label(summary["net_savings_paisa"])],
            ["Savings Rate", "{:.2f}%".format(summary["savings_rate_percent"])],
        ],
        colWidths=[150, 220],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F80ED")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    content.append(summary_table)
    content.append(Spacer(1, 16))

    content.append(Paragraph("Category-wise Expenses", styles["Heading2"]))
    category_data = [["Category", "Amount"]]
    category_data.extend(
        [
            str(row["category"]),
            paisa_label(int(row["amount_paisa"])),
        ]
        for row in category_rows
    )
    if len(category_data) == 1:
        category_data.append(["No expense data", "৳0.00"])
    category_table = Table(category_data, colWidths=[250, 120])
    category_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27AE60")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]
        )
    )
    content.append(category_table)
    content.append(Spacer(1, 16))

    content.append(Paragraph("Budget Performance", styles["Heading2"]))
    overall_budget = int(budget_usage.get("overall_budget_paisa", 0))
    budget_data = [
        ["Overall Budget", paisa_label(overall_budget)],
        ["Total Expense", paisa_label(int(budget_usage.get("total_expense_paisa", 0)))],
        ["Remaining", paisa_label(int(budget_usage.get("remaining_paisa", 0)))],
        ["Usage", "{:.2f}%".format(float(budget_usage.get("usage_percent", 0.0)))],
    ]
    for category_row in budget_usage.get("category_usage", []):
        budget_data.append(
            [
                "Category: " + str(category_row["category"]),
                "{} / {}".format(
                    paisa_label(int(category_row["spent_paisa"])),
                    paisa_label(int(category_row["budget_paisa"])),
                ),
            ]
        )
    budget_table = Table(budget_data, colWidths=[250, 120])
    budget_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F9FC")),
            ]
        )
    )
    content.append(budget_table)
    content.append(Spacer(1, 16))

    content.append(Paragraph("Financial Insights", styles["Heading2"]))
    insight_list = list(insights)
    if insight_list:
        for insight in insight_list:
            content.append(Paragraph(str(insight), styles["BodyText"]))
            content.append(Spacer(1, 4))
    else:
        content.append(
            Paragraph(
                "No confirmed insights were available for this period.",
                styles["BodyText"],
            )
        )

    document.build(content)
    return output.getvalue()
