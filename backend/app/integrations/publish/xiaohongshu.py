"""小红书。没有核到可代发笔记的服务端接口，保持待发布而不是假装已发。

2026-09-26 核对：
- xhs.postNote https://miniapp.xiaohongshu.com/doc/DC604617
  只能在小程序小组件里唤起客户端发布页，客户端版本 >= 8.94，不是服务端代发。
- 小程序介绍 https://miniapp.xiaohongshu.com/doc/DC137160
  笔记发布仍是用户在小程序里自己发出去。
- 电商开放平台 https://open.xiaohongshu.com/document/developer/file/42
  SDK 是商品、订单、库存和售后，不是笔记发布。
"""

from __future__ import annotations

from typing import Any

from app.integrations.publish.base import PublishAdapter, PublishContext, PublishOutcome

POST_NOTE_DOC = "https://miniapp.xiaohongshu.com/doc/DC604617"
MINIAPP_DOC = "https://miniapp.xiaohongshu.com/doc/DC137160"
ECOM_DOC = "https://open.xiaohongshu.com/document/developer/file/42"

MISSING = [
    "待连接：没有核验过的服务端笔记发布接口",
    "xhs.postNote 只能在小程序小组件里唤起客户端发布页，不能当作服务端代发",
    "open.xiaohongshu.com 现有文档是电商商品和订单，不是笔记发布",
]


class XiaohongshuPublishAdapter(PublishAdapter):
    platform = "xiaohongshu"

    def catalog(self) -> dict[str, Any]:
        return {
            "platform": "xiaohongshu",
            "account_type": "尚未核到可用于服务端代发的账号类型",
            "server_publish": False,
            "readiness_when_approved": "approved_ready_to_publish",
            "sources": [POST_NOTE_DOC, MINIAPP_DOC, ECOM_DOC],
            "missing": list(MISSING),
            "limits": [],
            "endpoints": [],
            "query_reliable": False,
            "live": False,
            "implemented": False,
        }

    def check_capability(self, connection) -> list[str]:
        del connection
        return list(MISSING)

    def _hold(self) -> PublishOutcome:
        return PublishOutcome(
            kind="not_ready",
            error_code="approved_ready_to_publish",
            error_message="小红书没有可核验的发布接口，保持待发布，不会生成平台编号",
        )

    def upload(self, ctx: PublishContext) -> PublishOutcome:
        del ctx
        return self._hold()

    def create_content(self, ctx: PublishContext) -> PublishOutcome:
        del ctx
        return self._hold()

    def query_status(self, ctx: PublishContext) -> PublishOutcome:
        del ctx
        return PublishOutcome(
            kind="unknown",
            error_code="publish_unknown",
            error_message="小红书没有可核验的发布查询，不会自动重新提交",
        )

    def submit(self, ctx: PublishContext) -> PublishOutcome:
        del ctx
        return self._hold()
