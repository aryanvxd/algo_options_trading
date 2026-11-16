"""swing: watchlists, the picks journal and fundamentals

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('watchlists',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('watchlist_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('watchlist_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=40), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('entry', sa.Float(), nullable=True),
    sa.Column('stop', sa.Float(), nullable=True),
    sa.Column('target', sa.Float(), nullable=True),
    sa.Column('price_added', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('watchlist_items', schema=None) as batch_op:
        batch_op.create_index('ix_watch_item', ['watchlist_id', 'symbol'], unique=True)
    op.create_table('swing_picks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scan_date', sa.Date(), nullable=False),
    sa.Column('symbol', sa.String(length=40), nullable=False),
    sa.Column('industry', sa.String(length=80), nullable=True),
    sa.Column('sector', sa.String(length=40), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('is_twin', sa.Boolean(), nullable=False),
    sa.Column('twin_of', sa.Integer(), nullable=True),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('atr', sa.Float(), nullable=False),
    sa.Column('entry', sa.Float(), nullable=False),
    sa.Column('stop', sa.Float(), nullable=False),
    sa.Column('target', sa.Float(), nullable=False),
    sa.Column('rr', sa.Float(), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('parts', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('fill_date', sa.Date(), nullable=True),
    sa.Column('fill_price', sa.Float(), nullable=True),
    sa.Column('exit_date', sa.Date(), nullable=True),
    sa.Column('exit_price', sa.Float(), nullable=True),
    sa.Column('last_close', sa.Float(), nullable=True),
    sa.Column('return_pct', sa.Float(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('swing_picks', schema=None) as batch_op:
        batch_op.create_index('ix_swing_pick', ['scan_date', 'symbol', 'is_twin'], unique=True)
        batch_op.create_index('ix_swing_pick_status', ['status'], unique=False)
    op.create_table('fundamentals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=40), nullable=False),
    sa.Column('metric', sa.String(length=60), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fundamentals', schema=None) as batch_op:
        batch_op.create_index('ix_fundamental', ['symbol', 'metric'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('fundamentals', schema=None) as batch_op:
        batch_op.drop_index('ix_fundamental')
    op.drop_table('fundamentals')
    with op.batch_alter_table('swing_picks', schema=None) as batch_op:
        batch_op.drop_index('ix_swing_pick_status')
        batch_op.drop_index('ix_swing_pick')
    op.drop_table('swing_picks')
    with op.batch_alter_table('watchlist_items', schema=None) as batch_op:
        batch_op.drop_index('ix_watch_item')
    op.drop_table('watchlist_items')
    op.drop_table('watchlists')
