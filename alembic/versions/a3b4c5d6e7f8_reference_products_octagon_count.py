"""Add octagon_count to catalog.reference_products for scan write-through.

Revision ID: a3b4c5d6e7f8
Revises: f0a1b2c3d4e5
Create Date: 2026-04-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("reference_products", schema="catalog"):
        return
    cols = [c["name"] for c in insp.get_columns("reference_products", schema="catalog")]
    if "octagon_count" not in cols:
        op.add_column(
            "reference_products",
            sa.Column("octagon_count", sa.Integer(), nullable=True),
            schema="catalog",
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("reference_products", schema="catalog"):
        return
    cols = [c["name"] for c in insp.get_columns("reference_products", schema="catalog")]
    if "octagon_count" in cols:
        op.drop_column("reference_products", "octagon_count", schema="catalog")
