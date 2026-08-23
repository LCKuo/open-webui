"""merge upstream and Interact migration heads

Revision ID: e7f9a1c2d3b4
Revises: d6e8f0a2b4c6, f0bd01a18a3d
Create Date: 2026-08-21

"""

from collections.abc import Sequence

revision: str = 'e7f9a1c2d3b4'
down_revision: tuple[str, str] = ('d6e8f0a2b4c6', 'f0bd01a18a3d')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
