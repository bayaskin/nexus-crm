import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-model-registry-test-key-2026-unique-value")

from app.core.database import Base  # noqa: E402
from app import models  # noqa: E402,F401


def test_all_inventory_models_are_registered_in_sqlalchemy_metadata():
    assert {"warehouses", "inventory_balances", "inventory_movements"}.issubset(Base.metadata.tables)
