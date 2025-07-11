"""initial migration

Revision ID: 6b1161ea1e80
Revises: 
Create Date: 2025-07-08 03:00:20.110643

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6b1161ea1e80'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('player',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('total_score', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('game',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=True),
    sa.Column('player1_id', sa.Integer(), nullable=True),
    sa.Column('player2_id', sa.Integer(), nullable=True),
    sa.Column('player3_id', sa.Integer(), nullable=True),
    sa.Column('player4_id', sa.Integer(), nullable=True),
    sa.Column('score1', sa.Integer(), nullable=True),
    sa.Column('score2', sa.Integer(), nullable=True),
    sa.Column('score3', sa.Integer(), nullable=True),
    sa.Column('score4', sa.Integer(), nullable=True),
    sa.Column('oka', sa.Integer(), nullable=True),
    sa.Column('uma1', sa.Integer(), nullable=True),
    sa.Column('uma2', sa.Integer(), nullable=True),
    sa.Column('uma3', sa.Integer(), nullable=True),
    sa.Column('uma4', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['player1_id'], ['player.id'], ),
    sa.ForeignKeyConstraint(['player2_id'], ['player.id'], ),
    sa.ForeignKeyConstraint(['player3_id'], ['player.id'], ),
    sa.ForeignKeyConstraint(['player4_id'], ['player.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('game')
    op.drop_table('player')
    # ### end Alembic commands ###
