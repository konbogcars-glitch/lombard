from __future__ import annotations

import csv
import io
import uuid
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from .calculations import (
    calculate_sale_realization,
    calculate_loan,
    cents_to_money,
    format_money,
    money_to_cents,
    repayment_amount_on,
)
from .database import get_db, next_contract_number, query_all, query_one
from .pdf import (
    CONTRACT_TEMPLATE_PLACEHOLDERS,
    DEFAULT_CONTRACT_TEMPLATE,
    build_contract_pdf,
)


bp = Blueprint("lombard", __name__)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_TEMPLATE_EXTENSIONS = {"txt", "md"}
ACCOUNTANT_EMAIL_KEY = "accountant_email"
ACCOUNTANT_NAME_KEY = "accountant_name"
CONTRACT_TEMPLATE_KEY = "contract_template_text"
AUTH_EXEMPT_ENDPOINTS = {"lombard.login"}


def _today() -> date:
    return date.today()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _current_user() -> dict | None:
    return getattr(g, "current_user", None)


def _is_admin() -> bool:
    user = _current_user()
    return bool(user and user["role"] == "admin")


def _current_user_branch_id() -> int | None:
    user = _current_user()
    if not user or user["role"] == "admin":
        return None
    return int(user["branch_id"]) if user["branch_id"] is not None else None


def _can_access_branch(branch_id: int | None) -> bool:
    if _is_admin():
        return True
    user_branch_id = _current_user_branch_id()
    return bool(branch_id and user_branch_id == int(branch_id))


def _require_branch_access(branch_id: int | None) -> None:
    if not _can_access_branch(branch_id):
        abort(403)


def _require_admin() -> None:
    if not _is_admin():
        abort(403)


def _branch_options() -> list[dict]:
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        return query_all("SELECT * FROM branches WHERE id = ? ORDER BY id", (user_branch_id,))
    return query_all("SELECT * FROM branches ORDER BY id")


def _get_setting(key: str) -> str:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else ""


def _save_setting(*, db, key: str, value: str) -> None:
    db.execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def _accountant_settings() -> dict:
    return {
        "email": _get_setting(ACCOUNTANT_EMAIL_KEY),
        "name": _get_setting(ACCOUNTANT_NAME_KEY),
    }


def _contract_template_text() -> str:
    return _get_setting(CONTRACT_TEMPLATE_KEY) or DEFAULT_CONTRACT_TEMPLATE


def _uploaded_contract_template_text(uploaded_file) -> str | None:
    if not uploaded_file or not uploaded_file.filename:
        return None

    extension = uploaded_file.filename.rsplit(".", 1)[-1].lower() if "." in uploaded_file.filename else ""
    if extension not in ALLOWED_TEMPLATE_EXTENSIONS:
        flash("Pominięto plik szablonu: dozwolone są pliki TXT albo MD.", "warning")
        return None

    payload = uploaded_file.read()
    if not payload.strip():
        flash("Pominięto pusty plik szablonu umowy.", "warning")
        return None

    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue

    flash("Nie udało się odczytać pliku szablonu. Zapisano treść z pola tekstowego.", "warning")
    return None


def _money_input(cents: int | None) -> str:
    return f"{cents_to_money(cents):.2f}".replace(".", ",")


def _current_branch_id() -> int | None:
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        return user_branch_id
    branch_id = session.get("branch_context_id")
    if branch_id is None:
        return None
    try:
        return int(branch_id)
    except (TypeError, ValueError):
        session.pop("branch_context_id", None)
        return None


def _branch_id_from_args() -> tuple[int | None, bool]:
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        return user_branch_id, False
    raw_value = request.args.get("branch_id")
    if raw_value == "all":
        return None, True
    if raw_value:
        return int(raw_value), False
    return _current_branch_id(), False


def _safe_redirect_target(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("lombard.dashboard")


def _branch_label(branch_id: int | None) -> str:
    if branch_id is None:
        return "wszystkie punkty"
    branch = query_one("SELECT city FROM branches WHERE id = ?", (branch_id,))
    return branch["city"] if branch else "wybrany punkt"


def _normalize_date_filter(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    try:
        _parse_date(normalized)
    except ValueError:
        flash(f"Pominięto nieprawidłową datę filtra: {label}.", "warning")
        return ""
    return normalized


def _date_filter_from_args(name: str, label: str) -> str:
    return _normalize_date_filter(request.args.get(name, ""), label)


def _accounting_period_label(date_from: str = "", date_to: str = "") -> str:
    if date_from and date_to:
        return f"okres od {date_from} do {date_to}"
    if date_from:
        return f"okres od {date_from}"
    if date_to:
        return f"okres do {date_to}"
    return "wszystkie rozliczone okresy"


def _accounting_filter_url_args(
    *,
    branch_id: int | None,
    showing_all_branches: bool,
    date_from: str = "",
    date_to: str = "",
    include_sent: bool = False,
) -> dict:
    args: dict[str, int | str] = {}
    if branch_id:
        args["branch_id"] = branch_id
    elif showing_all_branches:
        args["branch_id"] = "all"
    if date_from:
        args["date_from"] = date_from
    if date_to:
        args["date_to"] = date_to
    if include_sent:
        args["include_sent"] = 1
    return args


def _create_accounting_batch(
    *,
    db,
    branch_id: int | None,
    date_from: str = "",
    date_to: str = "",
    contracts_count: int,
    note: str = "",
) -> int:
    settings = _accountant_settings()
    cursor = db.execute(
        """
        INSERT INTO accounting_batches(
            branch_id, date_from, date_to, contracts_count,
            accountant_email, accountant_name, note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            branch_id,
            date_from,
            date_to,
            contracts_count,
            settings["email"],
            settings["name"],
            note,
        ),
    )
    return int(cursor.lastrowid)


def _store_accounting_batch_snapshot(*, db, batch_id: int) -> None:
    rows = _accounting_rows_for_batch(batch_id)
    csv_snapshot = _accounting_csv(rows)
    package_snapshot = _accounting_package_bytes(rows)
    db.execute(
        """
        UPDATE accounting_batches
        SET csv_snapshot = ?,
            package_snapshot = ?
        WHERE id = ?
        """,
        (csv_snapshot, package_snapshot, batch_id),
    )


def _accounting_mailto_url(
    settings: dict,
    *,
    branch_id: int | None,
    date_from: str = "",
    date_to: str = "",
) -> str | None:
    email = settings["email"].strip()
    if not email:
        return None

    branch_label = _branch_label(branch_id)
    period_label = _accounting_period_label(date_from, date_to)
    subject = f"Ewidencja umów lombardowych - {branch_label} - {period_label}"
    greeting = f"Dzień dobry {settings['name']}," if settings["name"].strip() else "Dzień dobry,"
    body = "\n\n".join(
        [
            greeting,
            "W załączeniu przesyłam paczkę ZIP z ewidencją CSV oraz PDF-ami rozliczonych umów lombardowych.",
            f"Zakres: {branch_label}, {period_label}.",
            "Proszę o zaksięgowanie przesłanych pozycji.",
            "Pozdrawiam",
        ]
    )
    return f"mailto:{quote(email, safe='@,;.+-_')}?{urlencode({'subject': subject, 'body': body})}"


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
    _require_branch_access(contract["branch_id"])
    return contract


def _client_or_404(client_id: int) -> dict:
    client = query_one("SELECT * FROM clients WHERE id = ?", (client_id,))
    if client is None:
        abort(404)
    return client


def _photo_rows(contract_id: int) -> list[dict]:
    return query_all(
        "SELECT * FROM contract_photos WHERE contract_id = ? ORDER BY uploaded_at DESC",
        (contract_id,),
    )


def _form_value(name: str, *, prefix: str = "") -> str:
    return request.form.get(f"{prefix}{name}", "").strip()


def _normalize_identity(value: str) -> str:
    return "".join(value.split()).upper()


def _client_name(client: dict) -> str:
    return f"{client['first_name']} {client['last_name']}"


def _find_existing_client_from_form(
    *,
    db,
    prefix: str = "",
    exclude_client_id: int | None = None,
) -> dict | None:
    clauses = []
    params: list[int | str] = []
    pesel = _normalize_identity(_form_value("pesel", prefix=prefix))
    document_number = _normalize_identity(_form_value("document_number", prefix=prefix))

    if pesel:
        clauses.append("REPLACE(pesel, ' ', '') = ?")
        params.append(pesel)
    if document_number:
        clauses.append("UPPER(REPLACE(document_number, ' ', '')) = ?")
        params.append(document_number)
    if not clauses:
        return None

    exclude_clause = ""
    if exclude_client_id is not None:
        exclude_clause = "AND id != ?"
        params.append(exclude_client_id)

    return db.execute(
        f"""
        SELECT *
        FROM clients
        WHERE ({' OR '.join(clauses)})
        {exclude_clause}
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()


def _insert_client_from_form(*, db, prefix: str = "") -> int:
    cursor = db.execute(
        """
        INSERT INTO clients(
            first_name, last_name, pesel, document_type, document_number,
            phone, email, street_address, postal_code, city, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _form_value("first_name", prefix=prefix),
            _form_value("last_name", prefix=prefix),
            _form_value("pesel", prefix=prefix),
            _form_value("document_type", prefix=prefix) or "Dowód Osobisty",
            _form_value("document_number", prefix=prefix),
            _form_value("phone", prefix=prefix),
            _form_value("email", prefix=prefix),
            _form_value("street_address", prefix=prefix),
            _form_value("postal_code", prefix=prefix),
            _form_value("city", prefix=prefix),
            _form_value("notes", prefix=prefix),
        ),
    )
    return int(cursor.lastrowid)


def _save_contract_photos(
    *,
    db,
    contract_id: int,
    uploaded,
    caption: str,
) -> int:
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / str(contract_id)
    saved = 0

    for file in uploaded:
        if not file or not file.filename:
            continue
        extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if extension not in ALLOWED_EXTENSIONS:
            flash(f"Pominięto plik {file.filename}: dozwolone są JPG, PNG i WEBP.", "warning")
            continue
        upload_dir.mkdir(parents=True, exist_ok=True)
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

    return saved


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


@bp.before_request
def load_current_user() -> Response | None:
    g.current_user = None
    user_id = session.get("user_id")
    if user_id:
        g.current_user = query_one(
            """
            SELECT users.*, branches.city AS branch_city, branches.code AS branch_code
            FROM users
            LEFT JOIN branches ON branches.id = users.branch_id
            WHERE users.id = ? AND users.is_active = 1
            """,
            (user_id,),
        )
        if g.current_user is None:
            session.clear()

    if request.endpoint in AUTH_EXEMPT_ENDPOINTS:
        return None

    if g.current_user is None:
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("lombard.login", next=next_url))

    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        session["branch_context_id"] = user_branch_id
    return None


@bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response:
    if _current_user() is not None:
        return redirect(url_for("lombard.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = query_one(
            """
            SELECT users.*, branches.city AS branch_city
            FROM users
            LEFT JOIN branches ON branches.id = users.branch_id
            WHERE users.username = ? AND users.is_active = 1
            """,
            (username,),
        )
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            if user["branch_id"]:
                session["branch_context_id"] = user["branch_id"]
            get_db().execute(
                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user["id"],),
            )
            get_db().commit()
            flash(f"Zalogowano jako {user['display_name']}.", "success")
            return redirect(_safe_redirect_target(request.form.get("next")))

        flash("Nieprawidłowy login lub hasło.", "warning")

    next_url = _safe_redirect_target(request.args.get("next"))
    return render_template("login.html", next_url=next_url)


@bp.route("/logout", methods=["POST"])
def logout() -> Response:
    session.clear()
    flash("Wylogowano z programu.", "success")
    return redirect(url_for("lombard.login"))


@bp.app_context_processor
def inject_branch_context() -> dict:
    current_branch_id = _current_branch_id()
    current_branch = None
    if current_branch_id:
        current_branch = query_one("SELECT * FROM branches WHERE id = ?", (current_branch_id,))
    return {
        "branch_options": _branch_options(),
        "current_branch": current_branch,
        "current_branch_id": current_branch_id,
        "current_user": _current_user(),
        "is_admin": _is_admin(),
    }


@bp.route("/context/branch", methods=["POST"])
def set_branch_context() -> Response:
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        branch = query_one("SELECT * FROM branches WHERE id = ?", (user_branch_id,))
        session["branch_context_id"] = user_branch_id
        flash(f"Konto punktu pracuje wyłącznie w lokalizacji: {branch['city']}.", "warning")
        return redirect(_safe_redirect_target(request.form.get("next")))

    raw_branch_id = request.form.get("branch_id", "").strip()
    if not raw_branch_id:
        session.pop("branch_context_id", None)
        flash("Pracujesz teraz na widoku wszystkich punktów.", "success")
    else:
        branch = query_one("SELECT * FROM branches WHERE id = ?", (int(raw_branch_id),))
        if branch is None:
            abort(404)
        session["branch_context_id"] = branch["id"]
        flash(f"Ustawiono aktywny punkt: {branch['city']}.", "success")
    return redirect(_safe_redirect_target(request.form.get("next")))


@bp.route("/")
def dashboard() -> str:
    _refresh_overdue_contracts()
    branch_id, showing_all_branches = _branch_id_from_args()
    branch_clause = "WHERE branch_id = ?" if branch_id else ""
    params = (branch_id,) if branch_id else ()
    stats = query_one(
        f"""
        SELECT
            COUNT(*) AS contracts_total,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN status IN ('settled', 'sold', 'accounted') THEN 1 ELSE 0 END) AS settled_count,
            SUM(CASE WHEN status IN ('settled', 'sold') AND accountant_sent_at IS NULL THEN 1 ELSE 0 END) AS accounting_pending
        FROM contracts
        {branch_clause}
        """,
        params,
    )
    upcoming_branch_clause = "AND contracts.branch_id = ?" if branch_id else ""
    upcoming = query_all(
        f"""
        SELECT contracts.*, clients.first_name, clients.last_name, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE contracts.status = 'active'
        {upcoming_branch_clause}
        ORDER BY contracts.due_date ASC
        LIMIT 10
        """,
        params,
    )
    return render_template(
        "dashboard.html",
        stats=stats,
        upcoming=upcoming,
        today=_today(),
        selected_branch_id=branch_id,
        showing_all_branches=showing_all_branches,
    )


def _branch_workload_rows() -> list[dict]:
    branch_filter = ""
    params: tuple[int, ...] = ()
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        branch_filter = "WHERE branches.id = ?"
        params = (user_branch_id,)
    return query_all(
        f"""
        SELECT
            branches.*,
            COUNT(contracts.id) AS contracts_total,
            COUNT(DISTINCT contracts.client_id) AS clients_count,
            COALESCE(SUM(CASE WHEN contracts.status IN ('active', 'expired') THEN 1 ELSE 0 END), 0) AS open_count,
            COALESCE(SUM(CASE WHEN contracts.status = 'expired' THEN 1 ELSE 0 END), 0) AS expired_count,
            COALESCE(SUM(
                CASE
                    WHEN contracts.status IN ('settled', 'sold')
                         AND contracts.accountant_sent_at IS NULL
                    THEN 1
                    ELSE 0
                END
            ), 0) AS accounting_pending_count,
            COALESCE(SUM(
                CASE
                    WHEN contracts.status IN ('active', 'expired')
                    THEN contracts.loan_amount_cents
                    ELSE 0
                END
            ), 0) AS open_loan_cents
        FROM branches
        LEFT JOIN contracts ON contracts.branch_id = branches.id
        {branch_filter}
        GROUP BY branches.id
        ORDER BY branches.id
        """,
        params,
    )


@bp.route("/branches")
def branches() -> str:
    _refresh_overdue_contracts()
    user_branch_id = _current_user_branch_id()
    contract_filter = "WHERE branch_id = ?" if user_branch_id else ""
    accounting_filter = "AND branch_id = ?" if user_branch_id else ""
    params = (user_branch_id,) if user_branch_id else ()
    totals = query_one(
        f"""
        SELECT
            (SELECT COUNT(*) FROM clients) AS clients_total,
            (SELECT COUNT(*) FROM contracts {contract_filter}) AS contracts_total,
            (SELECT COUNT(*)
             FROM contracts
             WHERE status IN ('settled', 'sold')
               AND accountant_sent_at IS NULL
               {accounting_filter}) AS accounting_pending_total
        """,
        params + params,
    )
    return render_template(
        "branches.html",
        branches=_branch_workload_rows(),
        totals=totals,
    )


@bp.route("/clients", methods=["GET", "POST"])
def clients() -> str | Response:
    db = get_db()
    if request.method == "POST":
        existing_client = _find_existing_client_from_form(db=db)
        if existing_client is not None:
            flash(
                f"Klient {_client_name(existing_client)} jest już w kartotece. "
                "Otworzono istniejącą kartę zamiast tworzyć duplikat.",
                "warning",
            )
            return redirect(url_for("lombard.client_detail", client_id=existing_client["id"]))

        _insert_client_from_form(db=db)
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
    client = _client_or_404(client_id)
    branch_clause = ""
    params: list[int] = [client_id]
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        branch_clause = "AND contracts.branch_id = ?"
        params.append(user_branch_id)
    contracts = query_all(
        f"""
        SELECT contracts.*, branches.city AS branch_city
        FROM contracts
        JOIN branches ON branches.id = contracts.branch_id
        WHERE client_id = ?
        {branch_clause}
        ORDER BY issue_date DESC, id DESC
        """,
        tuple(params),
    )
    return render_template("client_detail.html", client=client, contracts=contracts)


@bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
def client_edit(client_id: int) -> str | Response:
    client = _client_or_404(client_id)
    if request.method == "POST":
        db = get_db()
        existing_client = _find_existing_client_from_form(
            db=db,
            exclude_client_id=client_id,
        )
        if existing_client is not None:
            flash(
                f"PESEL lub numer dokumentu należy już do klienta {_client_name(existing_client)}. "
                "Nie zapisano zmian, aby nie połączyć dwóch kartotek.",
                "warning",
            )
            return redirect(url_for("lombard.client_detail", client_id=existing_client["id"]))

        db.execute(
            """
            UPDATE clients
            SET first_name = ?,
                last_name = ?,
                pesel = ?,
                document_type = ?,
                document_number = ?,
                phone = ?,
                email = ?,
                street_address = ?,
                postal_code = ?,
                city = ?,
                notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
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
                client_id,
            ),
        )
        db.commit()
        flash("Zaktualizowano dane klienta w kartotece.", "success")
        return redirect(url_for("lombard.client_detail", client_id=client_id))

    return render_template("client_form.html", client=client)


@bp.route("/contracts/new", methods=["GET", "POST"])
def contract_new() -> str | Response:
    db = get_db()
    clients = query_all("SELECT * FROM clients ORDER BY last_name, first_name")
    branches = _branch_options()
    selected_client_id = request.args.get("client_id", type=int)
    requested_branch_id = request.args.get("branch_id", type=int) or _current_branch_id()
    selected_branch_id = requested_branch_id if _can_access_branch(requested_branch_id) else _current_branch_id()

    if request.method == "POST":
        issue_date = _parse_date(request.form["issue_date"])
        branch_id = int(request.form["branch_id"])
        _require_branch_access(branch_id)
        client_mode = request.form.get("client_mode") or (
            "existing" if request.form.get("client_id") else "new"
        )
        reused_existing_client = None
        created_new_client = False
        if client_mode == "new":
            reused_existing_client = _find_existing_client_from_form(
                db=db,
                prefix="new_client_",
            )
            if reused_existing_client is not None:
                client_id = reused_existing_client["id"]
            else:
                client_id = _insert_client_from_form(db=db, prefix="new_client_")
                created_new_client = True
        else:
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
        contract_id = int(cursor.lastrowid)
        saved_photos = _save_contract_photos(
            db=db,
            contract_id=contract_id,
            uploaded=request.files.getlist("photos"),
            caption=request.form.get("photo_caption", "").strip(),
        )
        db.commit()
        if created_new_client:
            flash("Dodano klienta do kartoteki.", "success")
        elif reused_existing_client is not None:
            flash(
                f"Wykryto istniejącą kartotekę klienta {_client_name(reused_existing_client)}. "
                "Nowa umowa została podpięta do tej karty.",
                "warning",
            )
        if saved_photos:
            flash(f"Utworzono umowę i dodano zdjęcia: {saved_photos}.", "success")
        else:
            flash("Utworzono umowę. Możesz teraz dodać zdjęcia zabezpieczenia.", "success")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    return render_template(
        "contract_form.html",
        clients=clients,
        branches=branches,
        selected_client_id=selected_client_id,
        selected_branch_id=selected_branch_id,
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


@bp.route("/contracts/<int:contract_id>/edit", methods=["GET", "POST"])
def contract_edit(contract_id: int) -> str | Response:
    _refresh_overdue_contracts()
    contract = _contract_or_404(contract_id)
    if contract["status"] not in {"active", "expired"}:
        flash("Można poprawiać tylko umowy aktywne albo po terminie przed rozliczeniem.", "warning")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    issue_date = _parse_date(contract["issue_date"])
    if request.method == "POST":
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
        status = "expired" if calculation.due_date < _today() else "active"

        db = get_db()
        db.execute(
            """
            UPDATE contracts
            SET term_days = ?,
                due_date = ?,
                additional_period_end = ?,
                loan_amount_cents = ?,
                commission_amount_cents = ?,
                total_repayment_cents = ?,
                daily_increase_cents = ?,
                max_additional_fee_cents = ?,
                collateral_type = ?,
                collateral_description = ?,
                collateral_value_cents = ?,
                valuation_basis = ?,
                sale_mode = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
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
                status,
                contract_id,
            ),
        )
        db.commit()
        flash("Zapisano korektę umowy i przeliczono kwoty.", "success")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    form_values = {
        "loan_amount": _money_input(contract["loan_amount_cents"]),
        "commission_amount": _money_input(contract["commission_amount_cents"]),
        "collateral_value": _money_input(contract["collateral_value_cents"]),
    }
    return render_template(
        "contract_edit.html",
        contract=contract,
        form_values=form_values,
    )


@bp.route("/contracts/<int:contract_id>/photos", methods=["POST"])
def contract_photos(contract_id: int) -> Response:
    _contract_or_404(contract_id)
    db = get_db()
    saved = _save_contract_photos(
        db=db,
        contract_id=contract_id,
        uploaded=request.files.getlist("photos"),
        caption=request.form.get("caption", "").strip(),
    )

    db.commit()
    if saved:
        flash(f"Dodano zdjęcia: {saved}.", "success")
    return redirect(url_for("lombard.contract_detail", contract_id=contract_id))


@bp.route("/uploads/<int:contract_id>/<path:filename>")
def uploaded_photo(contract_id: int, filename: str) -> Response:
    _contract_or_404(contract_id)
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


@bp.route("/contracts/<int:contract_id>/realize", methods=["POST"])
def contract_realize(contract_id: int) -> Response:
    _refresh_overdue_contracts()
    contract = _contract_or_404(contract_id)
    if contract["status"] not in {"active", "expired"}:
        flash("Tę umowę już rozliczono albo przekazano do księgowości.", "warning")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    realization_date = _parse_date(request.form["realization_date"])
    sale_amount = money_to_cents(request.form["sale_amount"])
    calculation = calculate_sale_realization(
        base_total_cents=contract["total_repayment_cents"],
        due_date=_parse_date(contract["due_date"]),
        realization_date=realization_date,
        sale_amount_cents=sale_amount,
    )
    get_db().execute(
        """
        UPDATE contracts
        SET status = 'sold',
            realization_date = ?,
            sale_amount_cents = ?,
            realization_due_cents = ?,
            surplus_return_cents = ?,
            shortfall_cents = ?,
            realization_note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            realization_date.isoformat(),
            calculation.sale_amount_cents,
            calculation.amount_due_cents,
            calculation.surplus_return_cents,
            calculation.shortfall_cents,
            request.form.get("realization_note", "").strip(),
            contract_id,
        ),
    )
    get_db().commit()
    flash("Umowa została zrealizowana przez sprzedaż zabezpieczenia i czeka na księgowanie.", "success")
    return redirect(url_for("lombard.contract_detail", contract_id=contract_id))


@bp.route("/contracts/<int:contract_id>/account", methods=["POST"])
def contract_account(contract_id: int) -> Response:
    contract = _contract_or_404(contract_id)
    if contract["status"] == "accounted":
        flash("Ta umowa jest już oznaczona jako wysłana do księgowej.", "warning")
        return redirect(url_for("lombard.accounting"))
    if contract["status"] not in {"settled", "sold"}:
        flash("Do księgowości można przekazać tylko spłaconą albo zrealizowaną umowę.", "warning")
        return redirect(url_for("lombard.contract_detail", contract_id=contract_id))

    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    realization_date = contract["realization_date"] or contract["payment_date"] or ""
    accounting_note = request.form.get("accounting_note", "").strip()
    batch_id = _create_accounting_batch(
        db=db,
        branch_id=contract["branch_id"],
        date_from=realization_date,
        date_to=realization_date,
        contracts_count=1,
        note=accounting_note,
    )
    db.execute(
        """
        UPDATE contracts
        SET status = 'accounted',
            accounted_at = COALESCE(accounted_at, ?),
            accountant_sent_at = ?,
            accounting_note = ?,
            accounting_batch_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (now, now, accounting_note, batch_id, contract_id),
    )
    _store_accounting_batch_snapshot(db=db, batch_id=batch_id)
    db.commit()
    flash(
        f"Umowa została oznaczona jako zaksięgowana/wysłana do księgowej w paczce #{batch_id}.",
        "success",
    )
    return redirect(url_for("lombard.accounting"))


@bp.route("/accounting/settings", methods=["POST"])
def accounting_settings() -> Response:
    _require_admin()
    db = get_db()
    _save_setting(
        db=db,
        key=ACCOUNTANT_EMAIL_KEY,
        value=request.form.get("accountant_email", "").strip(),
    )
    _save_setting(
        db=db,
        key=ACCOUNTANT_NAME_KEY,
        value=request.form.get("accountant_name", "").strip(),
    )
    db.commit()
    flash("Zapisano dane księgowej do przygotowania wysyłki.", "success")
    return redirect(_safe_redirect_target(request.form.get("next")))


@bp.route("/contract-template", methods=["GET", "POST"])
def contract_template_settings() -> str | Response:
    _require_admin()
    db = get_db()
    if request.method == "POST":
        template_text = ""
        if request.form.get("reset_template") != "1":
            uploaded_template_text = _uploaded_contract_template_text(
                request.files.get("contract_template_file")
            )
            template_text = (
                uploaded_template_text
                if uploaded_template_text is not None
                else request.form.get("contract_template", "")
            ).strip()
        _save_setting(db=db, key=CONTRACT_TEMPLATE_KEY, value=template_text)
        db.commit()
        message = (
            "Przywrócono domyślny szablon umowy."
            if not template_text
            else "Zapisano szablon umowy do generowania PDF."
        )
        flash(message, "success")
        return redirect(url_for("lombard.contract_template_settings"))

    stored_template = _get_setting(CONTRACT_TEMPLATE_KEY)
    return render_template(
        "contract_template.html",
        template_text=stored_template or DEFAULT_CONTRACT_TEMPLATE,
        using_default=not bool(stored_template),
        placeholders=CONTRACT_TEMPLATE_PLACEHOLDERS,
    )


@bp.route("/accounting/bulk-account", methods=["POST"])
def accounting_bulk_account() -> Response:
    db = get_db()
    raw_branch_id = request.form.get("branch_id", "").strip()
    if _current_user_branch_id():
        branch_id = _current_user_branch_id()
        showing_all_branches = False
    else:
        showing_all_branches = raw_branch_id == "all"
        branch_id = int(raw_branch_id) if raw_branch_id and not showing_all_branches else None
    date_from = _normalize_date_filter(request.form.get("date_from", ""), "data od")
    date_to = _normalize_date_filter(request.form.get("date_to", ""), "data do")
    accounting_note = request.form.get("accounting_note", "").strip()
    now = datetime.now().isoformat(timespec="seconds")

    params: list[int | str] = []
    clauses = ["status IN ('settled', 'sold')"]
    if branch_id:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    if date_from:
        clauses.append("COALESCE(payment_date, realization_date) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("COALESCE(payment_date, realization_date) <= ?")
        params.append(date_to)
    where = " AND ".join(clauses)

    contracts_to_account = db.execute(
        f"SELECT id FROM contracts WHERE {where} ORDER BY id",
        tuple(params),
    ).fetchall()
    contract_ids = [row["id"] for row in contracts_to_account]

    if contract_ids:
        batch_id = _create_accounting_batch(
            db=db,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            contracts_count=len(contract_ids),
            note=accounting_note,
        )
        placeholders = ", ".join("?" for _ in contract_ids)
        update_params: list[int | str] = [now, now, accounting_note, batch_id, *contract_ids]
        cursor = db.execute(
            f"""
            UPDATE contracts
            SET status = 'accounted',
                accounted_at = COALESCE(accounted_at, ?),
                accountant_sent_at = ?,
                accounting_note = ?,
                accounting_batch_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            tuple(update_params),
        )
        _store_accounting_batch_snapshot(db=db, batch_id=batch_id)
        db.commit()
        flash(
            f"Oznaczono jako wysłane do księgowej: {cursor.rowcount}. Utworzono paczkę #{batch_id}.",
            "success",
        )
    else:
        db.commit()
        flash("Brak spłaconych lub zrealizowanych umów do oznaczenia dla wybranego filtra.", "warning")

    redirect_args = _accounting_filter_url_args(
        branch_id=branch_id,
        showing_all_branches=showing_all_branches,
        date_from=date_from,
        date_to=date_to,
    )
    return redirect(url_for("lombard.accounting", **redirect_args))


@bp.route("/contracts/<int:contract_id>/pdf")
def contract_pdf(contract_id: int) -> Response:
    contract = _contract_or_404(contract_id)
    photos = _photo_rows(contract_id)
    pdf = build_contract_pdf(
        contract,
        photos,
        Path(current_app.config["UPLOAD_FOLDER"]),
        template_text=_contract_template_text(),
    )
    filename = f"umowa_{contract['contract_number'].replace('/', '_')}.pdf"
    return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@bp.route("/archive")
def archive() -> str:
    _refresh_overdue_contracts()
    status = request.args.get("status", "")
    branch_id, showing_all_branches = _branch_id_from_args()
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
    branches = _branch_options()
    return render_template(
        "archive.html",
        contracts=contracts,
        branches=branches,
        selected_status=status,
        selected_branch_id=branch_id,
        showing_all_branches=showing_all_branches,
        q=q,
    )


@bp.route("/accounting")
def accounting() -> str:
    branch_id, showing_all_branches = _branch_id_from_args()
    date_from = _date_filter_from_args("date_from", "data od")
    date_to = _date_filter_from_args("date_to", "data do")
    contracts = _accounting_rows(
        branch_id=branch_id,
        include_sent=True,
        date_from=date_from,
        date_to=date_to,
    )
    branches = _branch_options()
    accountant_settings = _accountant_settings()
    export_args = _accounting_filter_url_args(
        branch_id=branch_id,
        showing_all_branches=showing_all_branches,
        date_from=date_from,
        date_to=date_to,
    )
    export_all_args = _accounting_filter_url_args(
        branch_id=branch_id,
        showing_all_branches=showing_all_branches,
        date_from=date_from,
        date_to=date_to,
        include_sent=True,
    )
    return render_template(
        "accounting.html",
        contracts=contracts,
        accounting_batches=_accounting_batch_rows(),
        branches=branches,
        selected_branch_id=branch_id,
        showing_all_branches=showing_all_branches,
        selected_date_from=date_from,
        selected_date_to=date_to,
        accountant_settings=accountant_settings,
        accounting_mailto_url=_accounting_mailto_url(
            accountant_settings,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        ),
        accounting_export_url=url_for("lombard.accounting_export", **export_args),
        accounting_package_url=url_for("lombard.accounting_package", **export_args),
        accounting_export_all_url=url_for("lombard.accounting_export", **export_all_args),
    )


def _accounting_batch_rows(limit: int = 8) -> list[dict]:
    branch_clause = ""
    params: list[int] = []
    user_branch_id = _current_user_branch_id()
    if user_branch_id:
        branch_clause = "WHERE accounting_batches.branch_id = ?"
        params.append(user_branch_id)
    params.append(limit)
    return query_all(
        f"""
        SELECT accounting_batches.*, branches.city AS branch_city
        FROM accounting_batches
        LEFT JOIN branches ON branches.id = accounting_batches.branch_id
        {branch_clause}
        ORDER BY accounting_batches.created_at DESC, accounting_batches.id DESC
        LIMIT ?
        """,
        tuple(params),
    )


def _accounting_batch_or_404(batch_id: int) -> dict:
    batch = query_one(
        """
        SELECT accounting_batches.*, branches.city AS branch_city
        FROM accounting_batches
        LEFT JOIN branches ON branches.id = accounting_batches.branch_id
        WHERE accounting_batches.id = ?
        """,
        (batch_id,),
    )
    if batch is None:
        abort(404)
    if _current_user_branch_id() and batch["branch_id"] != _current_user_branch_id():
        abort(403)
    return batch


def _accounting_rows(
    *,
    branch_id: int | None,
    include_sent: bool,
    date_from: str = "",
    date_to: str = "",
) -> list[dict]:
    clauses = [
        "contracts.status IN ('settled', 'sold', 'accounted')"
        if include_sent
        else "contracts.status IN ('settled', 'sold')"
    ]
    params: list[int | str] = []
    if branch_id:
        clauses.append("contracts.branch_id = ?")
        params.append(branch_id)
    if date_from:
        clauses.append("COALESCE(contracts.payment_date, contracts.realization_date) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("COALESCE(contracts.payment_date, contracts.realization_date) <= ?")
        params.append(date_to)
    where = " AND ".join(clauses)
    return query_all(
        f"""
        SELECT contracts.*, clients.first_name, clients.last_name, clients.pesel, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE {where}
        ORDER BY COALESCE(contracts.payment_date, contracts.realization_date) DESC, contracts.id DESC
        """,
        tuple(params),
    )


def _accounting_rows_for_batch(batch_id: int) -> list[dict]:
    return query_all(
        """
        SELECT contracts.*, clients.first_name, clients.last_name, clients.pesel, branches.city AS branch_city
        FROM contracts
        JOIN clients ON clients.id = contracts.client_id
        JOIN branches ON branches.id = contracts.branch_id
        WHERE contracts.accounting_batch_id = ?
        ORDER BY COALESCE(contracts.payment_date, contracts.realization_date) DESC, contracts.id DESC
        """,
        (batch_id,),
    )


def _accounting_csv(rows: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Numer umowy",
            "Punkt",
            "Klient",
            "PESEL",
            "Data umowy",
            "Rodzaj realizacji",
            "Data realizacji",
            "Kwota pożyczki",
            "Prowizja",
            "Należność",
            "Kwota zrealizowana",
            "Zwrot nadwyżki klientowi",
            "Niedobór po sprzedaży",
            "Status",
            "Notatka",
        ]
    )
    for row in rows:
        is_sale = bool(row.get("realization_date"))
        realized_amount = row["sale_amount_cents"] if is_sale else row["paid_amount_cents"]
        realization_date = row["realization_date"] if is_sale else row["payment_date"]
        writer.writerow(
            [
                row["contract_number"],
                row["branch_city"],
                f"{row['first_name']} {row['last_name']}",
                row["pesel"] or "",
                row["issue_date"],
                "sprzedaż zabezpieczenia" if is_sale else "spłata klienta",
                realization_date or "",
                format_money(row["loan_amount_cents"]),
                format_money(row["commission_amount_cents"]),
                format_money(row["realization_due_cents"] if is_sale else row["total_repayment_cents"]),
                format_money(realized_amount),
                format_money(row["surplus_return_cents"]) if is_sale else "",
                format_money(row["shortfall_cents"]) if is_sale else "",
                row["status"],
                row["realization_note"] if is_sale else row["accounting_note"] or "",
            ]
        )
    return "\ufeff" + output.getvalue()


def _contract_file_stem(contract_number: str) -> str:
    return "umowa_" + contract_number.replace("/", "_").replace(" ", "_")


@bp.route("/accounting/export.csv")
def accounting_export() -> Response:
    include_sent = request.args.get("include_sent") == "1"
    branch_id, _ = _branch_id_from_args()
    date_from = _date_filter_from_args("date_from", "data od")
    date_to = _date_filter_from_args("date_to", "data do")
    csv_data = _accounting_csv(
        _accounting_rows(
            branch_id=branch_id,
            include_sent=include_sent,
            date_from=date_from,
            date_to=date_to,
        )
    )
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ewidencja_ksiegowa.csv"},
    )


def _accounting_package_bytes(rows: list[dict]) -> bytes:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ewidencja_ksiegowa.csv", _accounting_csv(rows).encode("utf-8"))
        for row in rows:
            contract = _contract_or_404(row["id"])
            photos = _photo_rows(row["id"])
            pdf = build_contract_pdf(
                contract,
                photos,
                Path(current_app.config["UPLOAD_FOLDER"]),
                template_text=_contract_template_text(),
            )
            archive.writestr(f"umowy/{_contract_file_stem(contract['contract_number'])}.pdf", pdf.getvalue())

    archive_buffer.seek(0)
    return archive_buffer.getvalue()


def _accounting_package_response(rows: list[dict], *, download_name: str) -> Response:
    archive_buffer = io.BytesIO(_accounting_package_bytes(rows))
    return send_file(
        archive_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )


@bp.route("/accounting/package.zip")
def accounting_package() -> Response:
    include_sent = request.args.get("include_sent") == "1"
    branch_id, _ = _branch_id_from_args()
    date_from = _date_filter_from_args("date_from", "data od")
    date_to = _date_filter_from_args("date_to", "data do")
    rows = _accounting_rows(
        branch_id=branch_id,
        include_sent=include_sent,
        date_from=date_from,
        date_to=date_to,
    )
    return _accounting_package_response(rows, download_name="paczka_ksiegowa.zip")


@bp.route("/accounting/batches/<int:batch_id>/export.csv")
def accounting_batch_export(batch_id: int) -> Response:
    batch = _accounting_batch_or_404(batch_id)
    csv_data = batch.get("csv_snapshot") or _accounting_csv(_accounting_rows_for_batch(batch_id))
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=ewidencja_ksiegowa_paczka_{batch_id}.csv"
        },
    )


@bp.route("/accounting/batches/<int:batch_id>/package.zip")
def accounting_batch_package(batch_id: int) -> Response:
    batch = _accounting_batch_or_404(batch_id)
    if batch.get("package_snapshot"):
        return send_file(
            io.BytesIO(batch["package_snapshot"]),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"paczka_ksiegowa_{batch_id}.zip",
        )
    return _accounting_package_response(
        _accounting_rows_for_batch(batch_id),
        download_name=f"paczka_ksiegowa_{batch_id}.zip",
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
