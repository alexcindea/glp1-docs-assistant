"""answers: every question asked and how it ended, with tokens, cost and timings

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answers",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("search_query", sa.Text(), nullable=False),
        sa.Column("document_ids", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("refusal_reason", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("first_token_ms", sa.Float(), nullable=True),
        sa.Column("total_ms", sa.Float(), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "(refusal_reason IS NULL) OR (error_code IS NULL)", name=op.f("ck_answers_one_outcome")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answers")),
    )


def downgrade() -> None:
    op.drop_table("answers")
