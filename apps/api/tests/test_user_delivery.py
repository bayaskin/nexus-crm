import logging

from app.core.database import Base  # noqa: F401
from app.api.routes import users


def test_smtp_failure_is_reported_without_logging_credentials(monkeypatch, caplog):
    secret = "never-log-this-temporary-password"

    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("mail service unavailable")

    monkeypatch.setattr(users.smtplib, "SMTP", FailingSMTP)
    caplog.set_level(logging.DEBUG)

    assert users._send_email("recipient@example.test", "Welcome", f"Password: {secret}") is False
    assert secret not in caplog.text
