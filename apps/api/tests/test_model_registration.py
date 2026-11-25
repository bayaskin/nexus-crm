import ast
import os
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-model-registry-test-key-2026-unique-value")

from app.core.database import Base  # noqa: E402
from app import models  # noqa: E402,F401


def test_all_inventory_models_are_registered_in_sqlalchemy_metadata():
    assert {"warehouses", "inventory_balances", "inventory_movements"}.issubset(Base.metadata.tables)


def test_qa_execution_admission_models_are_registered_in_sqlalchemy_metadata():
    assert {"qa_execution_capacity", "qa_execution_admissions"}.issubset(Base.metadata.tables)


def test_qa_execution_capacity_singleton_id_is_not_autoincrementing():
    capacity = Base.metadata.tables["qa_execution_capacity"]
    column = capacity.c.id
    assert column.primary_key
    assert column.autoincrement is False
    assert "ck_qa_execution_capacity_singleton" in {
        constraint.name for constraint in capacity.constraints
    }
    assert "ck_qa_execution_capacity_bounds" in {
        constraint.name for constraint in capacity.constraints
    }

    migration_path = Path(__file__).parents[1] / "alembic/versions/f3a9c82b7d10_add_qa_execution_admissions.py"
    migration = ast.parse(migration_path.read_text(), filename=str(migration_path))
    id_columns = [
        node
        for node in ast.walk(migration)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Column"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "id"
        and any(
            keyword.arg == "autoincrement"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        )
    ]
    assert len(id_columns) == 1
