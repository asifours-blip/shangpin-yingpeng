"""仅处理发布任务；默认 PUBLISH_LIVE 关闭，绝不启动生成流水线。"""

from __future__ import annotations

import argparse
import time

from app.core.db import SessionLocal
from app.services.publish_jobs import reconcile_expired, run_due


def tick() -> None:
    with SessionLocal() as session:
        reconcile_expired(session)
        run_due(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="发布任务 worker；真实 HTTP 须单独配置 PUBLISH_LIVE=1")
    parser.add_argument("--once", action="store_true", help="只处理一次过期对账和一个到期节点")
    args = parser.parse_args()
    while True:
        tick()
        if args.once:
            return
        time.sleep(2)


if __name__ == "__main__":
    main()
