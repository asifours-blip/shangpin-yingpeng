"""独立 Worker 进程：轮询数据库领取 queued 任务。

不要用 Celery / RabbitMQ。启动：

    cd backend
    .venv\\Scripts\\python -m app.worker
"""

import sys
import time
import traceback

from app.core.db import SessionLocal
from app.core.minio_client import ensure_bucket
from app.services.worker_loop import claim_one, mark_stale_running, process_task

POLL_SECONDS = 2.0


def main() -> None:
    ensure_bucket()
    print("商品影棚 worker 已启动，等待 queued 任务…", flush=True)
    while True:
        db = SessionLocal()
        try:
            # 先把超时 running 标 unknown（不重投），再领取新任务
            stale_ids = mark_stale_running(db)
            if stale_ids:
                print(
                    f"超时 running → unknown（未自动重发）: {stale_ids}",
                    flush=True,
                )

            task_id = claim_one(db)
            if task_id is None:
                time.sleep(POLL_SECONDS)
                continue
            print(f"领取任务 {task_id}", flush=True)
            process_task(db, task_id)
            print(f"任务 {task_id} 处理结束", flush=True)
        except KeyboardInterrupt:
            print("worker 退出", flush=True)
            db.close()
            sys.exit(0)
        except Exception:
            traceback.print_exc()
            time.sleep(POLL_SECONDS)
        finally:
            db.close()


if __name__ == "__main__":
    main()
