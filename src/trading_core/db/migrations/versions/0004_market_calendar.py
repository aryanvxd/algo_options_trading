"""market calendar: events and source fetch status

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29 14:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('calendar_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('event_key', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=12), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=False),
    sa.Column('all_day', sa.Boolean(), nullable=False),
    sa.Column('country', sa.String(length=8), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('impact', sa.String(length=10), nullable=True),
    sa.Column('forecast', sa.String(length=40), nullable=True),
    sa.Column('previous', sa.String(length=40), nullable=True),
    sa.Column('actual', sa.String(length=40), nullable=True),
    sa.Column('symbol', sa.String(length=40), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('calendar_events', schema=None) as batch_op:
        batch_op.create_index('ix_calendar_key', ['source', 'event_key'], unique=True)
        batch_op.create_index('ix_calendar_ts', ['ts'], unique=False)
    op.create_table('calendar_sources',
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('source')
    )


def downgrade() -> None:
    op.drop_table('calendar_sources')
    with op.batch_alter_table('calendar_events', schema=None) as batch_op:
        batch_op.drop_index('ix_calendar_ts')
        batch_op.drop_index('ix_calendar_key')
    op.drop_table('calendar_events')
