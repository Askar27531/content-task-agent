"""Initial project tables. Graph checkpoints use a separate schema and their own migrations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("studio_sessions", sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("title", sa.String(200), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("revision", sa.BigInteger(), nullable=False), sa.Column("state", JSONB(), nullable=False))
    op.create_table("studio_events", sa.Column("session_id", sa.String(64), sa.ForeignKey("studio_sessions.id"), primary_key=True),
                    sa.Column("seq", sa.BigInteger(), primary_key=True), sa.Column("type", sa.String(80), nullable=False),
                    sa.Column("payload", JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("studio_jobs", sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("session_id", sa.String(64), sa.ForeignKey("studio_sessions.id"), nullable=False),
                    sa.Column("kind", sa.String(40), nullable=False), sa.Column("status", sa.String(24), nullable=False),
                    sa.Column("payload", JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("owner", sa.String(80)), sa.Column("lease_until", sa.DateTime(timezone=True)),
                    sa.Column("lease_epoch", sa.Integer(), nullable=False), sa.Column("error", sa.Text()))
    for name in ("session_id", "kind", "status"):
        op.create_index(f"ix_studio_jobs_{name}", "studio_jobs", [name])
    op.create_table("studio_operations", sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("session_id", sa.String(64), sa.ForeignKey("studio_sessions.id"), nullable=False),
                    sa.Column("run_id", sa.String(64)), sa.Column("step_id", sa.String(64), nullable=False),
                    sa.Column("operation_key", sa.String(180), nullable=False, unique=True), sa.Column("tool_name", sa.String(80), nullable=False),
                    sa.Column("display_name", sa.String(120), nullable=False), sa.Column("server_name", sa.String(80), nullable=False),
                    sa.Column("status", sa.String(24), nullable=False), sa.Column("public_input", JSONB(), nullable=False),
                    sa.Column("output", JSONB()), sa.Column("error", sa.Text()), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("server_started_at", sa.DateTime(timezone=True)), sa.Column("duration_ms", sa.Integer()), sa.Column("attempt", sa.Integer(), nullable=False))
    for name in ("session_id", "run_id"):
        op.create_index(f"ix_studio_operations_{name}", "studio_operations", [name])
    op.create_table("studio_proposals", sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("session_id", sa.String(64), sa.ForeignKey("studio_sessions.id"), nullable=False),
                    sa.Column("run_id", sa.String(64), unique=True, nullable=False), sa.Column("version", sa.Integer(), nullable=False),
                    sa.Column("body", JSONB(), nullable=False), sa.UniqueConstraint("session_id", "version"))
    op.create_index("ix_studio_proposals_session_id", "studio_proposals", ["session_id"])


def downgrade():
    for name in ("studio_proposals", "studio_operations", "studio_jobs", "studio_events", "studio_sessions"):
        op.drop_table(name)
