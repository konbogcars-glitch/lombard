import io
import tempfile
import unittest
import zipfile
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

    def _branch_id(self, code):
        with self.app.app_context():
            return get_db().execute(
                "SELECT id FROM branches WHERE code = ?",
                (code,),
            ).fetchone()["id"]

    def _create_client(self, *, first_name="Jan", last_name="Kowalski", pesel="90010112345"):
        response = self.client.post(
            "/clients",
            data={
                "first_name": first_name,
                "last_name": last_name,
                "pesel": pesel,
                "document_type": "Dowód Osobisty",
                "document_number": f"{pesel[-3:]}ABC",
                "phone": "500600700",
                "email": f"{first_name.lower()}@example.com",
                "street_address": "ul. Testowa 1",
                "postal_code": "28-100",
                "city": "Busko-Zdrój",
                "notes": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            return get_db().execute(
                "SELECT id FROM clients WHERE pesel = ? ORDER BY id DESC LIMIT 1",
                (pesel,),
            ).fetchone()["id"]

    def _create_contract(
        self,
        *,
        branch_code="BUS",
        client_id=None,
        issue_date="2026-01-01",
        term_days="7",
        loan_amount="2000,00",
        commission_amount="200,00",
    ):
        if client_id is None:
            client_id = self._create_client()
        response = self.client.post(
            "/contracts/new",
            data={
                "branch_id": self._branch_id(branch_code),
                "client_id": client_id,
                "issue_date": issue_date,
                "loan_amount": loan_amount,
                "commission_amount": commission_amount,
                "commission_rate": "10",
                "term_days": term_days,
                "collateral_type": "rzecz ruchoma",
                "collateral_description": "Telefon testowy IMEI 123",
                "collateral_value": "3000,00",
                "valuation_basis": "oględziny i oferty internetowe",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            return get_db().execute(
                "SELECT * FROM contracts ORDER BY id DESC LIMIT 1",
            ).fetchone()

    def test_client_card_can_be_updated_for_existing_contracts(self):
        client_id = self._create_client()
        contract = self._create_contract(client_id=client_id)

        edit_page = self.client.get(f"/clients/{client_id}/edit")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn("Jan", edit_page.get_data(as_text=True))

        response = self.client.post(
            f"/clients/{client_id}/edit",
            data={
                "first_name": "Jan",
                "last_name": "Nowak",
                "pesel": "90010112345",
                "document_type": "Dowód Osobisty",
                "document_number": "XYZ987654",
                "phone": "700800900",
                "email": "jan.nowak@example.com",
                "street_address": "ul. Poprawiona 2",
                "postal_code": "28-400",
                "city": "Pińczów",
                "notes": "Dane potwierdzone przy kolejnej wizycie.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        detail_page = response.get_data(as_text=True)
        self.assertIn("Jan Nowak", detail_page)
        self.assertIn("700800900", detail_page)

        contract_page = self.client.get(f"/contracts/{contract['id']}").get_data(as_text=True)
        self.assertIn("Jan Nowak", contract_page)
        self.assertIn("ul. Poprawiona 2", contract_page)

        with self.app.app_context():
            updated = get_db().execute(
                "SELECT last_name, phone, city FROM clients WHERE id = ?",
                (client_id,),
            ).fetchone()
            self.assertEqual(updated["last_name"], "Nowak")
            self.assertEqual(updated["phone"], "700800900")
            self.assertEqual(updated["city"], "Pińczów")

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

    def test_contract_photo_upload_is_stored_and_served(self):
        contract = self._create_contract()

        response = self.client.post(
            f"/contracts/{contract['id']}/photos",
            data={
                "caption": "Stan przedmiotu przy przyjęciu",
                "photos": [(io.BytesIO(b"fake image bytes"), "zastaw.jpg")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            photo = get_db().execute(
                "SELECT * FROM contract_photos WHERE contract_id = ?",
                (contract["id"],),
            ).fetchone()
            self.assertIsNotNone(photo)
            self.assertEqual(photo["caption"], "Stan przedmiotu przy przyjęciu")

        file_response = self.client.get(
            f"/uploads/{contract['id']}/{photo['stored_filename']}"
        )
        try:
            self.assertEqual(file_response.status_code, 200)
            self.assertEqual(file_response.get_data(), b"fake image bytes")
        finally:
            file_response.close()

    def test_overdue_status_and_accounting_transitions_are_guarded(self):
        contract = self._create_contract(issue_date="2025-01-01", term_days="1")

        response = self.client.get(f"/contracts/{contract['id']}")
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            refreshed = get_db().execute(
                "SELECT status FROM contracts WHERE id = ?",
                (contract["id"],),
            ).fetchone()
            self.assertEqual(refreshed["status"], "expired")

        account_active_response = self.client.post(
            f"/contracts/{contract['id']}/account",
            follow_redirects=True,
        )
        self.assertEqual(account_active_response.status_code, 200)
        with self.app.app_context():
            still_expired = get_db().execute(
                "SELECT status FROM contracts WHERE id = ?",
                (contract["id"],),
            ).fetchone()
            self.assertEqual(still_expired["status"], "expired")

        settle_response = self.client.post(
            f"/contracts/{contract['id']}/settle",
            data={"payment_date": "2025-01-02", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)
        with self.app.app_context():
            settled = get_db().execute(
                "SELECT status, paid_amount_cents FROM contracts WHERE id = ?",
                (contract["id"],),
            ).fetchone()
            self.assertEqual(settled["status"], "settled")
            original_paid_amount = settled["paid_amount_cents"]

        account_response = self.client.post(
            f"/contracts/{contract['id']}/account",
            data={"accounting_note": "wysłano zbiorczo"},
            follow_redirects=True,
        )
        self.assertEqual(account_response.status_code, 200)

        resettle_response = self.client.post(
            f"/contracts/{contract['id']}/settle",
            data={"payment_date": "2025-01-03", "paid_amount": "9999,99"},
            follow_redirects=True,
        )
        self.assertEqual(resettle_response.status_code, 200)
        with self.app.app_context():
            accounted = get_db().execute(
                """
                SELECT status, paid_amount_cents, accounting_note
                FROM contracts
                WHERE id = ?
                """,
                (contract["id"],),
            ).fetchone()
            self.assertEqual(accounted["status"], "accounted")
            self.assertEqual(accounted["paid_amount_cents"], original_paid_amount)
            self.assertEqual(accounted["accounting_note"], "wysłano zbiorczo")

    def test_accounting_can_be_filtered_by_branch(self):
        bus_client = self._create_client(first_name="Anna", pesel="80010112345")
        chm_client = self._create_client(first_name="Ewa", pesel="81010112345")
        bus_contract = self._create_contract(
            branch_code="BUS",
            client_id=bus_client,
            issue_date="2026-02-01",
        )
        chm_contract = self._create_contract(
            branch_code="CHM",
            client_id=chm_client,
            issue_date="2026-02-01",
        )

        for contract in (bus_contract, chm_contract):
            response = self.client.post(
                f"/contracts/{contract['id']}/settle",
                data={"payment_date": "2026-02-07", "paid_amount": ""},
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)

        branch_id = self._branch_id("BUS")
        page_response = self.client.get(f"/accounting?branch_id={branch_id}")
        self.assertEqual(page_response.status_code, 200)
        page = page_response.get_data(as_text=True)
        self.assertIn(bus_contract["contract_number"], page)
        self.assertNotIn(chm_contract["contract_number"], page)

        csv_response = self.client.get(f"/accounting/export.csv?branch_id={branch_id}")
        self.assertEqual(csv_response.status_code, 200)
        csv_data = csv_response.get_data(as_text=True)
        self.assertIn(bus_contract["contract_number"], csv_data)
        self.assertNotIn(chm_contract["contract_number"], csv_data)

    def test_bulk_accounting_marks_only_selected_branch(self):
        bus_client = self._create_client(first_name="Anna", pesel="80010112345")
        chm_client = self._create_client(first_name="Ewa", pesel="81010112345")
        bus_contract = self._create_contract(
            branch_code="BUS",
            client_id=bus_client,
            issue_date="2026-03-01",
        )
        chm_contract = self._create_contract(
            branch_code="CHM",
            client_id=chm_client,
            issue_date="2026-03-01",
        )

        for contract in (bus_contract, chm_contract):
            response = self.client.post(
                f"/contracts/{contract['id']}/settle",
                data={"payment_date": "2026-03-07", "paid_amount": ""},
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)

        branch_id = self._branch_id("BUS")
        response = self.client.post(
            "/accounting/bulk-account",
            data={"branch_id": branch_id, "accounting_note": "CSV wysłany zbiorczo"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            rows = get_db().execute(
                """
                SELECT contract_number, status, accounting_note
                FROM contracts
                WHERE id IN (?, ?)
                ORDER BY contract_number
                """,
                (bus_contract["id"], chm_contract["id"]),
            ).fetchall()

        by_number = {row["contract_number"]: row for row in rows}
        self.assertEqual(by_number[bus_contract["contract_number"]]["status"], "accounted")
        self.assertEqual(
            by_number[bus_contract["contract_number"]]["accounting_note"],
            "CSV wysłany zbiorczo",
        )
        self.assertEqual(by_number[chm_contract["contract_number"]]["status"], "settled")

    def test_branch_context_filters_dashboard_and_preselects_contract_branch(self):
        bus_contract = self._create_contract(branch_code="BUS", issue_date="2026-12-01")
        chm_client = self._create_client(first_name="Ewa", pesel="82010112345")
        chm_contract = self._create_contract(
            branch_code="CHM",
            client_id=chm_client,
            issue_date="2026-12-01",
        )

        branch_id = self._branch_id("CHM")
        response = self.client.post(
            "/context/branch",
            data={"branch_id": branch_id, "next": "/"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        dashboard = response.get_data(as_text=True)
        self.assertIn(chm_contract["contract_number"], dashboard)
        self.assertNotIn(bus_contract["contract_number"], dashboard)

        form_response = self.client.get("/contracts/new")
        self.assertEqual(form_response.status_code, 200)
        self.assertIn(f'value="{branch_id}" selected', form_response.get_data(as_text=True))

        all_response = self.client.get("/?branch_id=all")
        self.assertEqual(all_response.status_code, 200)
        all_dashboard = all_response.get_data(as_text=True)
        self.assertIn(chm_contract["contract_number"], all_dashboard)
        self.assertIn(bus_contract["contract_number"], all_dashboard)

    def test_accounting_package_contains_csv_and_contract_pdf(self):
        contract = self._create_contract(issue_date="2026-05-01")
        settle_response = self.client.post(
            f"/contracts/{contract['id']}/settle",
            data={"payment_date": "2026-05-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)

        response = self.client.get("/accounting/package.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")

        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            names = archive.namelist()
            self.assertIn("ewidencja_ksiegowa.csv", names)
            self.assertIn("umowy/umowa_BUS_2026_0001.pdf", names)
            csv_data = archive.read("ewidencja_ksiegowa.csv").decode("utf-8-sig")
            self.assertIn(contract["contract_number"], csv_data)

    def test_sold_contract_enters_accounting_register(self):
        contract = self._create_contract(issue_date="2026-01-01", term_days="1")

        response = self.client.post(
            f"/contracts/{contract['id']}/realize",
            data={
                "realization_date": "2026-02-01",
                "sale_amount": "3000,00",
                "realization_note": "sprzedaż bezpośrednia",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        detail_page = response.get_data(as_text=True)
        self.assertIn("Kwota sprzedaży", detail_page)
        self.assertIn("3 000,00 zł", detail_page)

        with self.app.app_context():
            sold = get_db().execute(
                """
                SELECT status, sale_amount_cents, realization_due_cents, surplus_return_cents
                FROM contracts
                WHERE id = ?
                """,
                (contract["id"],),
            ).fetchone()
            self.assertEqual(sold["status"], "sold")
            self.assertEqual(sold["sale_amount_cents"], 300_000)
            self.assertEqual(sold["realization_due_cents"], 264_000)
            self.assertEqual(sold["surplus_return_cents"], 28_800)

        csv_response = self.client.get("/accounting/export.csv")
        self.assertEqual(csv_response.status_code, 200)
        csv_data = csv_response.get_data(as_text=True)
        self.assertIn(contract["contract_number"], csv_data)
        self.assertIn("sprzedaż zabezpieczenia", csv_data)
        self.assertIn("288,00 zł", csv_data)

        account_response = self.client.post(
            f"/contracts/{contract['id']}/account",
            data={"accounting_note": "wysłano sprzedaż"},
            follow_redirects=True,
        )
        self.assertEqual(account_response.status_code, 200)
        with self.app.app_context():
            accounted = get_db().execute(
                "SELECT status, accounting_note FROM contracts WHERE id = ?",
                (contract["id"],),
            ).fetchone()
            self.assertEqual(accounted["status"], "accounted")
            self.assertEqual(accounted["accounting_note"], "wysłano sprzedaż")


if __name__ == "__main__":
    unittest.main()
