"""分页采集、去重、如实计数。局部失败不能标成完整成功。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.integrations.sources.base import (
    CollectionQuery,
    SourceError,
    SourceItemIn,
    SourcePage,
)
from app.integrations.sources.registry import get_adapter
from app.models.source import CollectionConfig, CollectionRun, SourceItem

FetchPage = Callable[[str | None], SourcePage]
MAX_PAGES = 20


@dataclass
class AccumulateResult:
    items: list[SourceItemIn] = field(default_factory=list)
    status: str = "succeeded"
    error_code: str | None = None
    error_summary: str | None = None
    cursor_checkpoint: str | None = None
    scope_description: str = ""
    provider_response_version: str = ""
    dropped_duplicate: int = 0
    dropped_invalid: int = 0

    @property
    def actual_count(self) -> int:
        return len(self.items)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def stable_external_id(external_id: str | None, url: str | None) -> str | None:
    if external_id and external_id.strip():
        return external_id.strip()
    if url and url.strip():
        digest = hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()
        return digest[:32]
    return None


def item_is_valid(item: SourceItemIn) -> bool:
    if item.item_kind not in {"product", "post"}:
        return False
    if not stable_external_id(item.external_id, item.url):
        return False
    return bool((item.title or "").strip() or (item.text_excerpt or "").strip())


def accumulate(fetch: FetchPage, *, max_items: int) -> AccumulateResult:
    if not 1 <= max_items <= 100:
        raise ValueError("max_items 必须在 1 到 100")
    seen: set[tuple[str, str, str]] = set()
    accepted: list[SourceItemIn] = []
    dropped_duplicate = 0
    dropped_invalid = 0
    cursor: str | None = None
    scope = ""
    version = ""
    try:
        for _ in range(MAX_PAGES):
            page = fetch(cursor)
            scope = page.scope_description or scope
            version = page.provider_response_version or version
            for raw in page.items:
                if len(accepted) >= max_items:
                    break
                external_id = stable_external_id(raw.external_id, raw.url)
                if not item_is_valid(raw) or not external_id:
                    dropped_invalid += 1
                    continue
                key = (raw.platform, raw.item_kind, external_id)
                if key in seen:
                    dropped_duplicate += 1
                    continue
                seen.add(key)
                accepted.append(
                    raw.model_copy(
                        update={
                            "external_id": external_id,
                            "source_rank": raw.source_rank or len(accepted) + 1,
                        }
                    )
                )
            if len(accepted) >= max_items or not page.next_cursor:
                return AccumulateResult(
                    items=accepted,
                    status="succeeded",
                    cursor_checkpoint=None,
                    scope_description=scope,
                    provider_response_version=version,
                    dropped_duplicate=dropped_duplicate,
                    dropped_invalid=dropped_invalid,
                )
            cursor = page.next_cursor
        return AccumulateResult(
            items=accepted,
            status="partial",
            error_code="page_limit",
            error_summary="分页未完成：达到单次页数上限，未把本轮标成完整成功",
            cursor_checkpoint=cursor,
            scope_description=scope,
            provider_response_version=version,
            dropped_duplicate=dropped_duplicate,
            dropped_invalid=dropped_invalid,
        )
    except SourceError as exc:
        status = "partial" if accepted else "failed"
        return AccumulateResult(
            items=accepted,
            status=status,
            error_code=exc.code,
            error_summary=exc.message,
            cursor_checkpoint=cursor,
            scope_description=scope,
            provider_response_version=version,
            dropped_duplicate=dropped_duplicate,
            dropped_invalid=dropped_invalid,
        )


def query_from_config(config: CollectionConfig) -> CollectionQuery:
    return CollectionQuery(
        provider=config.provider,
        item_kind=config.item_kind,  # type: ignore[arg-type]
        category=config.category,
        query=config.query,
        window=config.window,
        sort_metric=config.sort_metric,
        max_items=config.max_items,
        provider_settings=dict(config.provider_settings or {}),
    )


def execute_collection(db: Session, config: CollectionConfig) -> CollectionRun:
    now = datetime.now(timezone.utc)
    run = CollectionRun(
        config_id=config.id,
        started_at=now,
        status="running",
        actual_count=0,
        scope_description="",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    query = query_from_config(config)
    run_id = run.id
    db.rollback()  # End the post-refresh read transaction before any provider I/O.
    try:
        adapter = get_adapter(query.provider)
        adapter.ensure_ready(query)

        def fetch(cursor: str | None) -> SourcePage:
            return adapter.fetch_page(query, cursor)

        result = accumulate(fetch, max_items=query.max_items)
    except SourceError as exc:
        result = AccumulateResult(
            status="failed",
            error_code=exc.code,
            error_summary=exc.message,
        )
    except Exception as exc:  # noqa: BLE001 — 外部调用失败必须落成失败批次
        result = AccumulateResult(status="failed", error_code="transport", error_summary=str(exc))

    run = db.get(CollectionRun, run_id)
    existing: set[tuple[str, str, str]] = set()
    stored = 0
    for item in result.items:
        key = (item.platform, item.item_kind, item.external_id)
        if key in existing:
            result.dropped_duplicate += 1
            continue
        existing.add(key)
        stored += 1
        db.add(
            SourceItem(
                run_id=run.id,
                platform=item.platform,
                item_kind=item.item_kind,
                external_id=item.external_id,
                url=item.url,
                title=item.title,
                text_excerpt=item.text_excerpt,
                media_refs=list(item.media_refs),
                raw_metrics=dict(item.raw_metrics),
                observed_at=item.observed_at or now,
                source_rank=item.source_rank,
                raw_payload=item.raw_payload,
                rights_scope=item.rights_scope,
            )
        )
    run.status = result.status
    run.actual_count = stored
    run.cursor_checkpoint = result.cursor_checkpoint
    run.error_summary = result.error_summary
    run.provider_response_version = result.provider_response_version or None
    run.scope_description = result.scope_description
    run.dropped_duplicate = result.dropped_duplicate
    run.dropped_invalid = result.dropped_invalid
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run
