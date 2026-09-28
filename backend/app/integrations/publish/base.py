"""发布适配器。产品路径不调用未核验的端点，也不编造平台结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class PublishContext:
    platform: str
    copy_snapshot: dict[str, Any]
    asset_order: list[dict[str, Any]]
    local_request_id: str
    connection_id: int | None
    provider_request_id: str | None = None
    asset_bytes: tuple[bytes, ...] = ()
    ensure_claim: Callable[[], bool] | None = None
    ensure_authorization: Callable[[], bool] | None = None
    target_open_id: str | None = None
    cover_image_id: str | None = None
    video_upload_id: str | None = None


@dataclass(frozen=True)
class PublishOutcome:
    """kind 不是公开发布结论。uploaded / create_accepted 都不等于 published。"""

    kind: str
    provider_request_id: str | None = None
    provider_video_id: str | None = None
    cover_image_id: str | None = None
    video_upload_id: str | None = None
    content_item_id: str | None = None
    content_video_id: str | None = None
    log_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


class PublishAdapter:
    platform = ""

    def catalog(self) -> dict[str, Any]:
        raise NotImplementedError

    def check_capability(self, connection) -> list[str]:
        """账号能力检查。返回具体缺项；空列表表示这项检查通过。"""
        raise NotImplementedError

    def missing_requirements(self, connection) -> list[str]:
        return self.check_capability(connection)

    def upload(self, ctx: PublishContext) -> PublishOutcome:
        raise NotImplementedError

    def create_content(self, ctx: PublishContext) -> PublishOutcome:
        raise NotImplementedError

    def query_status(self, ctx: PublishContext) -> PublishOutcome:
        raise NotImplementedError

    def submit(self, ctx: PublishContext) -> PublishOutcome:
        """有上游任务号时只查询。默认不发网络请求。"""
        if ctx.provider_request_id:
            return self.query_status(ctx)
        uploaded = self.upload(ctx)
        if uploaded.kind != "uploaded":
            return uploaded
        if ctx.ensure_claim is not None and not ctx.ensure_claim():
            return PublishOutcome(kind="unknown", error_code="lease_lost", error_message="领取权已失效，停止创建")
        created = self.create_content(
            PublishContext(
                platform=ctx.platform,
                copy_snapshot=ctx.copy_snapshot,
                asset_order=ctx.asset_order,
                local_request_id=ctx.local_request_id,
                connection_id=ctx.connection_id,
                provider_request_id=uploaded.provider_request_id,
                asset_bytes=ctx.asset_bytes,
                ensure_claim=ctx.ensure_claim,
            )
        )
        return created
