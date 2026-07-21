"""add permanent title achievements

Revision ID: c2d4e8a91f10
Revises: f889586b403b
Create Date: 2026-07-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c2d4e8a91f10'
down_revision = 'f889586b403b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('title', schema=None) as batch_op:
        batch_op.add_column(sa.Column('required_max_consecutive_top1', sa.Integer(), nullable=True, server_default='0'))
        batch_op.add_column(sa.Column('required_max_consecutive_last', sa.Integer(), nullable=True, server_default='0'))
        batch_op.add_column(sa.Column('is_permanent', sa.Boolean(), nullable=True, server_default='0'))

    op.create_table(
        'player_title_achievement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('title_id', sa.Integer(), nullable=False),
        sa.Column('unlocked_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['player_id'], ['player.id']),
        sa.ForeignKeyConstraint(['title_id'], ['title.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_id', 'title_id', name='uq_player_title_achievement'),
    )


def downgrade():
    op.drop_table('player_title_achievement')
    with op.batch_alter_table('title', schema=None) as batch_op:
        batch_op.drop_column('is_permanent')
        batch_op.drop_column('required_max_consecutive_last')
        batch_op.drop_column('required_max_consecutive_top1')
