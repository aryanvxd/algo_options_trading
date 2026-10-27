"""capital used per position, entry/exit spot, journal notes

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27 22:45:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('capital_required', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('capital_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('spot_at_entry', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('spot_at_exit', sa.Float(), nullable=True))

    op.create_table('journal_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('algo_id', sa.String(length=40), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('position_id', sa.Integer(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['position_id'], ['positions.position_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('journal_notes', schema=None) as batch_op:
        batch_op.create_index('ix_notes_algo_date', ['algo_id', 'trade_date'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('journal_notes', schema=None) as batch_op:
        batch_op.drop_index('ix_notes_algo_date')
    op.drop_table('journal_notes')

    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_column('spot_at_exit')
        batch_op.drop_column('spot_at_entry')
        batch_op.drop_column('capital_source')
        batch_op.drop_column('capital_required')
