"""Legacy revision (no-op): was scan_snapshots id sequence fix; table never created after f3c8 trim.

Revision ID: b2e4f8a1c903
Revises: f3c8a91d2e04
Create Date: 2026-04-10

"""

from typing import Sequence, Union

revision: str = "b2e4f8a1c903"
down_revision: Union[str, None] = "f3c8a91d2e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
