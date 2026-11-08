"""Durable cost ledger, search cache and worker session exclusion."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('studio_cost_entries',sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('charge_key',sa.String(160),nullable=False,unique=True),
        sa.Column('session_id',sa.String(64),sa.ForeignKey('studio_sessions.id'),nullable=False),
        sa.Column('run_id',sa.String(64)),sa.Column('kind',sa.String(40),nullable=False),
        sa.Column('amount',sa.Numeric(38,12)),sa.Column('unit',sa.String(20),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('usage',JSONB(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    for key in ('session_id','run_id'):op.create_index('ix_studio_cost_entries_'+key,'studio_cost_entries',[key])
    op.create_table('studio_search_cache',sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('result',JSONB(),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_studio_search_cache_expires_at','studio_search_cache',['expires_at'])
    op.create_index('uq_studio_jobs_active_session_kind','studio_jobs',['session_id','kind'],unique=True,
                    postgresql_where=sa.text("status = 'running'"))


def downgrade():
    op.drop_index('uq_studio_jobs_active_session_kind',table_name='studio_jobs')
    op.drop_table('studio_search_cache');op.drop_table('studio_cost_entries')
