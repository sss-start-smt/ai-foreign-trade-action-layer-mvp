"""enforce NOT NULL tenant contract for D11 async/review tables

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-08-16 21:20:00.000000

D11 security revalidation showed that application-level organization filters are
not sufficient if the persistence schema itself still allows tenant-less rows.
This revision makes organization_id mandatory on the three D11 intermediate
state tables after quarantining legacy rows whose tenant cannot be proven.

The quarantine value is intentionally not a real tenant id and is never mapped
to an authenticated organization. Existing ambiguous rows therefore remain
inaccessible to normal tenant-scoped queries instead of being guessed into a
customer organization.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "k2l3m4n5o6p7"
down_revision: Union[str, None] = "j1k2l3m4n5o6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUARANTINE_ORG = "__FLOWORDER_QUARANTINE__"
TENANT_TABLES = ("intake_jobs", "source_messages", "candidate_reviews")


def _require_column(table_name: str, column_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    if column_name not in columns:
        raise RuntimeError(
            f"D11 tenant migration precondition failed: {table_name}.{column_name} is missing. "
            "Do not continue with a partially applied schema."
        )


def _quarantine_unknown_rows(table_name: str) -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"UPDATE {table_name} "
            "SET organization_id = :quarantine "
            "WHERE organization_id IS NULL OR TRIM(organization_id) = ''"
        ),
        {"quarantine": QUARANTINE_ORG},
    )


def upgrade() -> None:
    for table_name in TENANT_TABLES:
        _require_column(table_name, "organization_id")
        _quarantine_unknown_rows(table_name)

    # batch_alter_table works for SQLite test/upgrade smoke runs and emits a
    # normal ALTER COLUMN on PostgreSQL. No server default is added: every new
    # row must receive organization_id from authenticated server-side context.
    for table_name in TENANT_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.String(),
                nullable=False,
            )


def downgrade() -> None:
    # Downgrade only relaxes nullability. Quarantined rows are intentionally not
    # converted back to NULL because that would discard the explicit safety
    # provenance introduced by this migration.
    for table_name in reversed(TENANT_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "organization_id",
                existing_type=sa.String(),
                nullable=True,
            )
