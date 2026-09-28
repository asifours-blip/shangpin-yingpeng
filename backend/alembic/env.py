from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base
from app.models import (  # noqa: F401
    Appeal,
    Campaign,
    CampaignRun,
    CollectionConfig,
    CollectionRun,
    ContentVariant,
    CopywritingOperation,
    FreezeEvent,
    GenerationTask,
    GenerationTaskAsset,
    ImageAsset,
    Notification,
    OwnedProduct,
    PipelineStep,
    PlatformConnection,
    ProductFactVersion,
    PromptOperation,
    SocialAccount,
    SocialContact,
    SourceItem,
    User,
    VariantAsset,
)
from app.models.publish import PublishJob  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": settings.database_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
