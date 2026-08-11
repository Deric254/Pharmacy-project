"""add ai_conversations and ai_conversation_messages

Revision ID: 0025_ai_conversations
Revises: 0024_stock_take_cancel
Create Date: 2026-08-04

Adds persisted, per-user AI assistant chat history. Previously every
/ai/ask call was a one-shot, stateless request -- the frontend only
held the current thread in local React state, lost on refresh, with
no way to start a fresh thread or delete an old one. These two tables
back that: one row per conversation (owned by exactly one user, never
shared -- see AIConversation's docstring for why), one row per
prompt/answer turn within it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_ai_conversations"
down_revision: str | None = "0024_stock_take_cancel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ai_conversations_user_id", "ai_conversations", ["user_id"])

    op.create_table(
        "ai_conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("ai_conversations.id"),
            nullable=False,
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("provider_used", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_ai_conversation_messages_conversation_id",
        "ai_conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_ai_conversation_messages_created_at",
        "ai_conversation_messages",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_conversation_messages_created_at", table_name="ai_conversation_messages")
    op.drop_index(
        "ix_ai_conversation_messages_conversation_id", table_name="ai_conversation_messages"
    )
    op.drop_table("ai_conversation_messages")
    op.drop_index("ix_ai_conversations_user_id", table_name="ai_conversations")
    op.drop_table("ai_conversations")
