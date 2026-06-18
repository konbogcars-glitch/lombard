from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    pesel TEXT,
    document_type TEXT NOT NULL DEFAULT 'Dowód Osobisty',
    document_number TEXT,
    phone TEXT,
    email TEXT,
    street_address TEXT NOT NULL,
    postal_code TEXT,
    city TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_clients_pesel ON clients(pesel);

CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_number TEXT NOT NULL UNIQUE,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    issue_date TEXT NOT NULL,
    term_days INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    additional_period_end TEXT NOT NULL,
    loan_amount_cents INTEGER NOT NULL,
    commission_amount_cents INTEGER NOT NULL,
    total_repayment_cents INTEGER NOT NULL,
    daily_increase_cents INTEGER NOT NULL,
    max_additional_fee_cents INTEGER NOT NULL,
    collateral_type TEXT NOT NULL,
    collateral_description TEXT NOT NULL,
    collateral_value_cents INTEGER NOT NULL,
    valuation_basis TEXT,
    sale_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    payment_date TEXT,
    paid_amount_cents INTEGER,
    realization_date TEXT,
    sale_amount_cents INTEGER,
    realization_due_cents INTEGER,
    surplus_return_cents INTEGER,
    realization_note TEXT,
    accounted_at TEXT,
    accountant_sent_at TEXT,
    accounting_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contracts_client ON contracts(client_id);
CREATE INDEX IF NOT EXISTS idx_contracts_branch ON contracts(branch_id);
CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_due_date ON contracts(due_date);

CREATE TABLE IF NOT EXISTS contract_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    stored_filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    caption TEXT,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


BRANCHES = [
    ("BUS", "Lombard Busko-Zdrój", "Busko-Zdrój", "ul. Wojska Polskiego 3"),
    ("CHM", "Lombard Chmielnik", "Chmielnik", "ul. Legionistów 22"),
    ("PIN", "Lombard Pińczów", "Pińczów", "adres punktu do uzupełnienia"),
]


CONTRACT_COLUMN_MIGRATIONS = {
    "realization_date": "TEXT",
    "sale_amount_cents": "INTEGER",
    "realization_due_cents": "INTEGER",
    "surplus_return_cents": "INTEGER",
    "realization_note": "TEXT",
}


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = Path(current_app.config["DATABASE"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        connection.row_factory = dict_factory
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_: object | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA)
    _ensure_contract_columns(db)
    db.executemany(
        """
        INSERT INTO branches(code, name, city, address)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            city = excluded.city,
            address = excluded.address
        """,
        BRANCHES,
    )
    db.commit()


def _ensure_contract_columns(db: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(contracts)").fetchall()
    }
    for column_name, column_type in CONTRACT_COLUMN_MIGRATIONS.items():
        if column_name not in existing_columns:
            db.execute(f"ALTER TABLE contracts ADD COLUMN {column_name} {column_type}")


def next_contract_number(branch_id: int, issue_year: int) -> str:
    db = get_db()
    branch = db.execute("SELECT code FROM branches WHERE id = ?", (branch_id,)).fetchone()
    if branch is None:
        raise ValueError("Nie znaleziono punktu lombardu.")

    prefix = f"{branch['code']}/{issue_year}/"
    row = db.execute(
        "SELECT COUNT(*) AS count FROM contracts WHERE contract_number LIKE ?",
        (prefix + "%",),
    ).fetchone()
    next_number = int(row["count"]) + 1
    return f"{prefix}{next_number:04d}"


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    return get_db().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    return get_db().execute(sql, params).fetchone()
