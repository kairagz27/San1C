"""Separate source administration from owner sessions and retain reconciliation evidence."""
import sqlalchemy as sa
from alembic import op

revision = "0002_pilot"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("web_sessions", sa.Column("role", sa.String(8), nullable=False, server_default="owner"))
    source = op.create_table(
        "source_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_check_status", sa.String(32)),
    )
    op.bulk_insert(source, [{"id": 1, "managed": False}])
    op.create_table(
        "reconciliations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_name", sa.String(200), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("point", sa.String(160)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("reconciliations")
    op.drop_table("source_access")
    op.drop_column("web_sessions", "role")
