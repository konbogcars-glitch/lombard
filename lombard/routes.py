from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from .calculations import (
    calculate_loan,
    cents_to_money,
    format_money,
    money_to_cents,
    repayment_amount_on,
)
from .database import get_db, next_contract_number, query_all, query_one
from .pdf import build_contract_pdf


bp = Blueprint("lombard", __name__)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def _today() -> date:
    return date.today()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _contract_or_404(contract_id: int) -> dict:
    contract = query_one(
        """
        SELECT
            contracts.*,
            clients.first_name,
            clients.last_name,
            clients.pesel,
            clients.document_type,
            clients.document_number,
            clients.phone,
            clients.email,
            clients.street_address,
            clients.postal_code,
            clients.city AS client_city,
            branches.code AS branch_code,
            branches.name AS branch_name,
            branches.city AS branch_city,
            branches.address AS branch_address
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE contracts.id = ?
        """,
        (contract_id,),
    )
    if contract is None:
        abort(404)
    return contract


def _photo_rows(contract_id: int) -> list[dict]:
    return query_all(
        "SELECT * FROM contract_photos WHERE contract_id = ? ORDER BY uploaded_at DESC",
        (contract_id,),
    )


def _refresh_overdue_contracts(today: date | None = None) -> None:
    current_date = today or _today()
    db = get_db()
    db.execute(
        """
        UPDATE contracts
        SET status = 'expired',
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'active' AND due_date < ?
        """,
        (current_date.isoformat(),),
    )
    db.commit()


@bp.route("/")
def dashboard() -> str:
    _refresh_overdue_contracts()
    stats = query_one(
        """
        SELECT
            COUNT(*) AS contracts_total,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN status IN ('settled', 'accounted') THEN 1 ELSE 0 END) AS settled_count,
            SUM(CASE WHEN status = 'settled' AND accountant_sent_at IS NULL THEN 1 ELSE 0 END) AS accounting_pending
        FROM contracts
        """
    )
    upcoming = query_all(
        """
        SELECT contracts.*, clients.first_name, clients.last_name, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE contracts.status = 'active'
        ORDER BY contracts.due_date ASC
        LIMIT 10
        """
    )
    return render_template("dashboard.html", stats=stats, upcoming=upcoming, today=_today())


@bp.route("/clients", methods=["GET", "POST"])
def clients() -> str | Response:
    db = get_db()
    if request.method == "POST":
        db.execute(
            """
            INSERT INTO clients(
                first_name, last_name, pesel, document_type, document_number,
                phone, email, street_address, postal_code, city, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form["first_name"].strip(),
                request.form["last_name"].strip(),
                request.form.get("pesel", "").strip(),
                request.form.get("document_type", "Dowód Osobisty").strip(),
                request.form.get("document_number", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("email", "").strip(),
                request.form["street_address"].strip(),
                request.form.get("postal_code", "").strip(),
                request.form["city"].strip(),
                request.form.get("notes", "").strip(),
            ),
        )
        db.commit()
        flash("Dodano klienta do kartoteki.", "success")
        return redirect(url_for("lombard.clients"))

    search = request.args.get("q", "").strip()
    params: tuple = ()
    where = ""
    if search:
        like = f"%{search}%"
        where = """
        WHERE first_name LIKE ? OR last_name LIKE ? OR pesel LIKE ?
           OR document_number LIKE ? OR phone LIKE ?
        """
        params = (like, like, like, like, like)

    rows = query_all(
        f"""
        SELECT clients.*,
               COUNT(contracts.id) AS contracts_count
        FROM clients
        LEFT JOIN contracts ON contracts.client_id = clients.id
        {where}
        GROUP BY clients.id
        ORDER BY clients.last_name, clients.first_name
        """,
        params,
    )
    return render_template("clients.html", clients=rows, search=search)


@bp.route("/clients/<int:client_id>")
def client_detail(client_id: int) -> str:
    client = query_one("SELECT * FROM clients WHERE id = ?", (client_id,))
    if client is None:
        abort(404)
    contracts = query_all(
        """
        SELECT contracts.*, branches.city AS branch_city
        FROM contracts
        JOIN branches ON branches.id = contracts.branch_id
        WHERE client_id = ?
        ORDER BY issue_date DESC, id DESC
        """,
        (client_id,),
    )
    return render_template("client_detail.html", client=client, contracts=contracts)


@bp.route("/contracts/new", methods=["GET", "POST"])
def contract_new() -> str | Response:
    db = get_db()
    clients = query_all("SELECT * FROM clients ORDER BY last_name, first_name")
    branches = query_all("SELECT * FROM branches ORDER BY id")
    selected_client_id = request.args.get("client_id", type=int)

    if request.method == "POST":
        issue_date = _parse_date(request.form["issue_date"])
        branch_id = int(request.form["branch_id"])
        client_id = int(request.form["client_id"])
        loan_amount_cents = money_to_cents(request.form["loan_amount"])
        commission_raw = request.form.get("commission_amount", "").strip()
        commission_amount_cents = money_to_cents(commission_raw) if commission_raw else None
        term_days = int(request.form["term_days"])
        calculation = calculate_loan(
            issue_date=issue_date,
            loan_amount_cents=loan_amount_cents,
            commission_amount_cents=commission_amount_cents,
            commission_rate_percent=Decimal(request.form.get("commission_rate", "10") or "10"),
            term_days=term_days,
        )
        contract_number = next_contract_number(branch_id, issue_date.year)

        cursor = db.execute(
            """
            INSERT INTO contracts(
                contract_number, branch_id, client_id, issue_date, term_days, due_date,
                additional_period_end, loan_amount_cents, commission_amount_cents,
                total_repayment_cents, daily_increase_cents, max_additional_fee_cents,
                collateral_type, collateral_description, collateral_value_cents,
                valuation_basis, sale_mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_number,
                branch_id,
                client_id,
                issue_date.isoformat(),
                term_days,
                calculation.due_date.isoformat(),
                calculation.additional_period_end.isoformat(),
                calculation.loan_amount_cents,
                calculation.commission_amount_cents,
                calculation.total_repayment_cents,
                calculation.daily_increase_cents,
                calculation.max_additional_fee_cents,
                request.form["collateral_type"].strip(),
                request.form["collateral_description"].strip(),
                money_to_cents(request.form["collateral_value"]),
                request.form.get("valuation_basis", "").strip(),
                calculation.sale_mode,
            ),
        )
        db.commit()
        flash("Utworzono umowę. Możesz teraz dodać zdjęcia zabezpieczenia.", "success")
        return redirect(url_for("lombard.contract_detail", contract_id=cursor.lastrowid))

    return render_template(
        "contract_form.html",
        clients=clients,
        branches=branches,
        selected_client_id=selected_client_id,
        today=_today(),
    )


@bp.route("/contracts/<int:contract_id>")
def contract_detail(contract_id: int) -> str:
    _refresh_overdue_contracts()
    contract = _contract_or_404(contract_id)
    photos = _photo_rows(contract_id)
    expected_payment = repayment_amount_on(
        base_total_cents=contract["total_repayment_cents"],
        due_date=_parse_date(contract["due_date"]),
        payment_date=_today(),
    )
    return render_template(
        "contract_detail.html",
        contract=contract,
        photos=photos,
        expected_payment=expected_payment,
        today=_today(),
    )


@bp.route("/contracts/<int:contract_id>/photos", methods=["POST"])
def contract_photos(contract_id: int) -> Response:
    _contract_or_404(contract_id)
    uploaded = request.files.getlist("photos")
    caption = request.form.get("caption", "").strip()
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / str(contract_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    db = get_db()
    saved = 0

    for file in uploaded:
        if not file or not file.filename:
            continue
        extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if extension not in ALLOWED_EXTENSIONS:
            flash(f"Pominięto plik {file.filename}: dozwolone są JPG, PNG i WEBP.", "warning")
            continue
        safe_name = secure_filename(file.filename)
        stored_name = f"{uuid.uuid4().hex}_{safe_name}"
        file.save(upload_dir / stored_name)
        db.execute(
            """
            INSERT INTO contract_photos(contract_id, stored_filename, original_filename, caption)
            VALUES (?, ?, ?, ?)
            """,
            (contract_id, stored_name, file.filename, caption),
        )
        saved += 1

    db.commit()
    if saved:
        flash(f"Dodano zdjęcia: {saved}.", "success")
    return redirect(url_for("lombard.contract_detail", contract_id=contract_id))


@bp.route("/uploads/<int:contract_id>/<path:filename>")
def uploaded_photo(contract_id: int, filename: str) -> Response:
    directory = Path(current_app.config["UPLOAD_FOLDER"]) / str(contract_id)
    return send_from_directory(directory, filename)


@bp.route("/contracts/<int:contract_id>/settle", methods=["POST"])
def contract_settle(contract_id: int) -> Response:
    _refresh_overdue_contracts()
    contract = _contract_or_404(contract_id)
    if contract["status"] not in {"active", "expired"}:
        flash("Tę umowę już rozliczono albo przekazano do księgowości.", "warning")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    payment_date = _parse_date(request.form["payment_date"])
    paid_amount_raw = request.form.get("paid_amount", "").strip()
    paid_amount = (
        money_to_cents(paid_amount_raw)
        if paid_amount_raw
        else repayment_amount_on(
            base_total_cents=contract["total_repayment_cents"],
            due_date=_parse_date(contract["due_date"]),
            payment_date=payment_date,
        )
    )
    get_db().execute(
        """
        UPDATE contracts
        SET status = 'settled',
            payment_date = ?,
            paid_amount_cents = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (payment_date.isoformat(), paid_amount, contract_id),
    )
    get_db().commit()
    flash("Umowa została oznaczona jako spłacona i czeka na księgowanie.", "success")
    return redirect(url_for("lombard.contract_detail", contract_id=contract_id))


@bp.route("/contracts/<int:contract_id>/account", methods=["POST"])
def contract_account(contract_id: int) -> Response:
    contract = _contract_or_404(contract_id)
    if contract["status"] == "accounted":
        flash("Ta umowa jest już oznaczona jako wysłana do księgowej.", "warning")
        return redirect(url_for("lombard.accounting"))
    if contract["status"] != "settled":
        flash("Do księgowości można przekazać tylko spłaconą umowę.", "warning")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    now = datetime.now().isoformat(timespec="seconds")
    get_db().execute(
        """
        UPDATE contracts
        SET status = 'accounted',
            accounted_at = COALESCE(accounted_at, ?),
            accountant_sent_at = ?,
            accounting_note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (now, now, request.form.get("accounting_note", "").strip(), contract_id),
    )
    get_db().commit()
    flash("Umowa została oznaczona jako zaksięgowana/wysłana do księgowej.", "success")
    return redirect(url_for("lombard.accounting"))


@bp.route("/contracts/<int:contract_id>/pdf")
def contract_pdf(contract_id: int) -> Response:
    contract = _contract_or_404(contract_id)
    photos = _photo_rows(contract_id)
    pdf = build_contract_pdf(contract, photos, Path(current_app.config["UPLOAD_FOLDER"]))
    filename = f"umowa_{contract['contract_number'].replace('/', '_')}.pdf"
    return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@bp.route("/archive")
def archive() -> str:
    _refresh_overdue_contracts()
    status = request.args.get("status", "")
    branch_id = request.args.get("branch_id", type=int)
    q = request.args.get("q", "").strip()
    params: list = []
    clauses: list[str] = []
    if status:
        clauses.append("contracts.status = ?")
        params.append(status)
    if branch_id:
        clauses.append("contracts.branch_id = ?")
        params.append(branch_id)
    if q:
        clauses.append(
            "(contract_number LIKE ? OR clients.first_name LIKE ? OR clients.last_name LIKE ? OR clients.pesel LIKE ?)"
        )
        params.extend([f"%{q}%"] * 4)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    contracts = query_all(
        f"""
        SELECT contracts.*, clients.first_name, clients.last_name, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        {where}
        ORDER BY contracts.issue_date DESC, contracts.id DESC
        """,
        tuple(params),
    )
    branches = query_all("SELECT * FROM branches ORDER BY id")
    return render_template(
        "archive.html",
        contracts=contracts,
        branches=branches,
        selected_status=status,
        selected_branch_id=branch_id,
        q=q,
    )


@bp.route("/accounting")
def accounting() -> str:
    branch_id = request.args.get("branch_id", type=int)
    params: list[int] = []
    branch_clause = ""
    if branch_id:
        branch_clause = "AND contracts.branch_id = ?"
        params.append(branch_id)

    contracts = query_all(
        f"""
        SELECT contracts.*, clients.first_name, clients.last_name, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE contracts.status IN ('settled', 'accounted')
        {branch_clause}
        ORDER BY contracts.payment_date DESC, contracts.id DESC
        """,
        tuple(params),
    )
    branches = query_all("SELECT * FROM branches ORDER BY id")
    return render_template(
        "accounting.html",
        contracts=contracts,
        branches=branches,
        selected_branch_id=branch_id,
    )


@bp.route("/accounting/export.csv")
def accounting_export() -> Response:
    include_sent = request.args.get("include_sent") == "1"
    branch_id = request.args.get("branch_id", type=int)
    clauses = [
        "contracts.status IN ('settled', 'accounted')" if include_sent else "contracts.status = 'settled'"
    ]
    params: list[int] = []
    if branch_id:
        clauses.append("contracts.branch_id = ?")
        params.append(branch_id)
    where = " AND ".join(clauses)
    rows = query_all(
        f"""
        SELECT contracts.*, clients.first_name, clients.last_name, clients.pesel, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE {where}
        ORDER BY contracts.payment_date DESC, contracts.id DESC
        """,
        tuple(params),
    )
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Numer umowy",
            "Punkt",
            "Klient",
            "PESEL",
            "Data umowy",
            "Data spłaty",
            "Kwota pożyczki",
            "Prowizja",
            "Kwota spłacona",
            "Status",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["contract_number"],
                row["branch_city"],
                f"{row['first_name']} {row['last_name']}",
                row["pesel"] or "",
                row["issue_date"],
                row["payment_date"] or "",
                format_money(row["loan_amount_cents"]),
                format_money(row["commission_amount_cents"]),
                format_money(row["paid_amount_cents"]),
                row["status"],
            ]
        )
    csv_data = "\ufeff" + output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ewidencja_ksiegowa.csv"},
    )


@bp.app_template_filter("date_pl")
def date_pl(value: str | date | None) -> str:
    if value is None:
        return ""
    parsed = _parse_date(value) if isinstance(value, str) else value
    return parsed.strftime("%d-%m-%Y")


@bp.app_template_filter("sale_mode_label")
def sale_mode_label(value: str) -> str:
    return "sprzedaż bezpośrednia" if value == "direct" else "aukcja elektroniczna"
