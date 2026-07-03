"""Drop unused catalog.product_nutrition (no app reader; loader removed).

Revision ID: e1f2a3b4c5d7
Revises: c5d6e7f80912
Create Date: 2026-04-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d7"
down_revision: Union[str, None] = "c5d6e7f80912"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("product_nutrition", schema="catalog"):
        op.drop_table("product_nutrition", schema="catalog")


def downgrade() -> None:
    pass
