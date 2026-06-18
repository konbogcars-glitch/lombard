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
        self._login("admin", "admin123")

    def tearDown(self):
        self.tempdir.cleanup()

    def _login(self, username, password):
        response = self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return response

    def _logout(self):
        response = self.client.post("/logout", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response

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
                "document_number": f"{pesel}ABC",
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

    def test_login_is_required_for_application_routes(self):
        self._logout()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        login_page = self.client.get(response.headers["Location"])
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("Logowanie do programu lombardu", login_page.get_data(as_text=True))

    def test_branch_user_is_limited_to_own_contracts(self):
        bus_client = self._create_client(first_name="Anna", pesel="80010112345")
        chm_client = self._create_client(first_name="Ewa", pesel="81010112345")
        bus_contract = self._create_contract(branch_code="BUS", client_id=bus_client)
        chm_contract = self._create_contract(branch_code="CHM", client_id=chm_client)

        self._logout()
        self._login("busko", "lombard123")

        bus_page = self.client.get(f"/contracts/{bus_contract['id']}")
        self.assertEqual(bus_page.status_code, 200)
        self.assertIn(bus_contract["contract_number"], bus_page.get_data(as_text=True))

        chm_page = self.client.get(f"/contracts/{chm_contract['id']}")
        self.assertEqual(chm_page.status_code, 403)

        archive_page = self.client.get("/archive?branch_id=all")
        self.assertEqual(archive_page.status_code, 200)
        archive = archive_page.get_data(as_text=True)
        self.assertIn(bus_contract["contract_number"], archive)
        self.assertNotIn(chm_contract["contract_number"], archive)

        with self.app.app_context():
            contract_count = get_db().execute("SELECT COUNT(*) AS count FROM contracts").fetchone()["count"]

        tampered_response = self.client.post(
            "/contracts/new",
            data={
                "branch_id": self._branch_id("CHM"),
                "client_id": bus_client,
                "issue_date": "2026-05-01",
                "loan_amount": "1000,00",
                "commission_amount": "100,00",
                "commission_rate": "10",
                "term_days": "7",
                "collateral_type": "rzecz ruchoma",
                "collateral_description": "Próba cudzej lokalizacji",
                "collateral_value": "1500,00",
                "valuation_basis": "test",
            },
            follow_redirects=False,
        )
        self.assertEqual(tampered_response.status_code, 403)
        with self.app.app_context():
            unchanged_count = get_db().execute("SELECT COUNT(*) AS count FROM contracts").fetchone()["count"]
            self.assertEqual(unchanged_count, contract_count)

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

    def test_duplicate_client_submission_reuses_existing_card(self):
        existing_client_id = self._create_client()

        response = self.client.post(
            "/clients",
            data={
                "first_name": "Adam",
                "last_name": "Zdublowany",
                "pesel": "90010112345",
                "document_type": "Dowód Osobisty",
                "document_number": "999ABC",
                "phone": "111222333",
                "email": "adam@example.com",
                "street_address": "ul. Inna 2",
                "postal_code": "28-100",
                "city": "Busko-Zdrój",
                "notes": "",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("jest już w kartotece", page)
        self.assertIn("Jan Kowalski", page)
        self.assertNotIn("Adam Zdublowany", page)

        with self.app.app_context():
            db = get_db()
            client_count = db.execute("SELECT COUNT(*) AS count FROM clients").fetchone()["count"]
            existing = db.execute(
                "SELECT first_name, last_name FROM clients WHERE id = ?",
                (existing_client_id,),
            ).fetchone()
            self.assertEqual(client_count, 1)
            self.assertEqual(existing["first_name"], "Jan")
            self.assertEqual(existing["last_name"], "Kowalski")

    def test_client_edit_cannot_take_existing_identity(self):
        first_client_id = self._create_client(
            first_name="Jan",
            last_name="Kowalski",
            pesel="90010112345",
        )
        second_client_id = self._create_client(
            first_name="Ewa",
            last_name="Nowak",
            pesel="91010112345",
        )

        response = self.client.post(
            f"/clients/{second_client_id}/edit",
            data={
                "first_name": "Ewa",
                "last_name": "Nowak",
                "pesel": "90010112345",
                "document_type": "Dowód Osobisty",
                "document_number": "XYZ987654",
                "phone": "700800900",
                "email": "ewa.nowak@example.com",
                "street_address": "ul. Poprawiona 2",
                "postal_code": "28-400",
                "city": "Pińczów",
                "notes": "Próba duplikatu.",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Nie zapisano zmian", page)
        self.assertIn("Jan Kowalski", page)

        with self.app.app_context():
            rows = get_db().execute(
                """
                SELECT id, pesel
                FROM clients
                WHERE id IN (?, ?)
                ORDER BY id
                """,
                (first_client_id, second_client_id),
            ).fetchall()
            by_id = {row["id"]: row["pesel"] for row in rows}
            self.assertEqual(by_id[first_client_id], "90010112345")
            self.assertEqual(by_id[second_client_id], "91010112345")

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

    def test_active_contract_can_be_edited_and_recalculated(self):
        contract = self._create_contract(issue_date="2099-01-01")

        edit_page = self.client.get(f"/contracts/{contract['id']}/edit")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn("Korekta umowy", edit_page.get_data(as_text=True))

        response = self.client.post(
            f"/contracts/{contract['id']}/edit",
            data={
                "loan_amount": "3000,00",
                "commission_amount": "",
                "commission_rate": "5",
                "term_days": "10",
                "collateral_type": "sprzęt elektroniczny",
                "collateral_description": "Laptop Dell XPS po korekcie opisu",
                "collateral_value": "4200,00",
                "valuation_basis": "korekta po sprawdzeniu numeru seryjnego",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Zapisano korektę umowy", page)
        self.assertIn("Laptop Dell XPS po korekcie opisu", page)
        self.assertIn("3 150,00 zł", page)

        with self.app.app_context():
            updated = get_db().execute(
                """
                SELECT status, term_days, due_date, loan_amount_cents, commission_amount_cents,
                       total_repayment_cents, daily_increase_cents, max_additional_fee_cents,
                       collateral_type, collateral_value_cents, valuation_basis
                FROM contracts
                WHERE id = ?
                """,
                (contract["id"],),
            ).fetchone()
            self.assertEqual(updated["status"], "active")
            self.assertEqual(updated["term_days"], 10)
            self.assertEqual(updated["due_date"], "2099-01-10")
            self.assertEqual(updated["loan_amount_cents"], 300_000)
            self.assertEqual(updated["commission_amount_cents"], 15_000)
            self.assertEqual(updated["total_repayment_cents"], 315_000)
            self.assertEqual(updated["daily_increase_cents"], 3_150)
            self.assertEqual(updated["max_additional_fee_cents"], 63_000)
            self.assertEqual(updated["collateral_type"], "sprzęt elektroniczny")
            self.assertEqual(updated["collateral_value_cents"], 420_000)
            self.assertEqual(updated["valuation_basis"], "korekta po sprawdzeniu numeru seryjnego")

    def test_settled_contract_cannot_be_edited(self):
        contract = self._create_contract(issue_date="2099-02-01")

        settle_response = self.client.post(
            f"/contracts/{contract['id']}/settle",
            data={"payment_date": "2099-02-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)
        self.assertNotIn("Edytuj umowę", settle_response.get_data(as_text=True))

        edit_response = self.client.get(
            f"/contracts/{contract['id']}/edit",
            follow_redirects=True,
        )
        self.assertEqual(edit_response.status_code, 200)
        self.assertIn("Można poprawiać tylko umowy aktywne", edit_response.get_data(as_text=True))

        response = self.client.post(
            f"/contracts/{contract['id']}/edit",
            data={
                "loan_amount": "9999,99",
                "commission_amount": "1,00",
                "commission_rate": "1",
                "term_days": "30",
                "collateral_type": "nie powinno się zapisać",
                "collateral_description": "blokowana korekta",
                "collateral_value": "9999,99",
                "valuation_basis": "blokada",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            unchanged = get_db().execute(
                """
                SELECT status, term_days, loan_amount_cents, commission_amount_cents,
                       collateral_description
                FROM contracts
                WHERE id = ?
                """,
                (contract["id"],),
            ).fetchone()
            self.assertEqual(unchanged["status"], "settled")
            self.assertEqual(unchanged["term_days"], 7)
            self.assertEqual(unchanged["loan_amount_cents"], 200_000)
            self.assertEqual(unchanged["commission_amount_cents"], 20_000)
            self.assertEqual(unchanged["collateral_description"], "Telefon testowy IMEI 123")

    def test_contract_form_can_create_client_and_photos_in_one_step(self):
        response = self.client.post(
            "/contracts/new",
            data={
                "branch_id": self._branch_id("PIN"),
                "client_mode": "new",
                "new_client_first_name": "Maria",
                "new_client_last_name": "Wisniewska",
                "new_client_pesel": "92020212345",
                "new_client_document_type": "Dowód Osobisty",
                "new_client_document_number": "CDE123456",
                "new_client_phone": "501502503",
                "new_client_email": "maria@example.com",
                "new_client_street_address": "ul. Rynek 2",
                "new_client_postal_code": "28-400",
                "new_client_city": "Pińczów",
                "new_client_notes": "Stała klientka punktu Pińczów.",
                "issue_date": "2026-04-01",
                "loan_amount": "1500,00",
                "commission_amount": "",
                "commission_rate": "10",
                "term_days": "14",
                "collateral_type": "biżuteria",
                "collateral_description": "Pierścionek złoty próba 585",
                "collateral_value": "2200,00",
                "valuation_basis": "oględziny i waga przedmiotu",
                "photo_caption": "Zdjęcie przy przyjęciu",
                "photos": [(io.BytesIO(b"inline image bytes"), "pierscionek.jpg")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Maria Wisniewska", page)
        self.assertIn("Pińczów", page)

        with self.app.app_context():
            db = get_db()
            client = db.execute(
                "SELECT * FROM clients WHERE pesel = ?",
                ("92020212345",),
            ).fetchone()
            self.assertIsNotNone(client)
            contract = db.execute(
                """
                SELECT contracts.*, branches.code AS branch_code
                FROM contracts
                JOIN branches ON branches.id = contracts.branch_id
                WHERE contracts.client_id = ?
                """,
                (client["id"],),
            ).fetchone()
            self.assertEqual(contract["contract_number"], "PIN/2026/0001")
            self.assertEqual(contract["total_repayment_cents"], 165_000)
            photo = db.execute(
                "SELECT * FROM contract_photos WHERE contract_id = ?",
                (contract["id"],),
            ).fetchone()
            self.assertIsNotNone(photo)
            self.assertEqual(photo["caption"], "Zdjęcie przy przyjęciu")

        file_response = self.client.get(
            f"/uploads/{contract['id']}/{photo['stored_filename']}"
        )
        try:
            self.assertEqual(file_response.status_code, 200)
            self.assertEqual(file_response.get_data(), b"inline image bytes")
        finally:
            file_response.close()

    def test_contract_form_reuses_client_when_new_client_identity_exists(self):
        existing_client_id = self._create_client()

        response = self.client.post(
            "/contracts/new",
            data={
                "branch_id": self._branch_id("BUS"),
                "client_mode": "new",
                "new_client_first_name": "Jan",
                "new_client_last_name": "Powtórzony",
                "new_client_pesel": "90010112345",
                "new_client_document_type": "Dowód Osobisty",
                "new_client_document_number": "DUP123",
                "new_client_phone": "501502503",
                "new_client_email": "jan.duplicate@example.com",
                "new_client_street_address": "ul. Duplikat 9",
                "new_client_postal_code": "28-100",
                "new_client_city": "Busko-Zdrój",
                "new_client_notes": "",
                "issue_date": "2026-04-01",
                "loan_amount": "1500,00",
                "commission_amount": "",
                "commission_rate": "10",
                "term_days": "14",
                "collateral_type": "biżuteria",
                "collateral_description": "Łańcuszek złoty próba 585",
                "collateral_value": "2200,00",
                "valuation_basis": "oględziny i waga przedmiotu",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Wykryto istniejącą kartotekę klienta Jan Kowalski", page)
        self.assertIn("Umowa BUS/2026/0001", page)

        with self.app.app_context():
            db = get_db()
            client_count = db.execute("SELECT COUNT(*) AS count FROM clients").fetchone()["count"]
            contract = db.execute("SELECT client_id FROM contracts").fetchone()
            self.assertEqual(client_count, 1)
            self.assertEqual(contract["client_id"], existing_client_id)

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

    def test_accounting_period_filter_limits_exports_package_and_bulk_accounting(self):
        january_client = self._create_client(first_name="Anna", pesel="80010112345")
        february_client = self._create_client(first_name="Ewa", pesel="81010112345")
        january_contract = self._create_contract(
            client_id=january_client,
            issue_date="2026-01-01",
        )
        february_contract = self._create_contract(
            client_id=february_client,
            issue_date="2026-02-01",
        )

        response = self.client.post(
            f"/contracts/{january_contract['id']}/settle",
            data={"payment_date": "2026-01-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/contracts/{february_contract['id']}/settle",
            data={"payment_date": "2026-02-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        filter_query = "date_from=2026-02-01&date_to=2026-02-28"
        page_response = self.client.get(f"/accounting?{filter_query}")
        self.assertEqual(page_response.status_code, 200)
        page = page_response.get_data(as_text=True)
        self.assertIn(february_contract["contract_number"], page)
        self.assertNotIn(january_contract["contract_number"], page)
        self.assertIn('value="2026-02-01"', page)
        self.assertIn('value="2026-02-28"', page)

        csv_response = self.client.get(f"/accounting/export.csv?{filter_query}")
        self.assertEqual(csv_response.status_code, 200)
        csv_data = csv_response.get_data(as_text=True)
        self.assertIn(february_contract["contract_number"], csv_data)
        self.assertNotIn(january_contract["contract_number"], csv_data)

        package_response = self.client.get(f"/accounting/package.zip?{filter_query}")
        self.assertEqual(package_response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(package_response.get_data())) as archive:
            names = archive.namelist()
            self.assertIn("umowy/umowa_BUS_2026_0002.pdf", names)
            self.assertNotIn("umowy/umowa_BUS_2026_0001.pdf", names)

        bulk_response = self.client.post(
            "/accounting/bulk-account",
            data={
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
                "accounting_note": "wysłano za luty",
            },
            follow_redirects=True,
        )
        self.assertEqual(bulk_response.status_code, 200)

        with self.app.app_context():
            rows = get_db().execute(
                """
                SELECT contract_number, status, accounting_note
                FROM contracts
                WHERE id IN (?, ?)
                ORDER BY contract_number
                """,
                (january_contract["id"], february_contract["id"]),
            ).fetchall()

        by_number = {row["contract_number"]: row for row in rows}
        self.assertEqual(by_number[january_contract["contract_number"]]["status"], "settled")
        self.assertEqual(by_number[february_contract["contract_number"]]["status"], "accounted")
        self.assertEqual(
            by_number[february_contract["contract_number"]]["accounting_note"],
            "wysłano za luty",
        )

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

    def test_bulk_accounting_creates_reusable_accounting_batch_package(self):
        first_client = self._create_client(first_name="Anna", pesel="83010112345")
        second_client = self._create_client(first_name="Ewa", pesel="84010112345")
        first_contract = self._create_contract(
            client_id=first_client,
            issue_date="2026-08-01",
        )
        second_contract = self._create_contract(
            client_id=second_client,
            issue_date="2026-08-02",
        )

        for contract in (first_contract, second_contract):
            response = self.client.post(
                f"/contracts/{contract['id']}/settle",
                data={"payment_date": "2026-08-07", "paid_amount": ""},
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/accounting/bulk-account",
            data={"accounting_note": "wyslano zbiorczo za sierpien"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Archiwum wysyłek do księgowej", page)
        self.assertIn("#1", page)

        with self.app.app_context():
            db = get_db()
            rows = db.execute(
                """
                SELECT contract_number, status, accounting_note, accounting_batch_id
                FROM contracts
                WHERE id IN (?, ?)
                ORDER BY contract_number
                """,
                (first_contract["id"], second_contract["id"]),
            ).fetchall()
            batch_ids = {row["accounting_batch_id"] for row in rows}
            self.assertEqual(len(batch_ids), 1)
            batch_id = batch_ids.pop()
            batch = db.execute(
                "SELECT contracts_count, note FROM accounting_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
            self.assertEqual(batch["contracts_count"], 2)
            self.assertEqual(batch["note"], "wyslano zbiorczo za sierpien")
            for row in rows:
                self.assertEqual(row["status"], "accounted")
                self.assertEqual(row["accounting_note"], "wyslano zbiorczo za sierpien")

        default_csv = self.client.get("/accounting/export.csv").get_data(as_text=True)
        self.assertNotIn(first_contract["contract_number"], default_csv)

        batch_csv = self.client.get(f"/accounting/batches/{batch_id}/export.csv")
        self.assertEqual(batch_csv.status_code, 200)
        batch_csv_data = batch_csv.get_data(as_text=True)
        self.assertIn(first_contract["contract_number"], batch_csv_data)
        self.assertIn(second_contract["contract_number"], batch_csv_data)
        self.assertIn("wyslano zbiorczo za sierpien", batch_csv_data)

        package_response = self.client.get(f"/accounting/batches/{batch_id}/package.zip")
        self.assertEqual(package_response.status_code, 200)
        original_package = package_response.get_data()
        with zipfile.ZipFile(io.BytesIO(package_response.get_data())) as archive:
            names = archive.namelist()
            self.assertIn("ewidencja_ksiegowa.csv", names)
            self.assertIn("umowy/umowa_BUS_2026_0001.pdf", names)
            self.assertIn("umowy/umowa_BUS_2026_0002.pdf", names)

        edit_response = self.client.post(
            f"/clients/{first_client}/edit",
            data={
                "first_name": "Anna",
                "last_name": "PoZmianie",
                "pesel": "83010112345",
                "document_type": "Dowód Osobisty",
                "document_number": "83010112345ABC",
                "phone": "700800900",
                "email": "anna.changed@example.com",
                "street_address": "ul. Zmieniona 2",
                "postal_code": "28-400",
                "city": "Pińczów",
                "notes": "Aktualizacja po wysyłce do księgowej.",
            },
            follow_redirects=True,
        )
        self.assertEqual(edit_response.status_code, 200)

        repeated_batch_csv = self.client.get(f"/accounting/batches/{batch_id}/export.csv")
        self.assertEqual(repeated_batch_csv.status_code, 200)
        repeated_batch_csv_data = repeated_batch_csv.get_data(as_text=True)
        self.assertEqual(repeated_batch_csv_data, batch_csv_data)
        self.assertNotIn("PoZmianie", repeated_batch_csv_data)

        repeated_package = self.client.get(f"/accounting/batches/{batch_id}/package.zip")
        self.assertEqual(repeated_package.status_code, 200)
        self.assertEqual(repeated_package.get_data(), original_package)

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

    def test_branch_overview_shows_shared_workload_and_shortcuts(self):
        bus_contract = self._create_contract(branch_code="BUS", issue_date="2026-12-01")
        chm_client = self._create_client(first_name="Ewa", pesel="82010112345")
        chm_contract = self._create_contract(
            branch_code="CHM",
            client_id=chm_client,
            issue_date="2025-01-01",
            term_days="1",
        )
        pin_client = self._create_client(first_name="Maria", pesel="83010112345")
        pin_contract = self._create_contract(
            branch_code="PIN",
            client_id=pin_client,
            issue_date="2026-03-01",
        )

        settle_response = self.client.post(
            f"/contracts/{pin_contract['id']}/settle",
            data={"payment_date": "2026-03-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)

        response = self.client.get("/branches")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        self.assertIn("Punkty lombardu", page)
        self.assertIn("Busko-Zdrój", page)
        self.assertIn("Chmielnik", page)
        self.assertIn("Pińczów", page)
        self.assertIn("Klienci w kartotece", page)
        self.assertIn("Do księgowej", page)

        bus_branch_id = self._branch_id("BUS")
        chm_branch_id = self._branch_id("CHM")
        pin_branch_id = self._branch_id("PIN")
        self.assertIn(f'href="/archive?branch_id={bus_branch_id}"', page)
        self.assertIn(f'href="/accounting?branch_id={pin_branch_id}"', page)
        self.assertIn(f'href="/?branch_id={chm_branch_id}"', page)
        self.assertIn(f'href="/contracts/new?branch_id={pin_branch_id}"', page)

        with self.app.app_context():
            db = get_db()
            refreshed_chm = db.execute(
                "SELECT status FROM contracts WHERE id = ?",
                (chm_contract["id"],),
            ).fetchone()
            pending_accounting = db.execute(
                """
                SELECT COUNT(*) AS count
                FROM contracts
                WHERE branch_id = ?
                  AND status IN ('settled', 'sold')
                  AND accountant_sent_at IS NULL
                """,
                (pin_branch_id,),
            ).fetchone()
            self.assertEqual(refreshed_chm["status"], "expired")
            self.assertEqual(pending_accounting["count"], 1)
            self.assertIsNotNone(bus_contract)

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

    def test_accountant_contact_prepares_email_draft(self):
        contract = self._create_contract(issue_date="2026-06-01")
        settle_response = self.client.post(
            f"/contracts/{contract['id']}/settle",
            data={"payment_date": "2026-06-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)

        branch_id = self._branch_id("BUS")
        response = self.client.post(
            "/accounting/settings",
            data={
                "accountant_email": "ksiegowa@example.com",
                "accountant_name": "Pani Anno",
                "next": f"/accounting?branch_id={branch_id}",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('value="ksiegowa@example.com"', page)
        self.assertIn("mailto:ksiegowa@example.com", page)
        self.assertIn("Ewidencja+um%C3%B3w+lombardowych", page)
        self.assertIn("Pani+Anno", page)
        self.assertIn("Paczka ZIP", page)

        with self.app.app_context():
            settings = {
                row["key"]: row["value"]
                for row in get_db().execute("SELECT key, value FROM settings").fetchall()
            }
            self.assertEqual(settings["accountant_email"], "ksiegowa@example.com")
            self.assertEqual(settings["accountant_name"], "Pani Anno")

    def test_contract_template_can_be_saved_and_used_for_pdf_package(self):
        page_response = self.client.get("/contract-template")
        self.assertEqual(page_response.status_code, 200)
        self.assertIn("{client_name}", page_response.get_data(as_text=True))

        contract = self._create_contract(issue_date="2026-07-01")
        response = self.client.post(
            "/contract-template",
            data={
                "contract_template": (
                    "Własny wzór umowy {contract_number}\n"
                    "Klient: {client_name}\n"
                    "Do spłaty: {total_repayment}"
                )
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Własny wzór umowy {contract_number}", page)

        pdf_response = self.client.get(f"/contracts/{contract['id']}/pdf")
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.mimetype, "application/pdf")

        settle_response = self.client.post(
            f"/contracts/{contract['id']}/settle",
            data={"payment_date": "2026-07-07", "paid_amount": ""},
            follow_redirects=True,
        )
        self.assertEqual(settle_response.status_code, 200)
        package_response = self.client.get("/accounting/package.zip")
        self.assertEqual(package_response.status_code, 200)

        with self.app.app_context():
            template = get_db().execute(
                "SELECT value FROM settings WHERE key = 'contract_template_text'",
            ).fetchone()
            self.assertIn("Własny wzór", template["value"])

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
                SELECT status, sale_amount_cents, realization_due_cents, surplus_return_cents, shortfall_cents
                FROM contracts
                WHERE id = ?
                """,
                (contract["id"],),
            ).fetchone()
            self.assertEqual(sold["status"], "sold")
            self.assertEqual(sold["sale_amount_cents"], 300_000)
            self.assertEqual(sold["realization_due_cents"], 264_000)
            self.assertEqual(sold["surplus_return_cents"], 28_800)
            self.assertEqual(sold["shortfall_cents"], 0)

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

    def test_sale_shortfall_is_saved_and_exported_for_accounting(self):
        contract = self._create_contract(issue_date="2026-01-01", term_days="1")

        response = self.client.post(
            f"/contracts/{contract['id']}/realize",
            data={
                "realization_date": "2026-02-01",
                "sale_amount": "2000,00",
                "realization_note": "sprzedaż poniżej należności",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        detail_page = response.get_data(as_text=True)
        self.assertIn("Niedobór po sprzedaży", detail_page)
        self.assertIn("640,00 zł", detail_page)

        with self.app.app_context():
            sold = get_db().execute(
                """
                SELECT status, realization_due_cents, surplus_return_cents, shortfall_cents
                FROM contracts
                WHERE id = ?
                """,
                (contract["id"],),
            ).fetchone()
            self.assertEqual(sold["status"], "sold")
            self.assertEqual(sold["realization_due_cents"], 264_000)
            self.assertEqual(sold["surplus_return_cents"], 0)
            self.assertEqual(sold["shortfall_cents"], 64_000)

        csv_response = self.client.get("/accounting/export.csv")
        self.assertEqual(csv_response.status_code, 200)
        csv_data = csv_response.get_data(as_text=True)
        self.assertIn("Niedobór po sprzedaży", csv_data)
        self.assertIn("640,00 zł", csv_data)


if __name__ == "__main__":
    unittest.main()
