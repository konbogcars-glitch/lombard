from datetime import date
from decimal import Decimal
import unittest

from lombard.calculations import calculate_loan, money_to_words, repayment_amount_on


class LoanCalculationTest(unittest.TestCase):
    def test_calculates_repayment_terms(self):
        result = calculate_loan(
            issue_date=date(2026, 1, 1),
            loan_amount_cents=200_000,
            commission_amount_cents=20_000,
            term_days=7,
        )

        self.assertEqual(result.total_repayment_cents, 220_000)
        self.assertEqual(result.due_date, date(2026, 1, 7))
        self.assertEqual(result.additional_period_end, date(2026, 2, 6))
        self.assertEqual(result.daily_increase_cents, 2_200)
        self.assertEqual(result.max_additional_fee_cents, 44_000)
        self.assertEqual(result.sale_mode, "auction")

    def test_calculates_commission_from_percent_when_missing(self):
        result = calculate_loan(
            issue_date=date(2026, 1, 1),
            loan_amount_cents=100_000,
            commission_rate_percent=Decimal("12.5"),
            term_days=1,
        )

        self.assertEqual(result.commission_amount_cents, 12_500)
        self.assertEqual(result.total_repayment_cents, 112_500)
        self.assertEqual(result.due_date, date(2026, 1, 1))

    def test_repayment_after_due_is_capped_after_twenty_days(self):
        total = repayment_amount_on(
            base_total_cents=100_000,
            due_date=date(2026, 1, 7),
            payment_date=date(2026, 2, 20),
        )

        self.assertEqual(total, 120_000)

    def test_money_words(self):
        self.assertEqual(
            money_to_words(220_000),
            "dwa tysiące dwieście złotych i zero groszy",
        )


if __name__ == "__main__":
    unittest.main()
