"""从项目根目录 .env 读取配置。密钥不写进代码。"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# 探测记录：本机进程里若有无效 ARK_API_KEY 会盖掉文件。强制以项目 .env 为准。
load_dotenv(PROJECT_ROOT / ".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 15432
    POSTGRES_USER: str = "aigc"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "aigc"

    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 16379
    REDIS_PASSWORD: str = ""

    MINIO_ENDPOINT: str = "127.0.0.1:19000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "aigc-images"
    MINIO_CONSOLE: str = "http://127.0.0.1:19001"
    MINIO_PUBLIC_BASE_URL: str = ""

    ARK_API_KEY: str = ""
    ARK_IMAGE_ENDPOINT: str = ""
    ARK_VIDEO_ENDPOINT: str = ""
    ARK_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"

    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    SECRET_KEY: str = "change-me"
    SESSION_TTL_SECONDS: int = 86400
    CORS_ORIGINS: str = "http://127.0.0.1:5173,http://localhost:5173"
    SESSION_COOKIE: str = "studio_session"
    APP_ENV: str = "development"

    # 淘宝客物料搜索。留空则该来源显示待连接，不回退到虚构商品。
    TAOBAO_APP_KEY: str = ""
    TAOBAO_APP_SECRET: str = ""
    TAOBAO_ADZONE_ID: str = ""

    @property
    def allow_fixture_sources(self) -> bool:
        return self.APP_ENV.strip().lower() != "production"

    @property
    def database_url(self) -> str:
        pwd = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{pwd}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
