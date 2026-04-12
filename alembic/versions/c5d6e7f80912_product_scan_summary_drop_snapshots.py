"""Relational product scan summary; drop JSON scan_snapshots.

One row per product (``product_id`` UNIQUE): name, brand, barcode, core nutrients,
taxonomy, NOVA, KNPM octagon count — updated on each successful ``save_ocr_result_to_db``.

Revision ID: c5d6e7f80912
Revises: b2e4f8a1c903
Create Date: 2026-04-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f80912"
down_revision: Union[str, None] = "b2e4f8a1c903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str, schema: str) -> bool:
    return sa.inspect(bind).has_table(name, schema=schema)


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "product_scan_summary",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["app.products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="app",
    )
    op.create_index(
        "ix_product_scan_summary_product_id",
        "product_scan_summary",
        ["product_id"],
        unique=True,
        schema="app",
    )
    op.create_index(
        "ix_product_scan_summary_id",
        "product_scan_summary",
        ["id"],
        unique=False,
        schema="app",
    )

    if _has_table(bind, "scan_snapshots", schema="app"):
        op.drop_table("scan_snapshots", schema="app")


def downgrade() -> None:
    op.drop_table("product_scan_summary", schema="app")
