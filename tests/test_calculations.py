from datetime import date
from decimal import Decimal
import unittest

from lombard.calculations import (
    calculate_loan,
    calculate_sale_realization,
    money_to_words,
    repayment_amount_on,
)
from lombard.pdf import render_contract_template


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

    def test_sale_realization_tracks_surplus_return(self):
        result = calculate_sale_realization(
            base_total_cents=100_000,
            due_date=date(2026, 1, 7),
            realization_date=date(2026, 1, 10),
            sale_amount_cents=150_000,
        )

        self.assertEqual(result.amount_due_cents, 103_000)
        self.assertEqual(result.surplus_cents, 47_000)
        self.assertEqual(result.surplus_fee_cents, 9_400)
        self.assertEqual(result.surplus_return_cents, 37_600)
        self.assertEqual(result.shortfall_cents, 0)

    def test_sale_realization_tracks_shortfall(self):
        result = calculate_sale_realization(
            base_total_cents=100_000,
            due_date=date(2026, 1, 7),
            realization_date=date(2026, 1, 10),
            sale_amount_cents=80_000,
        )

        self.assertEqual(result.amount_due_cents, 103_000)
        self.assertEqual(result.surplus_cents, 0)
        self.assertEqual(result.surplus_fee_cents, 0)
        self.assertEqual(result.surplus_return_cents, 0)
        self.assertEqual(result.shortfall_cents, 23_000)

    def test_contract_template_replaces_known_placeholders(self):
        contract = {
            "contract_number": "BUS/2026/0001",
            "branch_city": "Busko-Zdrój",
            "branch_address": "ul. Wojska Polskiego 3",
            "issue_date": "2026-01-01",
            "first_name": "Jan",
            "last_name": "Kowalski",
            "street_address": "ul. Testowa 1",
            "postal_code": "28-100",
            "client_city": "Busko-Zdrój",
            "pesel": "90010112345",
            "document_type": "Dowód Osobisty",
            "document_number": "ABC123456",
            "loan_amount_cents": 200_000,
            "commission_amount_cents": 20_000,
            "total_repayment_cents": 220_000,
            "term_days": 7,
            "due_date": "2026-01-07",
            "additional_period_end": "2026-02-06",
            "daily_increase_cents": 2_200,
            "max_additional_fee_cents": 44_000,
            "collateral_type": "rzecz ruchoma",
            "collateral_description": "Telefon testowy",
            "collateral_value_cents": 300_000,
            "valuation_basis": "oględziny",
            "sale_mode": "auction",
        }

        rendered = render_contract_template(
            contract,
            "Umowa {contract_number}: {client_name}, spłata {total_repayment}, {missing_field}",
        )

        self.assertIn("BUS/2026/0001", rendered)
        self.assertIn("Jan Kowalski", rendered)
        self.assertIn("2 200,00 zł", rendered)
        self.assertIn("{missing_field}", rendered)


if __name__ == "__main__":
    unittest.main()
