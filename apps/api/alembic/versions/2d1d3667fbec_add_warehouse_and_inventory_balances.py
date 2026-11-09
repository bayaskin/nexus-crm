"""add warehouse and inventory balances

Revision ID: 2d1d3667fbec
Revises: a6336db9586a
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2d1d3667fbec"
down_revision: Union[str, None] = "a6336db9586a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WAREHOUSE_ID = uuid.uuid4().hex


def upgrade() -> None:
    # Validate existing movement history before any DDL operations.
    bind = op.get_bind()

    zero_movements = bind.execute(
        sa.text("""
            SELECT COUNT(*)
            FROM inventory_movements
            WHERE quantity_change = 0
        """)
    ).scalar_one()

    if zero_movements:
        raise RuntimeError(
            f"Found {zero_movements} zero-quantity movements."
        )

    negative_balances = bind.execute(
        sa.text("""
            SELECT product_id, SUM(quantity_change) AS balance
            FROM inventory_movements
            GROUP BY product_id
            HAVING SUM(quantity_change) < 0
        """)
    ).all()

    if negative_balances:
        raise RuntimeError(
            f"Historical movements produce negative balances: "
            f"{negative_balances}"
        )

    op.create_table(
        "warehouses",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_warehouses_code",
        "warehouses",
        ["code"],
        unique=True,
    )

    op.create_table(
        "inventory_balances",
        sa.Column(
            "warehouse_id",
            sa.Uuid(native_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(native_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "quantity_on_hand",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "reorder_level",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "id",
            sa.Uuid(native_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity_on_hand >= 0",
            name="ck_inventory_balance_nonnegative",
        ),
        sa.CheckConstraint(
            "reorder_level >= 0",
            name="ck_inventory_balance_reorder_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "warehouse_id",
            "product_id",
            name="uq_inventory_balance_warehouse_product",
        ),
    )

    op.create_index(
        "ix_inventory_balances_product_id",
        "inventory_balances",
        ["product_id"],
    )
    op.create_index(
        "ix_inventory_balances_warehouse_id",
        "inventory_balances",
        ["warehouse_id"],
    )

    # 2. Insert the default warehouse.

    bind.execute(
        sa.text(
            """
            INSERT INTO warehouses (id, code, name, is_active)
            VALUES (:id, :code, :name, :is_active)
            """
        ),
        {
            "id": WAREHOUSE_ID,
            "code": "MAIN",
            "name": "Main Warehouse",
            "is_active": True,
        },
    )

    # 3. Add warehouse_id as nullable to preserve existing rows.
    op.add_column(
        "inventory_movements",
        sa.Column(
            "warehouse_id",
            sa.Uuid(native_uuid=False),
            nullable=True,
        ),
    )

    # 4. Assign all historical movements to the default warehouse.
    bind.execute(
        sa.text(
            """
            UPDATE inventory_movements
            SET warehouse_id = :warehouse_id
            WHERE warehouse_id IS NULL
            """
        ),
        {"warehouse_id": WAREHOUSE_ID},
    )

    # 5. Validate historical movement data before reconstructing balances.
    zero_movements = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM inventory_movements
            WHERE quantity_change = 0
            """
        )
    ).scalar_one()

    if zero_movements:
        raise RuntimeError(
            f"Migration stopped: found {zero_movements} zero-quantity "
            "inventory movements. Review the ledger before proceeding."
        )

    negative_balances = bind.execute(
        sa.text(
            """
            SELECT product_id, SUM(quantity_change) AS balance
            FROM inventory_movements
            GROUP BY product_id
            HAVING SUM(quantity_change) < 0
            """
        )
    ).all()

    if negative_balances:
        raise RuntimeError(
            "Migration stopped: historical movements produce negative "
            f"balances for products: {negative_balances}"
        )

    # 6. Reconstruct balances from the movement ledger.
    # Products without movements receive an initial balance of zero.
    bind.execute(
        sa.text(
            """
            INSERT INTO inventory_balances (
                id,
                warehouse_id,
                product_id,
                quantity_on_hand,
                reorder_level
            )
            SELECT
                REPLACE(UUID(), '-', ''),
                :warehouse_id,
                p.id,
                COALESCE(SUM(m.quantity_change), 0),
                0
            FROM products AS p
            LEFT JOIN inventory_movements AS m
                ON m.product_id = p.id
            GROUP BY p.id
            """
        ),
        {"warehouse_id": WAREHOUSE_ID},
    )

    # 7. Add the warehouse foreign key and enforce non-null.
    op.create_index(
        "ix_inventory_movements_warehouse_id",
        "inventory_movements",
        ["warehouse_id"],
    )

    op.create_foreign_key(
        "fk_inventory_movements_warehouse_id_warehouses",
        "inventory_movements",
        "warehouses",
        ["warehouse_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.alter_column(
        "inventory_movements",
        "warehouse_id",
        existing_type=sa.Uuid(native_uuid=False),
        nullable=False,
    )

    # 8. Ensure future movement records cannot have a zero quantity change.
    op.create_check_constraint(
        "ck_inventory_movement_nonzero",
        "inventory_movements",
        "quantity_change != 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_inventory_movement_nonzero",
        "inventory_movements",
        type_="check",
    )

    op.drop_constraint(
        "fk_inventory_movements_warehouse_id_warehouses",
        "inventory_movements",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_inventory_movements_warehouse_id",
        table_name="inventory_movements",
    )

    op.drop_column("inventory_movements", "warehouse_id")

    op.drop_index(
        "ix_inventory_balances_warehouse_id",
        table_name="inventory_balances",
    )
    op.drop_index(
        "ix_inventory_balances_product_id",
        table_name="inventory_balances",
    )
    op.drop_table("inventory_balances")

    op.drop_index(
        "ix_warehouses_code",
        table_name="warehouses",
    )
    op.drop_table("warehouses")