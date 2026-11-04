"""documents.checklist_item_id, profiles.must_change_password

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('must_change_password', sa.Boolean(), server_default=sa.false(), nullable=True))
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('checklist_item_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_documents_checklist_item_id'), ['checklist_item_id'], unique=False)
        batch_op.create_foreign_key('fk_documents_checklist_item_id', 'onboarding_checklists', ['checklist_item_id'], ['id'])


def downgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_documents_checklist_item_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_documents_checklist_item_id'))
        batch_op.drop_column('checklist_item_id')
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.drop_column('must_change_password')
