"""Create normalized facts, day coverage, import history, browser sessions and widgets."""
import sqlalchemy as sa
from alembic import op


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def day_key():
    return [
        sa.Column("dataset", sa.String(4), primary_key=True),
        sa.Column("business_date", sa.Date(), primary_key=True),
    ]


def upgrade():
    timestamp = sa.DateTime(timezone=True)
    op.create_table("snapshots", *day_key(),
        sa.Column("generated_at", timestamp, nullable=False),
        sa.Column("received_at", timestamp, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False))
    op.create_table("facts", *day_key(),
        sa.Column("line_id", sa.String(160), primary_key=True),
        sa.Column("document_id", sa.String(160), nullable=False),
        sa.Column("store_id", sa.String(160), nullable=False),
        sa.Column("store_name", sa.String(240), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("product_name", sa.String(240), nullable=False),
        sa.Column("category_id", sa.String(160), nullable=False),
        sa.Column("category_name", sa.String(240), nullable=False),
        sa.Column("operation", sa.String(6), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.CheckConstraint("operation IN ('sale','return')", name="ck_facts_operation"),
        sa.CheckConstraint("amount >= 0 AND quantity >= 0", name="ck_facts_unsigned"))
    op.create_index("ix_facts_store", "facts", ["dataset", "store_id", "business_date"])
    op.create_index("ix_facts_category", "facts", ["dataset", "category_id", "business_date"])
    op.create_table("day_issues", *day_key(),
        sa.Column("generated_at", timestamp, nullable=True),
        sa.Column("received_at", timestamp, nullable=False),
        sa.Column("code", sa.String(40), nullable=False))
    op.create_table("sync_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset", sa.String(4), nullable=False),
        sa.Column("at", timestamp, nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=True))
    op.create_index("ix_sync_events_dataset_id", "sync_events", ["dataset", "id"])
    op.create_table("web_sessions",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("credential_tag", sa.String(64), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False))
    op.create_table("widgets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset", sa.String(4), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False))
    op.create_index("ix_widgets_owner", "widgets", ["dataset", "owner"])


def downgrade():
    for table in ("widgets", "web_sessions", "sync_events", "day_issues", "facts", "snapshots"):
        op.drop_table(table)
