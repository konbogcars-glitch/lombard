import tempfile
import unittest
from pathlib import Path

from lombard import create_app
from lombard.database import get_db


class AppFlowTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "test.sqlite3"),
                "UPLOAD_FOLDER": str(root / "uploads"),
                "SECRET_KEY": "test",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_client_contract_pdf_settlement_and_accounting_export(self):
        response = self.client.post(
            "/clients",
            data={
                "first_name": "Jan",
                "last_name": "Kowalski",
                "pesel": "90010112345",
                "document_type": "Dowód Osobisty",
                "document_number": "ABC123456",
                "phone": "500600700",
                "email": "jan@example.com",
                "street_address": "ul. Testowa 1",
                "postal_code": "28-100",
                "city": "Busko-Zdrój",
                "notes": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            db = get_db()
            client_id = db.execute("SELECT id FROM clients").fetchone()["id"]
            branch_id = db.execute("SELECT id FROM branches WHERE code = 'BUS'").fetchone()["id"]

        response = self.client.post(
            "/contracts/new",
            data={
                "branch_id": branch_id,
                "client_id": client_id,
                "issue_date": "2026-01-01",
                "loan_amount": "2000,00",
                "commission_amount": "200,00",
                "commission_rate": "10",
                "term_days": "7",
                "collateral_type": "rzecz ruchoma",
                "collateral_description": "Telefon testowy IMEI 123",
                "collateral_value": "3000,00",
                "valuation_basis": "oględziny i oferty internetowe",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            contract = get_db().execute("SELECT * FROM contracts").fetchone()
            self.assertEqual(contract["contract_number"], "BUS/2026/0001")
            self.assertEqual(contract["due_date"], "2026-01-07")
            contract_id = contract["id"]

        pdf_response = self.client.get(f"/contracts/{contract_id}/pdf")
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.mimetype, "application/pdf")

        settle_response = self.client.post(
            f"/contracts/{contract_id}/settle",
            data={"payment_date": "2026-01-08", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)

        csv_response = self.client.get("/accounting/export.csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("BUS/2026/0001", csv_response.get_data(as_text=True))
        self.assertIn("2 222,00 zł", csv_response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
