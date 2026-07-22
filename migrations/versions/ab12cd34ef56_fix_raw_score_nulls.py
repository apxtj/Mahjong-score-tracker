"""fix raw score null defaults

Revision ID: ab12cd34ef56
Revises: fb91e73a5159
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ab12cd34ef56'
down_revision = 'fb91e73a5159'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE title SET required_total_raw_score = 0 WHERE required_total_raw_score IS NULL"
    )
    op.execute(
        "UPDATE title SET required_max_raw_score = 0 WHERE required_max_raw_score IS NULL"
    )
    op.execute(
        "UPDATE title SET required_min_raw_score = 0 WHERE required_min_raw_score IS NULL"
    )

    with op.batch_alter_table('title', schema=None) as batch_op:
        batch_op.alter_column(
            'required_total_raw_score',
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.alter_column(
            'required_max_raw_score',
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.alter_column(
            'required_min_raw_score',
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table('title', schema=None) as batch_op:
        batch_op.alter_column(
            'required_min_raw_score',
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.alter_column(
            'required_max_raw_score',
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.alter_column(
            'required_total_raw_score',
            existing_type=sa.Integer(),
            nullable=True,
        )
