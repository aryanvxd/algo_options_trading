"""reference data fetched on the day: previous closes and Kite 1-minute candles

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30 12:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('prev_closes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('instrument_token', sa.BigInteger(), nullable=False),
    sa.Column('symbol', sa.String(length=40), nullable=False),
    sa.Column('prev_close', sa.Float(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('captured_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('prev_closes', schema=None) as batch_op:
        batch_op.create_index('ix_prev_close_day_token', ['trade_date', 'instrument_token'], unique=True)
    op.create_table('intraday_bars',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('instrument_token', sa.BigInteger(), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('open', sa.Float(), nullable=True),
    sa.Column('high', sa.Float(), nullable=True),
    sa.Column('low', sa.Float(), nullable=True),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('intraday_bars', schema=None) as batch_op:
        batch_op.create_index('ix_bars_token_ts', ['instrument_token', 'ts'], unique=True)
        batch_op.create_index('ix_bars_day', ['trade_date'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('intraday_bars', schema=None) as batch_op:
        batch_op.drop_index('ix_bars_day')
        batch_op.drop_index('ix_bars_token_ts')
    op.drop_table('intraday_bars')
    with op.batch_alter_table('prev_closes', schema=None) as batch_op:
        batch_op.drop_index('ix_prev_close_day_token')
    op.drop_table('prev_closes')
