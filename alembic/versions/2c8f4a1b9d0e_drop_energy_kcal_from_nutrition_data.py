"""Drop energy_kcal from nutrition_data (pipeline no longer uses it).

Revision ID: 2c8f4a1b9d0e
Revises: 15b732399207
Create Date: 2026-04-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2c8f4a1b9d0e"
down_revision: Union[str, None] = "15b732399207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("nutrition_data", "energy_kcal")


def downgrade() -> None:
    op.add_column(
        "nutrition_data",
        sa.Column("energy_kcal", sa.Float(), nullable=True),
    )
