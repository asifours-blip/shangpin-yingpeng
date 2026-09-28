"""抖音封面、视频与创建的独立执行节点。

Revision ID: 0011_publish_http_stages
Revises: 0010_publish_jobs
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_publish_http_stages"
down_revision: Union[str, None] = "0010_publish_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STRINGS = (
    ("target_open_id", 128), ("cover_image_id", 256), ("video_upload_id", 256),
    ("content_item_id", 256), ("content_video_id", 256),
    ("cover_sha256", 64), ("video_sha256", 64),
    ("cover_log_id", 256), ("video_log_id", 256), ("create_log_id", 256),
)
_TIMES = (
    "cover_intent_at", "cover_uploaded_at", "video_intent_at", "video_uploaded_at",
    "create_intent_at", "create_accepted_at",
    "next_attempt_at",
)


def upgrade() -> None:
    op.add_column("publish_jobs", sa.Column("phase", sa.String(32), nullable=False, server_default="pending_cover"))
    for name, size in _STRINGS:
        op.add_column("publish_jobs", sa.Column(name, sa.String(size), nullable=True))
    for name in _TIMES:
        op.add_column("publish_jobs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    # 5A 的所有存量任务都没有独立节点/冻结账号，保留旧字段而不猜测产物类型。
    op.execute("UPDATE publish_jobs SET phase = 'legacy_unknown'")


def downgrade() -> None:
    for name in reversed(_TIMES):
        op.drop_column("publish_jobs", name)
    for name, _size in reversed(_STRINGS):
        op.drop_column("publish_jobs", name)
    op.drop_column("publish_jobs", "phase")
