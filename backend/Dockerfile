FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-wqy-zenhei ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.deploy.txt ./requirements.deploy.txt
RUN pip install --no-cache-dir -r requirements.deploy.txt

COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY deploy/container-entrypoint.sh /usr/local/bin/studio-entrypoint
COPY deploy/verify_runtime.py ./verify_runtime.py
RUN chmod 755 /usr/local/bin/studio-entrypoint

ENTRYPOINT ["studio-entrypoint"]
CMD ["api"]
