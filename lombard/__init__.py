from __future__ import annotations

from pathlib import Path

from flask import Flask

from .calculations import format_money, money_to_words
from .database import close_db, init_db


STATUS_LABELS = {
    "active": "aktywna",
    "settled": "spłacona",
    "expired": "po terminie",
    "sold": "sprzedana",
    "accounted": "zaksięgowana",
}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    instance_path = Path(app.instance_path)
    app.config.from_mapping(
        SECRET_KEY="dev-change-me",
        DATABASE=str(instance_path / "lombard.sqlite3"),
        UPLOAD_FOLDER=str(instance_path / "uploads"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    instance_path.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["money_words"] = money_to_words
    app.jinja_env.filters["status_label"] = lambda value: STATUS_LABELS.get(value, value)

    with app.app_context():
        init_db()

    from .routes import bp

    app.register_blueprint(bp)
    return app
