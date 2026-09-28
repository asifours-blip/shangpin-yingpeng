"""采集适配器契约。一个适配器只覆盖它真正能返回的数据集。"""

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class SourceError(Exception):
    code = "source_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class CredentialsMissing(SourceError):
    code = "credentials_missing"


class CapabilityUnavailable(SourceError):
    code = "capability_unavailable"


class FixtureDisabled(SourceError):
    code = "fixture_disabled"


class CollectionQuery(BaseModel):
    provider: str
    item_kind: Literal["product", "post"]
    category: str = ""
    query: str = ""
    window: str = "7d"
    sort_metric: str = "total_sales"
    max_items: int = Field(default=100, ge=1, le=100)
    provider_settings: dict[str, Any] = Field(default_factory=dict)


class SourceItemIn(BaseModel):
    platform: str
    item_kind: Literal["product", "post"]
    external_id: str
    url: str | None = None
    title: str | None = None
    text_excerpt: str | None = None
    media_refs: list[str] = Field(default_factory=list)
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    source_rank: int | None = None
    raw_payload: dict[str, Any] | None = None
    rights_scope: str = "provider_terms"


class SourcePage(BaseModel):
    items: list[SourceItemIn]
    next_cursor: str | None = None
    scope_description: str
    provider_response_version: str = ""


class SourceAdapter(Protocol):
    provider: str

    def capabilities(self) -> set[str]: ...

    def ensure_ready(self, config: CollectionQuery) -> None: ...

    def fetch_page(self, config: CollectionQuery, cursor: str | None) -> SourcePage: ...
