"""expand artifact payload column to LONGBLOB

Revision ID: 0004_artifact_longblob
Revises: 0003_worker_accuracy
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0004_artifact_longblob"
down_revision = "0003_worker_accuracy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ml_model_artifacts",
        "artifact_payload",
        existing_type=sa.LargeBinary(),
        type_=mysql.LONGBLOB(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "ml_model_artifacts",
        "artifact_payload",
        existing_type=mysql.LONGBLOB(),
        type_=sa.LargeBinary(),
        existing_nullable=False,
    )
