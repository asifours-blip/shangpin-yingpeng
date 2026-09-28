"""商品影棚 API。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin,
    appeals,
    assets,
    auth,
    collections,
    copywriting,
    campaigns,
    generations,
    notifications,
    operation_plans,
    products,
    publish_accounts,
    prompts,
    social,
)
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.minio_client import ensure_bucket
from app.services.seed import seed_users_if_empty


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_bucket()
    db = SessionLocal()
    try:
        seed_users_if_empty(db)
    finally:
        db.close()
    yield


app = FastAPI(title="商品影棚", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(generations.router)
app.include_router(prompts.router)
app.include_router(admin.router)
app.include_router(appeals.router)
app.include_router(notifications.router)
app.include_router(social.router)
app.include_router(copywriting.router)
app.include_router(copywriting.admin_router)
app.include_router(collections.router)
app.include_router(products.router)
app.include_router(campaigns.router)
app.include_router(operation_plans.router)
app.include_router(publish_accounts.router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
