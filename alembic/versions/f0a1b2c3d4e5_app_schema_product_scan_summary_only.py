"""Drop legacy app.* ORM tables; keep only app.product_scan_summary (+ catalog.*).

Revision ID: f0a1b2c3d4e5
Revises: e1f2a3b4c5d7
Create Date: 2026-04-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e1f2a3b4c5d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # FK order: summary referenced products; scans referenced products; etc.
    for tbl in (
        "product_scan_summary",
        "scans",
        "product_ingredients",
        "nutrition_data",
        "ingredients",
        "products",
    ):
        if sa.inspect(bind).has_table(tbl, schema="app"):
            op.execute(text(f'DROP TABLE app."{tbl}" CASCADE'))

    op.create_table(
        "product_scan_summary",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("barcode", sa.String(length=50), nullable=True),
        sa.Column("total_fat_g", sa.Float(), nullable=True),
        sa.Column("sodium_g", sa.Float(), nullable=True),
        sa.Column("total_sugar_g", sa.Float(), nullable=True),
        sa.Column("class_name", sa.String(length=255), nullable=True),
        sa.Column("subclass_name", sa.String(length=255), nullable=True),
        sa.Column("nova", sa.Text(), nullable=True),
        sa.Column("octagon_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="app",
    )
    op.create_index(
        "ix_product_scan_summary_id",
        "product_scan_summary",
        ["id"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "ix_product_scan_summary_created_at",
        "product_scan_summary",
        ["created_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "ix_product_scan_summary_user_id",
        "product_scan_summary",
        ["user_id"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    """Legacy ``app.products`` / ``scans`` / … are not recreated; restore from backup if needed."""
    pass
