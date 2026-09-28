"""Run a bounded number of real pipeline steps, then exit; no resident worker."""

from app.core.db import SessionLocal
from app.pipeline_worker import tick


for count in range(1, 21):
    with SessionLocal() as db:
        step_id = tick(db)
    if step_id is None:
        print(f"public_flow_tick_complete steps={count - 1}")
        break
    print(f"public_flow_step={step_id}")
else:
    raise SystemExit("bounded tick limit reached")
